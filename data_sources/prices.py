"""
Price data fetching from EnergiDataService.
Moved from app.py lines 141-273 — NO LOGIC CHANGES.
"""
from __future__ import annotations
import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime, date, time

# -------- EnergiDataService endpoints --------
EDS_PRICE_URL_OLD = "https://api.energidataservice.dk/dataset/Elspotprices"
EDS_PRICE_URL_NEW = "https://api.energidataservice.dk/dataset/DayAheadPrices"
TZ_DK = "Europe/Copenhagen"


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_dayahead_prices_latest(area: str = "DK1") -> pd.DataFrame:
    r = requests.get(f"{EDS_PRICE_URL_NEW}?limit=200000", timeout=40)
    r.raise_for_status()
    recs = r.json().get("records", [])
    if not recs:
        return pd.DataFrame()

    df = pd.DataFrame.from_records(recs)

    # Normalize new -> old column names
    if "TimeDK" in df.columns:
        df = df.rename(columns={"TimeDK": "HourDK"})
    if "DayAheadPriceDKK" in df.columns:
        df = df.rename(columns={"DayAheadPriceDKK": "SpotPriceDKK"})
    if "DayAheadPriceEUR" in df.columns:
        df = df.rename(columns={"DayAheadPriceEUR": "SpotPriceEUR"})

    if "HourDK" not in df.columns or "PriceArea" not in df.columns:
        return pd.DataFrame()

    # Filter area
    df = df[df["PriceArea"] == area].copy()
    if df.empty:
        return pd.DataFrame()

    # Clean time axis first
    df["HourDK"] = pd.to_datetime(df["HourDK"], errors="coerce")
    df = df.dropna(subset=["HourDK"]).sort_values("HourDK")
    df = df[~df["HourDK"].duplicated(keep="first")]  # handle DST/dups

    # NOW build price column so its length matches the cleaned index
    if "SpotPriceDKK" in df.columns and df["SpotPriceDKK"].notna().any():
        df["price_dkk_per_kwh"] = df["SpotPriceDKK"].astype(float) / 1000.0  # DKK/MWh -> DKK/kWh
    elif "SpotPriceEUR" in df.columns and df["SpotPriceEUR"].notna().any():
        eur_to_dkk = 7.45
        df["price_dkk_per_kwh"] = df["SpotPriceEUR"].astype(float) * eur_to_dkk / 1000.0
    else:
        return pd.DataFrame()

    return df.set_index("HourDK")[["price_dkk_per_kwh"]]


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_elspot_prices(area: str = "DK1") -> pd.DataFrame:
    r = requests.get(f"{EDS_PRICE_URL_OLD}?limit=200000", timeout=40); r.raise_for_status()
    df = pd.DataFrame.from_records(r.json().get("records", []))
    if df.empty or "HourDK" not in df or "PriceArea" not in df or "SpotPriceDKK" not in df:
        return pd.DataFrame()
    df = df[df["PriceArea"] == area][["HourDK","SpotPriceDKK"]].copy()
    df["price_dkk_per_kwh"] = df["SpotPriceDKK"].astype(float) / 1000.0
    return (df.assign(HourDK=pd.to_datetime(df["HourDK"], errors="coerce"))
              .dropna(subset=["HourDK"])
              .sort_values("HourDK")
              .set_index("HourDK")[["price_dkk_per_kwh"]])


def step_hold_to_minutes(s_native, idx_min):
    s = s_native.copy()
    s.index = pd.to_datetime(s.index, errors="coerce")
    s = s[~s.index.isna()]
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)

    # Slice to the day window (optional but tidy)
    start, end = idx_min[0], idx_min[-1]
    s = s[(s.index >= start) & (s.index <= end)]

    # Direct step-hold upsample to minutes
    s_min = (
        s.reindex(idx_min)   # put values on the exact minute grid
         .ffill()            # hold-forward within each 15-min bin and past the last stamp
         .bfill()            # fill the first few minutes before the first stamp, if any
         .astype(float)
    )
    return s_min.rename("price_dkk_per_kwh")



def daily_price_dual(idx_min: pd.DatetimeIndex, period_start:date, period_end:date, area):
    """
    Returns:
      - price_plot: native-cadence series aligned to idx_min (for charts)
      - price_hourly: clean hourly series (for EMS/optimization)
      - note: optional note for the UI
    """
    tz = "Europe/Copenhagen"
    day_start = pd.Timestamp(period_start).tz_localize(tz).tz_localize(None)
    end   = datetime.combine(period_end, time(23, 59))
    day_end   = pd.Timestamp(end).tz_localize(tz).tz_localize(None)
    note = None

    # Try new dataset (15-min)
    df_new = _fetch_dayahead_prices_latest(area)
    if not df_new.empty:
        s_native = df_new["price_dkk_per_kwh"].loc[(df_new.index >= day_start) & (df_new.index < day_end)]
    else:
        s_native = pd.Series(dtype=float)

    # Fallback to old dataset (hourly)
    if s_native.empty:
        df_old = _fetch_elspot_prices(area)
        if not df_old.empty:
            s_native = df_old["price_dkk_per_kwh"].loc[(df_old.index >= day_start) & (df_old.index < day_end)]

    # If still nothing → placeholder
    if s_native.empty:
        hrs = (idx_min - idx_min[0]).total_seconds()/3600.0
        price_plot = pd.Series(2.0 + 0.8*np.sin(2*np.pi*(hrs-17)/24.0), index=idx_min, name="price_dkk_per_kwh")
        note = "No day-ahead price data available for this day. Showing a smooth placeholder curve."
        return price_plot, note

    # Build plotting series at native resolution → align to minute index for display only
    price_plot = step_hold_to_minutes(s_native, idx_min)


    # Optional note if we had gaps
    miss = int(s_native.isna().sum()) if hasattr(s_native, "isna") else 0
    if miss > 0:
        note = f"Filled {miss} missing price points by interpolation."

    return price_plot, note
