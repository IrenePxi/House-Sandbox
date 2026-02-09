"""
Device catalogue and default configurations.
Moved from app.py lines 1131-1648 — NO LOGIC CHANGES.
"""
from __future__ import annotations
import streamlit as st
from datetime import time as _time, datetime, date, timedelta
import numpy as np

# -------------------------------------------------------------------
# Device catalogue (labels + icons)
# -------------------------------------------------------------------
DEVICE_CATEGORIES = {
    "elec_fixed": {
        "title": "1a. Household electrical – fixed",
        "help": "Devices that are hard to shift in time.",
        "devices": [
            ("lights",        "💡 Lights"),
            ("fridge",        "🧊 Refrigerator"),
            ("range_hood",    "🍳 Hood"),
            ("oven",          "🔥 Oven"),
            ("induction",     "🍳 Stove"),
            ("microwave",     "🎛️ Microwave"),
            ("tv",            "📺 TV"),
            ("router",        "🛜 Router"),
            ("pc_desktop",    "🖥️ Desktop PC"),
            ("game_console",  "🎮 Game console"),
            ("printer",       "🖨️ Printer"),
            ("standby",       "🔌 Standby loads"),

            # --- 3 custom "other fixed" slots ---
            ("other_fixed_1", "🧩 Other #1"),
            ("other_fixed_2", "🧩 Other #2"),
            ("other_fixed_3", "🧩 Other #3"),
        ],
    },
    "elec_flex": {
        "title": "1b. Household electrical – flexible",
        "help": "Shiftable devices that can be move to cheaper/cleaner hours.",
        "devices": [
            ("wm",           "🧺 Washing machine"),
            ("dw",           "🍽 Dishwasher"),
            ("dryer",        "👕 Dryer"),
            ("robot_vac",    "🧹 Robot vacuum"),
            ("workshop",     "🔧 Workshop tools"),

            # --- 3 custom "other flexible" slots ---
            ("other_flex_1", "🧩 Other #1"),
            ("other_flex_2", "🧩 Other #2"),
            ("other_flex_3", "🧩 Other #3"),
        ],
    },
    "thermal": {
        "title": "2. Household thermal",
        "help": "Space heating, domestic hot water and leisure thermal loads.",
        "devices": [
            ("space_heat", "🔥 Space heating"),
            ("dhw",        "💧 DHW system"),
            ("leisure",    "🧖 Leisure thermal loads"),
            ],
        },
    "outside": {
        "title": "3. Electrical Vehicles",
        "help": "Electric vehicles.",
        "devices": [
            ("ev11",        "🚗 EV charger"),
            ("ebike",       "🚲 E-bike charger"),
        ],
    },
    "gen_store": {
        "title": "4. Generation & storage",
        "help": "PV, batteries and other on-site generation/storage units. (Currently only PV is available. Other models are under development)",
        "devices": [
            ("pv",          "☀️ PV system"),
            ("battery", "🔋 Battery"),
            ("fuel_cell", "⛽ Fuel cell (CHP)")
        ],
    },
    
}

# optional: build a lookup from full_key -> pretty label (for legend)
DEVICE_LABEL_MAP = {
    f"{cat}:{dev}": label
    for cat, info in DEVICE_CATEGORIES.items()
    for dev, label in info["devices"]
}

def resolve_display_label(full_key: str, dev_type: str, cfg_current: dict) -> str:
    """
    Reuse the same logic as device checkboxes:
    - Use catalogue emoji + text
    - For 'other_*' devices, replace text with custom_name if set.
    """
    base_label = DEVICE_LABEL_MAP.get(full_key, dev_type)

    if dev_type.startswith("other"):
        custom_name = cfg_current.get("custom_name")
        if custom_name:
            # keep emoji from base_label if present
            if " " in base_label:
                emoji = base_label.split(" ", 1)[0]
            else:
                emoji = "🧩"
            return f"{emoji} {custom_name}"

    return base_label


