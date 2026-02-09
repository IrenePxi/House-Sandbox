from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta

from core.devices import WeatherHotTub, DHWTank
from core.profiles import build_minute_profile

from core.solar import pv_from_weather_modelchain_from_df
from core.thermal_share import HeatSource, simulate_space_heating_shared

from models.schemas import DeviceConfig, SimulationContext

# Helper to get outdoor profile
def _availability_from_intervals(idx: pd.DatetimeIndex, intervals: list[dict]) -> np.ndarray:
    """
    intervals: [{"start": time, "end": time}, ...] (times can wrap over midnight)
    returns boolean array per minute.
    """
    if not intervals:
        return np.ones(len(idx), dtype=bool)

    mins = idx.hour * 60 + idx.minute
    mask = np.zeros(len(idx), dtype=bool)

    for iv in intervals:
        s = iv.get("start")
        e = iv.get("end")
        if s is None or e is None:
            continue

        smin = s.hour * 60 + s.minute
        emin = e.hour * 60 + e.minute

        if emin >= smin:
            mask |= (mins >= smin) & (mins < emin)
        else:
            # wraps past midnight
            mask |= (mins >= smin) | (mins < emin)

    return mask

def _get_outdoor_profile(context: SimulationContext) -> tuple[pd.DatetimeIndex, pd.Series]:
    if (
        context.temp_daily is not None 
        and isinstance(context.temp_daily, pd.Series) 
        and not context.temp_daily.empty
    ):
        # This assumes get_selected_day_data logic is handled or we replicate it.
        # Ideally, context should already contain the 24h series for the selected day.
        # But for now, we'll try to slice it if it's the full year series.
        # Actually, p2_devices uses `get_selected_day_data` from state.session.
        # We should probably assume context has the READY TO USE 24h series or index.
        # To avoid circular imports, let's assume context.temp_daily is the 24h slice if short,
        # or we slice it using selected_day.
        pass # Not easily replicable without the helper.
    
    # Re-implement fallback logic strictly based on Context
    sel_day = context.selected_day
    start = pd.Timestamp(sel_day)
    idx = pd.date_range(start, periods=24 * 60, freq="min")
    
    # If context has explicit weather_hr, we might use that for T_out?
    # But usually temp_daily is the source. 
    # Let's recreate the fallback synthetic curve if no data.
    hours = idx.hour + idx.minute / 60.0
    tout_minute = pd.Series(
        5.0 + 5.0 * np.sin(2 * np.pi * (hours - 15) / 24.0),
        index=idx,
        name="Tout_C",
    )
    
    # If context provided a series matching this index, use it.
    if context.temp_daily is not None:
         # simple check
         if len(context.temp_daily) == len(idx):
             tout_minute = pd.Series(context.temp_daily.values, index=idx)
    
    return idx, tout_minute

