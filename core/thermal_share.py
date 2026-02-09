from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Literal, Any

import numpy as np
import pandas as pd

from core.devices import WeatherHP


Kind = Literal["hp", "eh", "wood"]


@dataclass
class HeatSource:
    name: str
    kind: str                 # "hp", "eh", "wood"
    q_max_th_kw: float
    priority: int = 0
    enabled: bool = True
    available: Optional[Callable[[pd.DatetimeIndex], np.ndarray]] = None

    # dispatch shape
    dispatch_mode: str = "modulating"   # "modulating" or "fixed"
    q_fixed_th_kw: float = 0.0
    p_idle_el_kw: float = 0.0           # hp only

    # NEW: role in the 3-step logic
    role: str = "controlled"            # "controlled" or "forced"


def cop_simple(tout_c: np.ndarray | float, hp_family: str = "air_to_water") -> np.ndarray:
    """
    A simple, bounded COP curve. Replace later with a more accurate model if needed.

    hp_family:
      - "air_to_water"
      - "air_to_air"
    """
    tout = np.asarray(tout_c, dtype=float)

    if hp_family == "air_to_air":
        # slightly higher COP
        cop = 2.2 + (tout + 7.0) * (1.2 / 14.0)   # ~2.2 at -7C, ~3.4 at +7C
        return np.clip(cop, 1.6, 4.2)

    # air_to_water default
    cop = 2.0 + (tout + 7.0) * (1.0 / 14.0)       # ~2.0 at -7C, ~3.0 at +7C
    return np.clip(cop, 1.4, 3.8)


def _ensure_availability(
    idx: pd.DatetimeIndex,
    sources: List[HeatSource],
) -> Dict[str, np.ndarray]:
    n = len(idx)
    out: Dict[str, np.ndarray] = {}
    for s in sources:
        if s.available is None:
            out[s.name] = np.ones(n, dtype=bool)
        else:
            a = np.asarray(s.available(idx), dtype=bool)
            if a.shape != (n,):
                raise ValueError(f"Availability for '{s.name}' must return shape {(n,)}")
            out[s.name] = a
    return out


# assumes you already have:
# - HeatSource dataclass with: name, kind ('hp'/'eh'/'wood'), q_max_th_kw, enabled, available, dispatch_mode,
#   q_fixed_th_kw, p_idle_el_kw, role ('controlled'/'forced')
# - _ensure_availability(idx, srcs) -> dict[name] -> bool array
# - cop_simple(tout, hp_family=...) -> COP float


