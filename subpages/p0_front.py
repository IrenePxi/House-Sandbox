
import streamlit as st
import pandas as pd
from datetime import datetime
from pathlib import Path
import uuid

# --- Constants ---
ADMIN_PASSWORD = "FCCOGEN"
LOG_PATH = Path("usage_log.csv")

from utils.ui_styler import load_custom_css

import plotly.graph_objects as go

def log_event(event_name: str, profile: dict = None) -> str:
    """Logs an event (start or page_visit) to the CSV file."""
    clicked_ts = datetime.now().isoformat(timespec="seconds")
    is_new = not LOG_PATH.exists()
    
    if profile is None:
        profile = st.session_state.get("user_profile", {})

    df_row = pd.DataFrame([{
        "timestamp": clicked_ts,
        "event": event_name,
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

def log_user_profile_to_csv(profile: dict) -> str:
    # Legacy wrapper
    return log_event("start", profile)

def render_admin_stats():
    """Renders the high-end SaaS Admin Dashboard."""
    # Hide sidebar for a clean full-screen dashboard feel
    st.markdown("<style>section[data-testid='stSidebar'] { display: none !important; }</style>", unsafe_allow_html=True)
    
    # Header Section
    col_h1, col_h2 = st.columns([0.8, 0.2])
    with col_h1:
        st.markdown("""
            <h1 style='color: #2563EB; margin-bottom: 0;'>🛍️ Admin – App Usage Statistics</h1>
            <p style='color: #64748B; font-size: 1.1rem;'>Monitor application usage and user analytics</p>
        """, unsafe_allow_html=True)
    with col_h2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("⬅ Return to User Mode", type="primary", use_container_width=True):
            st.session_state["admin_active"] = False
            st.rerun()

    if not LOG_PATH.exists():
        st.info("No usage data recorded yet.")
        return

    df = pd.read_csv(LOG_PATH, parse_dates=["timestamp"])
    
    # 1. Metric Cards
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        with st.container(border=True):
            st.markdown("<div style='text-align: center;'><h3 style='margin:0;'>🖱️</h3><h1 style='margin:0;'>{}</h1><p style='color: #64748B;'>Total Clicks</p></div>".format(len(df)), unsafe_allow_html=True)
    with m2:
        with st.container(border=True):
            st.markdown("<div style='text-align: center;'><h3 style='margin:0;'>👥</h3><h1 style='margin:0;'>{}</h1><p style='color: #64748B;'>Unique Sessions</p></div>".format(df["session_id"].nunique()), unsafe_allow_html=True)
    with m3:
        with st.container(border=True):
            # Calculate Completed Flows (Page 3 reached)
            comp_count = 0
            if "event" in df.columns:
                comp_count = df[df["event"] == "analysis_reached"]["session_id"].nunique()
            st.markdown("<div style='text-align: center;'><h3 style='margin:0;'>🎯</h3><h1 style='margin:0;'>{}</h1><p style='color: #64748B;'>Completed Flows</p></div>".format(comp_count), unsafe_allow_html=True)
    with m4:
        with st.container(border=True):
            st.markdown("<div style='text-align: center;'><h3 style='margin:0;'>📍</h3><h1 style='margin:0;'>{}</h1><p style='color: #64748B;'>Unique Locations</p></div>".format(df["location"].nunique()), unsafe_allow_html=True)
    with m5:
        with st.container(border=True):
            st.markdown("<div style='text-align: center;'><h3 style='margin:0;'>🏢</h3><h1 style='margin:0;'>{}</h1><p style='color: #64748B;'>Occupation Types</p></div>".format(df["occupation"].nunique()), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 2. Charts Row
    col_funnel, col_occ = st.columns([0.4, 0.6])
    
    with col_funnel:
        with st.container(border=True):
            st.markdown("#### � Completion Rate")
            if "event" in df.columns:
                # Group by session and check if any record for that session reached analysis
                df["reached_p3"] = (df["event"] == "analysis_reached")
                completion_by_session = df.groupby("session_id")["reached_p3"].any()
                comp_counts = completion_by_session.value_counts()
                
                labels = ["Completed (Page 3)", "Incomplete"]
                # Ensure we handle cases where counts might be zero
                values = [comp_counts.get(True, 0), comp_counts.get(False, 0)]
                
                fig_fn = go.Figure(data=[go.Pie(
                    labels=labels, 
                    values=values,
                    hole=0.5,
                    marker=dict(colors=['#2563EB', '#E2E8F0']) # Premium Blue and Soft Gray
                )])
                fig_fn.update_layout(
                    height=350, margin=dict(l=10, r=10, t=10, b=10),
                    legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
                    template="plotly_white"
                )
                st.plotly_chart(fig_fn, use_container_width=True)
            else:
                st.info("Tracking data not yet available.")

    with col_occ:
        with st.container(border=True):
            st.markdown("#### 📊 Usage by Occupation")
            occ_counts = df["occupation"].value_counts().reset_index()
            occ_counts.columns = ["occupation", "count"]
            
            fig_occ = go.Figure(data=[go.Bar(
                x=occ_counts["occupation"], 
                y=occ_counts["count"],
                marker_color=['#2563EB', '#10B981', '#F59E0B', '#6366F1'],
                text=occ_counts["count"],
                textposition='auto',
            )])
            fig_occ.update_layout(
                height=350, margin=dict(l=20, r=20, t=20, b=20),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title="Occupation", yaxis_title="Number of Users",
                template="plotly_white"
            )
            st.plotly_chart(fig_occ, use_container_width=True)

    # 3. Time and Location Row
    col_time, col_loc = st.columns(2)

    with col_time:
        with st.container(border=True):
            st.markdown("#### 📈 Usage Over Time")
            df["date"] = df["timestamp"].dt.date
            time_counts = df.groupby("date").size().reset_index()
            time_counts.columns = ["date", "count"]
            
            fig_time = go.Figure(data=[go.Scatter(
                x=time_counts["date"], 
                y=time_counts["count"],
                mode='lines+markers',
                line=dict(color='#2563EB', width=3),
                fill='tozeroy',
                fillcolor='rgba(37, 99, 235, 0.1)'
            )])
            fig_time.update_layout(
                height=350, margin=dict(l=20, r=20, t=20, b=20),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title="Date", yaxis_title="Number of Clicks",
                template="plotly_white"
            )
            st.plotly_chart(fig_time, use_container_width=True)

    with col_loc:
        with st.container(border=True):
            st.markdown("#### 🧭 Location Distribution")
            loc_counts = df["location"].value_counts().reset_index()
            loc_counts.columns = ["location", "count"]
            
            fig_loc = go.Figure(data=[go.Pie(
                labels=loc_counts["location"], 
                values=loc_counts["count"],
                hole=0.5,
                marker=dict(colors=['#2563EB', '#10B981', '#F59E0B', '#6366F1', '#EC4899'])
            )])
            fig_loc.update_layout(
                height=350, margin=dict(l=20, r=20, t=40, b=20),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
                template="plotly_white"
            )
            st.plotly_chart(fig_loc, use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 4. Raw Data
    with st.container(border=True):
        c_table_h1, c_table_h2 = st.columns([0.8, 0.2])
        with c_table_h1:
            st.markdown("#### 📋 Raw Usage Log")
        with c_table_h2:
            st.download_button(
                "📥 Download CSV",
                df.to_csv(index=False).encode("utf-8"),
                file_name="usage_log.csv",
                mime="text/csv",
                use_container_width=True
            )
        
        # Display polished dataframe
        st.dataframe(
            df[["timestamp", "occupation", "location", "session_id"]],
            use_container_width=True,
            hide_index=True
        )

def ensure_user_profile():
    load_custom_css()
    
    # robust session id
    st.session_state.setdefault("session_id", str(uuid.uuid4()))

    # Check for Admin Override
    if st.session_state.get("admin_active"):
        render_admin_stats()
        st.stop()

    # If already confirmed, do nothing
    if st.session_state.get("user_profile_confirmed"):
        return

    # Center the entire login view
    _, col_card, _ = st.columns([1, 2, 1])

    with col_card:
        # Lightning Icon and Title
        st.markdown("""
            <div style="text-align: center; padding-top: 2rem;">
                <h1 style="font-size: 5rem; color: #2563EB; margin-bottom: 0;">⚡</h1>
                <h1 style="font-size: 2.8rem; color: #1E293B; margin-top: 0.5rem; margin-bottom: 0.5rem;">Daily EMS Sandbox</h1>
                <p style="font-size: 1.1rem; color: #64748B; margin-bottom: 2rem;">Before we start, tell us a bit about yourself</p>
            </div>
        """, unsafe_allow_html=True)

        with st.container(border=True):
            st.info("""
            **What does this tool do?**  
            The Daily EMS Sandbox lets you explore how a household energy system behaves over a single day — and how much smarter control can reduce cost and emissions.

            **In three simple steps, you can:**
            1. **Set up your system** – choose household devices such as PV, battery, heat pumps, and fuel cells  
            2. **Understand daily energy use** – see where electricity comes from and how it is consumed  
            3. **Run optimization** – compare a baseline operation with an optimized energy management strategy

            The goal is not perfect prediction, but **transparent insight**:  
            *Where is energy used? What can be shifted? And how much can be saved?*
            """)


            st.markdown("<br>", unsafe_allow_html=True)

            occupation = st.selectbox(
                "Your current role",
                ["Student", "Academic employee", "Industry", "Others"],
                index=None,
                placeholder="Choose your role...",
                help="Used for anonymous statistics.",
            )
            
            location = st.selectbox(
                "Where do you live?",
                ["Aalborg", "Aarhus", "Odense", "Copenhagen", "Others"],
                index=None,
                placeholder="Choose your location...",
                help="Select your city or choose Others."
            )

            st.markdown("<br>", unsafe_allow_html=True)
            
            ready = bool(occupation) and bool(location)
            clicked = st.button("▶ Start using the app", disabled=not ready, type="primary", use_container_width=True)

            if clicked and ready:
                profile = {"occupation": occupation, "location": location}
                st.session_state["user_profile"] = profile
                st.session_state["user_profile_confirmed"] = True

                clicked_ts = log_user_profile_to_csv(profile)
                st.session_state["app_start_timestamp"] = clicked_ts

                st.rerun()

        # Discreet Admin Access
        st.markdown("<br>", unsafe_allow_html=True)
        with st.expander("🛡 Admin (internal)", expanded=False):
            admin_mode = st.text_input("Admin password", type="password")
            if admin_mode == ADMIN_PASSWORD:
                st.session_state["admin_active"] = True
                st.rerun()

    st.stop()

def render_front_page():
    """Calculates/Checks user profile. If not confirmed, stops execution."""
    ensure_user_profile()