def simulate_device(full_key, cfg: DeviceConfig, context: SimulationContext, *, q_extra_kw=None) -> pd.Series:

    """
    Simulate a single device based on its config and simulation context.
    Returns a pandas Series of power [kW] indexed by minute for the selected day.
    """
    cat_key, dev_type = full_key.split(":", 1)
    
    # 1. PV
    if cat_key == "gen_store" and dev_type == "pv":
        idx = pd.date_range(pd.Timestamp(context.selected_day), periods=1440, freq="min")
        kwp = (cfg.module_wp * cfg.n_panels) / 1000.0
        
        if kwp <= 0:
             return pd.Series(0.0, index=idx, name=full_key)

        if context.weather_hr is not None and not context.weather_hr.empty:
            try:
                # We need to ensure pv_from_weather_modelchain_from_df is importable and works
                pv_series = pv_from_weather_modelchain_from_df(
                    idx_min=idx, 
                    dfh=context.weather_hr,
                    lat=context.geo_lat, 
                    lon=context.geo_lon,
                    kwp=kwp, 
                    tilt_deg=cfg.tilt, 
                    az_deg=cfg.azimuth, 
                    sys_loss_frac=cfg.loss_frac
                )
                return pv_series
            except Exception:
                pass
        
        # Fallback synthetic
        hours = idx.hour + idx.minute/60.0
        pv_series = pd.Series(kwp * np.maximum(0.0, np.sin(np.pi*(hours-6)/12)), index=idx)
        return pv_series

    # 2. Thermal
    if cat_key == "thermal":
        idx, tout = _get_outdoor_profile(context)

        if dev_type == "space_heat":
            # q_extra_kw here is thermal kW (from FC waste heat)
            q_extra = 0.0 if q_extra_kw is None else q_extra_kw

            dbg = simulate_space_heat_shared_debug(cfg, context, q_extra_th_kw=q_extra) or {}
            P_space = dbg.get("P_el_total_kw")

            if not isinstance(P_space, pd.Series) or P_space.empty:
                return pd.Series(0.0, index=idx, name="P_space_kW")

            P_space = P_space.copy()
            P_space.name = "P_space_kW"
            return P_space




        # B) DHW
        elif dev_type == "dhw":
            if cfg.dhw_mode == "None (external supply)":
                return pd.Series(0.0, index=idx, name="P_DHW_kW")

            # UI gives "Heater power (kW, thermal side)" so treat cfg.p_el_kw as kWth capacity
            p_th = float(cfg.p_el_kw)

            mean_T = (cfg.t_min_c + cfg.t_max_c) / 2.0
            hyst = max(cfg.t_max_c - cfg.t_min_c, 1.0)

            tank = DHWTank(
                volume_l=float(cfg.volume_l),
                t_set_c=float(mean_T),
                hyst_band_c=float(hyst),
                p_el_kw=float(p_th),              # kWth heater capacity in your current convention
                usage_level=str(cfg.usage_level),
                Ti0_c=float(mean_T),
            )

            COP_DHW = 2.5

  
            # External thermal input (optional). Provided by Page 3 when re-simulating DHW.
            q_extra = 0.0 if q_extra_kw is None else q_extra_kw

            if isinstance(q_extra, pd.Series):
                q_extra = q_extra.reindex(idx).fillna(0.0)
            elif isinstance(q_extra, (np.ndarray, list, tuple)):
                q_extra = np.asarray(q_extra, dtype=float)
                if len(q_extra) != len(idx):
                    raise ValueError("q_extra_kw must have same length as idx")
            else:
                q_extra = float(q_extra)


            Q_heater_th, T_tank, Q_used, Q_spill = tank.series_kw(
                idx,
                tout,
                q_extra_kw=q_extra,
                t_cap_c=float(cfg.t_max_c),
            )

            # Convert heater thermal output to electric demand depending on DHW mode
            if "Heat pump" in cfg.dhw_mode:
                P_dhw = Q_heater_th / COP_DHW
            else:
                P_dhw = Q_heater_th

            P_dhw = P_dhw.rename("P_DHW_kW")
            return P_dhw



         
        # C) Leisure
        elif dev_type == "leisure":
             P_total = pd.Series(0.0, index=idx, name="P_leisure_kW")
             
             if cfg.hot_tub_enabled:
                 ua_ht = 0.07 * {"Good cover": 0.6, "Poor": 1.4, "Average": 1.0}.get(cfg.ht_insulation, 1.0)
                 # Map strict Interval objects to dicts if needed by WeatherHotTub, 
                 # or update WeatherHotTub to handle objects. 
                 # WeatherHotTub expects dicts: sess.get("start").
                 # Our cfg.ht_sessions is List[dict] in schema, so it matches.
                 
                 ht = WeatherHotTub(
                     target_c=cfg.ht_target_c, idle_c=cfg.ht_idle_c, 
                     heater_kw=cfg.ht_heater_kw, water_l=cfg.ht_water_l, 
                     ua_kw_per_c=ua_ht, sessions=cfg.ht_sessions, 
                     use_outdoor_for_ambient=False, indoor_ambient_c=21.0
                 )
                 P_ht, _ = ht.series_kw(idx, tout)
                 P_total = P_total.add(P_ht, fill_value=0.0)

             if cfg.pool_enabled:
                 ua_pool = 0.15 * {"Good cover": 0.6, "Poor": 1.4, "Average": 1.0}.get(cfg.pool_insulation, 1.0)
                 pool = WeatherHotTub(
                     target_c=cfg.pool_target_c, idle_c=cfg.pool_idle_c, 
                     heater_kw=cfg.pool_heater_kw, water_l=cfg.pool_water_l, 
                     ua_kw_per_c=ua_pool, sessions=cfg.pool_sessions, 
                     use_outdoor_for_ambient=True, indoor_ambient_c=21.0
                 )
                 Q_pool, _ = pool.series_kw(idx, tout)
                 P_pool = Q_pool / 3.5
                 P_total = P_total.add(P_pool, fill_value=0.0)
                 
             return P_total

    # 3. Simple Profile (Fixed / Flex / EV / Others)
    # Reconstruct intervals list of dicts for build_minute_profile
    if cfg.intervals:
        # Convert Interval objects to dicts
        iv_dicts = [{"start": iv.start, "end": iv.end} for iv in cfg.intervals]
    else:
        # Fallback
        # Logic from p2_devices fallback
        start_t = cfg.start
        dur_min = cfg.duration_min
        # Construct a simple interval
        # Note: build_minute_profile wants dicts with datetime.time
        # We need to handle wrapping manually if we pass just times, 
        # BUT build_minute_profile handles wrapping.
        # However, we need to know if it wraps to create the list of dicts?
        # build_minute_profile expects: [{"start": t1, "end": t2}]
        # If we calculate end time:
        dummy_d = date(2025, 1, 10)
        st_dt = datetime.combine(dummy_d, start_t)
        en_dt = st_dt + timedelta(minutes=dur_min)
        iv_dicts = [{"start": start_t, "end": en_dt.time()}]

    # Calculate Total Power
    if cat_key in ("elec_fixed", "elec_flex"):
        # Power is per device in Watts in config for these
        # But we stored power_kw = power_w / 1000 in the schema normalization?
        # Wait, the schema has power_kw.
        # In p2: cfg["power_kw"] = cfg["power_w"] / 1000.0
        # So we can trust cfg.power_kw to be the per-device kW.
        # Total = power_kw * num_devices
        total_kw = cfg.power_kw * cfg.num_devices
    else:
        # Others (EV, etc) usually just have power_kw as total or handled differently
        # EV p2: power_kw is charger power.
        # For EV, build_minute_profile(power_w=..., intervals=...)
        total_kw = cfg.power_kw # EV implies 1 device usually, or handled in defaults
        
    prof_series = build_minute_profile(
        power_w=total_kw * 1000.0,
        intervals=iv_dicts,
        step_min=1
    )
    
    # Normalize index to selected day
    idx_common = pd.date_range(pd.Timestamp(context.selected_day), periods=1440, freq="min")
    # build_minute_profile returns dummy day index. We replace it.
    prof_series.index = idx_common
    prof_series.name = full_key
    
    return prof_series

