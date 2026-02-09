import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import time as _time
import time
from concurrent.futures import ThreadPoolExecutor

from models.schemas import DeviceConfig  

from services.P2_dailyprofile_service import build_series_for_analysis
from services.P3_ems_service import (
    compute_passive_dispatch,
    compute_auto_battery_plan,
    dispatch_with_plan,
    dispatch_with_plan_and_fc,
    compute_kpis_from_dispatch,
)
from services.P3_ems_globalopt_service import solve_stepA_relaxed_global_opt, stepB_project_real_thermal,solve_stepC_grid_batt_with_fixed_fc

from services.P2_devicesimulation_service import simulate_device,simulate_dhw_with_extra_debug, simulate_space_heat_shared_debug

from core.devices import DHWTank   # <-- wherever your DHWTank class lives
from subpages.p0_front import log_event


from utils.plotting import ems_power_split_plot, ems_soc_plot, _norm01

from state.session import build_simulation_context
from state.defaults import DEVICE_LABEL_MAP, resolve_display_label

#%% helper
import hashlib
import json
from datetime import datetime
def run_fc_heat_thermal_and_battery_dispatch(
    *,
    idx: pd.DatetimeIndex,
    context,
    cfgs: dict,
    batt_cfg: dict,
    fc_cfg: dict,
    plan_df: pd.DataFrame,
    dt_h: float,
    load0: pd.Series,
    pv_tot: pd.Series,
    p_thermal_dhw_el: pd.Series,
    p_thermal_space_el: pd.Series,
    ps: pd.Series | None,
    Q_draw_th: pd.Series,  # ✅ external DHW draw (kWth)
    dispatch_with_plan_and_fc,
    dispatch_with_plan,
    simulate_dhw_with_extra_debug,
    simulate_space_heat_shared_debug,
    DeviceConfig,
) -> dict:

    # -------- (0) Align key inputs ----------
    load0 = load0.reindex(idx).fillna(0.0)
    pv_tot = pv_tot.reindex(idx).fillna(0.0)
    p_thermal_dhw_el = p_thermal_dhw_el.reindex(idx).fillna(0.0)
    p_thermal_space_el = p_thermal_space_el.reindex(idx).fillna(0.0)
    Q_draw_th = Q_draw_th.reindex(idx).fillna(0.0)

    # -------- (1) FC electric dispatch ----------
    dispatch0 = dispatch_with_plan_and_fc(
        idx, load0, pv_tot,
        ps=ps.reindex(idx).fillna(0.0) if isinstance(ps, pd.Series) else pd.Series(0.0, index=idx),
        batt_cfg=batt_cfg,
        plan_df=plan_df,
        dt_h=dt_h,
        Price_ch3oh=float(fc_cfg.get("price_ch3oh", fc_cfg.get("Price_ch3oh", 1.0))),
        fc_cfg=fc_cfg,
    )

    p_fc_cmd_kw = dispatch0.get("p_fc_cmd_kw", pd.Series(0.0, index=idx)).reindex(idx).fillna(0.0)
    q_fc_avail_kw = dispatch0.get("p_fc_heat_avail_kw", pd.Series(0.0, index=idx)).reindex(idx).fillna(0.0)

    use_heat = bool(fc_cfg.get("use_waste_heat", False))
    prio = str(fc_cfg.get("heat_priority", "dhw_then_space")).lower().strip()

    # -------- configs ----------
    cfg_dhw = cfgs.get("thermal:dhw", None)
    cfg_space = cfgs.get("thermal:space_heat", None)

    cfg_dhw_obj = None
    if isinstance(cfg_dhw, dict) and cfg_dhw:
        cfg_dhw_obj = DeviceConfig.from_dict(cfg_dhw)
    elif cfg_dhw is not None and not isinstance(cfg_dhw, dict):
        cfg_dhw_obj = cfg_dhw  # already a DeviceConfig-like

    cfg_space_obj = None
    if isinstance(cfg_space, dict) and cfg_space:
        cfg_space_obj = DeviceConfig.from_dict(cfg_space)
    elif cfg_space is not None and not isinstance(cfg_space, dict):
        cfg_space_obj = cfg_space

    # default series
    q_to_dhw = pd.Series(0.0, index=idx, name="q_fc_to_dhw_kWth")
    q_to_space = pd.Series(0.0, index=idx, name="q_fc_to_space_kWth")
    q_dhw_used = pd.Series(0.0, index=idx, name="q_dhw_used_kWth")
    q_dhw_spill = pd.Series(0.0, index=idx, name="q_dhw_spill_kWth")
    q_space_used = pd.Series(0.0, index=idx, name="q_space_used_kWth")

    # -------- (2) allocate FC heat ----------
    if use_heat:
        if prio == "space":
            q_to_space = q_fc_avail_kw.rename("q_fc_to_space_kWth")
        elif prio == "dhw":
            q_to_dhw = q_fc_avail_kw.rename("q_fc_to_dhw_kWth")
        else:
            z = q_fc_avail_kw.rename("q_fc_to_dhw_kWth")  # dhw_then_space

    # -------- (3) Re-sim DHW ----------
    q_dhw_draw = pd.Series(0.0, index=idx, name="Q_dhw_draw_th_kW")

    if cfg_dhw_obj is not None:
        if use_heat and prio in ("dhw", "dhw_then_space"):
            p_dhw_new, _, q_dhw_used, q_dhw_spill, q_dhw_draw = simulate_dhw_with_extra_debug(
                cfg_dhw_obj,
                context,
                q_extra_kw=q_to_dhw,
                T_use_c=45.0,
            )
        else:
            p_dhw_new, _, q_dhw_used, q_dhw_spill, q_dhw_draw = simulate_dhw_with_extra_debug(
                cfg_dhw_obj,
                context,
                q_extra_kw=0.0,
                T_use_c=45.0,
            )
    else:
        p_dhw_new = pd.Series(0.0, index=idx, name="P_DHW_kW")


    # dhw_then_space: spill goes to space
    if use_heat and prio == "dhw_then_space":
        q_to_space = q_dhw_spill.reindex(idx).fillna(0.0)

    # -------- (4) Re-sim SPACE ----------
    if cfg_space_obj is not None:
        dbg_space = simulate_space_heat_shared_debug(cfg_space_obj, context, q_extra_th_kw=q_to_space)
        p_space_new = dbg_space.get("P_el_total_kw", pd.Series(0.0, index=idx)).reindex(idx).fillna(0.0)

        q_space_used = (
            (dbg_space.get("Q_th_by_device_kw", {}) or {}).get("fc_heat", pd.Series(0.0, index=idx))
        ).reindex(idx).fillna(0.0)
    else:
        p_space_new = pd.Series(0.0, index=idx, name="P_space_kW")

    # -------- (5) rebuild electric load ----------
    load1 = load0.copy()
    load1 = load1.sub(p_thermal_dhw_el, fill_value=0.0).add(p_dhw_new, fill_value=0.0)
    load1 = load1.sub(p_thermal_space_el, fill_value=0.0).add(p_space_new, fill_value=0.0)
    load1 = load1.clip(lower=0.0)

    # -------- (6) net after FC electricity ----------
    load1_net = (load1 - p_fc_cmd_kw).clip(lower=0.0)

    # -------- (7) battery/grid dispatch ----------
    dispatch = dispatch_with_plan(idx, load1_net, pv_tot, batt_cfg, plan_df, dt_h)

    # -------- attach FC + heat series ----------
    dispatch["p_fc_cmd_kw"] = p_fc_cmd_kw
    dispatch["p_fc_heat_avail_kw"] = q_fc_avail_kw
    dispatch["q_fc_to_dhw_kw"] = q_to_dhw
    dispatch["q_fc_to_space_kw"] = q_to_space
    dispatch["q_dhw_used_kw"] = q_dhw_used
    dispatch["q_dhw_spill_kw"] = q_dhw_spill
    dispatch["q_space_used_kw"] = q_space_used
    dispatch["load1_kw"] = load1
    dispatch["load1_net_kw"] = load1_net
    dispatch["q_dhw_draw_th_kw"] = q_dhw_draw

    dispatch["P_by_device_kw"] = {
        "Load_total_original": load0,
        "Load_total_final": load1,
        "PV": pv_tot,
        "Grid_import": dispatch["grid_import_kw"],
        "Battery": dispatch["p_bat_kw"],
        "Fuel_cell_el": p_fc_cmd_kw,
        "DHW_el": p_dhw_new,
        "Space_el": p_space_new,
    }

    return dispatch



