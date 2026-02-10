from __future__ import annotations
import pandas as pd
from typing import Dict, Tuple 
from models.schemas import DeviceConfig, SimulationContext
from services.P2_devicesimulation_service import simulate_device
import numpy as np



def compute_daily_profiles(
    sel: Dict[str, bool],
    cfgs: Dict[str, DeviceConfig], # Can also accept dicts if migrated/compatible
    context: SimulationContext
) -> Tuple[pd.DatetimeIndex, Dict[str, pd.Series], pd.Series]:
    """
    Compute daily profiles for all selected devices.
    Returns: (index, device_traces, total_load)
    """
    
    # Prepare common index
    idx_common = pd.date_range(pd.Timestamp(context.selected_day), periods=1440, freq="min")
    
    device_traces: Dict[str, pd.Series] = {}
    total = pd.Series(0.0, index=idx_common, name="P_total_kW")
    
    for full_key, checked in sel.items():
        if not checked:
            continue
            
        # config lookup
        cfg_obj = cfgs.get(full_key)
        if not cfg_obj:
            continue
            
        # Ensure it's a DeviceConfig object (handle mixed dict/obj during migration if needed)
        if isinstance(cfg_obj, dict):
             cfg_obj = DeviceConfig.from_dict(cfg_obj)
             
        # Simulate
        # (We could check for cached profile here if we wanted to support legacy cached data without re-simulating,
        # but the goal is to remove that dependency. So we simulate fresh.)
        series = simulate_device(full_key, cfg_obj, context)
        
        # Reindex just in case
        if not series.index.equals(idx_common):
             # Simple overwrite if length matches
             if len(series) == len(idx_common):
                 series.index = idx_common
             else:
                 series = series.reindex(idx_common, fill_value=0.0)

        device_traces[full_key] = series
        
        # Add to total (exclude PV)
        cat_key, dev_type = full_key.split(":", 1)
        if not (cat_key == "gen_store" and dev_type == "pv"):
            total = total.add(series, fill_value=0.0)
            
    if not device_traces:
        total = pd.Series(0.0, index=idx_common, name="P_total_kW")
        
    return idx_common, device_traces, total




def build_series_for_analysis(
    sel: Dict[str, bool],
    cfgs: Dict[str, dict],
    context,
) -> Tuple[
    pd.DatetimeIndex,
    pd.Series,  # load_tot_kw (kept for backward compatibility)
    pd.Series,  # pv_tot_kw
    Dict[str, float],  # energy_per_device_kwh
    pd.Series,  # p_load_nonthermal_kw
    pd.Series,  # p_thermal_el_kw
    pd.Series,  # p_thermal_dhw_el_kw
    pd.Series,  # p_thermal_space_el_kw
    pd.Series,  # p_thermal_leisure_el_kw
    Dict[str, pd.Series] # device_traces
]:
    idx, device_traces, _total = compute_daily_profiles(sel, cfgs, context)

    # totals
    load_tot = pd.Series(0.0, index=idx, name="P_load_kW")
    pv_tot   = pd.Series(0.0, index=idx, name="P_pv_kW")

    # new splits
    p_load_nonthermal = pd.Series(0.0, index=idx, name="P_load_nonthermal_kW")
    p_thermal_el      = pd.Series(0.0, index=idx, name="P_thermal_el_kW")
    p_thermal_dhw_el  = pd.Series(0.0, index=idx, name="P_thermal_dhw_el_kW")
    p_thermal_space_el= pd.Series(0.0, index=idx, name="P_thermal_space_el_kW")
    p_thermal_leisure_el = pd.Series(0.0, index=idx, name="P_thermal_leisure_el_kW")

    energy_per_device: Dict[str, float] = {}

    def _thermal_bucket(dev_type: str, cfg: dict) -> str:
        """
        Decide whether a thermal device contributes to DHW or Space.
        - Prefer explicit cfg["thermal_use"] if you have it.
        - Otherwise infer from dev_type string.
        """
        use = str(cfg.get("thermal_use", "")).lower().strip()
        if use in ("dhw", "hotwater", "hot_water"):
            return "dhw"
        if use in ("space", "space_heating", "heating"):
            return "space"
        if use in ("leisure", "hottub", "hot_tub", "pool", "spa"):
            return "leisure"

        s = dev_type.lower()
        if "leisure" in s or "hot_tub" in s or "hottub" in s or "pool" in s or "spa" in s:
            return "leisure"
        if "dhw" in s or "hotwater" in s or "hot_water" in s or "tank" in s:
            return "dhw"
        if "space" in s or "radiator" in s or "floor" in s or "heating" in s:
            return "space"

        return "unknown"

    for full_key, s in (device_traces or {}).items():
        if s is None or getattr(s, "empty", False):
            continue

        # Ensure aligned series
        s = pd.Series(s, index=idx).astype(float).fillna(0.0)

        cat_key, dev_type = full_key.split(":", 1)

        if cat_key == "gen_store" and dev_type == "pv":
            pv_tot = pv_tot.add(s, fill_value=0.0)
            # PV is not a "load", so skip energy_per_device for it if you want
            continue

        # everything that is not PV counts into total load
        load_tot = load_tot.add(s, fill_value=0.0)
        energy_per_device[full_key] = float(s.sum() / 60.0)  # 1-min -> kWh

        # split out thermal vs nonthermal
        if cat_key == "thermal":
            p_thermal_el = p_thermal_el.add(s, fill_value=0.0)
            cfg = cfgs.get(full_key, {}) or {}
            bucket = _thermal_bucket(dev_type, cfg)
            if bucket == "dhw":
                p_thermal_dhw_el = p_thermal_dhw_el.add(s, fill_value=0.0)
            elif bucket == "space":
                p_thermal_space_el = p_thermal_space_el.add(s, fill_value=0.0)
            elif bucket == "leisure":
                p_thermal_leisure_el = p_thermal_leisure_el.add(s, fill_value=0.0)
            else:
                pass
        else:
            p_load_nonthermal = p_load_nonthermal.add(s, fill_value=0.0)
    
    # Unit: kW_el
    # thermal devices are electric demand in this model
    return (
        idx,
        load_tot,
        pv_tot,
        energy_per_device,
        p_load_nonthermal,
        p_thermal_el,
        p_thermal_dhw_el,
        p_thermal_space_el,
        p_thermal_leisure_el,
        device_traces  # <--- NEW
    )
