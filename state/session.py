"""
Session state helpers.
Moved from app.py lines 712-868, 1035-1124, 2041-2078 — NO LOGIC CHANGES.
"""
from __future__ import annotations
import streamlit as st
import pandas as pd
import numpy as np
from datetime import time as _time, date as _date
from models.schemas import SimulationContext


def get_selected_day_data(input_series):
    sel_day = st.session_state.get("day") 

    day_start = pd.Timestamp(sel_day)
    day_end   = day_start + pd.Timedelta(days=1)
    df=input_series.copy()

    df = df.loc[(df.index >= day_start) & (df.index < day_end)]

    return df


def get_thermal_building_params():
    """
    Map global house_info (size, insulation) to:
      - ua_base  [kW/°C]
      - q_guess  [kW]
      - Cth_guess [kWh/°C]
    """
    hi = st.session_state.get("house_info", {
        "size": "Medium house",
        "insulation": "Average",
        "residents": 2,
    })
    size = hi.get("size", "Medium house")
    ins  = hi.get("insulation", "Average")

    # base UA & capacity guess by size
    if size == "Small apartment":
        ua_base = 0.10
        q_guess = 4.0
        Cth_guess = 0.50
    elif size == "Large house":
        ua_base = 0.14
        q_guess = 8.0
        Cth_guess = 0.75
    else:  # "Medium house"
        ua_base = 0.12
        q_guess = 6.0
        Cth_guess = 0.60

    # adjust UA by insulation
    if ins == "Poor":
        ua = ua_base * 1.3
    elif ins == "Good":
        ua = ua_base * 0.7
    else:
        ua = ua_base

    return ua, q_guess, Cth_guess, size, ins


def get_outdoor_minute_profile():
    """
    Return (idx_minute, tout_minute Series) for the selected day.
    Uses temp_daily from session if available; otherwise synthetic profile.
    """
    if (
        "temp_daily" in st.session_state
        and isinstance(st.session_state["temp_daily"], pd.Series)
        and not st.session_state["temp_daily"].empty
    ):
        tout_tot = st.session_state["temp_daily"]
        # you already have this helper elsewhere:
        tout_minute = get_selected_day_data(tout_tot)
        idx = tout_minute.index
        st.caption("Using fetched outdoor temperature for this preview.")
        return idx, tout_minute.astype(float)

    # fallback: synthetic daily sinusoid
    idx = pd.date_range("2025-01-10 00:00", periods=24 * 60, freq="min")
    hours = idx.hour + idx.minute / 60.0
    tout_minute = pd.Series(
        5.0 + 5.0 * np.sin(2 * np.pi * (hours - 15) / 24.0),
        index=idx,
        name="Tout_C",
    )
    st.caption(
        "No weather data found, using a synthetic outdoor temperature profile."
    )
    return idx, tout_minute.astype(float)


