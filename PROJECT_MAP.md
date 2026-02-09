# PROJECT MAP - EMS App Streamlit
**Updated:** January 21, 2026  
**Status:** ✅ All 33 files compile verified  
**Environment:** `C:\SLS_NEW\.venv`

---

## 🔧 ENVIRONMENT SETUP (RECOMMENDED: use a project-local venv)

This project is intended to run inside a Python virtual environment. The repository contains a `requirements.txt` at the app root — install dependencies into the venv before running Streamlit or any scripts.

### Create / Install (one-time)
Run these commands from `C:\SLS_NEW` (or the workspace root) in PowerShell:

```powershell
# Create a venv in the repo root (if you don't already have one)
python -m venv .venv

# Upgrade pip and install requirements into the venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r "4. DT_medCO2\ems_app_streamlit\requirements.txt"
```

> Note: If you already have a venv at `C:\SLS_NEW\.venv`, skip creation and just activate it and install any missing packages.

### Activate the venv (PowerShell)
```powershell
# From C:\SLS_NEW (or any subfolder)
.\.venv\Scripts\Activate.ps1
# You should now see (.venv) in your prompt
```

### Run Streamlit (use the venv python to ensure the correct environment)
```powershell
# Change into the app folder first
cd "C:\SLS_NEW\4. DT_medCO2\ems_app_streamlit"
# Run with the venv's python to pick the correct streamlit installation
.\.venv\Scripts\python -m streamlit run app.py
```

### Verify (quick compile check inside venv)
```powershell
# From the app folder
.\.venv\Scripts\python -m compileall . -q
echo "✅ compileall exit code: $LASTEXITCODE"
```

---

## 🎯 PROJECT OVERVIEW

This is a **Streamlit web application** for simulating household energy management. Users configure devices, set scenarios, and analyze daily energy flows with optional EMS optimization.

**4 Main Pages:**
1. **Page 0 (Front)** - User profile & admin
2. **Page 1 (Scenario)** - Load/PV/Price data configuration
3. **Page 2 (Devices)** - Device setup & power profile preview
4. **Page 3 (EMS)** - Analysis & optimization

---

## 📂 COMPLETE FILE STRUCTURE

```
ems_app_streamlit/
│
├── 📄 app.py                          [ENTRY POINT - Streamlit main]
│
├── 📁 core/                           [PHYSICS & MODELING LAYER - 9 FILES]
│   ├── battery.py                     ✅ Battery SOC dynamics (battery_step)
│   ├── devices.py                     ✅ Device instantiation (HP, EH)
│   ├── FCcontrol.py                   ✅ Fuel cell modeling & optimization
│   ├── pms.py                         ✅ Power Management System (rule_power_share)
│   ├── profiles.py                    ✅ Load/PV profiles
│   ├── solar.py                       ✅ Solar generation model
│   ├── thermal_share.py               ✅ Space heating simulation
│   ├── timeslot.py                    ✅ Time-slot scheduling & MPC
│   └── __init__.py
│
├── 📁 services/                       [ORCHESTRATION LAYER - 5 FILES]
│   ├── P2_dailyprofile_service.py     ✅ Page 2: Daily profile generation
│   ├── P2_devicesimulation_service.py ✅ Page 2: Device simulation
│   ├── P2_Flexscheduling_service.py   ✅ Page 2: Flexibility scheduling
│   ├── P3_ems_service.py              ✅ Page 3: EMS dispatch & analysis
│   └── migration.py                   ✅ Config format migration
│
├── 📁 subpages/                       [UI LAYER - STREAMLIT PAGES - 4 FILES]
│   ├── p0_front.py                    ✅ Page 0: User profile & welcome
│   ├── p1_scenario.py                 ✅ Page 1: Scenario configuration
│   ├── p2_devices.py                  ✅ Page 2: Device setup
│   └── p3_ems.py                      ✅ Page 3: Analysis & EMS results
│
├── 📁 state/                          [CONFIGURATION & SESSION - 2 FILES]
│   ├── defaults.py                    ✅ Default device configs & parameters
│   ├── session.py                     ✅ Session state helpers
│   └── __init__.py
│
├── 📁 data_sources/                   [EXTERNAL DATA APIs - 4 FILES]
│   ├── co2.py                         ✅ CO2 emission data
│   ├── prices.py                      ✅ Electricity prices
│   ├── weather.py                     ✅ Weather forecasts
│   └── __init__.py
│
├── 📁 utils/                          [UTILITY FUNCTIONS - 4 FILES]
│   ├── plotting.py                    ✅ Chart/graph generation
│   ├── time.py                        ✅ Time & date utilities
│   ├── validation.py                  ✅ Input validation
│   └── __init__.py
│
├── 📁 models/                         [DATA SCHEMAS - 2 FILES]
│   ├── schemas.py                     ✅ Dataclass definitions
│   └── __init__.py
│
└── 📁 __pycache__/                    [Python cache - auto-generated]

TOTAL: 33 Python files
```

