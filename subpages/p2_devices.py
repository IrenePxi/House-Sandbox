# p2_devices.py
# A working, consistent version that:
# - Builds a proper SimulationContext (always has a valid date + thermal params + weather/temp if available)
# - Fixes the space-heating preview_power_profile call
# - Guards None returns from the simulator
# - Keeps existing UI structure (including flex global expander)

from __future__ import annotations

import streamlit as st
from datetime import datetime, date, timedelta
from datetime import time as _time
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# === SERVICES / CORE ===
from services.P2_dailyprofile_service import compute_daily_profiles
from services import P2_devicesimulation_service

from core.devices import WeatherHotTub, DHWTank
from core.solar import pv_from_weather_modelchain_from_df
from core.profiles import build_minute_profile

from models.schemas import DeviceConfig, SimulationContext


# === STATE ===
from state.defaults import DEVICE_CATEGORIES, DEVICE_LABEL_MAP, get_default_config, resolve_display_label, HOUSE_TYPE_PRESETS
from state.session import (
    get_selected_day_data,
    suggest_best_interval_for_day,
    suggest_best_interval_for_ev,
    get_house_thermal_params,
    build_simulation_context
)

# === UTILS ===
from utils.time import extract_icon


# -------------------------------------------------------------------
# Context helpers (critical for stability)
# -------------------------------------------------------------------
def _normalize_selected_day(raw_day) -> date:
    """Return a safe python date no matter what is stored in st.session_state['day']."""
    if raw_day is None:
        return date.today()
    if isinstance(raw_day, date) and not isinstance(raw_day, datetime):
        return raw_day
    if isinstance(raw_day, datetime):
        return raw_day.date()
    if isinstance(raw_day, pd.Timestamp):
        return raw_day.date()
    # string or other
    try:
        return pd.Timestamp(raw_day).date()
    except Exception:
        return date.today()




# -------------------------------------------------------------------
# Helper: House Layout Figure
# -------------------------------------------------------------------
def build_house_layout_figure(sel: dict, cfgs: dict) -> go.Figure:
    fig = go.Figure()
    shapes = []

    shapes.append(dict(type="rect", x0=1, y0=2, x1=9, y1=7, line=dict(width=2), fillcolor="rgba(245,245,245,0.8)"))
    roof_path = "M 1 7 L 5 9 L 9 7 Z"
    shapes.append(dict(type="path", path=roof_path, line=dict(width=2), fillcolor="rgba(180, 255, 180, 0.5)"))

    shapes.append(dict(type="rect", x0=1.05, y0=4.0, x1=8.95, y1=6.95, line=dict(width=1, dash="dot"), fillcolor="rgba(210, 225, 255, 0.4)"))
    shapes.append(dict(type="rect", x0=1.1, y0=4.1, x1=4.8, y1=6.85, line=dict(width=1, dash="dot"), fillcolor="rgba(150, 200, 255, 0.4)"))
    shapes.append(dict(type="rect", x0=5.2, y0=4.1, x1=8.85, y1=6.85, line=dict(width=1, dash="dot"), fillcolor="rgba(255, 235, 170, 0.4)"))

    shapes.append(dict(type="rect", x0=1.05, y0=2.05, x1=8.95, y1=3.95, line=dict(width=1, dash="dot"), fillcolor="rgba(255, 190, 190, 0.45)"))
    shapes.append(dict(type="rect", x0=9.2, y0=1.95, x1=10.1, y1=3.02, line=dict(width=1.5), fillcolor="rgba(250,250,250,0.9)"))

    fig.update_layout(shapes=shapes)

    annotations = [
        dict(x=2.0, y=4.3, text="Electrical (fixed)", showarrow=False, font=dict(size=10)),
        dict(x=6.2, y=4.3, text="Electrical (flexible)", showarrow=False, font=dict(size=10)),
        dict(x=1.8, y=2.2, text="Thermal", showarrow=False, font=dict(size=10)),
        dict(x=5.0, y=7.2, text="Generation & Storage", showarrow=False, font=dict(size=11)),
    ]

    zone_devices = {"elec_fixed": [], "elec_flex": [], "thermal": [], "gen_store": [], "outside": []}

    for full_key, checked in sel.items():
        if not checked or ":" not in full_key:
            continue
        cat_key, dev_type = full_key.split(":", 1)
        if cat_key not in zone_devices:
            continue
        cfg_current = cfgs.get(full_key, {})
        label = resolve_display_label(full_key, dev_type, cfg_current)
        zone_devices[cat_key].append(label)

    def add_zone_devices_grid(labels, base_x, base_y, max_cols, max_rows, dx, dy):
        capacity = max_cols * max_rows
        for j, label in enumerate(labels[:capacity]):
            col = j % max_cols
            row = j // max_cols
            x = base_x + col * dx
            y = base_y - row * dy
            annotations.append(dict(
                x=x, y=y, text=extract_icon(label), showarrow=False, font=dict(size=11),
                bgcolor="rgba(255,255,255,0.9)", bordercolor="rgba(0,0,0,0.25)", borderwidth=1, borderpad=2,
                xanchor="center", yanchor="middle",
            ))

    add_zone_devices_grid(zone_devices["elec_fixed"], 1.6, 6.5, 5, 4, 0.68, 0.6)
    add_zone_devices_grid(zone_devices["elec_flex"], 5.65, 6.5, 5, 4, 0.68, 0.6)
    add_zone_devices_grid(zone_devices["thermal"], 3.0, 3.7, 4, 4, 1.3, 0.45)

    gen_slots = [
        (4.6, 8.4), (5.4, 8.4), (3.9, 8.0), (4.6, 8.0), (5.4, 8.0), (6.2, 8.0),
        (2.9, 7.5), (3.7, 7.5), (4.5, 7.5), (5.3, 7.5), (6.1, 7.5), (7.0, 7.5)
    ]
    for j, label in enumerate(zone_devices["gen_store"][:len(gen_slots)]):
        x, y = gen_slots[j]
        annotations.append(dict(
            x=x, y=y, text=extract_icon(label), showarrow=False, font=dict(size=11),
            bgcolor="rgba(255,255,255,0.9)", bordercolor="rgba(0,0,0,0.25)", borderwidth=1, borderpad=2,
            xanchor="center", yanchor="middle",
        ))

    ev_slots = [(9.65, 2.7), (9.65, 2.2)]
    for j, label in enumerate(zone_devices["outside"][:len(ev_slots)]):
        x, y = ev_slots[j]
        annotations.append(dict(
            x=x, y=y, text=extract_icon(label), showarrow=False, font=dict(size=11),
            bgcolor="rgba(255,255,255,0.9)", bordercolor="rgba(0,0,0,0.25)", borderwidth=1, borderpad=2,
            xanchor="center", yanchor="middle",
        ))

    fig.update_layout(
        annotations=annotations,
        xaxis=dict(visible=False, range=[0, 11]),
        yaxis=dict(visible=False, range=[0, 10]),
        margin=dict(l=10, r=10, t=10, b=10),
        template="plotly_white",
        autosize=True,
        height=None,
    )
    fig.update_yaxes(scaleanchor=None)
    return fig


# -------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------
def _clamp_float(x, lo, hi, fallback):
    try:
        v = float(x)
        if np.isnan(v) or np.isinf(v):
            return float(fallback)
        return float(min(max(v, lo), hi))
    except Exception:
        return float(fallback)

def ensure_device_cfg(full_key: str, cat_key: str, dev_type: str, cfgs: dict) -> dict:
    if full_key not in cfgs:
        cfgs[full_key] = get_default_config(dev_type, cat_key)
    return cfgs[full_key]


def _get_outdoor_profile_local():
    if (
        "temp_daily" in st.session_state
        and isinstance(st.session_state["temp_daily"], pd.Series)
        and not st.session_state["temp_daily"].empty
    ):
        tout_tot = st.session_state["temp_daily"]
        tout_minute = get_selected_day_data(tout_tot)
        idx = tout_minute.index
    else:
        sel_day = st.session_state.get("day")
        if sel_day:
            start = pd.Timestamp(sel_day)
            idx = pd.date_range(start, periods=24 * 60, freq="min")
        else:
            idx = pd.date_range("2025-01-10 00:00", periods=24 * 60, freq="min")

        hours = idx.hour + idx.minute / 60.0
        tout_minute = pd.Series(
            5.0 + 5.0 * np.sin(2 * np.pi * (hours - 15) / 24.0),
            index=idx,
            name="Tout_C",
        )
    return idx, tout_minute