def suggest_best_interval_for_day(
    duration_min: int,
    w_cost: float = 0.5,
    earliest: _time | None = None,
    latest:  _time | None = None,
) -> dict | None:
    """
    Returns {"start": time, "end": time} for the chosen day,
    restricted to [earliest, latest] if provided.
    """
    price = st.session_state.get("price_daily")
    co2   = st.session_state.get("co2_daily")

    sel_day = st.session_state.get("day")

    if not isinstance(sel_day, _date):
        pr = st.session_state.get("period_range")
        if pr and len(pr) == 2 and isinstance(pr[1], _date):
            sel_day = pr[1]

    if price is None or co2 is None or len(price) == 0 or not isinstance(sel_day, _date):
        return None

    df = pd.DataFrame(index=price.index.copy())
    df["price"] = np.asarray(price, dtype=float)
    df["co2"]   = co2.reindex(df.index).interpolate().bfill().ffill()

    day_start = pd.Timestamp(sel_day)
    day_end   = day_start + pd.Timedelta(days=1)
    df = df.loc[(df.index >= day_start) & (df.index < day_end)]
    if df.empty:
        return None

    # apply allowed window
    if earliest is not None and latest is not None:
        e_min = earliest.hour * 60 + earliest.minute
        l_min = latest.hour * 60 + latest.minute
        minutes_of_day = df.index.hour * 60 + df.index.minute
        if e_min <= l_min:
            mask = (minutes_of_day >= e_min) & (minutes_of_day <= l_min)
        else:
            # window wraps midnight
            mask = (minutes_of_day >= e_min) | (minutes_of_day <= l_min)
        df = df.loc[mask]
        if df.empty:
            return None

    # normalize
    for col in ["price", "co2"]:
        x = df[col].values.astype(float)
        mn, mx = np.nanmin(x), np.nanmax(x)
        if mx > mn:
            df[col] = (x - mn) / (mx - mn)
        else:
            df[col] = 0.5

    w_cost = float(np.clip(w_cost, 0.0, 1.0))
    df["score"] = w_cost * df["price"] + (1.0 - w_cost) * df["co2"]

    n = len(df)
    dur = int(duration_min)
    if dur <= 0:
        dur = 30
    if dur > n:
        dur = n

    best_score = None
    best_t0 = None
    for t0 in range(0, n - dur + 1):
        sc = float(df["score"].iloc[t0:t0 + dur].mean())
        if best_score is None or sc < best_score:
            best_score = sc
            best_t0 = t0

    if best_t0 is None:
        return None

    t_start = df.index[best_t0]
    t_end   = df.index[best_t0 + dur - 1] + pd.Timedelta(minutes=1)

    start_min = t_start.hour * 60 + t_start.minute
    end_min   = t_end.hour * 60 + t_end.minute
    start_min = max(0, min(start_min, 24 * 60 - 1))
    end_min   = max(1, min(end_min,   24 * 60 - 1))

    return {
        "start": _time(start_min // 60, start_min % 60),
        "end":   _time(end_min   // 60, end_min   % 60),
    }


def suggest_best_interval_for_ev(duration_min: int,
                                w_cost: float,
                                window_start_min: int = 60,
                                window_end_min: int = 360) -> dict | None:
    """
    Find best continuous interval of given length within [01:00, 06:00)
    using selected-day price & CO2 (minute series).
    Returns {"start": time, "end": time} or None.
    """

    price_all = st.session_state.get("price_daily")
    co2_all   = st.session_state.get("co2_daily")

    if not isinstance(price_all, pd.Series) or price_all.empty:
        return None
    if not isinstance(co2_all, pd.Series) or co2_all.empty:
        return None

    # Slice to selected day (same helper you already use elsewhere)
    price = get_selected_day_data(price_all)
    co2   = get_selected_day_data(co2_all)

    if price is None or co2 is None or price.empty or co2.empty:
        return None

    # Make sure aligned indices
    price, co2 = price.align(co2, join="inner")
    idx = price.index
    n = len(idx)
    if n == 0 or duration_min <= 0 or duration_min > n:
        return None

    rel_min = idx.hour * 60 + idx.minute
    prices  = price.values.astype(float)
    co2v    = co2.values.astype(float)

    w_c   = float(w_cost)
    w_co2 = 1.0 - w_c

    best_score = None
    best_start_pos = None

    # candidate starts only inside [window_start_min, window_end_min)
    candidates = np.where(
        (rel_min >= window_start_min) & (rel_min < window_end_min)
    )[0]

    for start_pos in candidates:
        end_pos = start_pos + duration_min
        if end_pos > n:
            break
        # ensure the *end* of block is still inside window
        if rel_min[end_pos - 1] >= window_end_min:
            continue

        p_seg = prices[start_pos:end_pos]
        c_seg = co2v[start_pos:end_pos]
        score = w_c * p_seg.mean() + w_co2 * c_seg.mean()

        if best_score is None or score < best_score:
            best_score = score
            best_start_pos = start_pos

    if best_start_pos is None:
        return None

    start_ts = idx[best_start_pos]
    end_ts   = idx[min(best_start_pos + duration_min, n - 1)] + pd.Timedelta(minutes=1)

    return {
        "start": start_ts.time(),
        "end":   end_ts.time(),
    }

def get_house_thermal_params():
    """
    Derive UA, C_th and default comfort band from house_info.

    ua_kw_per_c: kW/K
    C_th_kwh_per_c: kWh/K
    """
    hi = st.session_state.get(
        "house_info",
        {"size": "Medium house", "insulation": "Average", "residents": 2},
    )
    size = hi.get("size", "Medium house")
    ins  = hi.get("insulation", "Average")

    # --- floor area assumptions (m²) ---
    AREA_M2 = {
        "Small apartment": 60.0,
        "Medium house": 120.0,
        "Large house": 200.0,
    }
    area = float(AREA_M2.get(size, 120.0))

    # --- UA per m² (kW/K/m²) ---
    # Rough but realistic ranges (esp. DK 1980–2010)
    UA_PER_M2 = {
        "Poor": 1.6 / 1000.0,
        "Average": 1.0 / 1000.0,
        "Good": 0.6 / 1000.0,
    }
    ua_per_m2 = float(UA_PER_M2.get(ins, 1.0 / 1000.0))
    ua_kw_per_c = area * ua_per_m2

    # --- thermal mass per m² (kWh/K/m²) ---
    # This is what fixes the too-fast cycling.
    C_TH_PER_M2 = {
        "Small apartment": 0.06,
        "Medium house": 0.10,
        "Large house": 0.12,
    }
    c_per_m2 = float(C_TH_PER_M2.get(size, 0.10))
    C_th_kwh_per_c = area * c_per_m2

    # default comfort band (UI can override)
    return {
        "ua_kw_per_c": float(ua_kw_per_c),
        "C_th_kwh_per_c": float(C_th_kwh_per_c),
        "t_min_default": 20.0,
        "t_max_default": 22.0,
    }

def build_simulation_context() -> SimulationContext:
    """Build SimulationContext from Streamlit session state (single source of truth)."""
    selected_day = st.session_state.get("day")  # keep your normalize if you have it

    return SimulationContext(
        selected_day=selected_day,
        weather_hr=st.session_state.get("weather_hr") if isinstance(st.session_state.get("weather_hr"), pd.DataFrame) else None,
        temp_daily=st.session_state.get("temp_daily") if isinstance(st.session_state.get("temp_daily"), pd.Series) else None,
        geo_lat=float(st.session_state.get("geo_lat", 57.0488)),
        geo_lon=float(st.session_state.get("geo_lon", 9.9217)),
        thermal_house_params=st.session_state.get("thermal_house_params", {}) or {},
    )