def simulate_dhw_with_extra_debug(
    cfg: DeviceConfig,
    context: SimulationContext,
    *,
    q_extra_kw=0.0,
    T_use_c: float = 45.0,   # fixed service temp
):
    """
    Returns:
      P_DHW_kW              (kWel)
      T_tank_C              (°C)
      Q_dhw_extra_used_kW    (kWth)
      Q_dhw_extra_spill_kW   (kWth)
      Q_dhw_draw_th_kW       (kWth)  <-- external/service demand (NOT from tank temperature)
    """
    idx, tout = _get_outdoor_profile(context)

    if cfg.dhw_mode == "None (external supply)":
        z = pd.Series(0.0, index=idx)
        return (
            z.rename("P_DHW_kW"),
            z.rename("T_tank_C"),
            z.rename("Q_dhw_extra_used_kW"),
            z.rename("Q_dhw_extra_spill_kW"),
            z.rename("Q_dhw_draw_th_kW"),
        )

    p_th = float(cfg.p_el_kw)
    mean_T = (cfg.t_min_c + cfg.t_max_c) / 2.0
    hyst = max(cfg.t_max_c - cfg.t_min_c, 1.0)

    tank = DHWTank(
        volume_l=float(cfg.volume_l),
        t_set_c=float(mean_T),
        hyst_band_c=float(hyst),
        p_el_kw=float(p_th),
        usage_level=str(cfg.usage_level),
        Ti0_c=float(mean_T),
        T_cold_c=10.0,
        T_amb_c=21.0,
    )

    # external/service draw demand (independent of tank temperature)
    Q_draw_th = tank.build_Q_draw_external_kw(idx, T_use_c=T_use_c)

    # align q_extra
    if isinstance(q_extra_kw, pd.Series):
        q_extra = q_extra_kw.reindex(idx).fillna(0.0)
    else:
        q_extra = q_extra_kw

    Q_heater_th, T_tank, Q_used, Q_spill = tank.series_kw(
        idx=idx,
        tout_c=tout,
        q_extra_kw=q_extra,
        t_cap_c=float(cfg.t_max_c),
    )

    COP_DHW = 2.5
    if "Heat pump" in cfg.dhw_mode:
        P_dhw_el = (Q_heater_th / COP_DHW)
    else:
        P_dhw_el = Q_heater_th

    return (
        P_dhw_el.rename("P_DHW_kW"),
        T_tank.rename("T_tank_C"),
        Q_used.rename("Q_dhw_extra_used_kW"),
        Q_spill.rename("Q_dhw_extra_spill_kW"),
        Q_draw_th.rename("Q_dhw_draw_th_kW"),
    )