def preview_power_profile(cfg: dict, index, p_kw_values, label: str):
    idx = pd.to_datetime(index)
    vals = np.asarray(p_kw_values, dtype=float)

    # keep your existing downstream behavior
    cfg["profile_index"] = idx.astype(str).tolist()
    cfg["profile_kw"] = vals.tolist()

    fig = go.Figure()
    fig.add_scatter(x=idx, y=vals, mode="lines", name=label)
    fig.update_layout(
        height=180,
        margin=dict(l=10, r=10, t=8, b=8),
        xaxis_title="Time",
        yaxis_title="kW",
        showlegend=False,
    )
    st.plotly_chart(fig, width="stretch")


def preview_series(index, values, label: str, y_title: str):
    idx = pd.to_datetime(index)
    vals = np.asarray(values, dtype=float)

    fig = go.Figure()
    fig.add_scatter(x=idx, y=vals, mode="lines", name=label)
    fig.update_layout(
        height=180,
        margin=dict(l=10, r=10, t=8, b=8),
        xaxis_title="Time",
        yaxis_title=y_title,
        showlegend=False,
    )
    st.plotly_chart(fig, width="stretch")


# -------------------------------------------------------------------
# Device Editor Renderers (unchanged except the context + space-heating preview fix)
# -------------------------------------------------------------------
def render_editor_fixed(settings_id, cfg, full_key, open_key):
    with st.container():
        st.markdown("<hr style='border-top: 1px dashed #bbb;'/>", unsafe_allow_html=True)

        cfg["num_devices"] = int(st.number_input(
            "Number of devices", min_value=1, max_value=30, step=1,
            value=int(cfg.get("num_devices", 1)), key=f"{settings_id}_numdev"
        ))

        default_power_w = float(cfg.get("power_w", cfg.get("power_kw", 0.1) * 1000.0))
        cfg["power_w"] = st.number_input(
            "Power per device (W)", min_value=0.0, max_value=5000.0, step=10.0,
            value=default_power_w, key=f"{settings_id}_power"
        )
        cfg["power_kw"] = cfg["power_w"] / 1000.0

        st.caption("On/off intervals (you can add multiple):")
        intervals = cfg.setdefault("intervals", [])
        if not intervals:
            intervals.append({"start": _time(18, 0), "end": _time(23, 0)})

        to_delete = None
        for j, iv in enumerate(intervals):
            c_a, c_b, c_c = st.columns([0.4, 0.4, 0.2])
            with c_a:
                iv["start"] = st.time_input("Start", value=iv.get("start", _time(18, 0)), key=f"{settings_id}_start_{j}")
            with c_b:
                iv["end"] = st.time_input("End", value=iv.get("end", _time(23, 0)), key=f"{settings_id}_end_{j}")
            with c_c:
                if st.button("🗑", key=f"{settings_id}_ivdel_{j}"):
                    to_delete = j

        if to_delete is not None:
            intervals.pop(to_delete)
            st.rerun()

        if st.button("➕ Add interval", key=f"{settings_id}_add_interval"):
            intervals.append({"start": _time(18, 0), "end": _time(23, 0)})
            st.rerun()

        st.markdown("<div style='height:0.5rem'></div>**Daily load profile (preview)**", unsafe_allow_html=True)
        prof = build_minute_profile(
            power_w=cfg["power_w"] * cfg["num_devices"],
            intervals=intervals,
            step_min=1,
        )
        preview_power_profile(cfg, prof.index, prof.values, "P_fixed_total_kW")

        if st.button("▲ Hide", key=f"hide_{full_key}"):
            st.session_state[open_key] = False
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


def render_editor_flex(settings_id, cfg, full_key, open_key, dev_type, cfgs):
    with st.container():
        st.markdown("<hr style='border-top: 1px dashed #bbb;'/>", unsafe_allow_html=True)

        cfg["num_devices"] = int(st.number_input(
            "Number of devices", min_value=1, max_value=10, step=1,
            value=int(cfg.get("num_devices", 1)), key=f"{settings_id}_numdev"
        ))

        cfg["power_w"] = st.number_input(
            "Power per device (W)", min_value=0.0, max_value=5000.0, step=50.0,
            value=float(cfg.get("power_w", 1200.0)), key=f"{settings_id}_power_flex"
        )
        cfg["power_kw"] = cfg["power_w"] / 1000.0

        cfg["duration_min"] = int(st.number_input(
            "Operation duration (minutes)", min_value=15, max_value=600, step=15,
            value=int(cfg.get("duration_min", 90)), key=f"{settings_id}_dur"
        ))

        intervals = cfg.setdefault("intervals", [])
        if not intervals:
            intervals.append({"start": _time(20, 0), "end": _time(21, 30)})
        elif len(intervals) > 1:
            intervals[:] = intervals[:1]

        current_iv = intervals[0]
        st.caption("Current scheduled interval (one continuous block):")
        c_a, c_b = st.columns(2)
        with c_a:
            current_iv["start"] = st.time_input("Start", value=current_iv.get("start", _time(20, 0)), key=f"{settings_id}_flex_start")
        with c_b:
            current_iv["end"] = st.time_input("End", value=current_iv.get("end", _time(21, 30)), key=f"{settings_id}_flex_end")

        st.markdown("**Daily load profile (preview)**")
        prof = build_minute_profile(
            power_w=cfg["power_w"] * cfg["num_devices"],
            intervals=intervals,
            step_min=1,
        )
        preview_power_profile(cfg, prof.index, prof.values, "P_flex_total_kW")

        if st.button("↩ Reset this device to defaults", key=f"{settings_id}_reset_flex"):
            cfgs[full_key] = get_default_config(dev_type, "elec_flex")
            st.success("Device reset to default flexible settings.")
            st.rerun()

        if st.button("▲ Hide details", key=f"{settings_id}_hide_flex"):
            st.session_state[open_key] = False
            st.rerun()