def simulate_space_heating_shared(
    idx: pd.DatetimeIndex,
    tout_c: pd.Series,
    *,
    t_min_c: float,
    t_max_c: float,
    ua_kw_per_c: float,
    C_th_kwh_per_c: float,
    sources: List,  # List[HeatSource]
    Ti0_c: Optional[float] = None,
    hp_family: str = "air_to_water",
    strategy: Literal["band_control"] = "band_control",
    q_extra_th_kw: float | np.ndarray | pd.Series = 0.0,   # extra thermal heat to space (kWth)
) -> Dict[str, Any]:
    """
    Space-heating simulator (1-zone RC model) with multiple heat sources + optional extra thermal heat.

    Units:
      - Thermal powers Q_* are kWth
      - Electrical powers P_* are kWel
      - Temperatures in °C
      - Time step is 1 minute (freq="min")

    EXTRA HEAT (q_extra_th_kw):
      - Interpreted as *thermal* power injected to the zone (kWth), e.g., FC waste heat sent to SPACE.
      - Used only when the thermostat is calling for heat (Ti <= t_min ... until Ti >= t_max).
      - It reduces the remaining thermal demand before dispatching controllable devices.
      - It is included in the indoor temperature update.
      - It does NOT add electrical consumption.

    Returns a dict containing:
      Ti_C (°C): indoor temperature
      Q_req_raw_th_kw (kWth): raw required heat (before extra heat and before dispatch)
      Q_req_net_th_kw (kWth): remaining required heat after applying extra heat and forced heat
      Q_delivered_th_kw (kWth): total delivered heat including extra heat
      unmet_th_kw (kWth): remaining unmet heat after everything
      P_el_total_kw (kWel): total electrical consumption of active sources (HP + EH + idle)
      P_el_by_device_kw (dict[str, Series]): per-source electric power (kWel)
      Q_th_by_device_kw (dict[str, Series]): per-source thermal power (kWth), includes "fc_heat"
    """
    # -------- checks --------
    if len(idx) != len(tout_c):
        raise ValueError("idx and tout_c must have same length")
    if t_max_c <= t_min_c:
        raise ValueError("t_max_c must be > t_min_c")
    if ua_kw_per_c < 0:
        raise ValueError("ua_kw_per_c must be >= 0")
    if C_th_kwh_per_c <= 0:
        raise ValueError("C_th_kwh_per_c must be > 0")

    # enforce 1-minute step assumption (your whole model uses minute profiles)
    dt_h = 1.0 / 60.0

    tout = tout_c.reindex(idx).ffill().bfill() .to_numpy(dtype=float)
    n = len(idx)

    # -------- normalize extra heat to array length n (kWth) --------
    if isinstance(q_extra_th_kw, pd.Series):
        q_ex = q_extra_th_kw.reindex(idx).fillna(0.0).to_numpy(dtype=float)
    elif isinstance(q_extra_th_kw, (np.ndarray, list, tuple)):
        q_ex = np.asarray(q_extra_th_kw, dtype=float)
        if len(q_ex) != n:
            raise ValueError("q_extra_th_kw must have same length as idx")
    else:
        q_ex = np.full(n, float(q_extra_th_kw), dtype=float)

    q_ex = np.maximum(q_ex, 0.0)

    # -------- filter + availability --------
    srcs = [s for s in sources if getattr(s, "enabled", True) and float(getattr(s, "q_max_th_kw", 0.0)) > 0.0]
    srcs = sorted(srcs, key=lambda s: (str(getattr(s, "kind", "")), str(getattr(s, "name", ""))))
    avail = _ensure_availability(idx, srcs)

    # -------- initial temp --------
    t_set = 0.5 * (t_min_c + t_max_c)
    Ti = float(t_set if Ti0_c is None else Ti0_c)

    # -------- outputs arrays --------
    Ti_arr = np.zeros(n, dtype=float)

    Q_req_raw_arr = np.zeros(n, dtype=float)   # before extra heat
    Q_req_net_arr = np.zeros(n, dtype=float)   # after extra heat + forced heat removal
    Q_del_arr = np.zeros(n, dtype=float)       # delivered (including extra heat herinner)
    unmet_arr = np.zeros(n, dtype=float)

    Q_th_by: Dict[str, np.ndarray] = {s.name: np.zeros(n, dtype=float) for s in srcs}
    P_el_by: Dict[str, np.ndarray] = {s.name: np.zeros(n, dtype=float) for s in srcs}

    # virtual source to report extra heat
    fc_name = "fc_heat"
    Q_th_by[fc_name] = np.zeros(n, dtype=float)
    P_el_by[fc_name] = np.zeros(n, dtype=float)

    # -------- partitions --------
    forced_srcs = [s for s in srcs if str(getattr(s, "role", "controlled")).lower() == "forced"]
    hp_srcs = [s for s in srcs if str(getattr(s, "kind", "")).lower() == "hp"
               and str(getattr(s, "role", "controlled")).lower() == "controlled"]
    eh_backup_srcs = [s for s in srcs if str(getattr(s, "kind", "")).lower() == "eh"
                      and str(getattr(s, "role", "controlled")).lower() == "controlled"]

    # states for fixed-stage HPs
    hp_state = {
        s.name: (Ti <= t_min_c)
        for s in hp_srcs
        if str(getattr(s, "dispatch_mode", "modulating")).lower() == "fixed"
    }

    # backup EH hysteresis + timers (optional tuning)
    eh_state = {s.name: False for s in eh_backup_srcs}
    eh_on_delta_c = 0.10
    eh_off_delta_c = 0.30
    eh_min_on_min = 10
    eh_min_off_min = 10
    eh_on_timer = {s.name: 0 for s in eh_backup_srcs}
    eh_off_timer = {s.name: 0 for s in eh_backup_srcs}

    # band call-for-heat memory
    call_heat = (Ti <= t_min_c)

    # -------- main loop --------
    for k in range(n):
        # losses (kWth)
        q_loss_kw = ua_kw_per_c * (Ti - tout[k])

        # update call state (deadband with memory)
        if Ti <= t_min_c:
            call_heat = True
        elif Ti >= t_max_c:
            call_heat = False

        # RAW required heat to move toward top of band (kWth)
        if not call_heat:
            q_req_raw = 0.0
        else:
            q_raise_kw = (C_th_kwh_per_c * (t_max_c - Ti)) / dt_h
            q_req_raw = max(0.0, q_loss_kw + q_raise_kw)

        Q_req_raw_arr[k] = float(q_req_raw)

        # apply extra heat (kWth) only when heating requested
        q_req = float(q_req_raw)
        q_fc_avail = float(q_ex[k])
        if call_heat and q_req > 1e-12 and q_fc_avail > 1e-12:
            q_fc_used = min(q_fc_avail, q_req)
        else:
            q_fc_used = 0.0

        Q_th_by[fc_name][k] = q_fc_used
        q_req = max(0.0, q_req - q_fc_used)

        # -------- forced heat (wood / user-scheduled EH) --------
        q_forced = 0.0
        for s in forced_srcs:
            if not avail[s.name][k]:
                continue
            q_on = float(getattr(s, "q_fixed_th_kw", 0.0) or 0.0)
            if q_on <= 0.0:
                q_on = float(getattr(s, "q_max_th_kw", 0.0))
            q_take = min(float(getattr(s, "q_max_th_kw", 0.0)), q_on)
            Q_th_by[s.name][k] = q_take
            q_forced += q_take

        q_need_after_forced = max(0.0, q_req - q_forced)

        # Do not intentionally overshoot t_max in one minute with controlled devices
        q_cap_to_tmax = q_loss_kw + ((t_max_c - Ti) * C_th_kwh_per_c) / dt_h
        q_budget_ctrl = max(0.0, float(q_cap_to_tmax))

        # -------- HP main --------
        # For modulating HPs, we use the WeatherHP class which has proper proportional control
        # For fixed HPs, we use the existing on/off logic
        q_hp_total = 0.0
        q_need = float(q_need_after_forced)

        for s in hp_srcs:
            if q_need <= 1e-9 or q_budget_ctrl <= 1e-9:
                break
            if not avail[s.name][k]:
                continue

            mode = str(getattr(s, "dispatch_mode", "modulating")).lower()
            
            if mode == "modulating":
                # Use WeatherHP class for proper modulating control
                # Note: WeatherHP simulates the whole cycle, but we extract just this timestep
                # We'll use a simplified proportional control here that matches WeatherHP logic
                
                # Proportional control: Q = Q_base + Kp * error
                q_base = q_loss_kw  # heat needed to maintain current temp
                temp_error = t_set - Ti  # positive when too cold
                mod_kp = 1.0  # kW/°C gain (could be configurable via s.mod_kp if added)
                
                q_req = q_base + mod_kp * temp_error
                q_max = float(getattr(s, "q_max_th_kw", 0.0))
                
                # Clip to capacity and budget
                q_take = min(max(0.0, q_req), q_max, q_budget_ctrl)
                
                # Optional: minimum modulation fraction (prevent cycling at very low loads)
                mod_min_frac = 0.0  # could be configurable
                if q_take > 0.0 and mod_min_frac > 0.0:
                    q_take = max(q_take, mod_min_frac * q_max)
                    
            else:  # fixed mode
                st_on = bool(hp_state.get(s.name, False))
                if Ti <= t_min_c:
                    st_on = True
                elif Ti >= t_max_c:
                    st_on = False
                hp_state[s.name] = st_on
                if not st_on:
                    continue

                q_on = float(getattr(s, "q_fixed_th_kw", 0.0) or 0.0)
                if q_on <= 0.0:
                    q_on = float(getattr(s, "q_max_th_kw", 0.0))
                q_take = min(float(getattr(s, "q_max_th_kw", 0.0)), q_on, q_budget_ctrl)

            Q_th_by[s.name][k] = q_take
            q_hp_total += q_take
            q_need = max(0.0, q_need - q_take)
            q_budget_ctrl -= q_take

        q_need_after_hp = max(0.0, q_need_after_forced - q_hp_total)

        # -------- EH backup (only if HP insufficient) --------
        hp_exists = (len(hp_srcs) > 0)
        allow_eh_backup = (not hp_exists and q_need_after_forced > 1e-9) or (hp_exists and q_need_after_hp > 1e-9)

        q_need_for_eh = float(q_need_after_hp)

        for s in eh_backup_srcs:
            if q_need_for_eh <= 1e-9 or q_budget_ctrl <= 1e-9:
                break

            # timers
            if eh_on_timer[s.name] > 0:
                eh_on_timer[s.name] -= 1
            if eh_off_timer[s.name] > 0:
                eh_off_timer[s.name] -= 1

            if not avail[s.name][k]:
                eh_state[s.name] = False
                eh_on_timer[s.name] = 0
                continue

            st_on = bool(eh_state.get(s.name, False))

            if st_on:
                if eh_on_timer[s.name] <= 0:
                    if (Ti >= (t_min_c + eh_off_delta_c)) or (not allow_eh_backup) or (not call_heat):
                        st_on = False
                        eh_state[s.name] = False
                        eh_off_timer[s.name] = int(eh_min_off_min)
            else:
                if eh_off_timer[s.name] <= 0:
                    if allow_eh_backup and call_heat and (Ti <= (t_min_c - eh_on_delta_c)):
                        st_on = True
                        eh_state[s.name] = True
                        eh_on_timer[s.name] = int(eh_min_on_min)

            if not st_on:
                continue

            q_on = float(getattr(s, "q_fixed_th_kw", 0.0) or 0.0)
            if q_on <= 0.0:
                q_on = float(getattr(s, "q_max_th_kw", 0.0))

            q_take = min(float(getattr(s, "q_max_th_kw", 0.0)), q_on, q_need_for_eh, q_budget_ctrl)
            Q_th_by[s.name][k] = q_take
            q_need_for_eh = max(0.0, q_need_for_eh - q_take)
            q_budget_ctrl -= q_take

        # NET required after extra+forced (useful for debugging)
        Q_req_net_arr[k] = float(q_need_after_forced)

        # -------- electric conversion (kWel) for real sources only --------
        for s in srcs:
            q_take = float(Q_th_by[s.name][k])
            kind = str(getattr(s, "kind", "")).lower()

            if kind == "wood":
                P_el_by[s.name][k] = 0.0
            elif kind == "eh":
                P_el_by[s.name][k] = q_take
            elif kind == "hp":
                if q_take > 1e-9:
                    cop = float(cop_simple(tout[k], hp_family=hp_family))
                    P_el_by[s.name][k] = q_take / max(cop, 1e-6)
                else:
                    P_el_by[s.name][k] = float(getattr(s, "p_idle_el_kw", 0.0) or 0.0)
            else:
                raise ValueError(f"Unknown source kind '{kind}' for '{getattr(s, 'name', '??')}'")

        # delivered heat:
        q_delivered_ctrl = float(sum(Q_th_by[s.name][k] for s in srcs))
        q_delivered_total = q_delivered_ctrl + float(Q_th_by[fc_name][k])

        Q_del_arr[k] = q_delivered_total

        # unmet after EVERYTHING
        unmet = max(0.0, float(q_req_raw) - float(q_delivered_total))
        unmet_arr[k] = unmet

        # RC update uses total delivered heat
        dTi = ((q_delivered_total - q_loss_kw) / C_th_kwh_per_c) * dt_h
        Ti = Ti + dTi
        Ti_arr[k] = Ti

    # -------- build series outputs --------
    Ti_s = pd.Series(Ti_arr, index=idx, name="Ti_C")
    Q_req_raw_s = pd.Series(Q_req_raw_arr, index=idx, name="Q_req_raw_th_kw")
    Q_req_net_s = pd.Series(Q_req_net_arr, index=idx, name="Q_req_net_th_kw")
    Q_del_s = pd.Series(Q_del_arr, index=idx, name="Q_delivered_th_kw")
    unmet_s = pd.Series(unmet_arr, index=idx, name="unmet_th_kw")

    Q_th_by_series = {name: pd.Series(arr, index=idx, name=f"Qth_{name}_kw") for name, arr in Q_th_by.items()}
    P_el_by_series = {name: pd.Series(arr, index=idx, name=f"Pel_{name}_kw") for name, arr in P_el_by.items()}

    # total electrical (exclude fc_heat)
    P_total = pd.Series(0.0, index=idx, name="P_space_total_kw")
    for name, s in P_el_by_series.items():
        if name == fc_name:
            continue
        P_total = P_total.add(s, fill_value=0.0)

    return {
        # temperature
        "Ti_C": Ti_s,

        # thermal demand bookkeeping
        "Q_req_raw_th_kw": Q_req_raw_s,   # kWth (before extra heat)
        "Q_req_net_th_kw": Q_req_net_s,   # kWth (after extra heat + forced removal)
        "Q_delivered_th_kw": Q_del_s,     # kWth (including extra heat)
        "unmet_th_kw": unmet_s,           # kWth

        # electrical consumption (what goes into page2/page3 load curves)
        "P_el_total_kw": P_total,         # kWel

        # breakdowns (useful for debug plots)
        "P_el_by_device_kw": P_el_by_series,  # kWel
        "Q_th_by_device_kw": Q_th_by_series,  # kWth (includes fc_heat)
    }
