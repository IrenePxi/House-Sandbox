# services/P3_ems_service.py
from __future__ import annotations

import numpy as np
import pandas as pd

from core.battery import battery_step
from core.pms import rule_power_share
from core.timeslot import (
    generate_smart_time_slots,
    assign_data_to_time_slots_single,
    mpc_opt_single,
    format_results_single,
)
from core.FCcontrol import build_fc_ref_profile_kw,smooth_fc_schedule,fc_cost_rate_dkk_per_h
from core.FCgating import (
    compute_battery_charge_cap_kw,
    apply_fc_feasibility_no_export,apply_fc_soc_gate,
)

from utils.plotting import _norm01


def compute_passive_dispatch(
    idx: pd.DatetimeIndex,
    load_tot: pd.Series,
    pv_tot: pd.Series,
    batt_cfg: dict,
    dt_h: float,
) -> dict:
    """Passive battery: charge on PV surplus, discharge on deficit (SOC/power limited)."""

    soc = float(batt_cfg.get("soc_init", 60.0)) * 0.01  # fraction 0..1

    pbat_list, soc_list, grid_list = [], [], []

    for t in range(len(idx)):
        L = float(load_tot.iloc[t])
        P = float(pv_tot.iloc[t]) if pv_tot is not None else 0.0

        p_cmd = L - P  # + discharge, - charge
        soc, p_bat = battery_step(soc, p_cmd, dt_h, batt_cfg)

        # net grid: +import, -unused PV
        g = L - P - p_bat

        pbat_list.append(p_bat)
        soc_list.append(soc)
        grid_list.append(g)

    p_bat = pd.Series(pbat_list, index=idx, name="P_bat_kW")       # +dis, -ch
    soc_frac = pd.Series(soc_list, index=idx, name="SOC_frac")     # 0..1
    grid_net = pd.Series(grid_list, index=idx, name="P_grid_net_kW")

    grid_import = grid_net.clip(lower=0.0)
    pv_unused = (-grid_net).clip(lower=0.0)

    # SOC in kWh (for plotting)
    E_kWh = float(batt_cfg.get("E_kWh", 0.0))
    soc_kwh = soc_frac * E_kWh

    return dict(
        grid_import_kw=grid_import,
        pv_unused_kw=pv_unused,
        p_bat_kw=p_bat,
        soc_kwh=soc_kwh,
        soc_end_pct=float(soc_frac.iloc[-1]) * 100.0 if len(soc_frac) else 0.0,
    )


def compute_auto_battery_plan(
    idx: pd.DatetimeIndex,
    load_tot: pd.Series,
    pv_tot: pd.Series,
    ps: pd.Series,
    cs: pd.Series,
    batt_cfg: dict,
    w_cost: float,
) -> pd.DataFrame:
    """Return plan_df with columns: start, end, soc_setpoint_pct, grid_charge_allowed."""

    price_n = _norm01(ps)
    co2_n = _norm01(cs)
    signal = (w_cost * price_n + (1 - w_cost) * co2_n).rename("device_objective")

    df_minute = pd.DataFrame({
        "DateTime": idx,
        "Load": load_tot.values,
        "PV": pv_tot.values if pv_tot is not None else np.zeros(len(idx)),
        "ElectricityPrice": ps.values,
        "signal": signal.values,
        "co2": cs.values,
    }).sort_values("DateTime")

    time_slots = generate_smart_time_slots(df_minute)
    df_slots = assign_data_to_time_slots_single(df_minute, time_slots)

    SOC0 = float(batt_cfg.get("soc_init", 60.0))
    SOC_min = float(batt_cfg.get("soc_min", 10.0))
    SOC_max = float(batt_cfg.get("soc_max", 90.0))

    Pbat_chargemax = float(batt_cfg.get("P_ch_max_kW", 0.0))
    Qbat = float(batt_cfg.get("E_kWh", 0.0))

    SOC_opt, Qgrid, _ = mpc_opt_single(
        df_slots,
        SOC0=SOC0, SOC_min=SOC_min, SOC_max=SOC_max,
        Pbat_chargemax=Pbat_chargemax,
        Qbat=Qbat,
    )

    df_plan = format_results_single(SOC_opt, Qgrid, df_slots)
    df_today = df_plan[df_plan["Datetime"] == df_plan["Datetime"].iloc[0]].copy()

    df_today["start"] = pd.to_datetime(df_today["TimeSlot"].str.split(" - ").str[0], format="%H:%M").dt.time
    df_today["end"]   = pd.to_datetime(df_today["TimeSlot"].str.split(" - ").str[1], format="%H:%M").dt.time
    df_today["soc_setpoint_pct"] = df_today["SOC"].astype(float)
    df_today["grid_charge_allowed"] = df_today["Grid_Charge"].astype(int)

    plan_df = df_today[["start", "end", "soc_setpoint_pct", "grid_charge_allowed"]].copy()
    return plan_df