def render_editor_thermal(settings_id, cfg, full_key, open_key, dev_type, cfgs):
    st.caption(
    """
    **Space heating logic**

    The indoor temperature is allowed to vary freely between the minimum and maximum values.

    • The **heat pump** is the main heating source and only runs when the temperature drops below the minimum limit.  
    • The **electric heater** can act as a **backup**, turning on only if the heat pump is running at full capacity but still cannot maintain the minimum temperature, or as a **user-scheduled** heat source.  
    • The **wood stove** is fully user-controlled and injects heat during selected time intervals, which may temporarily raise the indoor temperature above the maximum limit.

    """
    )


    hpar = st.session_state.get("thermal_house_params", {})
    tmin_def = float(hpar.get("t_min_default", 20.0))
    tmax_def = float(hpar.get("t_max_default", 22.0))

    # Use the robust context builder
    context = build_simulation_context()
    idx_hp, tout = _get_outdoor_profile_local()

    if dev_type == "space_heat":
        st.markdown("**Space heating (shared system)**")
        cfg["space_control_mode"] = "normal"  # normal-only UI; schedules only for wood (we'll wire it in simulator next)

        # --- temp band ---
        tmin_default_safe = _clamp_float(cfg.get("t_min_c", tmin_def), 10.0, 30.0, 20.0)
        tmax_default_safe = _clamp_float(cfg.get("t_max_c", tmax_def), 10.0, 30.0, 22.0)

        c_tmin, c_tmax = st.columns(2)
        cfg["t_min_c"] = c_tmin.number_input(
            "Min indoor temperature (°C)",
            min_value=10.0, max_value=30.0, step=0.5,
            value=tmin_default_safe,
            key=f"{settings_id}_tmin",
        )
        cfg["t_max_c"] = c_tmax.number_input(
            "Max indoor temperature (°C)",
            min_value=10.0, max_value=30.0, step=0.5,
            value=tmax_default_safe,
            key=f"{settings_id}_tmax",
        )
        if cfg["t_max_c"] <= cfg["t_min_c"]:
            cfg["t_max_c"] = cfg["t_min_c"] + 0.5
            st.info("Adjusted max temperature to be above min temperature.")

        # --- which devices exist ---
        c1, c2, c3 = st.columns(3)
        has_hp = c1.checkbox("Heat pump", value=bool(cfg.get("has_hp", True)), key=f"{settings_id}_has_hp")
        has_eh = c2.checkbox("Electric heater", value=bool(cfg.get("has_eh", True)), key=f"{settings_id}_has_eh")
        has_wood = c3.checkbox("Wood stove", value=bool(cfg.get("has_wood", False)), key=f"{settings_id}_has_wood")

        cfg["has_hp"] = has_hp
        cfg["has_eh"] = has_eh
        cfg["has_wood"] = has_wood

        enabled_names = []
        if has_hp: enabled_names.append("Heat pump")
        if has_eh: enabled_names.append("Electric heater")
        if has_wood: enabled_names.append("Wood stove")

        if not enabled_names:
            st.warning("Select at least one device or space heating will be zero.")

       
       

        # helper for wood schedule UI
        def _edit_intervals(block_key: str, default_start=_time(18, 0), default_end=_time(23, 0)):
            intervals = cfg.setdefault(block_key, [])
            if not intervals:
                intervals.append({"start": default_start, "end": default_end})

            del_idx = None
            for j, iv in enumerate(intervals):
                a, b, c = st.columns([0.4, 0.4, 0.2])
                iv["start"] = a.time_input(
                    "Start", value=iv.get("start", default_start),
                    key=f"{settings_id}_{block_key}_s{j}"
                )
                iv["end"] = b.time_input(
                    "End", value=iv.get("end", default_end),
                    key=f"{settings_id}_{block_key}_e{j}"
                )
                if c.button("🗑", key=f"{settings_id}_{block_key}_del{j}"):
                    del_idx = j

            if del_idx is not None:
                intervals.pop(del_idx)
                st.rerun()

            if st.button("➕ Add interval", key=f"{settings_id}_{block_key}_add"):
                intervals.append({"start": default_start, "end": default_end})
                st.rerun()

            return intervals

        # --- build sources for simulator ---
        sources = []

        if has_hp:
            st.subheader("Heat pump")

            # Ensure hp_dispatch_mode has a default value
            if "hp_dispatch_mode" not in cfg:
                cfg["hp_dispatch_mode"] = "modulating"

            hp_q = st.number_input(
                "Capacity (kW thermal per device)",
                min_value=1.0, max_value=30.0, step=0.5,
                value=float(cfg.get("hp_q_th_kw", 6.0)),
                key=f"{settings_id}_hpq",
            )
            cfg["hp_q_th_kw"] = float(hp_q)

            hp_n = st.number_input(
                "Number of heat pumps",
                min_value=1, max_value=10, step=1,
                value=int(cfg.get("hp_n", 1)),
                key=f"{settings_id}_hpn",
            )
            cfg["hp_n"] = int(hp_n)

            st.markdown("**Heat pump control**")
            hp_mode = st.radio(
                "Control mode",
                ["Modulating (variable power)", "Fixed-stage (on/off)"],
                index=0 if cfg.get("hp_dispatch_mode", "modulating") == "modulating" else 1,
                key=f"{settings_id}_hp_mode",
            )
            cfg["hp_dispatch_mode"] = "modulating" if "Modulating" in hp_mode else "fixed"

            # fixed-stage: ON means a fixed thermal output (default = capacity)
            if cfg["hp_dispatch_mode"] == "fixed":
                hp_fixed_default = float(cfg.get("hp_q_fixed_th_kw", hp_q))
                cfg["hp_q_fixed_th_kw"] = st.number_input(
                    "Thermal output when ON (kWth per device)",
                    min_value=0.1, max_value=float(hp_q), step=0.1,
                    value=min(max(0.1, hp_fixed_default), float(hp_q)),
                    key=f"{settings_id}_hp_fixed_q",
                )
            else:
                cfg["hp_q_fixed_th_kw"] = float(cfg.get("hp_q_fixed_th_kw", hp_q))

            cfg["hp_p_idle_el_kw"] = st.number_input(
                "Idle electric power when not heating (kW)",
                min_value=0.0, max_value=1.0, step=0.01,
                value=float(cfg.get("hp_p_idle_el_kw", 0.05)),
                key=f"{settings_id}_hp_idle",
            )

            sources.append({
                "name": "Heat pump",
                "kind": "hp",
                "q_max_th_kw": float(hp_q),
                "num_devices": int(hp_n),
                "enabled": True,
                "intervals": [],  # normal-only
                "dispatch_mode": cfg["hp_dispatch_mode"],
                "q_fixed_th_kw": float(cfg.get("hp_q_fixed_th_kw", hp_q)),
                "p_idle_el_kw": float(cfg["hp_p_idle_el_kw"]),
            })


        if has_eh:
            st.subheader("Electric heater")

            eh_q_default = _clamp_float(cfg.get("eh_q_th_kw", 6.0), 0.5, 30.0, 6.0)
            eh_q = st.number_input(
                "Capacity (kW thermal per device)",
                min_value=0.5, max_value=30.0, step=0.5,
                value=eh_q_default,
                key=f"{settings_id}_ehq",
            )
            cfg["eh_q_th_kw"] = float(eh_q)

            eh_n_default = int(_clamp_float(cfg.get("eh_n", 1), 1, 10, 1))
            eh_n = st.number_input(
                "Number of electric heaters",
                min_value=1, max_value=10, step=1,
                value=eh_n_default,
                key=f"{settings_id}_ehn",
            )
            cfg["eh_n"] = int(eh_n)

            st.markdown("**Electric heater mode**")
            eh_mode_label = st.radio(
                "Mode",
                ["Backup (automatic)", "User scheduled"],
                index=0 if cfg.get("eh_mode", "backup") == "backup" else 1,
                key=f"{settings_id}_eh_mode",
            )
            cfg["eh_mode"] = "backup" if "Backup" in eh_mode_label else "user"

            # Fixed-stage by default (you can later add a “modulating” option, but don’t now)
            cfg["eh_dispatch_mode"] = "fixed"

            # fixed output when ON: default to full capacity
            eh_fixed_default = float(cfg.get("eh_q_fixed_th_kw", eh_q))
            cfg["eh_q_fixed_th_kw"] = st.number_input(
                "Thermal output when ON (kWth per device)",
                min_value=0.1, max_value=float(eh_q), step=0.1,
                value=min(max(0.1, eh_fixed_default), float(eh_q)),
                key=f"{settings_id}_eh_fixed_q",
            )

            eh_intervals = []
            if cfg["eh_mode"] == "user":
                st.caption("When is the electric heater ON? (user-driven)")
                eh_intervals = _edit_intervals("eh_intervals", _time(18, 0), _time(23, 0))
            else:
                # Backup mode: no schedule
                cfg["eh_intervals"] = []

            sources.append({
                "name": "Electric heater",
                "kind": "eh",
                "q_max_th_kw": float(eh_q),
                "num_devices": int(eh_n),
                "enabled": True,
                "intervals": eh_intervals,

                # pass-through fields for simulator
                "role": "forced" if cfg["eh_mode"] == "user" else "controlled",
                "dispatch_mode": "fixed",
                "q_fixed_th_kw": float(cfg["eh_q_fixed_th_kw"]),
            })


        if has_wood:
            st.subheader("Wood stove")

            wood_q_default = _clamp_float(cfg.get("wood_q_th_kw", 6.0), 0.5, 30.0, 6.0)
            wood_q = st.number_input(
                "Capacity (kW thermal per device)",
                min_value=0.5, max_value=30.0, step=0.5,
                value=wood_q_default,
                key=f"{settings_id}_woodq",
            )
            cfg["wood_q_th_kw"] = float(wood_q)

            wood_n_default = int(_clamp_float(cfg.get("wood_n", 1), 1, 5, 1))
            wood_n = st.number_input(
                "Number of stoves",
                min_value=1, max_value=5, step=1,
                value=wood_n_default,
                key=f"{settings_id}_woodn",
            )
            cfg["wood_n"] = int(wood_n)

            c_eta, c_lhv = st.columns(2)
            eta_default = _clamp_float(cfg.get("wood_eta", 0.70), 0.1, 1.0, 0.70)
            cfg["wood_eta"] = c_eta.number_input(
                "Efficiency η (0–1)",
                min_value=0.1, max_value=1.0, step=0.01,
                value=eta_default,
                key=f"{settings_id}_eta",
            )
            lhv_default = _clamp_float(cfg.get("wood_lhv_kwh_per_kg", 4.0), 1.0, 6.0, 4.0)
            cfg["wood_lhv_kwh_per_kg"] = c_lhv.number_input(
                "LHV (kWh/kg)",
                min_value=1.0, max_value=6.0, step=0.1,
                value=lhv_default,
                key=f"{settings_id}_lhv",
            )

            st.caption("When do you burn wood? (user-driven)")
            wood_intervals = _edit_intervals("wood_intervals", _time(18, 0), _time(23, 0))

            sources.append({
                "name": "Wood stove",
                "kind": "wood",
                "q_max_th_kw": float(wood_q),
                "num_devices": int(wood_n),
                "enabled": True,
                "intervals": wood_intervals,
            })



        cfg["space_sources"] = sources

        sim_cfg = DeviceConfig.from_dict(cfg)
        dbg = P2_devicesimulation_service.simulate_space_heat_shared_debug(sim_cfg, context) or {}

        P_total = dbg.get("P_el_total_kw")
        Ti = dbg.get("Ti_C")
        P_by = dbg.get("P_el_by_device_kw", {}) or {}
        wood_kg_day = float(dbg.get("wood_kg_day", 0.0))

        st.markdown("**Space-heating electric power (total)**")
        if isinstance(P_total, pd.Series) and not P_total.empty:
            preview_power_profile(cfg, P_total.index, P_total.values, "P_space_total_kW")
        else:
            st.info("No space-heating power profile returned yet (check simulator output).")

        st.markdown("**Indoor temperature (preview)**")
        if isinstance(Ti, pd.Series) and not Ti.empty:
            preview_series(Ti.index, Ti.values, "Ti", y_title="°C")
        else:
            st.info("No indoor temperature series returned yet (check simulator output).")

        st.markdown("**Electric power split by device**")
        fig = go.Figure()
        plotted = False
        for name, s in P_by.items():
            if not isinstance(s, pd.Series) or s.empty:
                continue
            if float(np.nanmax(s.values)) <= 1e-6:
                continue
            fig.add_scatter(x=s.index, y=s.values, mode="lines", name=name)
            plotted = True
        fig.update_layout(height=220, margin=dict(l=10, r=10, t=8, b=8), xaxis_title="Time", yaxis_title="kW")
        if plotted:
            st.plotly_chart(fig, width="stretch")
        else:
            st.caption("No per-device split available.")

        if has_wood:
            st.markdown("**Wood consumption (kg/day)**")
            st.metric("Wood (kg/day)", f"{wood_kg_day:.2f}")

        if st.button("▲ Hide", key=f"{settings_id}_hide_space"):
            st.session_state[open_key] = False
            st.rerun()


    elif dev_type == "dhw":
        st.markdown("<hr style='border-top: 1px dotted #bfdbfe; margin:0.4rem 0;'/>", unsafe_allow_html=True)
        st.markdown("**Domestic hot water (DHW)**")

        dhw_opts = ["Electric DHW tank", "Heat pump DHW tank"]
        cfg["dhw_mode"] = st.radio(
            "How is your domestic hot water heated?",
            dhw_opts,
            index=dhw_opts.index(cfg.get("dhw_mode", "Electric DHW tank")) if cfg.get("dhw_mode") in dhw_opts else 0,
            key=f"{settings_id}_dhw_mode",
        )

        P_dhw, T_tank = None, None
        # Always show DHW tank settings since we removed the "None" option
        c_vol, c_use = st.columns(2)
        cfg["volume_l"] = c_vol.number_input(
            "Tank volume (L)", min_value=50.0, max_value=500.0, step=25.0,
            value=float(cfg.get("volume_l", 200.0)), key=f"{settings_id}_dhw_vol"
        )

        label_map = {
            "Low – 1–2 persons, short showers": "Low",
            "Medium – 3–4 persons, normal use": "Medium",
            "High – 5+ persons or long showers": "High",
        }
        rev_map = {v: k for k, v in label_map.items()}
        usage_label = c_use.selectbox(
            "Usage level",
            list(label_map.keys()),
            index=list(label_map.keys()).index(rev_map.get(cfg.get("usage_level", "Medium"), "Medium – 3–4 persons, normal use")),
            key=f"{settings_id}_dhw_usage",
        )
        cfg["usage_level"] = label_map[usage_label]

        c_tmin, c_tmax = st.columns(2)
        cfg["t_min_c"] = c_tmin.number_input(
            "Min tank temperature (°C)", min_value=30.0, max_value=70.0, step=1.0,
            value=float(cfg.get("t_min_c", 45.0)), key=f"{settings_id}_dhw_tmin"
        )
        cfg["t_max_c"] = c_tmax.number_input(
            "Max tank temperature (°C)", min_value=30.0, max_value=70.0, step=1.0,
            value=float(cfg.get("t_max_c", 55.0)), key=f"{settings_id}_dhw_tmax"
        )

        p_def = 2.0 if cfg["dhw_mode"] == "Electric DHW tank" else 1.5
        cfg["p_el_kw"] = st.number_input(
            "Heater power (kW, thermal side)", min_value=0.5, max_value=10.0, step=0.5,
            value=float(cfg.get("p_el_kw", p_def)), key=f"{settings_id}_dhw_pel"
        )

        tank = DHWTank(
            volume_l=cfg["volume_l"],
            t_set_c=(cfg["t_min_c"] + cfg["t_max_c"]) / 2,
            hyst_band_c=max(cfg["t_max_c"] - cfg["t_min_c"], 1.0),
            p_el_kw=float(cfg["p_el_kw"]),  # treat UI input as kWth heater capacity
            usage_level=cfg["usage_level"],
            Ti0_c=(cfg["t_min_c"] + cfg["t_max_c"]) / 2,
        )

        

        COP_DHW = 2.5
        Q_heater_th, T_tank, Q_used, Q_spill= tank.series_kw(
            idx_hp, tout,
            q_extra_kw=0.0,
            t_cap_c=float(cfg.get("t_max_c", 55.0)),
        )

        if "Heat pump" in cfg["dhw_mode"]:
            P_dhw = Q_heater_th / COP_DHW
        else:
            P_dhw = Q_heater_th

        # --- Debug: tank temperature ---
        if isinstance(T_tank, pd.Series) and not T_tank.empty:
            st.markdown("**DHW tank temperature (preview)**")
            preview_series(T_tank.index, T_tank.values, "T_tank", y_title="°C")

            st.caption(
                f"T_tank min/avg/max: "
                f"{float(T_tank.min()):.1f} / {float(T_tank.mean()):.1f} / {float(T_tank.max()):.1f} °C"
            )
        else:
            st.info("No tank temperature series returned.")



        if P_dhw is not None:
            st.markdown("**DHW electrical power (preview)**")
            preview_power_profile(cfg, P_dhw.index, P_dhw.values, "P_DHW_kW")

        if st.button("▲ Hide", key=f"{settings_id}_hide_dhw"):
            st.session_state[open_key] = False
            st.rerun()

    elif dev_type == "leisure":
        st.markdown("<hr style='border-top: 1px dotted #bbf7d0; margin:0.4rem 0;'/>**Leisure thermal loads**", unsafe_allow_html=True)
        col_ht, col_pool = st.columns(2)
        cfg["hot_tub_enabled"] = col_ht.checkbox("🛁 Hot tub / spa", bool(cfg.get("hot_tub_enabled", False)), key=f"{settings_id}_ht_enable")
        cfg["pool_enabled"] = col_pool.checkbox("🏊 Pool heater", bool(cfg.get("pool_enabled", False)), key=f"{settings_id}_pool_enable")

        P_total = pd.Series(0.0, index=idx_hp, name="P_leisure_kW")

        if cfg["hot_tub_enabled"]:
            st.markdown("### 🛁 Hot tub")
            c_tgt, c_idle = st.columns(2)
            cfg["ht_target_c"] = c_tgt.number_input(
                "Target water temperature (°C)",
                min_value=25.0,
                max_value=45.0,
                value=float(cfg.get("ht_target_c", 40.0)),
                step=0.5,
                key=f"{settings_id}_ht_Ttarget",
            )
            cfg["ht_idle_c"] = c_idle.number_input(
                "Idle temperature (°C)",
                min_value=10.0,
                max_value=40.0,
                value=float(cfg.get("ht_idle_c", 30.0)),
                step=0.5,
                key=f"{settings_id}_ht_Tidle",
            )
            c_vol, c_pow = st.columns(2)
           
            cfg["ht_water_l"] = c_vol.number_input(
                "Water volume (L)",
                min_value=400.0,
                max_value=3000.0,
                value=float(cfg.get("ht_water_l", 1200.0)),
                step=50.0,
                key=f"{settings_id}_ht_vol",
            )
            cfg["ht_heater_kw"] = c_pow.number_input(
                "Heater capacity (kW)",
                min_value=1.0,
                max_value=12.0,
                value=float(cfg.get("ht_heater_kw", 5.0)),
                step=0.5,
                key=f"{settings_id}_ht_kw",
            )
            cfg["ht_insulation"] = st.selectbox("Cover / insulation level", ["Good cover", "Average", "Poor"], index=["Good cover", "Average", "Poor"].index(cfg.get("ht_insulation", "Average")), key=f"{settings_id}_ht_ins")
            ua_ht = 0.07 * {"Good cover": 0.6, "Poor": 1.4, "Average": 1.0}[cfg["ht_insulation"]]

            sessions = cfg.setdefault("ht_sessions", [])
            del_idx = None
            for j, sess in enumerate(sessions):
                c_s, c_d, c_del = st.columns([0.4, 0.4, 0.2])
                sess["start"] = c_s.time_input("Start", value=sess.get("start", _time(20, 0)), key=f"{settings_id}_ht_s_{j}")
                sess["duration_min"] = c_d.number_input("Duration (min)", 15, 600, 15, int(sess.get("duration_min", 60)), key=f"{settings_id}_ht_d_{j}")
                if c_del.button("🗑", key=f"{settings_id}_ht_del_{j}"):
                    del_idx = j
            if del_idx is not None:
                sessions.pop(del_idx)
                st.rerun()
            if st.button("➕ Add use session", key=f"{settings_id}_ht_add"):
                sessions.append({"start": _time(19, 0), "duration_min": 60})
                st.rerun()

            ht = WeatherHotTub(
                target_c=cfg["ht_target_c"],
                idle_c=cfg["ht_idle_c"],
                heater_kw=cfg["ht_heater_kw"],
                water_l=cfg["ht_water_l"],
                ua_kw_per_c=ua_ht,
                sessions=sessions,
                use_outdoor_for_ambient=False,
                indoor_ambient_c=21.0,
            )
            P_ht, _T_ht = ht.series_kw(idx_hp, tout)
            P_total += P_ht
            st.plotly_chart(go.Figure().add_scatter(x=P_ht.index, y=P_ht.values, mode="lines").update_layout(height=160, margin=dict(l=10, r=10, t=8, b=8), showlegend=False), width="stretch")

        if cfg["pool_enabled"]:
            st.markdown("### 🏊 Pool heater settings")
            c_tgt, c_idle = st.columns(2)
            cfg["pool_target_c"] = c_tgt.number_input(
                "Target water temperature (°C)",
                min_value=20.0,
                max_value=35.0,
                value=float(cfg.get("pool_target_c", 28.0)),
                step=0.5,
                key=f"{settings_id}_pool_Ttarget",
            )
            cfg["pool_idle_c"] = c_idle.number_input(
                "Idle temperature (°C)",
                min_value=5.0,
                max_value=35.0,
                value=float(cfg.get("pool_idle_c", 24.0)),
                step=0.5,
                key=f"{settings_id}_pool_Tidle",
            )
            c_vol, c_pow = st.columns(2)
            cfg["pool_water_l"] = c_vol.number_input(
                "Water volume (L)",
                min_value=5000.0,
                max_value=80000.0,
                value=float(cfg.get("pool_water_l", 30000.0)),
                step=500.0,
                key=f"{settings_id}_pool_vol",
            )
            cfg["pool_heater_kw"] = c_pow.number_input(
                "Heater capacity (kW, thermal)",
                min_value=3.0,
                max_value=40.0,
                value=float(cfg.get("pool_heater_kw", 15.0)),
                step=1.0,
                key=f"{settings_id}_pool_kw",
            )
            cfg["pool_insulation"] = st.selectbox("Cover / insulation level", ["Good cover", "Average", "Poor"], index=["Good cover", "Average", "Poor"].index(cfg.get("pool_insulation", "Average")), key=f"{settings_id}_pool_ins")
            ua_pool = 0.15 * {"Good cover": 0.6, "Poor": 1.4, "Average": 1.0}[cfg["pool_insulation"]]

            sessions = cfg.setdefault("pool_sessions", [])
            del_idx = None
            for j, sess in enumerate(sessions):
                c_s, c_d, c_del = st.columns([0.4, 0.4, 0.2])
                sess["start"] = c_s.time_input("Start", value=sess.get("start", _time(8, 0)), key=f"{settings_id}_pool_s_{j}")
                sess["duration_min"] = c_d.number_input("Duration (min)", 30, 1440, 30, int(sess.get("duration_min", 480)), key=f"{settings_id}_pool_d_{j}")
                if c_del.button("🗑", key=f"{settings_id}_pool_del_{j}"):
                    del_idx = j
            if del_idx is not None:
                sessions.pop(del_idx)
                st.rerun()
            if st.button("➕ Add Use sessions", key=f"{settings_id}_pool_add"):
                sessions.append({"start": _time(13, 0), "duration_min": 60})
                st.rerun()

            pool = WeatherHotTub(
                target_c=cfg["pool_target_c"],
                idle_c=cfg["pool_idle_c"],
                heater_kw=cfg["pool_heater_kw"],
                water_l=cfg["pool_water_l"],
                ua_kw_per_c=ua_pool,
                sessions=sessions,
                use_outdoor_for_ambient=True,
                indoor_ambient_c=21.0,
            )
            Q_pool, _T_pool = pool.series_kw(idx_hp, tout)
            P_pool = Q_pool / 3.5
            P_total += P_pool
            st.plotly_chart(go.Figure().add_scatter(x=P_pool.index, y=P_pool.values, mode="lines").update_layout(height=160, margin=dict(l=10, r=10, t=8, b=8), showlegend=False), width="stretch")

        if cfg["hot_tub_enabled"] or cfg["pool_enabled"]:
            st.markdown("**Total leisure electrical power**")
            preview_power_profile(cfg, P_total.index, P_total.values, "P_leisure_kW")

        if st.button("▲ Hide", key=f"{settings_id}_hide_leisure"):
            st.session_state[open_key] = False
            st.rerun()