def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _stable_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str)

def _signature(mode: str, batt_cfg: dict, plan_df: pd.DataFrame | None, w_cost: float | None,
               has_fc: bool, fc_cfg: dict) -> str:
    payload = {
        "mode": mode,
        "batt_cfg": batt_cfg,
        "w_cost": w_cost,
        "has_fc": has_fc,
        "fc_cfg": fc_cfg,
        "plan": None if plan_df is None else plan_df.to_dict(orient="records"),
    }
    s = _stable_json(payload)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]


def _runs():
    return st.session_state.setdefault("ems_runs", [])

def _save_run(*, name: str, mode: str, signature: str, dispatch: dict, kpis: dict, plan_df: pd.DataFrame | None):
    runs = _runs()

    run_id = f"{mode}-{signature}"
    entry = {
        "id": run_id,
        "name": name,
        "mode": mode,
        "created": _now_str(),
        "signature": signature,
        "pinned": False,
        "dispatch": dispatch,
        "kpis": kpis,
        "plan_df": None if plan_df is None else plan_df.copy(),
    }

    # if same id exists, replace (keeps clean)
    for i, r in enumerate(runs):
        if r.get("id") == run_id:
            entry["pinned"] = bool(r.get("pinned", False))
            runs[i] = entry
            st.session_state["ems_selected_run_id"] = run_id
            return

    runs.append(entry)
    st.session_state["ems_selected_run_id"] = run_id

