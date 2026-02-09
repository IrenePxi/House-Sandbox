# core/fc_gating.py
from __future__ import annotations
import numpy as np
import pandas as pd

def _as_series(x, idx: pd.DatetimeIndex, name: str) -> pd.Series:
    if isinstance(x, pd.Series):
        return x.reindex(idx).astype(float).fillna(0.0).rename(name)
    arr = np.asarray(x, dtype=float)
    if arr.size == 1:
        return pd.Series(float(arr.item()), index=idx, name=name)
    if arr.size != len(idx):
        raise ValueError(f"{name} length must match idx")
    return pd.Series(arr, index=idx, name=name)

def compute_battery_charge_cap_kw(
    *,
    idx: pd.DatetimeIndex,
    soc_kwh: pd.Series,          # battery energy in kWh (0..E_kWh)
    batt_cfg: dict,
    dt_h: float,
) -> pd.Series:
    """
    Returns per-minute max additional charge power [kW] the battery can still absorb,
    limited by charge power limit AND remaining SOC headroom.
    """
    E_kWh = float(batt_cfg.get("E_kWh", 0.0))
    soc_max_pct = float(batt_cfg.get("soc_max", 90.0))
    P_ch_max = float(batt_cfg.get("P_ch_max_kW", 0.0))

    if E_kWh <= 0.0 or P_ch_max <= 0.0:
        return pd.Series(0.0, index=idx, name="P_bat_charge_cap_kw")

    E_max_kWh = (soc_max_pct / 100.0) * E_kWh
    soc = soc_kwh.reindex(idx).astype(float).fillna(method="ffill").fillna(0.0).clip(lower=0.0)

    headroom_kwh = (E_max_kWh - soc).clip(lower=0.0)
    # If only dt_h hours remain in this step, the max power to not exceed SOCmax:
    headroom_cap_kw = headroom_kwh / max(dt_h, 1e-9)

    cap = np.minimum(P_ch_max, headroom_cap_kw)
    return pd.Series(cap, index=idx, name="P_bat_charge_cap_kw")

def apply_fc_feasibility_no_export(
    *,
    idx: pd.DatetimeIndex,
    p_fc_ref_kw: pd.Series,   # reference FC power (>=0)
    load_kw: pd.Series,
    pv_kw: pd.Series,
    batt_charge_cap_kw: pd.Series | None = None,
) -> pd.Series:
    """
    Clamp FC power so we never have 'extra power goes nowhere' (no export).
    Convention: load_kw and pv_kw are >=0.
    If batt_charge_cap_kw is provided, it represents extra absorption possible (>=0).
    """
    p_ref = _as_series(p_fc_ref_kw, idx, "p_fc_ref_kw").clip(lower=0.0)
    load = _as_series(load_kw, idx, "load_kw").clip(lower=0.0)
    pv = _as_series(pv_kw, idx, "pv_kw").clip(lower=0.0)

    if batt_charge_cap_kw is None:
        cap_bat = pd.Series(0.0, index=idx, name="cap_bat_kw")
    else:
        cap_bat = _as_series(batt_charge_cap_kw, idx, "cap_bat_kw").clip(lower=0.0)

    net_demand = (load - pv).clip(lower=0.0)  # cannot be negative if no export
    p_max_feasible = net_demand + cap_bat

    p_cmd = np.minimum(p_ref.values, p_max_feasible.values)
    return pd.Series(p_cmd, index=idx, name="P_fc_cmd_kW")

def apply_fc_soc_gate(
    *,
    idx: pd.DatetimeIndex,
    p_fc_kw: pd.Series,
    soc_kwh: pd.Series,
    batt_cfg: dict,
    plan_df: pd.DataFrame | None,      # start,end,soc_setpoint_pct
    on_delta_pct: float = 3.0,
    off_delta_pct: float = 0.0,
) -> pd.Series:
    """
    Optional gate: only allow FC ON when SOC is 'below target'.
    This is NOT the feasibility clamp; it is a behavioral preference gate.

    Rules:
      - ON if SOC_pct <= (setpoint - on_delta_pct)
      - OFF if SOC_pct >= (setpoint + off_delta_pct)
      - hysteresis memory in-between
    If plan_df missing -> returns original p_fc_kw unchanged.
    """
    if plan_df is None or plan_df.empty:
        return _as_series(p_fc_kw, idx, "P_fc_soc_gated_kW")

    E_kWh = float(batt_cfg.get("E_kWh", 0.0))
    if E_kWh <= 0:
        return _as_series(p_fc_kw, idx, "P_fc_soc_gated_kW")

    soc = soc_kwh.reindex(idx).astype(float).fillna(method="ffill").fillna(0.0)
    soc_pct = (soc / E_kWh * 100.0).clip(lower=0.0, upper=100.0)

    # Build minute-level setpoint series from plan_df
    # plan_df columns: start (time), end (time), soc_setpoint_pct
    sp = pd.Series(np.nan, index=idx, name="soc_setpoint_pct")
    for _, r in plan_df.iterrows():
        st = r["start"]
        en = r["end"]
        val = float(r["soc_setpoint_pct"])
        mask = (idx.time >= st) & (idx.time < en)
        sp.loc[mask] = val
    sp = sp.fillna(method="ffill").fillna(method="bfill").fillna(60.0)

    p_in = _as_series(p_fc_kw, idx, "P_fc_in_kW").clip(lower=0.0)
    gate = np.zeros(len(idx), dtype=bool)

    # hysteresis with memory
    on_state = False
    for k in range(len(idx)):
        if on_state:
            if soc_pct.iloc[k] >= (sp.iloc[k] + off_delta_pct):
                on_state = False
        else:
            if soc_pct.iloc[k] <= (sp.iloc[k] - on_delta_pct):
                on_state = True
        gate[k] = on_state

    p_out = p_in.copy()
    p_out[~gate] = 0.0
    p_out.name = "P_fc_soc_gated_kW"
    return p_out