def render_editor_ev(settings_id, cfg, full_key, open_key, dev_type):
    is_bike = (dev_type == "ebike")
    default_p = 0.5 if is_bike else 11.0
    default_cap = 1.0 if is_bike else 75.0

    with st.container():
        st.markdown(f"<hr style='border-top: 1px dotted #bfdbfe; margin:0.4rem 0;'/>**{'E-bike' if is_bike else 'EV charging'} settings**", unsafe_allow_html=True)
        if is_bike:
            st.markdown("**(Default 01:00–06:00 window)**")

        c_p, c_cap = st.columns(2)
        
        cfg["power_kw"] = c_p.number_input(
            "Charger power (kW)",
            min_value=0.1,
            max_value=50.0,
            value=float(cfg.get("power_kw", default_p)),
            step=0.1 if is_bike else 1.0,
            key=f"{settings_id}_p",
        )

        cfg["capacity_kwh"] = c_cap.number_input(
            "Battery capacity (kWh)",
            min_value=0.2,
            max_value=200.0,
            value=float(cfg.get("capacity_kwh", default_cap)),
            step=0.1 if is_bike else 1.0,
            key=f"{settings_id}_cap",
        )

        c_soc_a, c_soc_t = st.columns(2)
        cfg["soc_arrive"] = c_soc_a.number_input(
            "Arrival SOC (%)",
            min_value=0.0,
            max_value=100.0,
            value=float(cfg.get("soc_arrive", 40.0 if is_bike else 20.0)),
            step=5.0,
            key=f"{settings_id}_soc_a",
        )

        cfg["soc_target"] = c_soc_t.number_input(
            "Target SOC (%)",
            min_value=0.0,
            max_value=100.0,
            value=float(cfg.get("soc_target", 100.0 if is_bike else 80.0)),
            step=5.0,
            key=f"{settings_id}_soc_t",
        )
        if cfg["soc_target"] < cfg["soc_arrive"]:
            cfg["soc_target"] = cfg["soc_arrive"]

        energy = (cfg["soc_target"] - cfg["soc_arrive"]) / 100.0 * cfg["capacity_kwh"]
        dur_min = int(np.ceil(energy * 60.0 / cfg["power_kw"])) if cfg["power_kw"] > 0 and energy > 0 else 0
        cfg["duration_min"] = dur_min
        st.caption(f"Energy needed ≈ **{energy:.1f} kWh**, time ≈ **{dur_min} min**")

        cfg["w_cost"] = st.slider("Preference (0 = CO₂ only, 1 = cost only)", 0.0, 1.0, 0.05, float(cfg.get("w_cost", 1)), key=f"{settings_id}_w_cost")

        intervals = cfg.setdefault("intervals", [])
        if not intervals:
            intervals.append({"start": _time(1, 0), "end": _time(6, 0)})
        elif len(intervals) > 1:
            intervals[:] = intervals[:1]

        current_iv = intervals[0]
        c_a, c_b = st.columns(2)
        current_iv["start"] = c_a.time_input("Start", value=current_iv.get("start", _time(1, 0)), key=f"{settings_id}_start")
        end_time = (datetime.combine(date.today(), current_iv["start"]) + timedelta(minutes=dur_min)).time() if dur_min > 0 else current_iv.get("end", _time(6, 0))
        c_b.time_input("End (computed)", value=end_time, disabled=True, key=f"{settings_id}_end_disp")
        current_iv["end"] = end_time

        if st.button("💡 Suggest cheapest/cleanest (01:00–06:00)", key=f"{settings_id}_suggest"):
            if dur_min <= 0:
                st.warning("No energy needed")
            else:
                iv = suggest_best_interval_for_ev(duration_min=dur_min, w_cost=cfg["w_cost"], window_start_min=60, window_end_min=360)
                if iv:
                    intervals[0] = iv
                    st.rerun()
                else:
                    st.warning("No data available")

        st.markdown("**Charging profile (preview)**")
        prof = build_minute_profile(power_w=cfg["power_kw"] * 1000.0, intervals=intervals, step_min=1)
        preview_power_profile(cfg, prof.index, prof.values, "P_EV_kW")

        if st.button("▲ Hide details", key=f"{settings_id}_hide"):
            st.session_state[open_key] = False
            st.rerun()