def _delete_run(run_id: str):
    runs = _runs()
    st.session_state["ems_runs"] = [r for r in runs if r.get("id") != run_id]
    if st.session_state.get("ems_selected_run_id") == run_id:
        st.session_state["ems_selected_run_id"] = st.session_state["ems_runs"][-1]["id"] if st.session_state["ems_runs"] else None

def _get_run(run_id: str):
    for r in _runs():
        if r.get("id") == run_id:
            return r
    return None


def _rename_run(run_id: str, new_name: str):
    runs = _runs()
    for r in runs:
        if r.get("id") == run_id:
            r["name"] = new_name
            return



def _set_preview(dispatch, kpis, mode_key, name, plan_df, signature):
    st.session_state["ems_preview"] = {
        "id": f"preview-{mode_key}-{signature}",
        "name": f"(preview) {name}",
        "mode": mode_key,
        "created": _now_str(),
        "signature": signature,
        "dispatch": dispatch,
        "kpis": kpis,
        "plan_df": None if plan_df is None else plan_df.copy(),
    }

def _set_pinned(run_id: str, pinned: bool):
    for r in _runs():
        if r.get("id") == run_id:
            r["pinned"] = bool(pinned)
            return

def _get_saved_runs_only():
    out = []
    for r in _runs():
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id", ""))
        if rid.startswith("preview-"):
            continue
        if "dispatch" in r and "kpis" in r:
            out.append(r)
    return out

def _kpi_compare_df(runs: list[dict]) -> pd.DataFrame:
    rows = []
    for r in runs:
        k = r.get("kpis", {}) or {}
        d = r.get("dispatch", {}) or {}
        rows.append({
            "id": r.get("id"),
            "Name": r.get("name"),
            "Mode": r.get("mode"),
            "Created": r.get("created"),
            "Grid import (kWh)": k.get("E_grid_ems"),
            "Unused PV (kWh)": k.get("E_unused_ems"),
            "PV utilization (%)": k.get("pv_util_ems"),
            "End SOC (%)": d.get("soc_end_pct"),
            "Cost (DKK)": k.get("cost_ems"),
            "CO₂ (kg)": k.get("co2_ems_kg"),
        })
    return pd.DataFrame(rows)