def dispatch_with_plan(
    idx: pd.DatetimeIndex,
    load_tot: pd.Series,
    pv_tot: pd.Series,
    batt_cfg: dict,
    plan_df: pd.DataFrame,
    dt_h: float,
) -> dict:
    """Dispatch using PMS rule_power_share, then reconstruct unused PV and KPIs."""

    # rule_power_share uses single p_max_kw; battery has separate limits → conservative choice:
    p_ch = float(batt_cfg.get("P_ch_max_kW", 0.0))
    p_dis = float(batt_cfg.get("P_dis_max_kW", 0.0))
    p_max_kw = float(min(p_ch, p_dis))

    ems_out = rule_power_share(
        idx=idx,
        load_kw=load_tot,
        pv_kw=pv_tot,
        plan_slots=plan_df,
        cap_kwh=float(batt_cfg.get("E_kWh", 0.0)),
        p_max_kw=p_max_kw,
        soc0_kwh=(float(batt_cfg.get("soc_init", 60.0)) / 100.0) * float(batt_cfg.get("E_kWh", 0.0)),
        eta_ch=float(batt_cfg.get("eta_ch", 0.95)),
        eta_dis=float(batt_cfg.get("eta_dis", 0.95)),
        energy_pattern=2,
    )

    grid_import = ems_out["grid_import_kw"]
    p_bat = ems_out["batt_discharge_kw"] - ems_out["batt_charge_kw"]  # +dis, -ch
    soc_kwh = ems_out["batt_soc_kwh"]

    # reconstruct unused PV from net balance
    grid_net = (load_tot - pv_tot - p_bat)           # +import, -unused PV
    pv_unused = (-grid_net).clip(lower=0.0)

    soc_end_pct = (float(soc_kwh.iloc[-1]) / float(batt_cfg.get("E_kWh", 0.0)) * 100.0) if float(batt_cfg.get("E_kWh", 0.0)) > 0 else 0.0

    return dict(
        grid_import_kw=grid_import,
        pv_unused_kw=pv_unused,
        p_bat_kw=p_bat,
        soc_kwh=soc_kwh,
        soc_end_pct=soc_end_pct,
    )

