from __future__ import annotations
from models.schemas import DeviceConfig, FlexPrefs, Interval

# Import original logic to reuse
from state.session import suggest_best_interval_for_day, suggest_best_interval_for_ev

def suggest_interval_for_device(
    full_key: str, 
    cfg: DeviceConfig, 
    prefs: FlexPrefs
) -> Interval | None:
    """
    Suggest best interval based on flex prefs.
    """
    # Original logic uses simple params
    best_iv = suggest_best_interval_for_day(
        duration_min=cfg.duration_min,
        w_cost=prefs.w_cost,
        earliest=prefs.earliest,
        latest=prefs.latest
    )
    
    if best_iv:
        return Interval(start=best_iv["start"], end=best_iv["end"])
    return None

def suggest_ev_interval(
    cfg: DeviceConfig,
    # cost_data/co2_data would be passed here ideally
) -> Interval | None:
    
    best_iv = suggest_best_interval_for_ev(
        duration_min=cfg.duration_min,
        w_cost=cfg.w_cost,
        window_start_min=60, # 01:00
        window_end_min=360   # 06:00
    )
    
    if best_iv:
         return Interval(start=best_iv["start"], end=best_iv["end"])
    return None