def _plot_kpi_bars(df: pd.DataFrame, metrics: list[str]):
    # robust numeric conversion (keeps None as NaN)
    dff = df.copy()
    for m in metrics:
        dff[m] = pd.to_numeric(dff[m], errors="coerce")

    fig = go.Figure()
    x = dff["Name"].astype(str).tolist()
    for m in metrics:
        fig.add_bar(name=m, x=x, y=dff[m].values)

    fig.update_layout(
        barmode="group",
        height=340,
        margin=dict(l=10, r=10, t=30, b=90),
        xaxis_title="Run",
        yaxis_title="Value",
        legend=dict(orientation="h", yanchor="top", y=-0.35, xanchor="center", x=0.5),
    )
    return fig

def _plot_metric_delta(df: pd.DataFrame, baseline_name: str, metric: str):
    dff = df.copy()
    dff[metric] = pd.to_numeric(dff[metric], errors="coerce")
    base_val = dff.loc[dff["Name"] == baseline_name, metric]
    if base_val.empty or pd.isna(base_val.iloc[0]):
        return None
    base = float(base_val.iloc[0])
    fig = go.Figure()
    fig.add_bar(x=dff["Name"].astype(str), y=dff[metric] - base, name=f"Δ {metric}")
    fig.update_layout(
        height=260,
        margin=dict(l=10, r=10, t=30, b=90),
        xaxis_title="Run",
        yaxis_title=f"Delta vs {baseline_name}",
        showlegend=False,
    )
    return fig



#%% main page
from utils.ui_styler import load_custom_css