def render_editor_pv(settings_id, cfg, full_key, open_key):
    with st.container():
        st.markdown("<hr style='border:0;border-top:1px dotted #bbf7d0;margin:0.4rem 0;'/>**PV system settings**", unsafe_allow_html=True)
        c_wp, c_np = st.columns(2)
        cfg["module_wp"] = c_wp.number_input(
            "Module nameplate (Wp)",
            min_value=50.0,
            max_value=1000.0,
            value=float(cfg.get("module_wp", 400.0)),
            step=10.0,
            key=f"{settings_id}_mod_wp",
        )

        cfg["n_panels"] = c_np.number_input(
            "Number of panels",
            min_value=0,
            max_value=2000,
            value=int(cfg.get("n_panels", 16)),
            step=1,
            key=f"{settings_id}_n_panels",
        )
        kwp = (cfg["module_wp"] * cfg["n_panels"]) / 1000.0
        st.caption(f"Total DC size: **{kwp:.2f} kWp**")

        c_tilt, c_az = st.columns(2)
        cfg["tilt"] = c_tilt.number_input(
            "Tilt (°)",
            min_value=0.0,
            max_value=90.0,
            value=float(cfg.get("tilt", 30.0)),
            step=1.0,
            key=f"{settings_id}_tilt",
        )

        cfg["azimuth"] = c_az.number_input(
            "Azimuth (180 = South)",
            min_value=0.0,
            max_value=360.0,
            value=float(cfg.get("azimuth", 180.0)),
            step=1.0,
            key=f"{settings_id}_az",
        )

        cfg["loss_frac"] = st.number_input(
            "System losses (fraction)",
            min_value=0.0,
            max_value=0.5,
            value=float(cfg.get("loss_frac", 0.14)),
            step=0.01,
            key=f"{settings_id}_loss",
        )
        sel_day = _normalize_selected_day(st.session_state.get("day"))
        idx_pv = pd.date_range(pd.Timestamp(sel_day), periods=1440, freq="min")

        pv_series = None
        if isinstance(st.session_state.get("weather_hr"), pd.DataFrame) and kwp > 0:
            try:
                pv_series = pv_from_weather_modelchain_from_df(
                    idx_min=idx_pv,
                    dfh=st.session_state["weather_hr"],
                    lat=float(st.session_state.get("geo_lat", 57.0488)),
                    lon=float(st.session_state.get("geo_lon", 9.9217)),
                    kwp=kwp,
                    tilt_deg=float(cfg["tilt"]),
                    az_deg=float(cfg["azimuth"]),
                    sys_loss_frac=float(cfg["loss_frac"]),
                )
                st.caption("Using fetched weather for PV preview.")
            except Exception:
                pv_series = None

        if pv_series is None:
            hours = idx_pv.hour + idx_pv.minute / 60.0
            pv_series = pd.Series(kwp * np.maximum(0.0, np.sin(np.pi * (hours - 6) / 12)), index=idx_pv)
            st.caption("Synthetic PV curve.")

        preview_power_profile(cfg, pv_series.index, pv_series.values, "P_PV_kW")

        if st.button("▲ Hide details", key=f"{settings_id}_hide_pv"):
            st.session_state[open_key] = False
            st.rerun()