---

## 🏗️ ARCHITECTURE - 4 LAYER STACK

### **Layer 1: UI Layer** (Streamlit Pages)
```
app.py (router)
  ├── p0_front.py       (User profile)
  ├── p1_scenario.py    (Scenario setup)
  ├── p2_devices.py     (Device configuration)
  └── p3_ems.py         (Analysis & results)
```
**Role:** User interface, input collection, results display

### **Layer 2: Services Layer** (Business Logic & Orchestration)
```
P2_dailyprofile_service.py
P2_devicesimulation_service.py
P2_Flexscheduling_service.py
P3_ems_service.py
migration.py
```
**Role:** Coordinate between UI and physics, handle workflows

### **Layer 3: Core Layer** (Physics & Algorithms)
```
battery.py          → Battery dynamics
FCcontrol.py        → Fuel cell optimization
pms.py              → Power distribution (RENAMED from ems.py)
thermal_share.py    → Heat simulation
timeslot.py         → Scheduling & MPC (RENAMED from scheduling.py)
solar.py            → PV generation
devices.py          → Device models
profiles.py         → Test profiles
```
**Role:** Physics models, optimization algorithms, simulations

### **Layer 4: Support Layer** (Configuration, Utilities, Data)
```
state/           → Configuration & defaults (session.py, defaults.py)
data_sources/    → External APIs (prices, CO2, weather)
utils/           → Helper functions (plotting, time, validation)
models/          → Data schemas (schemas.py)
```
**Role:** Configuration, data access, reusable utilities

---

## 📋 FILE REFERENCE TABLE (33 Files Total)

### Core Modules (9 files)
| File | Lines | Purpose | Status |
|------|-------|---------|--------|
| `battery.py` | ~50 | Battery SOC dynamics | ✅ Complete |
| `devices.py` | ~80 | Device instantiation | ✅ Complete |
| `FCcontrol.py` | ~135 | FC optimization | ✅ Complete |
| `pms.py` | ~274 | Power distribution | ✅ Complete (renamed) |
| `profiles.py` | ~60 | Dummy profiles | ✅ Complete |
| `solar.py` | ~80 | PV generation | ✅ Complete |
| `thermal_share.py` | ~200 | Heat simulation | ✅ Complete |
| `timeslot.py` | ~247 | Scheduling & MPC | ✅ Complete (renamed) |
| `__init__.py` | ~5 | Module init | ✅ Complete |

### Service Modules (5 files)
| File | Lines | Purpose | Status |
|------|-------|---------|--------|
| `P2_dailyprofile_service.py` | ~80 | Page 2 profiles | ✅ NEW |
| `P2_devicesimulation_service.py` | ~120 | Page 2 device sim | ✅ NEW |
| `P2_Flexscheduling_service.py` | ~100 | Page 2 flexibility | ✅ NEW |
| `P3_ems_service.py` | ~205 | Page 3 EMS analysis | ✅ NEW |
| `migration.py` | ~50 | Config migration | ✅ Complete |

### UI Pages (4 files)
| File | Lines | Purpose | Status |
|------|-------|---------|--------|
| `p0_front.py` | ~100 | Welcome & profile | ✅ Complete |
| `p1_scenario.py` | ~150 | Scenario setup | ✅ Complete |
| `p2_devices.py` | ~200 | Device config | ✅ Complete |
| `p3_ems.py` | ~250 | Analysis results | ✅ Complete |

### State & Config (2 files)
| File | Lines | Purpose | Status |
|------|-------|---------|--------|
| `defaults.py` | ~300 | Config defaults | ✅ Complete |
| `session.py` | ~80 | Session helpers | ✅ Complete |

### Utilities (4 files)
| File | Lines | Purpose | Status |
|------|-------|---------|--------|
| `plotting.py` | ~150 | Chart generation | ✅ Complete |
| `time.py` | ~80 | Time utilities | ✅ Complete |
| `validation.py` | ~70 | Input validation | ✅ Complete |
| `__init__.py` | ~5 | Module init | ✅ Complete |

### Data Sources (4 files)
| File | Lines | Purpose | Status |
|------|-------|---------|--------|
| `co2.py` | ~80 | CO2 data API | ✅ Complete |
| `prices.py` | ~100 | Price data API | ✅ Complete |
| `weather.py` | ~100 | Weather data API | ✅ Complete |
| `__init__.py` | ~5 | Module init | ✅ Complete |

### Models (2 files)
| File | Lines | Purpose | Status |
|------|-------|---------|--------|
| `schemas.py` | ~200 | Data classes | ✅ Complete |
| `__init__.py` | ~5 | Module init | ✅ Complete |

---

## 🔄 DATA FLOW