def render_analysis_page():
    # 📝 Tracking: Log that user reached the final page (once per session)
    if not st.session_state.get("logged_analysis_reach", False):
        log_event("analysis_reached")
        st.session_state["logged_analysis_reach"] = True

    import plotly.graph_objects as go
    load_custom_css()
    
    # --- Page Header ---
    st.markdown("""
        <h1 style='margin-bottom: 0.5rem;'>Analysis</h1>
        <p style='color: #64748B; font-size: 1rem; margin-bottom: 2rem; line-height: 1.6;'>
            <strong>Understand your consumption and unlock your saving potential.</strong> <br><br>
            Use the <b>Consumption Analysis</b> tab to explore your current usage patterns and see a detailed breakdown of 
            energy use by device. <br><br> Switch to the <b>Optimal Dispatch</b> tab to run our smart optimization engine—it 
            calculates the most cost-effective and eco-friendly plan for your household based on real-time electricity 
            prices and your unique device configuration.
        </p>
    """, unsafe_allow_html=True)

    # ---- session state ----
    sel  = st.session_state.get("device_selection", {})
    cfgs = st.session_state.get("device_configs", {})

    if not sel or not any(sel.values()):
        st.warning("Please select at least one device on page 2 first.")
        st.stop()

    has_battery = bool(sel.get("gen_store:battery", False))
    batt_cfg = cfgs.get("gen_store:battery", {}) or {}
    has_fc = bool(sel.get("gen_store:fuel_cell", False))
    fc_cfg = cfgs.get("gen_store:fuel_cell", {}) or {}

    # ---------- 1) Build load / PV series ----------
    context = build_simulation_context()
    (
        idx,
        load_tot,
        pv_tot,
        energy_per_device,
        p_load_nonthermal,
        p_thermal_el,
        p_thermal_dhw_el,
        p_thermal_space_el,
        p_thermal_leisure_el
    ) = build_series_for_analysis(sel, cfgs, context)
    load0 = load_tot

    # --- External DHW draw (kWth) based on fixed service temperature (optimization-safe) ---
    cfg_dhw = cfgs.get("thermal:dhw", {}) or {}
    cfg_dhw_obj = DeviceConfig.from_dict(cfg_dhw) if isinstance(cfg_dhw, dict) else cfg_dhw

    tank = DHWTank(
        volume_l=float(getattr(cfg_dhw_obj, "volume_l", 200.0)),
        t_set_c=float((getattr(cfg_dhw_obj, "t_min_c", 45.0) + getattr(cfg_dhw_obj, "t_max_c", 55.0)) / 2.0),
        hyst_band_c=float(max(getattr(cfg_dhw_obj, "t_max_c", 55.0) - getattr(cfg_dhw_obj, "t_min_c", 45.0), 1.0)),
        p_el_kw=float(getattr(cfg_dhw_obj, "p_el_kw", 2.0)),
        usage_level=str(getattr(cfg_dhw_obj, "usage_level", "Medium")),
        Ti0_c=float((getattr(cfg_dhw_obj, "t_min_c", 45.0) + getattr(cfg_dhw_obj, "t_max_c", 55.0)) / 2.0),
        T_cold_c=10.0,
        T_amb_c=21.0,
    )
    Q_draw_th = tank.build_Q_draw_external_kw(idx).reindex(idx).fillna(0.0)

    if len(idx) < 2:
        st.warning("Not enough time points to analyze this day.")
        st.stop()

    dt_h = (idx[1] - idx[0]).total_seconds() / 3600.0
    if dt_h <= 0:
        st.warning("Time index has non-positive step. Cannot compute energies.")
        st.stop()

    # ---------- baseline flows (no dispatch) ----------
    pv_self_kw  = np.minimum(load_tot, pv_tot)
    grid_import = (load_tot - pv_self_kw).clip(lower=0.0)
    pv_unused   = (pv_tot - pv_self_kw).clip(lower=0.0)  # no export

    # energies (kWh)
    E_load   = float((load_tot    * dt_h).sum())
    E_pv     = float((pv_tot      * dt_h).sum())
    E_self   = float((pv_self_kw  * dt_h).sum())
    E_grid   = float((grid_import * dt_h).sum())
    E_unused = float((pv_unused   * dt_h).sum())

    pv_cov  = (E_self / E_load * 100.0) if E_load > 0 else 0.0
    pv_util = ((E_pv - E_unused) / E_pv * 100.0) if E_pv > 0 else 0.0

    # ---------- price / CO2 series ----------
    price_series = st.session_state.get("price_daily")  # DKK/kWh
    co2_series   = st.session_state.get("co2_daily")    # g/kWh

    ps = None
    if isinstance(price_series, (pd.Series, pd.DataFrame)):
        ps = price_series.iloc[:, 0] if isinstance(price_series, pd.DataFrame) else price_series
        ps = ps.reindex(idx, method="nearest").fillna(0.0)

    cs = None
    if isinstance(co2_series, (pd.Series, pd.DataFrame)):
        cs = co2_series.iloc[:, 0] if isinstance(co2_series, pd.DataFrame) else co2_series
        cs = cs.reindex(idx, method="nearest").fillna(0.0)

    # baseline cost/CO2 (for deltas in KPI function)
    total_cost = None
    if ps is not None:
        total_cost = float((grid_import * ps * dt_h).sum())

    total_co2_grid_kg = None
    if cs is not None:
        total_co2_grid_kg = float((grid_import * (cs / 1000.0) * dt_h).sum())  # kg/kWh

    # Baseline KPIs in your standard schema (E_grid_ems, cost_ems, etc.)
    baseline_kpis = compute_kpis_from_dispatch(
        idx=idx,
        pv_tot=pv_tot,
        grid_import_kw=grid_import,
        pv_unused_kw=pv_unused,
        dt_h=dt_h,
        total_cost_baseline=total_cost,
        total_co2_baseline_kg=total_co2_grid_kg,
        ps=ps,
        cs=cs,
        p_fc_kw=None,
        fc_cfg=None,
        cost_fc_dkk=None,
    )

    # ---------- Tabs Dashboard ----------
    tab_base, tab_go = st.tabs(["📊 Consumption Analysis", "🚀 Optimal Dispatching"])

    with tab_base:
        st.markdown("### Daily Consumption Analysis")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Load", f"{E_load:.1f} kWh")
        m2.metric("PV Generation", f"{E_pv:.1f} kWh")
        m3.metric("PV Utilization", f"{pv_util:.0f}%")
        m4.metric("Grid Import", f"{E_grid:.1f} kWh")

        m5, m6 = st.columns(2)
        m5.metric("Energy Cost", f"{total_cost:.2f} DKK" if total_cost is not None else "n/a")
        m6.metric("CO₂ Footprint", f"{total_co2_grid_kg:.1f} kg" if total_co2_grid_kg is not None else "n/a")

        st.markdown("<br>", unsafe_allow_html=True)
        
        row1_col1, row1_col2 = st.columns([2, 1])
        with row1_col1:
            with st.container(border=True):
                st.markdown("#### Power Profile Breakdown")
                fig_ts = go.Figure()
                fig_ts.add_scatter(x=idx, y=load_tot.values, mode="lines", name="Total Load", line=dict(color="#1E293B", width=2))
                if float(pv_tot.max()) > 0:
                    fig_ts.add_scatter(x=idx, y=-pv_tot.values, mode="lines", name="Solar Generation", fill="tozeroy", line=dict(color="#10B981", width=1))
                fig_ts.add_scatter(x=idx, y=grid_import.values, mode="lines", name="Grid Import", line=dict(color="#2563EB", dash="dot"))
                fig_ts.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="", yaxis_title="kW", template="plotly_white", legend=dict(orientation="h", y=-0.2))
                st.plotly_chart(fig_ts, width="stretch")

        with row1_col2:
            with st.container(border=True):
                st.markdown("#### Consumption Mix")
                labels, values = [], []
                cat_energy = {"Fixed": 0.0, "Flexible": 0.0, "Thermal": 0.0, "EV": 0.0}
                for fk, E_kwh in energy_per_device.items():
                    ck, dt = fk.split(":", 1)
                    if ck == "elec_fixed": cat_energy["Fixed"] += E_kwh
                    elif ck == "elec_flex": cat_energy["Flexible"] += E_kwh
                    elif ck == "thermal": cat_energy["Thermal"] += E_kwh
                    elif ck == "outside": cat_energy["EV"] += E_kwh
                for k, v in cat_energy.items():
                    if v > 0.01: labels.append(k); values.append(v)
                
                if values:
                    fig_pie = go.Figure(data=[go.Pie(labels=labels, values=values, hole=0.4, marker=dict(colors=["#1E293B", "#2563EB", "#10B981", "#F59E0B"]))])
                    fig_pie.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=-0.1))
                    st.plotly_chart(fig_pie, width="stretch")
                else:
                    st.caption("No non-zero loads to show.")

        st.markdown("#### Device Consumption List")
        if energy_per_device:
            # Sort devices by energy (highest first)
            sorted_energy = sorted(energy_per_device.items(), key=lambda x: x[1], reverse=True)
            
            data = []
            for fk, val in sorted_energy:
                if val < 0.001: continue
                # Resolve label
                cat, dev = fk.split(":", 1)
                cfg = cfgs.get(fk, {})
                label = resolve_display_label(fk, dev, cfg)
                data.append({"Device": label, "Consumption (kWh)": round(val, 2)})
            
            if data:
                st.table(pd.DataFrame(data))
            else:
                st.info("No significant device consumption recorded.")
        else:
            st.info("No devices selected or simulating.")

    # =========================
    # Tab 2: Global optimization
    # =========================
    with tab_go:
        st.markdown("### System Optimization")
        st.info("Run a combined heat and power optimization to minimize costs and carbon intensity via smart dispatch of battery and FC (if enabled).")
        
        if st.button("🚀 Run Global Optimization (60s Limit)", type="primary", width="stretch", key="go_run_btn"):
            # Initialize progress bar
            pbar = st.progress(0, text="Initializing optimization engine...")
            start_time = time.time()
            estimated_duration = 60.0  # seconds

            try:
                tout = st.session_state.get("temp_daily")
                if isinstance(tout, (pd.Series, pd.DataFrame)):
                    if isinstance(tout, pd.DataFrame): tout = tout.iloc[:, 0]
                    tout = tout.reindex(idx, method="nearest").ffill().bfill()
                else: tout = pd.Series(10.0, index=idx)

                # internal function to run in thread
                def _run_optimization_task():
                    # --- Step A: 15-min Resolution (Speedup) ---
                    # Resample integer/float inputs to 15T
                    # Fix: DatetimeIndex has no resample, wrap in Series
                    idx_15 = pd.Series(0, index=idx).resample("15min").first().index
                    # Note: .resample("15min").mean() is good for power/temp. 
                    # For price we can use mean or first? Mean is safer for cost.
                    
                    def _re(s: pd.Series):
                        return s.resample("15min").mean().reindex(idx_15).fillna(0.0)
                    
                    p_load_15 = _re(p_load_nonthermal)
                    pv_tot_15 = _re(pv_tot)
                    ps_15 = _re(ps)
                    tout_15 = _re(tout)
                    Q_draw_15 = _re(Q_draw_th.reindex(idx).fillna(0.0))
                    p_leisure_15 = _re(p_thermal_leisure_el)

                    A_15 = solve_stepA_relaxed_global_opt(
                        idx=idx_15, load_nonthermal_kw=p_load_15, pv_avail_kw=pv_tot_15, price_el=ps_15, tout_c=tout_15,
                        dhw_draw_th_kw=Q_draw_15, leisure_el_kw=p_leisure_15,
                        batt_cfg=batt_cfg if has_battery else None, fc_cfg=fc_cfg if has_fc else None,
                        space_cfg=dict(cfgs.get("thermal:space_heat", {})), dhw_cfg=dict(cfgs.get("thermal:dhw", {})),
                        dt_h=0.25, # 15 min steps
                        enable_fc=has_fc, enable_batt=has_battery,
                    )
                    
                    # Upsample FC result to 1-min for Step B/C
                    # ffill makes it a blocky schedule (physically realistic "hold" command)
                    p_fc_1min = A_15["p_fc_kw"].reindex(idx).ffill().fillna(0.0)

                    # --- Step B & C: 1-min Resolution (Precision) ---
                    B = stepB_project_real_thermal(idx=idx, context=context, cfgs=cfgs, simulate_device_fn=simulate_device, simulate_dhw_with_extra_debug_fn=simulate_dhw_with_extra_debug, fc_power_kw=p_fc_1min, fc_cfg=fc_cfg or {})
                    load1 = load0.copy().sub(p_thermal_dhw_el, fill_value=0.0).add(B["p_dhw_el_kw"], fill_value=0.0).sub(p_thermal_space_el, fill_value=0.0).add(B["p_space_el_kw"], fill_value=0.0).clip(lower=0.0)
                    C = solve_stepC_grid_batt_with_fixed_fc(idx=idx, load_kw=load1, pv_avail_kw=pv_tot, p_fc_kw=p_fc_1min, price_el=ps, batt_cfg=batt_cfg if has_battery else None, dt_h=dt_h, objective="cost", enforce_end_soc="eq")
                    
                    dispatch_go = dict(C)
                    dispatch_go.update({"p_fc_cmd_kw": p_fc_1min, "q_fc_avail_kw": B["q_fc_avail_kw"], "q_dhw_used_kw": B["q_dhw_used_kw"], "q_fc_to_space_kw": B["q_to_space_kw"], "load1_kw": load1})
                    dispatch_go["P_by_device_kw"] = {"Optimized Load": load1, "PV Used": C["pv_used_kw"], "Battery": C["p_bat_kw"], "Grid Import": C["grid_import_kw"], "FC Generation": p_fc_1min}
                    
                    kpis_go = compute_kpis_from_dispatch(idx=idx, pv_tot=pv_tot, grid_import_kw=C["grid_import_kw"], pv_unused_kw=C["pv_unused_kw"], dt_h=dt_h, total_cost_baseline=total_cost, total_co2_baseline_kg=total_co2_grid_kg, ps=ps, cs=cs, p_fc_kw=p_fc_1min, fc_cfg=fc_cfg if has_fc else None, cost_fc_dkk=C.get("fc_cost_dkk"))
                    
                    return dispatch_go, kpis_go

                # Execute in thread to allow UI updates
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_run_optimization_task)
                    
                    while not future.done():
                        elapsed = time.time() - start_time
                        # Clamp at 95% so it doesn't look "done" until it actually returns
                        pct = min(int((elapsed / estimated_duration) * 100), 95)
                        pbar.progress(pct, text=f"Optimizing... {pct}% (Max 60s)")
                        time.sleep(0.1)
                    
                    dispatch_final, kpis_final = future.result()

                pbar.progress(100, text="Done!")
                st.session_state["globalopt_last"] = {"dispatch": dispatch_final, "kpis": kpis_final}
                
                st.success("Global optimization finished.")
                st.rerun()

            except Exception as e:
                pbar.empty()
                st.error(f"Optimization failed: {str(e)}")

        globalopt_result = st.session_state.get("globalopt_last")
        if globalopt_result:
            d, k = globalopt_result["dispatch"], globalopt_result["kpis"]
            st.markdown("#### Key Optimization Results")
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Grid Import", f"{float(k.get('E_grid_ems') or 0.0):.1f} kWh", delta=f"{float(k.get('E_grid_ems') or 0.0) - E_grid:+.1f}", delta_color="inverse")
            r2.metric("FC Generation", f"{float(k.get('E_fc_el_kwh') or 0.0):.1f} kWh")
            r3.metric("PV Utilization", f"{float(k.get('pv_util_ems') or 0.0):.0f}%", delta=f"{float(k.get('pv_util_ems') or 0.0) - pv_util:+.0f}%")
            r4.metric("Total Cost", f"{float(k.get('cost_ems') or 0.0):.2f} DKK", delta=f"{float(k.get('cost_ems') or 0.0) - total_cost:+.2f}" if total_cost else None, delta_color="inverse")

            with st.container(border=True):
                st.markdown("#### Optimized Dispatch Plan")
                fig_dev = go.Figure()
                for name, s in d.get("P_by_device_kw", {}).items():
                    if isinstance(s, pd.Series): fig_dev.add_scatter(x=idx, y=s.reindex(idx).fillna(0.0).values, mode="lines", name=name)
                fig_dev.update_layout(height=400, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="", yaxis_title="kW", template="plotly_white", legend=dict(orientation="h", y=-0.2))
                st.plotly_chart(fig_dev, width="stretch")

            if has_battery and "soc_kwh" in d:
                with st.container(border=True):
                    st.markdown("#### Battery State of Charge (%)")
                    cap_kwh = float(batt_cfg.get("E_kWh", 5.0))
                    soc_kwh = d["soc_kwh"]
                    if isinstance(soc_kwh, pd.Series) and cap_kwh > 0:
                        fig_soc = ems_soc_plot(idx, soc_kwh, cap_kwh)
                        st.plotly_chart(fig_soc, width="stretch")
        else:
            st.info("Click **Run Global Optimization** to compute results.")

    st.markdown("<div style='height: 2rem;'></div>", unsafe_allow_html=True)
    
    _, col_btn1, col_btn2, _ = st.columns([1.2, 1, 1, 1.2])
    with col_btn1:
        if st.button("⬅ Step 2: Devices & Layout", key="back_to_p2"):
            st.session_state["active_page"] = "Devices & Layout"
            st.rerun()
    with col_btn2:
        if st.button("↩ Back to Start", key="restart_flow"):
            st.session_state["active_page"] = "Market & Weather"
            st.rerun()
