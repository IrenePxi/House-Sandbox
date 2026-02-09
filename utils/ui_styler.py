import streamlit as st

def load_custom_css():
    """
    Injects professional CSS to make Streamlit look like a high-end SaaS product.
    """
    st.markdown("""
    <style>
        /* Hide Streamlit Branding */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        
        /* Hide Sidebar */
    [data-testid="stSidebar"] {
        display: none;
    }


    /* Style radio buttons as text links for navigation (TARGETED by Column) */
    /* We target the radio button that is inside the header's navigation column. */
    
    /* 1. HIDE THE RADIO INPUT AND CIRCLE COMPLETELY */
    /* This targets the outer div of the radio option and hides the first child (the circle) */
    div[row-widget="radio"] div[role="radiogroup"] > label > div:first-child {
        display: none !important;
    }
    
    /* 2. LAYOUT THE TEXT LINKS HORIZONTALLY */
    div[row-widget="radio"] div[role="radiogroup"] {
        gap: 2rem !important;
        display: flex !important;
        flex-direction: row !important;
        align-items: center !important;
        margin-top: 0.5rem !important;
    }

    /* 3. STYLE THE TEXT (Normal State) */
    div[row-widget="radio"] div[role="radiogroup"] label {
        background: transparent !important;
        border: none !important;
        padding: 0.2rem 0 !important;
        cursor: pointer !important;
        margin: 0 !important;
    }
    
    div[row-widget="radio"] div[role="radiogroup"] label div[data-testid="stMarkdownContainer"] p {
        color: #475569 !important; /* Slate-600 */
        font-size: 1rem !important;
        font-weight: 500 !important;
        margin: 0 !important;
        border-bottom: 2px solid transparent !important; /* Reserve space for underline */
        transition: all 0.2s ease !important;
    }

    /* 4. HOVER STATE (Underline) */
    div[row-widget="radio"] div[role="radiogroup"] label:hover div[data-testid="stMarkdownContainer"] p {
        color: #2563EB !important; /* Blue-600 */
        border-bottom: 2px solid #2563EB !important;
    }

    /* 5. ACTIVE STATE (Bold + Underline) */
    div[row-widget="radio"] div[role="radiogroup"] label[data-checked="true"] div[data-testid="stMarkdownContainer"] p {
        color: #0F172A !important; /* Slate-900 */
        font-weight: 700 !important;
        border-bottom: 2px solid #2563EB !important;
    }


    /* Top Navigation Styling */
    .stButton > button {
        border-radius: 8px;
    }
    
    /* Highlight active tab - custom class approach via markdown if needed, 
       but we can use standard button types too. */
    div[data-testid="stHorizontalBlock"] {
        align-items: center;
    }
    
    /* Tighten Layout */
        .block-container {
            padding-top: 1rem;
            padding-bottom: 2rem;
            padding-left: 3rem;
            padding-right: 3rem;
        }
        
        /* Modern Cards */
        .stCard {
            border: 1px solid #E2E8F0;
            border-radius: 8px;
            padding: 1.5rem;
            background-color: #FFFFFF;
            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.06);
            margin-bottom: 1rem;
        }
        
        /* Sharp Metrics */
        [data-testid="stMetricLabel"] {
            font-size: 0.8rem !important;
            text-transform: uppercase !important;
            color: #64748B !important;
            font-weight: 600 !important;
        }
        [data-testid="stMetricValue"] {
            font-size: 1.8rem !important;
            font-weight: 700 !important;
            color: #1E293B !important;
        }
        
        /* Custom sidebar styling */
        section[data-testid="stSidebar"] {
            background-color: #F8FAFC;
            border-right: 1px solid #E2E8F0;
        }
        
        /* Typography */
        h1, h2, h3 {
            color: #0F172A;
            font-weight: 700 !important;
        }

        /* Bottom Navigation Styling */
        .bottom-nav-container {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            margin-top: 4rem;
            margin-bottom: 2rem;
            width: 100%;
        }
        
        .bottom-nav-line {
            width: 100%;
            max-width: 500px;
            height: 1px;
            background-color: #E2E8F0;
            margin-bottom: 1.2rem;
            position: relative;
        }
        
        .bottom-nav-progress {
            position: absolute;
            left: 0;
            top: -0.5px;
            height: 2px;
            background-color: #1E293B;
            transition: width 0.5s ease;
        }

        .bottom-nav-text {
            color: #64748B !important;
            font-size: 0.85rem !important;
            margin-bottom: 2.5rem !important;
            font-weight: 500 !important;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        /* Target buttons at the bottom - we will use a specific container width in python */
        .stButton > button {
            transition: all 0.2s ease !important;
        }

        /* Specifically style the nav buttons by their label content */
        button[aria-label*="Step"], button[aria-label*="Back to Start"] {
            background-color: #FFFFFF !important;
            color: #2563EB !important;
            border: 2px solid #2563EB !important;
            padding: 0.6rem 2.5rem !important;
            font-weight: 600 !important;
            border-radius: 6px !important;
            width: auto !important;
            min-width: 280px !important;
        }

        /* Force centering of the container that holds these buttons */
        div[data-testid="stButton"]:has(button[aria-label*="Step"]), 
        div[data-testid="stButton"]:has(button[aria-label*="Back to Start"]) {
            display: flex !important;
            justify-content: center !important;
            width: 100% !important;
        }

        button[aria-label*="Step"]:hover, button[aria-label*="Back to Start"]:hover {
            background-color: #EFF6FF !important;
            border-color: #1D4ED8 !important;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1) !important;
        }
    </style>
    """, unsafe_allow_html=True)