def render_editor_battery(settings_id, cfg, full_key, open_key):
    st.markdown("**Battery settings**")

    c1, c2 = st.columns(2)
    cfg["E_kWh"] = c1.number_input(
        "Capacity (kWh)",
        min_value=0.1, max_value=1000.0, step=0.5,
        value=float(cfg.get("E_kWh", 10.0)),
        key=f"{settings_id}_E_kWh",
    )
    cfg["P_ch_max_kW"] = c2.number_input(
        "Max charge power (kW)",
        min_value=0.0, max_value=500.0, step=0.5,
        value=float(cfg.get("P_ch_max_kW", 5.0)),
        key=f"{settings_id}_Pch",
    )

    c3, c4 = st.columns(2)
    cfg["P_dis_max_kW"] = c3.number_input(
        "Max discharge power (kW)",
        min_value=0.0, max_value=500.0, step=0.5,
        value=float(cfg.get("P_dis_max_kW", 5.0)),
        key=f"{settings_id}_Pdis",
    )
    cfg["soc_init"] = c4.slider(
        "Initial SOC",
        0.0, 100.0, float(cfg.get("soc_init", 60.0)), 1.0,
        key=f"{settings_id}_soc0",
    )

    c5, c6 = st.columns(2)
    cfg["soc_min"] = c5.slider(
        "Min SOC",
        0.0, 100.0, float(cfg.get("soc_min", 10.0)), 1.0,
        key=f"{settings_id}_socmin",
    )
    cfg["soc_max"] = c6.slider(
        "Max SOC",
        0.0, 100.0, float(cfg.get("soc_max", 90.0)), 1.0,
        key=f"{settings_id}_socmax",
    )

    c7, c8 = st.columns(2)
    cfg["eta_ch"] = c7.slider(
        "Charge efficiency",
        0.5, 1.0, float(cfg.get("eta_ch", 0.95)), 0.01,
        key=f"{settings_id}_etach",
    )
    cfg["eta_dis"] = c8.slider(
        "Discharge efficiency",
        0.5, 1.0, float(cfg.get("eta_dis", 0.95)), 0.01,
        key=f"{settings_id}_etadis",
    )

    if cfg["soc_min"] > cfg["soc_max"]:
        st.error("Min SOC must be ≤ Max SOC.")

    if st.button("▲ Hide", key=f"{settings_id}_hide"):
        st.session_state[open_key] = False
        st.rerun()

def render_editor_simple(settings_id, cfg, full_key, open_key):
    with st.container():
        st.markdown("<div style='border:1px solid #ddd;border-radius:4px;padding:0.5rem;'>", unsafe_allow_html=True)
        c_p, c_s, c_d = st.columns(3)
        cfg["power_kw"] = c_p.number_input("Power (kW)", 0.0, 50.0, 0.1, float(cfg.get("power_kw", 0.5)), key=f"p_{full_key}")
        cfg["start"] = c_s.time_input("Start", value=cfg.get("start", _time(18, 0)), key=f"start_{full_key}")
        cfg["duration_min"] = int(c_d.number_input("Duration (min)", 0, 1440, 15, int(cfg.get("duration_min", 60)), key=f"dur_{full_key}"))

        if st.button("▲ Hide", key=f"hide_{full_key}"):
            st.session_state[open_key] = False
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

def render_editor_fuel_cell(settings_id, cfg, full_key, open_key):
    st.markdown("**Fuel cell (CHP) — fixed 5 kW methanol HT-PEMFC**")

    cfg["price_ch3oh"] = st.number_input(
        "Methanol price (DKK/kg)",
        min_value=0.0, max_value=100.0,
        value=float(cfg.get("price_ch3oh", 1.0)),
        step=0.1,
        key=f"{settings_id}_ch3oh",
    )

    c1, c2 = st.columns(2)
    cfg["min_on_min"] = c1.number_input(
        "Min ON time (min)",
        min_value=0, max_value=24*60,
        value=int(cfg.get("min_on_min", 60)),
        step=5,
        key=f"{settings_id}_min_on",
    )
    cfg["min_off_min"] = c2.number_input(
        "Min OFF time (min)",
        min_value=0, max_value=24*60,
        value=int(cfg.get("min_off_min", 60)),
        step=5,
        key=f"{settings_id}_min_off",
    )

    st.markdown("**Waste heat**")
    cfg["use_waste_heat"] = st.checkbox(
        "Reuse waste heat",
        value=bool(cfg.get("use_waste_heat", True)),
        key=f"{settings_id}_use_heat",
    )

    if cfg["use_waste_heat"]:
        prio_map = {
            "DHW": "dhw",
            "Space heating": "space",
            "DHW → Space heating": "dhw_then_space",
        }
        inv_map = {v: k for k, v in prio_map.items()}

        choice = st.selectbox(
            "Heat goes first to…",
            options=list(prio_map.keys()),
            index=list(prio_map.keys()).index(inv_map.get(cfg.get("heat_priority", "dhw"), "DHW")),
            key=f"{settings_id}_heat_prio",
        )
        cfg["heat_priority"] = prio_map[choice]

    if st.button("▲ Hide", key=f"{settings_id}_hide_fc"):
        st.session_state[open_key] = False
        st.rerun()