def simulate_space_heat_shared_debug(
    cfg: DeviceConfig,
    context: SimulationContext,
    q_extra_th_kw=0.0,   # thermal extra heat to space (kWth)
) -> dict:
    idx, tout = _get_outdoor_profile(context)

    hpar = context.thermal_house_params or {}
    ua_base = float(hpar.get("ua_kw_per_c", 0.12))
    Cth_base = float(hpar.get("C_th_kwh_per_c", 0.60))

    t_min = float(getattr(cfg, "t_min_c", 20.0))
    t_max = float(getattr(cfg, "t_max_c", 22.0))
    if t_max <= t_min:
        t_max = t_min + 0.5

    extra_mass = 0.0
    dist = getattr(cfg, "distribution", "")
    if dist == "Floor heating":
        extra_mass = 0.5
    elif dist == "Both":
        extra_mass = 0.3
    C_eff = Cth_base * (1.0 + extra_mass)

    space_sources = getattr(cfg, "space_sources", None) or []
    sources: list[HeatSource] = []
    wood_name: str | None = None

    for s in space_sources:
        if not isinstance(s, dict) or not s.get("enabled", True):
            continue

        kind = str(s.get("kind", "")).strip().lower()
        name = str(s.get("name", kind)).strip()
        if kind not in ("hp", "eh", "wood"):
            continue

        qmax_per = float(s.get("q_max_th_kw", 0.0))
        ndev = int(s.get("num_devices", 1))
        qmax = max(0.0, qmax_per) * max(1, ndev)
        if qmax <= 0:
            continue

        prio = int(s.get("priority", 999))

        role = str(s.get("role", "controlled") or "controlled").strip().lower()
        if role not in ("controlled", "forced"):
            role = "controlled"

        if kind == "wood":
            role = "forced"
            wood_name = name

        available_fn = None
        if role == "forced":
            iv_list = s.get("intervals", []) or []
            def _mk_avail(iv_copy):
                return lambda ix: _availability_from_intervals(ix, iv_copy)
            available_fn = _mk_avail(iv_list)

        dispatch_mode = str(s.get("dispatch_mode", "modulating") or "modulating").strip().lower()
        if dispatch_mode not in ("modulating", "fixed"):
            dispatch_mode = "modulating"
        if kind == "wood":
            dispatch_mode = "fixed"

        q_fixed = float(s.get("q_fixed_th_kw", 0.0) or 0.0)
        p_idle = float(s.get("p_idle_el_kw", 0.0) or 0.0)
        if dispatch_mode == "fixed" and q_fixed <= 0:
            q_fixed = float(qmax)

        sources.append(HeatSource(
            name=name,
            kind=kind,
            q_max_th_kw=float(qmax),
            priority=prio,
            enabled=True,
            available=available_fn,
            dispatch_mode=dispatch_mode,
            q_fixed_th_kw=q_fixed,
            p_idle_el_kw=p_idle,
            role=role,
        ))

    if not sources:
        z = pd.Series(0.0, index=idx, name="P_space_total_kw")
        return {
            "Ti_C": pd.Series(np.nan, index=idx, name="Ti_C"),
            "P_el_total_kw": z,
            "P_el_by_device_kw": {},
            "Q_th_by_device_kw": {},
            "wood_kg_day": 0.0,
        }

    out = simulate_space_heating_shared(
        idx=idx,
        tout_c=tout,
        t_min_c=t_min,
        t_max_c=t_max,
        ua_kw_per_c=ua_base,
        C_th_kwh_per_c=C_eff,
        sources=sources,
        Ti0_c=0.5 * (t_min + t_max),
        hp_family="air_to_water",
        q_extra_th_kw=q_extra_th_kw,   # ✅ correct name + correct unit
    )

    # wood kg/day
    wood_kg_day = 0.0
    q_by = out.get("Q_th_by_device_kw", {}) or {}
    if wood_name and wood_name in q_by:
        qwood_kw = q_by[wood_name]
        qwood_kwh_day = float(qwood_kw.sum()) / 60.0
        eta = float(getattr(cfg, "wood_eta", 0.70))
        lhv = float(getattr(cfg, "wood_lhv_kwh_per_kg", 4.0))
        eta = min(max(eta, 0.1), 1.0)
        lhv = max(lhv, 1.0)
        wood_kg_day = qwood_kwh_day / max(1e-6, eta * lhv)

    out["wood_kg_day"] = wood_kg_day
    return out
