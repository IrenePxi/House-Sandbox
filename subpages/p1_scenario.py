
import streamlit as st
import pandas as pd
from datetime import date, timedelta
from utils.plotting import plot_period_minute, plot_period_bar
from data_sources.prices import daily_price_dual
from data_sources.co2 import daily_co2_with_note
from data_sources.weather import fetch_weather_open_meteo, daily_temperature_with_note
from core.profiles import minute_index

from utils.ui_styler import load_custom_css
from subpages.p0_front import log_event

def render_scenario_page():
    """Renders the Market & Weather page for selecting dates, location, and fetching data."""
    # 📝 Tracking: Log that user reached Page 1 (once per session)
    if not st.session_state.get("logged_p1_reach", False):
        log_event("p1_reached")
        st.session_state["logged_p1_reach"] = True

    load_custom_css()
    
    # Page Header
    st.markdown("""
        <h1 style='margin-bottom: 0.5rem;'>Market & Weather</h1>
        <p style='color: #64748B; font-size: 1rem; margin-bottom: 2rem; line-height: 1.6;'>
            <strong>Start here!</strong> First, choose the date range and the location you want to analyze. 
            When you click "Fetch Data", we'll automatically download electricity prices, CO₂ emission data, 
            and weather information. This data forms the environment for your device simulations.
        </p>
    """, unsafe_allow_html=True)

    today = date.today()
    min_date = date(2025, 10, 1)

    # 1) Choose period and selected date (Card Layout)
    with st.container(border=True):
        st.markdown("#### Choose period and selected date")
        c1, c2, c3 = st.columns(3)
        with c1:
            if "period_start" not in st.session_state: st.session_state["period_start"] = today - timedelta(days=15)
            period_start = st.date_input("Period start", value=st.session_state["period_start"], min_value=min_date, max_value=today+timedelta(days=1), key="p_start_input")
        with c2:
            if "period_end" not in st.session_state: st.session_state["period_end"] = today
            period_end = st.date_input("Period end", value=st.session_state["period_end"], min_value=min_date, max_value=today+timedelta(days=1), key="p_end_input")
        with c3:
            selected_day = st.date_input("Selected day for analysis", value=st.session_state["day"], key="day")

    st.markdown("<br>", unsafe_allow_html=True)

    # 2) Location Selection (Card Layout)
    with st.container(border=True):
        st.markdown("#### Location Selection")
        col_loc_in, col_loc_map = st.columns([1, 1])
        
        preset_locations = {
            "Aalborg (DK1)":    {"lat": 57.0488, "lon": 9.9217,  "area": "DK1"},
            "Aarhus (DK1)":     {"lat": 56.1629, "lon": 10.2039, "area": "DK1"},
            "Odense (DK1)":     {"lat": 55.4038, "lon": 10.4024, "area": "DK1"},
            "Copenhagen (DK2)": {"lat": 55.6761, "lon": 12.5683, "area": "DK2"},
        }

        if "city_choice" not in st.session_state and "user_profile" in st.session_state:
            prof_loc = st.session_state["user_profile"].get("location")
            if prof_loc:
                # Find the first preset that contains the user's city name
                match = next((k for k in preset_locations.keys() if prof_loc in k), None)
                if match:
                    st.session_state["city_choice"] = match

        with col_loc_in:
            choice = st.selectbox("Choose city", list(preset_locations.keys()), key="city_choice")
            preset = preset_locations[choice]
            st.session_state["geo_lat"] = preset["lat"]
            st.session_state["geo_lon"] = preset["lon"]
            st.session_state["price_area"] = preset["area"]
            
            st.markdown("<br><br>", unsafe_allow_html=True)
            fetch_btn = st.button("Fetch Data", width="stretch", type="primary")

        with col_loc_map:
            df_map = pd.DataFrame({"lat": [st.session_state["geo_lat"]], "lon": [st.session_state["geo_lon"]]})
            st.map(df_map, zoom=6)

    # --- Data Fetching Logic ---
    if period_end < period_start:
        st.error("End date must be on or after start date.")
    elif fetch_btn:
        if (period_end - period_start).days > 180:
            st.warning("Please limit synchronization to 180 days.")
        else:
            with st.spinner("Synchronizing with remote data sources..."):
                idx = minute_index(period_start, period_end)
                price_plot, note_price = daily_price_dual(idx, period_start, period_end, st.session_state["price_area"])
                co2, note_co2 = daily_co2_with_note(idx, period_start, period_end, st.session_state["price_area"])
                weather_hr = fetch_weather_open_meteo(st.session_state["geo_lat"], st.session_state["geo_lon"], start_date=period_start, end_date=period_end, tz="Europe/Copenhagen")
                tout_minute, note_temp = daily_temperature_with_note(idx, weather_hr)

                st.session_state["price_daily"] = price_plot
                st.session_state["co2_daily"] = co2
                st.session_state["temp_daily"] = tout_minute
                st.session_state["weather_hr"] = weather_hr
                st.session_state["note_price"] = note_price
                st.session_state["note_co2"] = note_co2
                st.session_state["note_temp"] = note_temp
                st.success("Data synchronization complete.")

    st.markdown("---")

    # --- Visualization Section ---
    price_series = st.session_state.get("price_daily",  pd.Series(dtype=float))
    co2_series   = st.session_state.get("co2_daily",    pd.Series(dtype=float))
    temp_series  = st.session_state.get("temp_daily",   pd.Series(dtype=float))

    if not price_series.empty and not co2_series.empty and not temp_series.empty:
        st.markdown("### Historical Trends")
        
        with st.container(border=True):
            st.markdown("#### Electricity Market Prices")
            fig_price = plot_period_bar(price_series, selected_day=selected_day, title="", ytitle="DKK/kWh")
            st.plotly_chart(fig_price, width="stretch")
            if st.session_state.get("note_price"): st.caption(st.session_state["note_price"])

        with st.container(border=True):
            st.markdown("#### Carbon Intensity")
            fig_co2 = plot_period_bar(co2_series, selected_day=selected_day, title="", ytitle="gCO₂/kWh")
            st.plotly_chart(fig_co2, width="stretch")
            if st.session_state.get("note_co2"): st.caption(st.session_state["note_co2"])

        with st.container(border=True):
            st.markdown("#### Ambient Temperature")
            fig_temp = plot_period_minute(temp_series, selected_day=selected_day, title="", ytitle="°C")
            st.plotly_chart(fig_temp, width="stretch")
            if st.session_state.get("note_temp"): st.caption(st.session_state["note_temp"])

    st.markdown("<div style='height: 2rem;'></div>", unsafe_allow_html=True)
    
    _, col_btn, _ = st.columns([1.2, 1, 1.2])
    with col_btn:
        if st.button("Go to Step 2: Devices & Layout ➔", key="next_to_p2"):
            st.session_state["active_page"] = "Devices & Layout"
            st.rerun()