# -------------------------------------------------------------------
# Layout & Page Rendering
# -------------------------------------------------------------------
from utils.ui_styler import load_custom_css
from subpages.p0_front import log_event

# -------------------------------------------------------------------
# Layout & Page Rendering
# -------------------------------------------------------------------
def render_devices_page_house():
    # 📝 Tracking: Log that user reached Page 2 (once per session)
    if not st.session_state.get("logged_p2_reach", False):
        log_event("p2_reached")
        st.session_state["logged_p2_reach"] = True

    load_custom_css()
    
    # --- Page Header ---
    st.markdown("""
        <h1 style='margin-bottom: 0.5rem;'>Devices & Layout</h1>
        <p style='color: #64748B; font-size: 1rem; margin-bottom: 2rem; line-height: 1.6;'>
            <strong>Tell us about your home!</strong> First, describe your house size and how many people live there. 
            Then, check the boxes for appliances you have (like washing machine, electric car, solar panels, etc.). 
            For each device, you can set when you typically use it and how much energy it needs. 
            Don't worry if you're not sure about the numbers—we provide sensible defaults that work for most homes!
        </p>
    """, unsafe_allow_html=True)

    # --- Relocated House Inputs (Card) ---
    with st.container(border=True):
        st.markdown("#### 🏠 Household Environment")
        hi = st.session_state.get("house_info", {"size": "Medium house", "insulation": "Average", "residents": 2})
        
        c1, c2, c3 = st.columns(3)
        with c1:
            house_size_options = {
                "Small apartment": "40–80 m²,  1–3 rooms",
                "Medium house": "90–150 m²,  3–5 rooms",
                "Large house": "160–250 m², 5+ rooms",
            }
            hi["size"] = st.selectbox(
                "House size",
                options=list(house_size_options.keys()),
                index=["Small apartment", "Medium house", "Large house"].index(hi["size"]),
            )
            st.caption(f"**{house_size_options[hi['size']]}**")
            
        with c2:
            insulation_desc = {
                "Poor": "Old (pre-1980)",
                "Average": "Typical (1980–2010)",
                "Good": "New / Renovated",
            }
            hi["insulation"] = st.selectbox(
                "Insulation quality",
                list(insulation_desc.keys()),
                index=["Poor", "Average", "Good"].index(hi["insulation"]),
            )
            st.caption(f"**{insulation_desc[hi['insulation']]}**")
            
        with c3:
            hi["residents"] = st.number_input(
                "Residents",
                min_value=1, max_value=8, step=1,
                value=int(hi["residents"]),
            )
            st.caption("People living in the house")
            
        st.session_state["house_info"] = hi

    st.markdown("<br>", unsafe_allow_html=True)

    # --- Preset Application Logic ---
    prev_size = st.session_state.get("last_house_size")
    current_size = hi["size"]
    prev_res = st.session_state.get("last_house_residents")
    current_res = hi["residents"]

    if prev_size != current_size:
        st.session_state["last_house_size"] = current_size
        if prev_size is not None:
            # Apply Preset on change
            preset = HOUSE_TYPE_PRESETS.get(current_size)
            if preset:
                # 1. Update selection
                st.session_state["device_selection"] = {k: True for k in preset["selected_devices"]}
                
                # 2. Reset and apply config overrides
                st.session_state["device_configs"] = {}
                for full_key, overrides in preset.get("config_overrides", {}).items():
                    cat, dev = full_key.split(":")
                    cfg = get_default_config(dev, cat)
                    cfg.update(overrides)
                    st.session_state["device_configs"][full_key] = cfg
                
                # 3. Clear widget states to force UI update
                for k in list(st.session_state.keys()):
                    if k.startswith("chk_"):
                        del st.session_state[k]
                st.rerun()

    elif prev_res != current_res:
        st.session_state["last_house_residents"] = current_res
        if prev_res is not None:
            # Resident count changed -> update sensitive defaults for enabled devices
            for full_key, cfg in st.session_state.get("device_configs", {}).items():
                cat, dev = full_key.split(":")
                # Only update if it's a resident-sensitive device
                if dev in ["dhw", "tv", "pc_desktop", "range_hood", "oven", "induction"]:
                    new_def = get_default_config(dev, cat)
                    if dev == "dhw":
                        cfg["usage_level"] = new_def["usage_level"]
                        cfg["volume_l"] = new_def["volume_l"]
                    elif dev in ["tv", "pc_desktop"]:
                        cfg["num_devices"] = new_def["num_devices"]
                    elif dev in ["range_hood", "oven", "induction"]:
                        cfg["duration_min"] = new_def["duration_min"]

    if "device_selection" not in st.session_state:
        preset = HOUSE_TYPE_PRESETS.get(current_size, HOUSE_TYPE_PRESETS["Medium house"])
        st.session_state["device_selection"] = {k: True for k in preset["selected_devices"]}

    if "device_configs" not in st.session_state:
        st.session_state["device_configs"] = {}
        preset = HOUSE_TYPE_PRESETS.get(current_size, HOUSE_TYPE_PRESETS["Medium house"])
        for full_key, overrides in preset.get("config_overrides", {}).items():
            cat, dev = full_key.split(":")
            cfg = get_default_config(dev, cat)
            cfg.update(overrides)
            st.session_state["device_configs"][full_key] = cfg

    sel = st.session_state["device_selection"]
    cfgs = st.session_state["device_configs"]

    # --- Reset button ---
    if st.button("🔄 Reset all devices to house defaults", width="stretch"):
        preset = HOUSE_TYPE_PRESETS.get(current_size)
        if preset:
            st.session_state["device_selection"] = {k: True for k in preset["selected_devices"]}
            st.session_state["device_configs"] = {}
            for full_key, overrides in preset.get("config_overrides", {}).items():
                cat, dev = full_key.split(":")
                cfg = get_default_config(dev, cat)
                cfg.update(overrides)
                st.session_state["device_configs"][full_key] = cfg
            
            # Clear widget states to force UI update
            for k in list(st.session_state.keys()):
                if k.startswith("chk_"):
                    del st.session_state[k]
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # Build and reuse one context for the whole page
    context = build_simulation_context()

    st.markdown("---")
    st.markdown("### Device Catalog")
    st.info("Select and configure individual devices below to tailor the simulation to your actual home.")

    render_global_flex_settings()

    for cat in ["elec_fixed", "elec_flex", "thermal", "outside", "gen_store"]:
        render_category_block(cat, sel, cfgs)

    # Generate charts AFTER device selection
    st.markdown("---")
    st.markdown("### Environment Preview")
    
    top_left, top_right = st.columns([1, 1])
    
    with top_left:
        with st.container(border=True):
            st.markdown("#### House Layout Visualization")
            layout_fig = build_house_layout_figure(sel, cfgs)
            st.plotly_chart(layout_fig, width="stretch", config={"responsive": True})

    with top_right:
        with st.container(border=True):
            st.markdown("#### Aggregated Daily Load")
            idx, device_traces, total = compute_daily_profiles(sel, cfgs, context)

            figp = go.Figure()

            for full_key, series in device_traces.items():
                if series is None or not isinstance(series, pd.Series) or series.empty:
                    continue
                if series.max() <= 0:
                    continue
                if full_key.startswith("gen_store") and "pv" in full_key:
                    continue
                label = DEVICE_LABEL_MAP.get(full_key, full_key)
                cfg = cfgs.get(full_key, {})
                if int(cfg.get("num_devices", 1)) > 1 and not full_key.startswith("other"):
                    label += f" (x{cfg['num_devices']})"
                figp.add_scatter(x=idx, y=series.values, mode="lines", name=label)

            for full_key, series in device_traces.items():
                if series is None or not isinstance(series, pd.Series) or series.empty:
                    continue
                if full_key.startswith("gen_store") and "pv" in full_key and series.max() > 0:
                    figp.add_scatter(x=idx, y=series.values, mode="lines", name="PV generation", fill="tozeroy", line=dict(width=1))

            figp.add_scatter(x=idx, y=total.values, mode="lines", name="Total load", line=dict(width=3, color="#2563EB"))
            figp.update_layout(
                height=300, 
                margin=dict(l=10, r=10, t=10, b=10), 
                xaxis_title="", 
                yaxis_title="kW", 
                legend=dict(orientation="h", yanchor="bottom", y=-0.8, xanchor="center", x=0.5),
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                template="plotly_white"
            )
            st.plotly_chart(figp, width="stretch")

    st.markdown("<div style='height: 2rem;'></div>", unsafe_allow_html=True)
    
    _, col_btn1, col_btn2, _ = st.columns([1.2, 1, 1, 1.2])
    with col_btn1:
        if st.button("⬅ Step 1: Market & Weather", key="back_to_p1"):
            st.session_state["active_page"] = "Market & Weather"
            st.rerun()
    with col_btn2:
        if st.button("Go to Step 3: Analysis ➔", key="next_to_p3"):
            st.session_state["active_page"] = "Analysis"
            st.rerun()