def dispatch_with_plan_and_fc(
    idx, load_tot, pv_tot, ps, batt_cfg, plan_df, dt_h,
    Price_ch3oh, fc_cfg=None
):
    """
    Two-pass approach:
      Pass1: dispatch battery ignoring FC -> get soc_kwh -> estimate charge-cap
      FC: build reference (mode-dependent) + optional SOC gating + feasibility clamp (no export)
      Pass2: dispatch battery with net load (load - FC)
    Returns normal dispatch dict + extra FC series.
    """
    

    # optional heat model
    try:
        from core.FCheat import FCSimpleModel, FCSimpleModelCfg
        _HAS_HEAT = True
    except Exception:
        _HAS_HEAT = False

    fc_cfg = fc_cfg or {}

    # ---- read config (support both old/new names) ----
    schedule_mode = str(fc_cfg.get("schedule_mode", "price")).lower()  # off/price/soc/hybrid

    Pmin_W   = float(fc_cfg.get("Pmin_W",  1900.0))
    Prated_W = float(fc_cfg.get("Prated_W", 4800.0))
    fc_min_on_min  = int(fc_cfg.get("min_on_min", 60))
    fc_min_off_min = int(fc_cfg.get("min_off_min", 60))

    # For SOC gating behavior (optional)
    on_delta_pct  = float(fc_cfg.get("soc_on_delta_pct",  3.0))
    off_delta_pct = float(fc_cfg.get("soc_off_delta_pct", 0.0))

    # ---- Pass 1: baseline battery dispatch (no FC) ----
    d1 = dispatch_with_plan(idx, load_tot, pv_tot, batt_cfg, plan_df, dt_h)
    soc_kwh_1 = d1.get("soc_kwh", pd.Series(0.0, index=idx))

    # ---- build FC reference (kW) depending on mode ----
    if schedule_mode in ("off", "0", "false", "none"):
        p_fc_ref_kw = pd.Series(0.0, index=idx, name="P_fc_ref_kW")

    elif schedule_mode == "price":
        # your current behavior
        p_fc_ref_kw = build_fc_ref_profile_kw(
            idx=idx,
            elec_price=ps,
            Price_ch3oh=float(Price_ch3oh),
            Pmin_W=float(Pmin_W),
            Prated_W=float(Prated_W),
            smooth=True,
            min_on_min=int(fc_min_on_min),
            min_off_min=int(fc_min_off_min),
        )

    elif schedule_mode == "soc":
        # SOC-support: simple constant "want ON at rated" profile (then anti-cycling smooth)
        p_rated_kw = Prated_W / 1000.0
        p_fc_ref_kw = pd.Series(p_rated_kw, index=idx, name="P_fc_ref_kW")
        # enforce min on/off using the same smoother
        p_fc_ref_kw = smooth_fc_schedule(
            p_fc_ref_kw, dt_min=int(round(dt_h * 60)),  # works for minute index too
            min_on_min=fc_min_on_min,
            min_off_min=fc_min_off_min,
        )
        p_fc_ref_kw.name = "P_fc_ref_kW"

    elif schedule_mode == "hybrid":
        # Hybrid: start from price ref, then SOC-gate it later
        p_fc_ref_kw = build_fc_ref_profile_kw(
            idx=idx,
            elec_price=ps,
            Price_ch3oh=float(Price_ch3oh),
            Pmin_W=float(Pmin_W),
            Prated_W=float(Prated_W),
            smooth=True,
            min_on_min=int(fc_min_on_min),
            min_off_min=int(fc_min_off_min),
        )

    else:
        # unknown -> fall back to price
        p_fc_ref_kw = build_fc_ref_profile_kw(
            idx=idx,
            elec_price=ps,
            Price_ch3oh=float(Price_ch3oh),
            Pmin_W=float(Pmin_W),
            Prated_W=float(Prated_W),
            smooth=True,
            min_on_min=int(fc_min_on_min),
            min_off_min=int(fc_min_off_min),
        )

    # ---- optional SOC gating (only for soc/hybrid) ----
    p_fc_after_soc_kw = p_fc_ref_kw
    if schedule_mode in ("soc", "hybrid"):
        p_fc_after_soc_kw = apply_fc_soc_gate(
            idx=idx,
            p_fc_kw=p_fc_ref_kw,
            soc_kwh=soc_kwh_1,
            batt_cfg=batt_cfg,
            plan_df=plan_df,
            on_delta_pct=on_delta_pct,
            off_delta_pct=off_delta_pct,
        )

    # ---- battery absorption cap (how much extra power can be stored) ----
    if float(batt_cfg.get("E_kWh", 0.0)) > 0.0 and isinstance(soc_kwh_1, pd.Series):
        cap_bat_kw = compute_battery_charge_cap_kw(
            idx=idx,
            soc_kwh=soc_kwh_1,
            batt_cfg=batt_cfg,
            dt_h=dt_h,
        )
    else:
        cap_bat_kw = pd.Series(0.0, index=idx, name="P_bat_charge_cap_kw")

    # ---- feasibility clamp: no export ----
    p_fc_cmd_kw = apply_fc_feasibility_no_export(
        idx=idx,
        p_fc_ref_kw=p_fc_after_soc_kw,
        load_kw=load_tot,
        pv_kw=pv_tot,
        batt_charge_cap_kw=cap_bat_kw,
        Pmin_kW=Pmin_W / 1000.0,
    )
    # ---- FC fuel cost (DKK) using your equivalent-price method ----
    # fc_cost_rate_dkk_per_h(P_fc_kw, price_ch3oh) returns DKK/h at that power
    price_ch3oh = float(Price_ch3oh)

    p_fc_cost_rate_dkk_per_h = p_fc_cmd_kw.reindex(idx).fillna(0.0).clip(lower=0.0).apply(
        lambda P: float(fc_cost_rate_dkk_per_h(float(P), price_ch3oh))
    ).rename("fc_cost_rate_dkk_per_h")

    fc_cost_dkk_ts = (p_fc_cost_rate_dkk_per_h * float(dt_h)).rename("fc_cost_dkk_ts")
    fc_cost_dkk = float(fc_cost_dkk_ts.sum())


    # ---- Pass 2: re-dispatch with net load ----
    load_net = (load_tot - p_fc_cmd_kw).clip(lower=0.0)
    d2 = dispatch_with_plan(idx, load_net, pv_tot, batt_cfg, plan_df, dt_h)

    # ---- Optional: compute FC heat available (NOT "utilized" yet) ----
    p_fc_th_kw = None
    p_bop_kw = None
    m_ch3oh_kg_s = None

    if _HAS_HEAT:
        # create model (can be cached later if you want)
        eta_recovery = float(fc_cfg.get("eta_recovery", 1.0))
        heat_cfg = FCSimpleModelCfg(eta_recovery=eta_recovery)
        model = FCSimpleModel(heat_cfg)

        q_list, bop_list, m_list = [], [], []
        for pkw in p_fc_cmd_kw.values:
            out = model.compute_from_power(float(pkw))
            q_list.append(out["q_fc_kw"])
            bop_list.append(out["p_bop_kw"])
            m_list.append(out["m_ch3oh_kg_s"])

        p_fc_th_kw = pd.Series(q_list, index=idx, name="P_fc_heat_avail_kW")
        p_bop_kw = pd.Series(bop_list, index=idx, name="P_fc_bop_kW")
        m_ch3oh_kg_s = pd.Series(m_list, index=idx, name="m_ch3oh_kg_s")

    # Attach FC outputs
    d2["p_fc_ref_kw"] = p_fc_ref_kw
    d2["p_fc_soc_kw"] = p_fc_after_soc_kw
    d2["p_fc_cmd_kw"] = p_fc_cmd_kw
    d2["load_net_kw"] = load_net
    d2["fc_batt_absorb_cap_kw"] = cap_bat_kw
    # --- FC cost outputs (for KPIs/UI) ---
    d2["fc_cost_rate_dkk_per_h"] = p_fc_cost_rate_dkk_per_h
    d2["fc_cost_dkk_ts"] = fc_cost_dkk_ts
    d2["fc_cost_dkk"] = fc_cost_dkk


    # record what params were used (debug honesty)
    d2["fc_used_params"] = {
        "schedule_mode": schedule_mode,
        "min_on_min": fc_min_on_min,
        "min_off_min": fc_min_off_min,
        "Pmin_W": Pmin_W,
        "Prated_W": Prated_W,
        "Price_ch3oh": float(Price_ch3oh),
    }

    # heat (available only)
    if p_fc_th_kw is not None:
        d2["p_fc_heat_avail_kw"] = p_fc_th_kw
        d2["p_fc_bop_kw"] = p_bop_kw
        d2["m_ch3oh_kg_s"] = m_ch3oh_kg_s
    # --- NEW: choose how much FC heat goes to DHW (simple first version) ---
    if p_fc_th_kw is not None:
        dhw_share = float(fc_cfg.get("dhw_heat_share", 0.0))  # 0..1
        d2["q_fc_to_dhw_kw"] = (p_fc_th_kw * dhw_share).rename("q_fc_to_dhw_kw")
    else:
        d2["q_fc_to_dhw_kw"] = pd.Series(0.0, index=idx, name="q_fc_to_dhw_kw")

    return d2

