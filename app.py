
#%%
from __future__ import annotations
import streamlit as st
from datetime import date
from subpages.p0_front import render_front_page
from subpages.p1_scenario import render_scenario_page
from subpages.p2_devices import render_devices_page_house
from subpages.p3_ems import render_analysis_page

st.set_page_config(page_title="Daily EMS Sandbox", layout="wide")

from utils.ui_styler import load_custom_css
load_custom_css()

if "day" not in st.session_state:
    st.session_state["day"] = date.today()

# Check user profile before showing anything else
render_front_page()

# --- Navigation Logic ---
def render_header():
    """Renders the top navigation bar with text-based links."""
    
    col_logo, col_nav, col_spacer, col_prof = st.columns([0.18, 0.48, 0.28, 0.06])
    
    with col_logo:
        st.markdown("""
            <div style='display: flex; align-items: center; gap: 0.8rem;'>
                <span style='font-size: 1.6rem;'>⚡</span>
                <span style='font-weight: 600; color: #1E293B; font-size: 1rem; letter-spacing: 0.03em;'>DAILY EMS SANDBOX</span>
                <div style='height: 25px; width: 1px; background: #d1d5db; margin-left: 0.3rem;'></div>
            </div>
        """, unsafe_allow_html=True)
    
    with col_nav:
        if "active_page" not in st.session_state:
            st.session_state["active_page"] = "Market & Weather"
        if "prev_active_page" not in st.session_state:
            st.session_state["prev_active_page"] = st.session_state["active_page"]
        
        # Detect if a button changed the page
        if st.session_state["active_page"] != st.session_state["prev_active_page"]:
            st.session_state["page_nav"] = st.session_state["active_page"]
            st.session_state["prev_active_page"] = st.session_state["active_page"]

        page = st.radio(
            "nav",
            ["Market & Weather", "Devices & Layout", "Analysis"],
            horizontal=True,
            label_visibility="collapsed",
            key="page_nav"
        )
        
        if page != st.session_state.get("active_page"):
            st.session_state["active_page"] = page
            st.session_state["prev_active_page"] = page
            st.rerun()

    with col_prof:
        with st.popover("👤", width="content"):
            profile = st.session_state.get("user_profile", {})
            st.markdown(f"""
                <div style='border-bottom: 1px solid #f1f5f9; padding-bottom: 8px; margin-bottom: 12px;'>
                    <p style='margin:0; font-size: 0.8rem; color: #64748B;'>Logged in as</p>
                    <p style='margin:0; font-weight: bold;'>{profile.get('occupation', 'User')}</p>
                    <p style='margin:0; font-size: 0.8rem; color: #64748B;'>{profile.get('location', 'Global')}</p>
                </div>
            """, unsafe_allow_html=True)
            
            if st.button("📝 Edit Profile", width="stretch", key="edit_profile"):
                st.session_state["user_profile_confirmed"] = False
                st.rerun()
            
            if st.button("🚪 Logout", width="stretch", type="primary", key="logout"):
                # Clear all session state for fresh start
                keys_to_clear = [
                    "user_profile_confirmed", "user_profile", "admin_active",
                    "price_daily", "co2_daily", "temp_daily", "weather_hr",
                    "note_price", "note_co2", "note_temp", "device_configs",
                    "device_selection", "active_page", "page_nav"
                ]
                for key in keys_to_clear:
                    if key in st.session_state:
                        del st.session_state[key]
                st.rerun()
    
    # Add bottom border
    st.markdown("<hr style='margin: 1rem 0 2rem 0; border: none; border-top: 1px solid #e5e7eb;'>", unsafe_allow_html=True)

# ---------------------------------------------------------
# Main App Flow
# ---------------------------------------------------------

# Initial states for house info if not exists
if "house_info" not in st.session_state:
    st.session_state["house_info"] = {
        "location": "Aalborg",
        "size": "Medium house",
        "insulation": "Average",
        "residents": 2,
    }

# Check user profile before showing anything else
render_front_page()

# Header / Navigation
render_header()

# Page Routing
page = st.session_state.get("active_page", "Market & Weather")
if "Market" in page:
    render_scenario_page()
elif "Devices" in page:
    render_devices_page_house()
elif "Analysis" in page:
    render_analysis_page()
