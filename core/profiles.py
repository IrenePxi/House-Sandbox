from __future__ import annotations
import numpy as np
import pandas as pd
from datetime import datetime, date, time

def minute_index(period_start: date, period_end: date, step_min: int = 1) -> pd.DatetimeIndex:
    start = datetime.combine(period_start, time(0, 0))
    end   = datetime.combine(period_end, time(23, 59))
    return pd.date_range(start=start, end=end, freq=f"{step_min}min")

def default_price_profile(idx: pd.DatetimeIndex) -> pd.Series:
    minutes = (idx.view('i8') - idx[0].to_datetime64().astype('datetime64[ns]').astype('int64')) // (60*10**9)
    t = (minutes % 1440) / 1440.0 * 2*np.pi
    base = 1.5 + 0.4*np.sin(t - 0.5) + 0.8*np.maximum(0, np.sin(2*t))
    hour = idx.hour + idx.minute/60.0
    peak = 0.9*np.exp(-0.5*((hour-8.0)/1.5)**2) + 1.0*np.exp(-0.5*((hour-19.0)/1.8)**2)
    price = base + peak
    return pd.Series(price, index=idx, name="price_dkk_per_kwh")

def default_co2_profile(idx: pd.DatetimeIndex) -> pd.Series:
    hour = idx.hour + idx.minute/60.0
    co2 = 250 + 80*np.cos((hour-12)/12*np.pi) + 40*np.exp(-0.5*((hour-19)/1.5)**2)
    return pd.Series(co2, index=idx, name="co2_g_per_kwh")

def simple_pv_profile(idx: pd.DatetimeIndex, kwp: float = 3.0) -> pd.Series:
    hour = idx.hour + idx.minute/60.0
    pv = np.zeros(len(idx), dtype=float)
    mask = (hour >= 6.0) & (hour <= 18.0)
    x = (hour[mask]-6.0)/12.0 * np.pi
    pv[mask] = kwp * np.sin(x)
    return pd.Series(pv, index=idx, name="pv_kw")

def synthetic_outdoor_temp(idx: pd.DatetimeIndex, mean_c: float = 6.0, swing_c: float = 4.0, phase_hours: float = 15.0) -> pd.Series:
    """Very light diurnal outdoor temperature (°C). Peak around 'phase_hours' local time by default."""
    h = idx.hour + idx.minute/60.0
    t = mean_c + swing_c * np.sin((h - phase_hours)/24.0 * 2*np.pi)
    return pd.Series(t, index=idx, name="Tout_C")


def build_minute_profile(power_w: float,
                            intervals: list[dict],
                            step_min: int = 1) -> pd.Series:
    """
    Build a 24h minutely (or step_min) profile for one device.
    power_w: device power when ON (constant)
    intervals: list of dicts with 'start'/'end' as datetime.time
    Returns Series [kW] indexed from 00:00–24:00 (dummy date).
    """
    if step_min <= 0:
        step_min = 1

    # Use a dummy date (just for plotting)
    dummy_day = date(2025, 1, 10)
    start_dt = pd.Timestamp(dummy_day)
    periods = (24 * 60) // step_min
    idx = pd.date_range(start=start_dt, periods=periods, freq=f"{step_min}min")

    power = np.zeros(len(idx), dtype=float)  # [kW]

    # Precompute interval masks
    for it in intervals:
        s: time = it["start"]
        e: time = it["end"]

        # Map to minutes since midnight
        s_min = s.hour * 60 + s.minute
        e_min = e.hour * 60 + e.minute

        # Handle "wrap around" (if end < start, we treat it as crossing midnight)
        if e_min <= s_min:
            ranges = [(s_min, 24 * 60), (0, e_min)]
        else:
            ranges = [(s_min, e_min)]

        for (m0, m1) in ranges:
            # indices of idx where minutes since midnight in [m0, m1)
            rel_min = ((idx - idx[0]).total_seconds() / 60.0).astype(int)
            mask = (rel_min >= m0) & (rel_min < m1)
            power[mask] = power_w / 1000.0  # convert W → kW

    return pd.Series(power, index=idx, name="P_device_kW")

