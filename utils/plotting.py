"""
Plotting utility functions.
Moved from app.py lines 428-573, 4158-4203 — NO LOGIC CHANGES.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import date, timedelta


def plot_period_minute(
    series: pd.Series,
    selected_day: date | None,
    title: str,
    ytitle: str,
    height: int = 250
) -> go.Figure:
    """
    Plot a minute-level time series for an arbitrary period and highlight one selected day
    (if it lies inside the period).

    - series: minute-level Series with a DateTimeIndex (tz-naive)
    - selected_day: date chosen in sidebar (can be None)
    """
    fig = go.Figure()

    if series is None or series.empty:
        fig.update_layout(
            title=title,
            xaxis_title="Time",
            yaxis_title=ytitle,
            hovermode="x unified",
            height=height,
            margin=dict(l=20, r=20, t=30, b=80),
            legend=dict(orientation="h", yanchor="top", y=-0.35, xanchor="center", x=0.5)
        )
        return fig

    s = series.copy()
    s.index = pd.to_datetime(s.index)

    # 1) Full-period line
    fig.add_scatter(
        x=s.index,
        y=s.values,
        mode="lines",
        name="Full period",
        line=dict(color="rgba(100,100,100,0.7)", width=1),
    )

    # 2) Highlight selected day (if given and within range)
    if selected_day is not None:
        day_start = pd.Timestamp(selected_day)
        day_end   = day_start + timedelta(days=1)

        mask = (s.index >= day_start) & (s.index < day_end)
        if mask.any():
            s_sel = s[mask]

            # Background stripe for that day
            fig.add_vrect(
                x0=day_start,
                x1=day_end,
                fillcolor="rgba(200, 30, 30, 0.05)",
                line_width=0,
                layer="below",
            )

            # Thicker overlay line on that day
            fig.add_scatter(
                x=s_sel.index,
                y=s_sel.values,
                mode="lines",
                name="Selected day",
                line=dict(color="crimson", width=3),
            )

    fig.update_layout(
        title=title,
        xaxis_title="Time",
        yaxis_title=ytitle,
        hovermode="x unified",
        height=height,
        margin=dict(l=20, r=20, t=30, b=50),
        legend=dict(orientation="h", yanchor="top", y=-0.3, xanchor="center", x=0.5)
    )

    return fig

def plot_period_bar(
    series: pd.Series,
    selected_day: date | None,
    title: str,
    ytitle: str,
    bar_opacity: float = 0.8,
    height: int = 250
) -> go.Figure:
    """
    Plot a bar chart over a raw time series (minute/5-min/15-min/hourly).
    Highlight selected day with a different color and a background band.
    """
    fig = go.Figure()

    if series is None or series.empty:
        fig.update_layout(
            title=title, 
            xaxis_title="Time", 
            yaxis_title=ytitle,
            height=height,
            margin=dict(l=20, r=20, t=30, b=80),
            legend=dict(orientation="h", yanchor="top", y=-0.35, xanchor="center", x=0.5)
        )
        return fig

    s = series.copy()
    s.index = pd.to_datetime(s.index)

    # Detect step automatically (for bar width)
    if len(s) > 1:
        step_seconds = (s.index[1] - s.index[0]).total_seconds()
        bar_width_ms = step_seconds * 1000  # milliseconds
    else:
        bar_width_ms = 60000  # fallback: 1 min

    # 1) Entire period bars
    fig.add_bar(
        x=s.index,
        y=s.values,
        name="Full period",
        marker=dict(color="lightgray"),
        opacity=bar_opacity,
        width=bar_width_ms,
    )

    # 2) Highlight selected day
    if selected_day is not None:
        day_start = pd.Timestamp(selected_day)
        day_end = day_start + timedelta(days=1)
        mask = (s.index >= day_start) & (s.index < day_end)

        if mask.any():
            s_sel = s[mask]

            # Background shading for the day
            fig.add_vrect(
                x0=day_start,
                x1=day_end,
                fillcolor="rgba(200, 30, 30, 0.08)",
                line_width=0,
                layer="below",
            )

            # Overlay bars for selected day
            fig.add_bar(
                x=s_sel.index,
                y=s_sel.values,
                name="Selected day",
                marker=dict(color="crimson"),
                opacity=1.0,
                width=bar_width_ms,
            )

    fig.update_layout(
        title=title,
        xaxis_title="Time",
        yaxis_title=ytitle,
        barmode="overlay",
        hovermode="x unified",
        height=height,
        margin=dict(l=20, r=20, t=30, b=50),
        legend=dict(orientation="h", yanchor="top", y=-0.3, xanchor="center", x=0.5)
    )

    return fig


def _norm01(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    lo, hi = float(np.nanmin(s.values)), float(np.nanmax(s.values))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-12:
        # flat if constant/missing
        return pd.Series(0.5, index=s.index, dtype=float)
    out = (s - lo) / (hi - lo)
    return out.reindex(s.index).interpolate().bfill().ffill().astype(float)


def _ts_layout(title: str, ytitle: str = "kW", height: int = 280):
    return dict(
        title=dict(
            text=title,
            x=0.01, xanchor="left",
            y=0.98, yanchor="top",
            pad=dict(t=2, b=0, l=0, r=0)   # <- extra space under the title
        ),
        hovermode="x unified",
        height=height,
        margin=dict(l=36, r=16, t=64, b=32),     # <- larger top margin
        xaxis=dict(
            title="Time",
            type="date",
            rangeslider=dict(visible=False),
        ),
        yaxis=dict(title=ytitle),
        legend=dict(orientation="h", yanchor="bottom", y=1.06, x=0)
    )

def ems_power_split_plot(idx, load, pv, grid_import, pbat):
    """Stack-like view for EMS split."""
    fig = go.Figure()
    fig.add_scatter(x=idx, y=load, name="Load (kW)", mode="lines")
    fig.add_scatter(x=idx, y=pv,   name="PV (kW)",   mode="lines")
    fig.add_scatter(x=idx, y=grid_import, name="Grid import (kW)", mode="lines")
    fig.add_scatter(x=idx, y=pbat, name="Battery power (kW, +dis/-ch)", mode="lines")
    fig.update_layout(_ts_layout("Power split (EMS)", ytitle="kW", height=320))
    return fig

def ems_soc_plot(idx, soc_kwh, cap_kwh):
    soc_pct = (soc_kwh / max(cap_kwh, 1e-9)) * 100.0
    fig = go.Figure()
    fig.add_scatter(x=idx, y=soc_pct, name="SOC (%)", mode="lines")
    fig.update_layout(_ts_layout("Battery SOC", ytitle="%", height=220))
    return fig
