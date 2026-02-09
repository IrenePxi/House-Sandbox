from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import time, date
from typing import List, Optional, Any
import pandas as pd
from datetime import datetime, time as dtime
from datetime import time as _time

def _time_to_str(t: _time | None) -> str | None:
    if t is None:
        return None
    return t.strftime("%H:%M:%S")

def _parse_time(x: Any, default: dtime) -> dtime:
    """Accept time object or 'HH:MM'/'HH:MM:SS' string; return a time."""
    if isinstance(x, dtime):
        return x
    if isinstance(x, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(x, fmt).time()
            except ValueError:
                pass
    return default

def _to_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _to_int(x: Any, default: int) -> int:
    try:
        return int(x)
    except Exception:
        return default

@dataclass
class Interval:
    start: time
    end: time

@dataclass
class HouseInfo:
    size: str = "Medium house"
    insulation: str = "Average"
    residents: int = 2

@dataclass
class FlexPrefs:
    w_cost: float = 1.0
    window_mode: str = "Daytime (08–17)"
    earliest: time = time(8, 0)
    latest: time = time(17, 0)
    # Using field for mutable default
    earliest_custom: time = field(default_factory=lambda: time(7, 0))
    latest_custom: time = field(default_factory=lambda: time(22, 0))

@dataclass
class SimulationContext:
    selected_day: date
    weather_hr: Optional[pd.DataFrame] = None
    temp_daily: Optional[pd.Series] = None
    geo_lat: float = 57.0488
    geo_lon: float = 9.9217
    # Thermal params dict as passed in state
    thermal_house_params: dict = field(default_factory=dict)

@dataclass
class DeviceConfig:
    # Common fields
    num_devices: int = 1
    power_kw: float = 0.5
    
    # Scheduling
    start: time = time(18, 0)
    duration_min: int = 60
    intervals: List[Interval] = field(default_factory=list)
    
    # Flexible specific
    w_cost: float = 1.0
    
    # Thermal/HP/DHW specific
    space_mode: str = "None (external supply)"
    t_min_c: float = 20.0
    t_max_c: float = 22.0
    q_kw: float = 6.0
    hp_type: str = "Fixed power"
    distribution: str = "Radiators"

    # shared space heating config
    space_control_mode: str = "auto"  # "auto" | "manual"
    space_sources: Optional[List[dict]] = None 

    # NEW: wood conversion (Option A)
    wood_eta: float = 0.70
    wood_lhv_kwh_per_kg: float = 4.0
    
    dhw_mode: str = "None (external supply)"
    volume_l: float = 200.0
    usage_level: str = "Medium"
    p_el_kw: float = 2.0
    
    hot_tub_enabled: bool = False
    ht_target_c: float = 40.0
    ht_idle_c: float = 30.0
    ht_water_l: float = 1200.0
    ht_heater_kw: float = 5.0
    ht_insulation: str = "Average"
    ht_sessions: List[dict] = field(default_factory=list) # List of dicts for session info

    pool_enabled: bool = False
    pool_target_c: float = 28.0
    pool_idle_c: float = 24.0
    pool_water_l: float = 30000.0
    pool_heater_kw: float = 15.0
    pool_insulation: str = "Average"
    pool_sessions: List[dict] = field(default_factory=list)

    # EV specific
    capacity_kwh: float = 75.0
    soc_arrive: float = 20.0
    soc_target: float = 80.0
    
    # PV specific
    module_wp: float = 400.0
    n_panels: int = 16
    tilt: float = 30.0
    azimuth: float = 180.0
    loss_frac: float = 0.14
    
    # Other
    custom_name: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceConfig":
        # Start from defaults, so we always have a reference
        defaults = cls()

        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in (data or {}).items() if k in valid_keys}

        # ---- normalize time fields ----
        filtered["start"] = _parse_time((data or {}).get("start"), defaults.start)

        # ---- normalize common numeric fields ----
        filtered["num_devices"]  = max(1, _to_int((data or {}).get("num_devices", defaults.num_devices), defaults.num_devices))
        filtered["power_kw"]     = max(0.0, _to_float((data or {}).get("power_kw", defaults.power_kw), defaults.power_kw))
        filtered["duration_min"] = max(0, _to_int((data or {}).get("duration_min", defaults.duration_min), defaults.duration_min))

        # ---- normalize intervals ----
        raw_intervals = (data or {}).get("intervals") or []
        cleaned: list[Interval] = []
        for iv in raw_intervals:
            if isinstance(iv, Interval):
                s = _parse_time(iv.start, defaults.start)
                e = _parse_time(iv.end, defaults.start)
                cleaned.append(Interval(start=s, end=e))
            elif isinstance(iv, dict):
                s = _parse_time(iv.get("start"), defaults.start)
                e = _parse_time(iv.get("end"), defaults.start)
                cleaned.append(Interval(start=s, end=e))
        filtered["intervals"] = cleaned

        # ---- normalize shared space heating fields ----
        scm = (data or {}).get("space_control_mode", defaults.space_control_mode)
        filtered["space_control_mode"] = str(scm) if scm is not None else defaults.space_control_mode

        # keep space_sources as list[dict] if provided
        ss = (data or {}).get("space_sources", None)
        if isinstance(ss, list):
            # keep only dict-like entries
            filtered["space_sources"] = [x for x in ss if isinstance(x, dict)]
        elif ss is None:
            filtered["space_sources"] = None

        filtered["wood_eta"] = max(0.01, min(1.0, _to_float((data or {}).get("wood_eta", defaults.wood_eta), defaults.wood_eta)))
        filtered["wood_lhv_kwh_per_kg"] = max(0.1, _to_float((data or {}).get("wood_lhv_kwh_per_kg", defaults.wood_lhv_kwh_per_kg), defaults.wood_lhv_kwh_per_kg))


        return cls(**filtered)


    def to_dict(self) -> dict:
        import datetime
        from dataclasses import asdict

        def convert(obj):
            if isinstance(obj, datetime.time):
                return obj.strftime("%H:%M:%S")
            if isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [convert(v) for v in obj]
            return obj

        return convert(asdict(self))


    def to_jsonable_dict(self) -> dict:
        """
        JSON-safe dict: converts datetime.time objects to strings.
        Use this when sending configs over an API or saving as JSON.
        """
        d = asdict(self)

        # start time
        if isinstance(d.get("start"), dtime):
            d["start"] = d["start"].strftime("%H:%M:%S")

        # intervals
        json_intervals = []
        for iv in d.get("intervals", []):
            s = iv.get("start")
            e = iv.get("end")
            json_intervals.append({
                "start": s.strftime("%H:%M:%S") if isinstance(s, dtime) else s,
                "end":   e.strftime("%H:%M:%S") if isinstance(e, dtime) else e,
            })
        d["intervals"] = json_intervals

        return d