# -------------------------------------------------------------------
# House Type Presets
# -------------------------------------------------------------------
HOUSE_TYPE_PRESETS = {
    "Small apartment": {
        "selected_devices": [
            "elec_fixed:lights", "elec_fixed:fridge", "elec_fixed:microwave", 
            "elec_fixed:tv", "elec_fixed:router", "elec_fixed:standby",
            "elec_flex:wm", "elec_flex:robot_vac",
            "thermal:space_heat", "thermal:dhw",
            "outside:ebike"
        ],
        "config_overrides": {
            "thermal:space_heat": {"has_hp": True, "has_eh": False, "has_wood": False, "hp_q_th_kw": 3.5},
            "thermal:dhw": {"dhw_mode": "Electric DHW tank", "volume_l": 100.0, "p_el_kw": 2.0},
            "outside:ebike": {"power_kw": 0.25},
            "gen_store:pv": {"n_panels": 8} 
        }
    },
    "Medium house": {
        "selected_devices": [
            "elec_fixed:lights", "elec_fixed:fridge", "elec_fixed:range_hood", 
            "elec_fixed:oven", "elec_fixed:induction", "elec_fixed:microwave", 
            "elec_fixed:tv", "elec_fixed:router", "elec_fixed:pc_desktop", "elec_fixed:standby",
            "elec_flex:wm", "elec_flex:dw", "elec_flex:dryer", "elec_flex:robot_vac",
            "thermal:space_heat", "thermal:dhw",
            "outside:ev11",
            "gen_store:pv"
        ],
        "config_overrides": {
            "thermal:space_heat": {"has_hp": True, "has_eh": True, "has_wood": False, "hp_q_th_kw": 6.0},
            "thermal:dhw": {"dhw_mode": "Heat pump DHW tank", "volume_l": 200.0, "p_el_kw": 1.5},
            "outside:ev11": {"power_kw": 11.0},
            "gen_store:pv": {"n_panels": 16}
        }
    },
    "Large house": {
        "selected_devices": [
            "elec_fixed:lights", "elec_fixed:fridge", "elec_fixed:range_hood", 
            "elec_fixed:oven", "elec_fixed:induction", "elec_fixed:microwave", 
            "elec_fixed:tv", "elec_fixed:router", "elec_fixed:pc_desktop", 
            "elec_fixed:game_console", "elec_fixed:printer", "elec_fixed:standby",
            "elec_flex:wm", "elec_flex:dw", "elec_flex:dryer", "elec_flex:robot_vac", "elec_flex:workshop",
            "thermal:space_heat", "thermal:dhw", "thermal:leisure",
            "outside:ev11", "outside:ebike",
            "gen_store:pv", "gen_store:battery"
        ],
        "config_overrides": {
            "thermal:space_heat": {"has_hp": True, "has_eh": True, "has_wood": True, "hp_q_th_kw": 10.0},
            "thermal:dhw": {"dhw_mode": "Heat pump DHW tank", "volume_l": 300.0, "p_el_kw": 2.0},
            "thermal:leisure": {"hot_tub_enabled": True, "pool_enabled": False},
            "outside:ev11": {"power_kw": 11.0},
            "outside:ebike": {"power_kw": 0.5},
            "gen_store:pv": {"n_panels": 30},
            "gen_store:battery": {"E_kWh": 10.0}
        }
    }
}