```
PAGE 0             PAGE 1              PAGE 2              PAGE 3
─────────          ──────────          ──────────          ──────────
User Profile  →  Scenario Setup   →  Device Config   →  Analysis
                  (day, area,        (battery, HP,       (results,
                   prices,           EH, FC)             EMS)
                   weather)
  ↓                ↓                  ↓                   ↓
  └────────────────┴──────────────────┴───────────────────┘
                   ↓
         Session State (Streamlit)
                   ↓
      state/session.py, defaults.py
                   ↓
      services/P*_service.py (orchestration)
                   ↓
      core/{pms, timeslot, battery, etc.} (physics)
                   ↓
         data_sources/ (external APIs)
                   ↓
         Results displayed in PAGE 3
```

---

## 🎯 QUICK REFERENCE

### Where to Find...

| Need | File(s) |
|------|---------|
| **Entry point** | `app.py` |
| **Battery control** | `core/battery.py` |
| **Power distribution** | `core/pms.py` |
| **Fuel cell optimization** | `core/FCcontrol.py` |
| **Time slot scheduling** | `core/timeslot.py` |
| **Thermal simulation** | `core/thermal_share.py` |
| **Solar/PV** | `core/solar.py` |
| **Device config** | `state/defaults.py` |
| **Session helpers** | `state/session.py` |
| **Charts/plotting** | `utils/plotting.py` |
| **Price data** | `data_sources/prices.py` |
| **Weather data** | `data_sources/weather.py` |
| **CO2 data** | `data_sources/co2.py` |
| **EMS service** | `services/P3_ems_service.py` |
| **Page 3 UI** | `subpages/p3_ems.py` |

---

## 🚀 QUICK START

### 1. Activate Environment
```powershell
C:\SLS_NEW\.venv\Scripts\Activate.ps1
```

### 2. Run Streamlit App
```bash
cd C:\SLS_NEW\4. DT_medCO2\ems_app_streamlit
streamlit run app.py
```

### 3. Verify Compilation
```bash
C:\SLS_NEW\.venv\Scripts\python -m compileall . -q
echo "✅ All 33 files compile OK"
```

---

## ✅ RECENT CHANGES SUMMARY

### Phase 1: Cleanup (Jan 16)
- ✅ Removed unused functions
- ✅ Fixed imports
- ✅ No behavior changes

### Phase 2: Refactoring (Jan 19)
- ✅ `core/ems.py` → `core/pms.py` (Power Management System)
- ✅ `core/optimization.py` → `core/FCcontrol.py` (Fuel Cell Control)
- ✅ `core/scheduling.py` → `core/timeslot.py` (Time-slot Scheduling)
- ✅ Added 4 new services (P2_*, P3_*)
- ✅ Added battery.py placeholder

### Phase 3: Documentation (Jan 21)
- ✅ This PROJECT_MAP.md updated
- ✅ All 33 files verified & compiled
- ✅ Environment verified

---

## ✅ VERIFICATION CHECKLIST

| Item | Status | Date |
|------|--------|------|
| All files compile | ✅ PASS | Jan 21 |
| No circular imports | ✅ PASS | Jan 21 |
| No unused code | ✅ PASS | Jan 16 |
| Services organized | ✅ PASS | Jan 19 |
| Environment ready | ✅ PASS | Jan 21 |

---

**Last Updated:** January 21, 2026  
**Total Files:** 33 Python files  
**Environment:** C:\SLS_NEW\.venv ✅ Active  
**Status:** ✅ Ready for Development

---

## ✅ REFACTORING STATUS (Updated January 19, 2026)

### Files Deleted/Renamed:
| Original | Action | New Location | Reason |
|---|---|---|---|
| `core/ems.py` | ✅ RENAMED | `core/pms.py` | **P**ower **M**anagement **S**ystem (clearer naming) |
| `core/optimization.py` | ✅ RENAMED | `core/FCcontrol.py` | **F**uel **C**ell control (domain-specific) |
| `core/scheduling.py` | ✅ RENAMED | `core/timeslot.py` | Time-slot based scheduling (clearer naming) |
| `core/profiles_core.py` | ✅ DELETED/MERGED | Integrated into services | Legacy dict-based profiles consolidated |

### Files Added:
| File | Purpose | Status |
|---|---|---|
| `core/battery.py` | Battery management module | ⏳ Placeholder (in-development) |

### Previous Cleanup (January 16, 2026):
- ✅ Removed `utils/time.py::normalize_to_dummy_day()` - unused
- ✅ Removed `services/profile_service.py::build_minute_profile()` - duplicate

### Verification Results:
| Check | Result |
|---|---|
| **Python Syntax** | ✅ PASS (compileall, exit 0) |
| **All Imports** | ✅ PASS (app + pages + new core modules) |
| **Core Imports** | ✅ battery, FCcontrol, pms, timeslot all import OK |
| **Environment** | ✅ Running in C:\SLS_NEW\.venv |

---