def render_global_flex_settings():
    default_flex = {
        "w_cost": 1,
        "window_mode": "Daytime (08–17)",
        "earliest": _time(8, 0),
        "latest": _time(17, 0),
        "earliest_custom": _time(7, 0),
        "latest_custom": _time(22, 0),
    }
    flex_prefs = st.session_state.setdefault("flex_prefs", default_flex.copy())
    if not isinstance(flex_prefs, dict):
        st.session_state["flex_prefs"] = default_flex.copy()
    return


def render_category_block(cat_key: str, sel: dict, cfgs: dict):
    info = DEVICE_CATEGORIES[cat_key]
    st.markdown(f"#### {info['title']}")
    st.caption(info["help"])

    if cat_key == "elec_flex":
        _render_flex_globals(cfgs, sel)

    # Define the dialog function once for this category
    @st.dialog("Configure Device", width="large")
    def show_device_config_dialog(full_key, cat_key, dev_type, label, cfg, cfgs):
        display_label = resolve_display_label(full_key, dev_type, cfg)
        st.markdown(f"### {display_label}")
        
        settings_id = full_key.replace(":", "_")
        open_key = f"open_cfg_{full_key}"
        
        if dev_type.startswith("other"):
            base = label.split(" ", 1)[1] if " " in label else label
            cfg["custom_name"] = st.text_input("Name", value=cfg.get("custom_name", base), key=f"name_{full_key}")

        if cat_key == "elec_fixed":
            render_editor_fixed(settings_id, cfg, full_key, open_key)
        elif cat_key == "elec_flex":
            render_editor_flex(settings_id, cfg, full_key, open_key, dev_type, cfgs)
        elif cat_key == "thermal":
            render_editor_thermal(settings_id, cfg, full_key, open_key, dev_type, cfgs)
        elif cat_key == "outside":
            render_editor_ev(settings_id, cfg, full_key, open_key, dev_type)
        elif cat_key == "gen_store" and dev_type == "pv":
            render_editor_pv(settings_id, cfg, full_key, open_key)
        elif cat_key == "gen_store" and dev_type == "battery":
            render_editor_battery(settings_id, cfg, full_key, open_key)
        elif cat_key == "gen_store" and dev_type == "fuel_cell":
            render_editor_fuel_cell(settings_id, cfg, full_key, open_key)
        elif cat_key == "gen_store":
            st.markdown("🚧 **Model not implemented yet**")
        else:
            render_editor_simple(settings_id, cfg, full_key, open_key)
        
        # Close button
        if st.button("Close", type="primary", width="stretch"):
            st.session_state[f"dialog_{full_key}"] = False
            st.rerun()

    cols = st.columns(3)
    for i, (dev_type, label) in enumerate(info["devices"]):
        full_key = f"{cat_key}:{dev_type}"
        cfg = ensure_device_cfg(full_key, cat_key, dev_type, cfgs)

        display_label = resolve_display_label(full_key, dev_type, cfg)

        with cols[i % 3]:
            with st.container(border=True):
                c1, c2 = st.columns([0.8, 0.2])
                with c1:
                    sel[full_key] = st.checkbox(display_label, value=sel.get(full_key, False), key=f"chk_{full_key}")
                with c2:
                    if st.button("⚙️", key=f"cfg_{full_key}"):
                        st.session_state[f"dialog_{full_key}"] = True

    # Check if any dialog should be opened (only one at a time)
    for i, (dev_type, label) in enumerate(info["devices"]):
        full_key = f"{cat_key}:{dev_type}"
        if st.session_state.get(f"dialog_{full_key}", False):
            cfg = ensure_device_cfg(full_key, cat_key, dev_type, cfgs)
            show_device_config_dialog(full_key, cat_key, dev_type, label, cfg, cfgs)
            break  # Only open one dialog


def _render_flex_globals(cfgs, sel):
    flex_prefs = st.session_state["flex_prefs"]
    with st.expander("⚙️ Flexible load – global settings", expanded=False):
        window_options = ["Any time (00–24)", "Daytime (08–17)", "Evening (17–23)", "Night (00–06)", "Custom"]
        flex_prefs["window_mode"] = st.radio(
            "When is it OK to run?",
            window_options,
            index=window_options.index(flex_prefs.get("window_mode", "Daytime (08–17)")),
            key="flex_window_mode",
            horizontal=True
        )

        mode = flex_prefs["window_mode"]
        if mode == "Custom":
            c1, c2 = st.columns(2)
            flex_prefs["earliest"] = c1.time_input("Earliest", flex_prefs.get("earliest_custom", _time(7, 0)), key="flex_earliest_custom")
            flex_prefs["latest"] = c2.time_input("Latest", flex_prefs.get("latest_custom", _time(22, 0)), key="flex_latest_custom")
            flex_prefs["earliest_custom"] = flex_prefs["earliest"]
            flex_prefs["latest_custom"] = flex_prefs["latest"]
        else:
            mapping = {
                "Any time (00–24)": (0, 0, 23, 59),
                "Daytime (08–17)": (8, 0, 17, 0),
                "Evening (17–23)": (17, 0, 23, 0),
                "Night (00–06)": (0, 0, 6, 0),
            }
            h1, m1, h2, m2 = mapping.get(mode, (8, 0, 17, 0))
            flex_prefs["earliest"] = _time(h1, m1)
            flex_prefs["latest"] = _time(h2, m2)

        flex_prefs["w_cost"] = st.slider("Preference (0=CO₂, 1=Cost)", 0.0, 1.0, 0.05, float(flex_prefs.get("w_cost", 1)), key="flex_w_cost")

        c1, c2 = st.columns(2)
        if c1.button("💡 Suggest schedules for all", key="flex_suggest_all", type="primary", width="stretch"):
            any_up = False
            for fk, cfg in cfgs.items():
                if fk.startswith("elec_flex:") and cfg:
                    iv = suggest_best_interval_for_day(int(cfg.get("duration_min", 60)), flex_prefs["w_cost"], flex_prefs["earliest"], flex_prefs["latest"])
                    if iv:
                        cfg["intervals"] = [iv]
                        prof = build_minute_profile(float(cfg.get("power_w", 1000)) * int(cfg.get("num_devices", 1)), [iv])
                        cfg["profile_index"] = prof.index.astype(str).tolist()
                        cfg["profile_kw"] = prof.values.tolist()
                        any_up = True
            if any_up:
                st.success("Updated.")
                st.rerun()
            else:
                st.info("No updates possible.")

        if c2.button("↩ Reset defaults", key="flex_reset_all", width="stretch"):
            st.session_state["flex_prefs"] = {
                "w_cost": 1,
                "window_mode": "Daytime (08–17)",
                "earliest": _time(8, 0),
                "latest": _time(17, 0),
                "earliest_custom": _time(7, 0),
                "latest_custom": _time(22, 0),
            }
            for fk in list(cfgs.keys()):
                if fk.startswith("elec_flex:"):
                    cfgs[fk] = get_default_config(fk.split(":", 1)[1], "elec_flex")
            st.success("Reset basic defaults.")
            st.rerun()

        rows = []
        for dev_type, label in DEVICE_CATEGORIES["elec_flex"]["devices"]:
            fk = f"elec_flex:{dev_type}"
            if sel.get(fk) and cfgs.get(fk):
                cfg = cfgs[fk]
                lbl = resolve_display_label(fk, dev_type, cfg)
                intervals = cfg.get("intervals", [])
                time_str = ", ".join([f"{i.get('start').strftime('%H:%M')}–{i.get('end').strftime('%H:%M')}" for i in intervals if i.get("start") and i.get("end")])
                rows.append({"Device": lbl, "#": cfg.get("num_devices", 1), "Dur": cfg.get("duration_min"), "Time": time_str})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.caption("No flexible devices selected.")