def compute_kpis_from_dispatch(
    *,
    idx: pd.DatetimeIndex,
    pv_tot: pd.Series,
    grid_import_kw: pd.Series,
    pv_unused_kw: pd.Series,
    dt_h: float,
    total_cost_baseline: float | None,
    total_co2_baseline_kg: float | None,
    ps: pd.Series | None,
    cs: pd.Series | None,
    # ✅ NEW (optional):
    p_fc_kw: pd.Series | None = None,
    fc_cfg: dict | None = None,
    cost_fc_dkk: float | None = None,   # optional direct override
) -> dict:
    """Compute energy + utilization + (optional) cost/CO2 deltas, incl. optional FC fuel cost."""

    # --- energy KPIs ---
    pv_tot = pv_tot.reindex(idx).fillna(0.0) if pv_tot is not None else pd.Series(0.0, index=idx)
    grid_import_kw = grid_import_kw.reindex(idx).fillna(0.0)
    pv_unused_kw = pv_unused_kw.reindex(idx).fillna(0.0)

    E_pv = float((pv_tot * dt_h).sum())
    E_grid = float((grid_import_kw * dt_h).sum())
    E_unused = float((pv_unused_kw * dt_h).sum())
    pv_util = ((E_pv - E_unused) / E_pv * 100.0) if E_pv > 0 else 0.0

    # optional FC energy
    E_fc_el = None
    if isinstance(p_fc_kw, pd.Series):
        pfc = p_fc_kw.reindex(idx).fillna(0.0).clip(lower=0.0)
        E_fc_el = float((pfc * dt_h).sum())

    # --- cost ---
    cost_grid_ems = None
    if ps is not None:
        p = ps.reindex(idx, method="nearest").fillna(0.0)
        cost_grid_ems = float((grid_import_kw * p * dt_h).sum())

    # FC cost (prefer explicit cost passed from dispatch)
    cost_fc_ems = 0.0

    if cost_fc_dkk is not None:
        cost_fc_ems = float(cost_fc_dkk)

    elif isinstance(p_fc_kw, pd.Series):
        # compute from your existing equivalent-price function
        fc_cfg = fc_cfg or {}
        price_ch3oh = float(fc_cfg.get("price_ch3oh", fc_cfg.get("Price_ch3oh", 0.0)))

        if price_ch3oh > 0:
            pfc = p_fc_kw.reindex(idx).fillna(0.0).clip(lower=0.0)
            cost_rate = pfc.apply(lambda P: float(fc_cost_rate_dkk_per_h(float(P), price_ch3oh)))
            cost_fc_ems = float((cost_rate * float(dt_h)).sum())
        else:
            # no ch3oh price -> cannot compute honestly
            cost_fc_ems = 0.0


    cost_ems = None
    if cost_grid_ems is not None:
        cost_ems = cost_grid_ems + float(cost_fc_ems)

    # --- CO2 (still grid-only unless you later add FC CO2 factors) ---
    co2_ems_kg = None
    if cs is not None:
        c = cs.reindex(idx, method="nearest").fillna(0.0) / 1000.0  # g/kWh -> kg/kWh
        co2_ems_kg = float((grid_import_kw * c * dt_h).sum())

    return dict(
        E_grid_ems=E_grid,
        E_unused_ems=E_unused,
        pv_util_ems=pv_util,
        # ✅ keep your existing key
        cost_ems=cost_ems,
        co2_ems_kg=co2_ems_kg,
        # ✅ new breakdown keys (won't break UI)
        cost_grid_ems=cost_grid_ems,
        cost_fc_ems=float(cost_fc_ems),
        E_fc_el_kwh=E_fc_el,
        savings=(total_cost_baseline - cost_ems) if (total_cost_baseline is not None and cost_ems is not None) else None,
        co2_reduction=(total_co2_baseline_kg - co2_ems_kg) if (total_co2_baseline_kg is not None and co2_ems_kg is not None) else None,
    )
