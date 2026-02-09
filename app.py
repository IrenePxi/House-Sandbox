
#%%
from __future__ import annotations
import streamlit as st
from datetime import date
from subpages.p0_front import render_front_page
from subpages.p1_scenario import render_scenario_page
from subpages.p2_devices import render_devices_page_house
from subpages.p3_ems import render_analysis_page

st.set_page_config(page_title="Daily EMS Sandbox", layout="wide")

<<<<<<< HEAD
from utils.ui_styler import load_custom_css
load_custom_css()
=======
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "local_debug")
LOG_PATH = Path("usage_log.csv")

def log_user_profile_to_csv(profile: dict) -> str:
    clicked_ts = datetime.now().isoformat(timespec="seconds")
    is_new = not LOG_PATH.exists()

    df_row = pd.DataFrame([{
        "timestamp": clicked_ts,
        "occupation": profile.get("occupation", ""),
        "location": profile.get("location", ""),
        "session_id": st.session_state.get("session_id", ""),
    }])

    df_row.to_csv(
        LOG_PATH,
        index=False,
        mode="w" if is_new else "a",
        header=is_new,
    )
    return clicked_ts

def render_admin_stats():
    st.title("🔐 Admin – App usage statistics")

    if not LOG_PATH.exists():
        st.info("No usage data recorded yet.")
        return

    df = pd.read_csv(LOG_PATH, parse_dates=["timestamp"])

    st.subheader("Summary")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total clicks", len(df))
    c2.metric("Unique sessions", df["session_id"].nunique())
    c3.metric("Unique locations", df["location"].nunique())

    st.subheader("Usage by occupation")
    st.bar_chart(df["occupation"].value_counts())

    st.subheader("Usage over time")
    df["date"] = df["timestamp"].dt.date
    st.line_chart(df.groupby("date").size())

    st.subheader("Raw usage log")
    st.dataframe(df, use_container_width=True)

    st.download_button(
        "📥 Download usage_log.csv",
        df.to_csv(index=False).encode("utf-8"),
        file_name="usage_log.csv",
        mime="text/csv",
    )

def ensure_user_profile():
    # robust session id
    st.session_state.setdefault("session_id", str(uuid.uuid4()))

    # If already confirmed, do nothing
    if st.session_state.get("user_profile_confirmed"):
        return

    st.title("Daily EMS Sandbox")
    st.subheader("Before we start, tell us a bit about yourself 👇")

    

    occupation = st.radio(
        "Your current role",
        [
            "Bachelor student","Master student","PhD student","Research assistant",
            "Postdoc","Assistant Professor","Associate Professor","Professor",
            "Industry","Others",
        ],
        index=None,
        help="Used for anonymous statistics.",
    )
    location = st.text_input(
        "Where do you live?",
        placeholder="City, Country (e.g. Aalborg, Denmark)",
    )

    ready = bool(occupation) and bool(location.strip())
    clicked = st.button("Start using the app ▶️", disabled=not ready)

    # ---- Admin (front page only) ----
    with st.expander("Admin (internal)", expanded=False):
        admin_mode = st.text_input("Admin password", type="password")
        if admin_mode == ADMIN_PASSWORD:
            render_admin_stats()
            st.stop()

    if clicked and ready:
        profile = {"occupation": occupation, "location": location.strip()}
        st.session_state["user_profile"] = profile
        st.session_state["user_profile_confirmed"] = True

        clicked_ts = log_user_profile_to_csv(profile)
        st.session_state["app_start_timestamp"] = clicked_ts

        st.rerun()

    st.stop()

ensure_user_profile()


#%% helper for page 1
# -------- EnergiDataService endpoints --------
EDS_PRICE_URL_OLD = "https://api.energidataservice.dk/dataset/Elspotprices"
EDS_PRICE_URL_NEW = "https://api.energidataservice.dk/dataset/DayAheadPrices"
EDS_CO2_HIST_URL  = "https://api.energidataservice.dk/dataset/CO2Emis"
EDS_CO2_PROG_URL  = "https://api.energidataservice.dk/dataset/CO2EmisProg"
TZ_DK = "Europe/Copenhagen"
>>>>>>> d90081dd697e27996f67dd1192011d6df69d3b7f

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
        # Use radio for navigation but style it to look like text links
        if "active_page" not in st.session_state:
            st.session_state["active_page"] = "Market & Weather"
        
        page = st.radio(
            "nav",
            ["Market & Weather", "Devices & Layout", "Analysis"],
            index=["Market & Weather", "Devices & Layout", "Analysis"].index(st.session_state.get("active_page", "Market & Weather")),
            horizontal=True,
            label_visibility="collapsed",
            key="page_nav"
        )
        
        if page != st.session_state.get("active_page"):
            st.session_state["active_page"] = page
            st.rerun()

    with col_prof:
        with st.popover("👤", use_container_width=False):
            profile = st.session_state.get("user_profile", {})
            st.markdown(f"""
                <div style='border-bottom: 1px solid #f1f5f9; padding-bottom: 8px; margin-bottom: 12px;'>
                    <p style='margin:0; font-size: 0.8rem; color: #64748B;'>Logged in as</p>
                    <p style='margin:0; font-weight: bold;'>{profile.get('occupation', 'User')}</p>
                    <p style='margin:0; font-size: 0.8rem; color: #64748B;'>{profile.get('location', 'Global')}</p>
                </div>
            """, unsafe_allow_html=True)
            
            if st.button("📝 Edit Profile", use_container_width=True, key="edit_profile"):
                st.session_state["user_profile_confirmed"] = False
                st.rerun()
            
            if st.button("🚪 Logout", use_container_width=True, type="primary", key="logout"):
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