# -------------------------------------------------------------------
# Helper: default config per device type
# -------------------------------------------------------------------
def get_default_config(dev_type: str, category: str) -> dict:
    """Return default config (power, schedule, etc.) for a device."""
    base = dict(
        power_kw=0.5,
        start=_time(18, 0),
        duration_min=60,
    )

    # ====== read house_info and map to small/medium/large ==========
    hi = st.session_state.get("house_info", {
        "size": "Medium house",
        "insulation": "Average",
        "residents": 2,
    })
    size_str = (hi.get("size") or "Medium house").lower()

    if "small" in size_str:
        size = "small"
    elif "large" in size_str:
        size = "large"
    else:
        size = "medium"

    def _by_size(small: int, medium: int, large: int) -> int:
        if size == "small":
            return small
        if size == "large":
            return large
        return medium

    residents = int(hi.get("residents", 2))
    def _by_res(base: int, step: float = 0.5) -> int:
        """Simple linear scaling with residents: base + floor(step * (residents-1))"""
        return int(base + np.floor(step * (residents - 1)))

    # ======================================================
    # 1) FIXED ELECTRICAL (all 17 + other slots)
    # ======================================================
    if category == "elec_fixed":
        # All powers are per device (W).
        # num_devices scales with house size where it makes sense.
        defaults: dict[str, dict] = {
            "lights": {
                "num_devices": _by_size(8, 12, 18),
                "power_w": 8.0,  # per LED fixture
                "intervals": [
                    {"start": _time(6, 0),  "end": _time(8, 0)},   # morning
                    {"start": _time(17, 0), "end": _time(23, 0)},  # evening
                ],
            },
            "fridge": {
                "num_devices": 1,
                "power_w": 80.0,  # average over compressor cycling
                "intervals": [
                    {"start": _time(0, 0), "end": _time(23, 59)},
                ],
            },
            "freezer": {
                "num_devices": _by_size(1, 1, 2),
                "power_w": 90.0,
                "intervals": [
                    {"start": _time(0, 0), "end": _time(23, 59)},
                ],
            },
            "fridge_freezer": {
                "num_devices": _by_size(1, 1, 2),
                "power_w": 110.0,
                "intervals": [
                    {"start": _time(0, 0), "end": _time(23, 59)},
                ],
            },
            "range_hood": {
                "num_devices": 1,
                "power_w": 120.0,
                "duration_min": 40 + 5 * residents,
                "intervals": [{"start": _time(18, 0), "end": (datetime.combine(date.today(), _time(18, 0)) + timedelta(minutes=40 + 5 * residents)).time()}],
            },
            "oven": {
                "num_devices": _by_size(1, 1, 2),
                "power_w": 2000.0,
                "duration_min": 40 + 5 * residents,
                "intervals": [{"start": _time(18, 0), "end": (datetime.combine(date.today(), _time(18, 0)) + timedelta(minutes=40 + 5 * residents)).time()}],
            },
            "induction": {
                "num_devices": 1,
                "power_w": 2500.0,
                "duration_min": 30 + 5 * residents,
                "intervals": [{"start": _time(17, 30), "end": (datetime.combine(date.today(), _time(17, 30)) + timedelta(minutes=30 + 5 * residents)).time()}],
            },
            "microwave": {
                "num_devices": 1,
                "power_w": 1200.0,
                "intervals": [
                    {"start": _time(7,  0), "end": _time(7, 15)},
                    {"start": _time(12, 0), "end": _time(12, 15)},
                    {"start": _time(21, 0), "end": _time(21, 15)},
                ],
            },
            "tv": {
                "num_devices": _by_res(1, 0.5), # roughly 1 per 2 people
                "power_w": 80.0,
                "intervals": [
                    {"start": _time(19, 0), "end": _time(23, 0)},
                ],
            },
            "router": {
                "num_devices": _by_size(1, 2, 3),
                "power_w": 10.0,
                "intervals": [
                    {"start": _time(0, 0), "end": _time(23, 59)},
                ],
            },
            "pc_desktop": {
                "num_devices": _by_res(1, 0.4), # scaling a bit slower
                "power_w": 150.0,
                "intervals": [
                    {"start": _time(9, 0), "end": _time(17, 0)},   # work-from-home
                ],
            },
            "laptop": {
                "num_devices": _by_size(1, 2, 3),
                "power_w": 60.0,
                "intervals": [
                    {"start": _time(9, 0),  "end": _time(12, 0)},
                    {"start": _time(19, 0), "end": _time(23, 0)},
                ],
            },
            "game_console": {
                "num_devices": _by_size(1, 1, 2),
                "power_w": 120.0,
                "intervals": [
                    {"start": _time(20, 0), "end": _time(22, 0)},
                ],
            },
            "printer": {
                "num_devices": _by_size(1, 1, 2),
                "power_w": 40.0,
                "intervals": [
                    {"start": _time(10, 0), "end": _time(12, 0)},  # sporadic use
                ],
            },
            "ventilation": {
                "num_devices": 1,
                "power_w": 60.0,   # HRV unit
                "intervals": [
                    {"start": _time(0, 0), "end": _time(23, 59)},
                ],
            },
            "humidifier": {
                "num_devices": _by_size(1, 1, 2),
                "power_w": 40.0,
                "intervals": [
                    {"start": _time(22, 0), "end": _time(7, 0)},  # night
                ],
            },
            "baby_monitor": {
                "num_devices": _by_size(1, 1, 1),
                "power_w": 5.0,
                "intervals": [
                    {"start": _time(19, 0), "end": _time(7, 0)},
                ],
            },
            "smoke_detector": {
                "num_devices": _by_size(2, 3, 4),
                "power_w": 2.0,
                "intervals": [
                    {"start": _time(0, 0), "end": _time(23, 59)},
                ],
            },
            "standby": {
                "num_devices": 1,
                "power_w": _by_size(30.0, 50.0, 80.0),  # sum of small phantom loads
                "intervals": [
                    {"start": _time(0, 0), "end": _time(23, 59)},
                ],
            },

            # You can also initialize the 3 "other" slots
            "other_fixed_1": {
                "num_devices": 1,
                "power_w": 100.0,
                "intervals": [
                    {"start": _time(18, 0), "end": _time(22, 0)},
                ],
            },
            "other_fixed_2": {
                "num_devices": 1,
                "power_w": 100.0,
                "intervals": [
                    {"start": _time(18, 0), "end": _time(22, 0)},
                ],
            },
            "other_fixed_3": {
                "num_devices": 1,
                "power_w": 100.0,
                "intervals": [
                    {"start": _time(18, 0), "end": _time(22, 0)},
                ],
            },
        }

        cfg = defaults.get(dev_type, {}).copy()
        if not cfg:
            # unknown device → just base
            return base

        # derive power_kw / start / duration_min for compatibility
        first_iv = cfg["intervals"][0]
        start_t = first_iv["start"]
        end_t   = first_iv["end"]

        # assume same day; if end < start we treat it as overnight (add 24h)
        start_dt = datetime.combine(date.today(), start_t)
        end_dt   = datetime.combine(date.today(), end_t)
        if end_dt <= start_dt:
            end_dt = end_dt.replace(day=end_dt.day + 1)

        dur_min = int((end_dt - start_dt).total_seconds() / 60.0)
        if dur_min <= 0:
            dur_min = 60

        cfg["power_kw"] = cfg["power_w"] / 1000.0
        cfg["start"] = start_t
        cfg["duration_min"] = dur_min

        # merge with base so we still have generic keys
        return {**base, **cfg}
    
    # ======================================================
    # 2) FLEXIBLE ELECTRICAL (shiftable loads)
    # ======================================================
    if category == "elec_flex":
        # small helper already defined above in your function:
        #   size = "small"/"medium"/"large"
        #   def _by_size(small, medium, large): ...

        defaults: dict[str, dict] = {
            # Washing machine
            "wm": {
                "num_devices": 1,
                "power_w": 1200.0,
                "start": _time(19, 0),      # typical evening wash
                "duration_min": 90,
                "w_cost": 1,
            },
            # Dishwasher
            "dw": {
                "num_devices": 1,
                "power_w": 1400.0,
                "start": _time(21, 0),      # after dinner
                "duration_min": 90,
                "w_cost": 1,
            },
            # Tumble dryer (resistive)
            "dryer": {
                "num_devices": 1,
                "power_w": 2000.0,
                "start": _time(20, 0),
                "duration_min": 60,
                "w_cost": 1,
            },
            # Robot vacuum
            "robot_vac": {
                "num_devices": 1,
                "power_w": 250.0,
                "start": _time(11, 0),      # mid-day cleaning
                "duration_min": 60,
                "w_cost": 1,
            },
            # Workshop / hobby tools
            "workshop": {
                "num_devices": 1,
                "power_w": 700.0,
                "start": _time(17, 0),
                "duration_min": 120,
                "w_cost": 1,
            },

            # you can add more flexible types later, just extend DEVICE_CATEGORIES
            # and add entries here with the same pattern.

            # custom slots – just give a neutral default
            "other_flex_1": {
                "num_devices": 1,
                "power_w": 1000.0,
                "start": _time(18, 0),
                "duration_min": 60,
                "w_cost": 1,
            },
            "other_flex_2": {
                "num_devices": 1,
                "power_w": 1000.0,
                "start": _time(18, 0),
                "duration_min": 60,
                "w_cost": 1,
            },
            "other_flex_3": {
                "num_devices": 1,
                "power_w": 1000.0,
                "start": _time(18, 0),
                "duration_min": 60,
                "w_cost": 1,
            },
        }

        cfg = defaults.get(dev_type, {}).copy()
        if not cfg:
            # unknown flexible type → fall back to generic
            return base

        # derive an initial single interval from start + duration_min
        start_t = cfg.get("start", _time(20, 0))
        dur_min = int(cfg.get("duration_min", 60))
        if dur_min <= 0:
            dur_min = 60

        start_dt = datetime.combine(date.today(), start_t)
        end_dt   = start_dt + timedelta(minutes=dur_min)
        # clamp to time of day (ignore day overflow)
        end_t = (end_dt.time().replace(second=0, microsecond=0))

        cfg["intervals"] = [{"start": start_t, "end": end_t}]
        cfg["power_kw"]  = float(cfg.get("power_w", base["power_kw"] * 1000.0)) / 1000.0
        cfg["start"]     = start_t
        cfg["duration_min"] = dur_min

        # keep w_cost if present, default 0.5
        cfg["w_cost"] = float(cfg.get("w_cost", 1))

        return {**base, **cfg}


    # ======================================================
    # 2) FLEXIBLE / THERMAL / GEN / OUTSIDE  (unchanged)
    # ======================================================

    if category == "thermal":
        defaults = {
            # Space heating: external supply by default → no P_el
            "space_heat": {
                "space_mode": "None (external supply)",
                "t_min_c": 20.0,
                "t_max_c": 22.0,
                # these are only used if user changes away from "None"
                "q_kw": 6.0,
            },
            # DHW: Electric DHW tank by default
            "dhw": {
                "dhw_mode": "Electric DHW tank",
                "volume_l": _by_res(80, 40), # 150-200-250 range
                "usage_level": "Low" if residents <= 2 else ("Medium" if residents <= 4 else "High"),
                "t_min_c": 45.0,
                "t_max_c": 55.0,
                "p_el_kw": 2.0,
            },
            # Leisure: all disabled by default → no P_el
            "leisure": {
                "hot_tub_enabled": False,
                "pool_enabled": False,
            },
        }

        cfg = defaults.get(dev_type, {}).copy()
        if not cfg:
            # unknown thermal device: just inherit base but no real load
            cfg = {}

        # keep generic keys so other code that expects them doesn't crash
        cfg.setdefault("power_kw", base["power_kw"])
        cfg.setdefault("start", base["start"])
        cfg.setdefault("duration_min", base["duration_min"])

        return {**base, **cfg}

    if category == "gen_store":
        # For now we only want PV to matter; others should default to 0 kW.
        # Also override the base so gen_store things don't inherit 0.5 kW.
        base_gen = dict(
            power_kw=0.0,
            start=_time(0, 0),
            duration_min=0,
        )

        defaults = {
            # PV: we don't actually use power_kw/start/duration for PV,
            # but we can store some sizing-related defaults here if you like.
            "pv": {
                "module_wp": 400.0,
                "n_panels": 16,
                "tilt": 30.0,
                "azimuth": 180.0,
                "loss_frac": 0.14,
                # keep power_kw etc. at 0 so it never shows as a "load"
                "power_kw": 0.0,
                "start": _time(0, 0),
                "duration_min": 0,
            },
            
            "battery": {
                # battery sizing
                "E_kWh": 70.0,
                "P_ch_max_kW": 9.0,
                "P_dis_max_kW": 9.0,

                # SOC limits
                "soc_init": 50.0,   # 0..1
                "soc_min":  10.0,
                "soc_max":  95.0,

                # efficiencies
                "eta_ch": 0.95,
                "eta_dis": 0.95,

                # keep these so it never behaves like a scheduled load
                "power_kw": 0.0,
                "start": _time(0, 0),
                "duration_min": 0,
            },

            "fuel_cell": {
                "enabled": True,

                # electrical
                "p_rated_kw": 4.86,
                "p_min_kw": 1.90,

                # economics (keep units consistent with your FCcontrol solver)
                "price_ch3oh": 1.0,      # placeholder (DKK/kWh_e-equiv OR whatever you use)
                "schedule_mode": "price", # "off" | "price" | "soc" | "hybrid"

                # smoothing / dynamics
                "min_on_min": 60,
                "min_off_min": 60,

                # SOC gating
                "soc_low": 30.0,
                "soc_high": 80.0,

                # heat
                "use_waste_heat": True,
                "heat_priority": "DHW",      # "DHW" | "Space" | "DHW→Space"
                "eta_recovery": 1.0,
                "t_tank_max_c": 65.0,        # radiator dump threshold like your old model
            },


            # everything else → no default power / duration
            "diesel_gen":   {"power_kw": 0.0, "start": _time(0, 0), "duration_min": 0},
            "electrolyzer": {"power_kw": 0.0, "start": _time(0, 0), "duration_min": 0},
        }

        return {**base_gen, **defaults.get(dev_type, {})}

    if category == "outside":
        defaults = {
            "ev11":         dict(power_kw=11.0, start=_time(1, 0), duration_min=240),
            "ev22":         dict(power_kw=22.0, start=_time(1, 0), duration_min=120),
            "ebike":        dict(power_kw=0.5,  start=_time(1, 0), duration_min=180),
            "outdoor_light":dict(power_kw=0.2,  start=_time(17, 0),duration_min=600),
            "patio_heater": dict(power_kw=2.0,  start=_time(18, 0),duration_min=240),
        }
        return {**base, **defaults.get(dev_type, {})}

    return base   
