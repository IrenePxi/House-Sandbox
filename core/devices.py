from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd



# ---------- Fixed window devices ----------

# ---------- Weather-aware HP ----------

@dataclass
class WeatherHP:
    name: str = "heat_pump_weather"

    # Building + HP parameters
    ua_kw_per_c: float = 0.25          # heat loss coefficient [kW/°C]
    t_set_c: float = 21.0              # thermostat setpoint [°C]
    q_rated_kw: float = 6.0            # HP rated thermal output [kW]

    # COP parameters
    cop_at_7c: float = 3.2
    cop_a: float | None = None         # if both a,b given -> COP = a + b * Tout
    cop_b: float | None = None
    cop_min: float = 1.6
    cop_max: float = 4.2
    defrost: bool = True               # simple penalty below ~3°C

    # Thermostat + building dynamics
    hyst_band_c: float = 0.6           # thermostat hysteresis width [°C] (±0.3°C)
    C_th_kwh_per_c: float = 3.0        # thermal capacitance of building [kWh/°C]
    Ti0_c: float = 21.0                # initial indoor temp [°C]
    internal_gains_kw: float = 0.0     # constant internal gains (optional) [kW]
    
    p_off_kw: float = 0.05   # 50 W standby when OFF


    
    # Optional: minimum ON/OFF time (set both to 0 to disable)
    min_on_min: int = 0
    min_off_min: int = 0

    # NEW: operation mode & parameters
    mode: str = "onoff"        # "onoff" or "modulating"
    mod_kp: float = 1.0        # kW/°C proportional gain
    mod_min_frac: float = 0.0  # minimum modulation fraction (0..1)


    # ---- helpers ----
    def _cop_params(self):
        if self.cop_a is not None and self.cop_b is not None:
            return float(self.cop_a), float(self.cop_b)
        b = 0.05
        a = self.cop_at_7c - b * 7.0
        return a, b

    def _cop(self, Tout: np.ndarray) -> np.ndarray:
        a, b = self._cop_params()
        cop = np.clip(a + b * Tout, self.cop_min, self.cop_max)
        if self.defrost:
            cop = cop * np.where(Tout < 3.0, 0.92, 1.0)
        return np.maximum(cop, 1e-6)

    def _min_period_guard(self, state_hist: np.ndarray, state: bool, t: int, dt_min: float) -> bool:
        """Return True if we must keep current 'state' to respect min on/off time."""
        if self.min_on_min <= 0 and self.min_off_min <= 0:
            return False
        # how long (minutes) we've been in current state?
        run = 0
        i = t - 1
        while i >= 0 and state_hist[i] == state:
            run += 1
            i -= 1
        held_min = run * dt_min
        if state and self.min_on_min > 0:
            return held_min < self.min_on_min
        if (not state) and self.min_off_min > 0:
            return held_min < self.min_off_min
        return False

    # ---- main ----
    def series_kw(self, idx: pd.DatetimeIndex, tout_c: pd.Series) -> pd.Series:
        tout = pd.Series(tout_c, index=idx).astype(float)
        n = len(idx)
        if n == 0:
            return pd.Series(dtype=float, index=idx, name=self.name)

        if n > 1:
            dt_min = (idx[1] - idx[0]).total_seconds() / 60.0
        else:
            dt_min = 1.0
        dt_h = dt_min / 60.0

        T_out = tout.values
        cop   = self._cop(T_out)

        Ti = np.zeros(n, dtype=float)
        P  = np.zeros(n, dtype=float)
        state_hist = np.zeros(n, dtype=bool)

        Ti[0] = float(self.Ti0_c)

        low  = self.t_set_c - self.hyst_band_c / 2.0
        high = self.t_set_c + self.hyst_band_c / 2.0

        hp_on = Ti[0] < low

        # simple internal gains profile
        hours = pd.Index(idx).hour.values if isinstance(idx, pd.DatetimeIndex) else np.zeros(n)
        G = 0.2 + 0.2 * ((hours >= 9) & (hours <= 20)).astype(float)

        for k in range(1, n):
            heat_loss = self.ua_kw_per_c * (Ti[k-1] - T_out[k])
            if self.mode == "onoff":
                # --- your original thermostat logic ---
                desired_on = hp_on
                if Ti[k-1] < low:
                    desired_on = True
                elif Ti[k-1] > high:
                    desired_on = False

                if self._min_period_guard(state_hist, hp_on, k, dt_min):
                    desired_on = hp_on

                hp_on = desired_on
                state_hist[k] = hp_on

                Q_hp = self.q_rated_kw if hp_on else 0.0

            else:  # --- modulating mode ---
                Q_base = heat_loss - G[k]          # hold temperature
                err    = self.t_set_c - Ti[k-1]    # °C

                Q_req = Q_base + self.mod_kp * err
                Q_hp  = np.clip(Q_req, 0.0, self.q_rated_kw)

                if Q_hp > 0.0 and self.mod_min_frac > 0.0:
                    Q_hp = max(Q_hp, self.mod_min_frac * self.q_rated_kw)

                hp_on = Q_hp > 0.0
                state_hist[k] = hp_on

            # common part: power + temperature update
            P[k] = (Q_hp / cop[k]) if Q_hp > 0.0 else self.p_off_kw
            dTi = (Q_hp + G[k] - heat_loss) / max(self.C_th_kwh_per_c, 1e-6) * dt_h
            Ti[k] = Ti[k-1] + dTi

        sP  = pd.Series(P,  index=idx, name="P_HP_kW")
        sTi = pd.Series(Ti, index=idx, name="Ti_C")

        return sP, sTi

  


# ---------- Weather-aware HP ----------

@dataclass
class WeatherELheater:
    name: str = "EL_heater_weather"

    # Building + HP parameters
    ua_kw_per_c: float = 0.25          # heat loss coefficient [kW/°C]
    t_set_c: float = 21.0              # thermostat setpoint [°C]
    q_rated_kw: float = 6.0            # HP rated thermal output [kW]


    # Thermostat + building dynamics
    hyst_band_c: float = 0.6           # thermostat hysteresis width [°C] (±0.3°C)
    C_th_kwh_per_c: float = 3.0        # thermal capacitance of building [kWh/°C]
    Ti0_c: float = 21.0                # initial indoor temp [°C]
    internal_gains_kw: float = 0.0     # constant internal gains (optional) [kW]
    
    p_off_kw: float = 0.00   # 0 W standby when OFF


    
    # Optional: minimum ON/OFF time (set both to 0 to disable)
    min_on_min: int = 0
    min_off_min: int = 0

    # ---- helpers ----


    def _min_period_guard(self, state_hist: np.ndarray, state: bool, t: int, dt_min: float) -> bool:
        """Return True if we must keep current 'state' to respect min on/off time."""
        if self.min_on_min <= 0 and self.min_off_min <= 0:
            return False
        # how long (minutes) we've been in current state?
        run = 0
        i = t - 1
        while i >= 0 and state_hist[i] == state:
            run += 1
            i -= 1
        held_min = run * dt_min
        if state and self.min_on_min > 0:
            return held_min < self.min_on_min
        if (not state) and self.min_off_min > 0:
            return held_min < self.min_off_min
        return False

    # ---- main ----
    def series_kw(self, idx: pd.DatetimeIndex, tout_c: pd.Series) -> pd.Series:
        # Align inputs
        tout = pd.Series(tout_c, index=idx).astype(float)
        n = len(idx)
        if n == 0:
            return pd.Series(dtype=float, index=idx, name=self.name)

        # time step (minutes / hours)
        if n > 1:
            dt_min = (idx[1] - idx[0]).total_seconds() / 60.0
        else:
            dt_min = 1.0
        dt_h = dt_min / 60.0

        T_out = tout.values
        cop = 1

        # Storage for results
        Ti = np.zeros(n, dtype=float)
        P  = np.zeros(n, dtype=float)        # electrical power [kW]
        state_hist = np.zeros(n, dtype=bool) # ON/OFF for min-period guard

        # initial indoor temp near setpoint
        Ti[0] = float(self.Ti0_c)

        # thermostat thresholds
        low  = self.t_set_c - self.hyst_band_c/2.0
        high = self.t_set_c + self.hyst_band_c/2.0

        hp_on = Ti[0] < low

        # just before the loop in WeatherHP.series_kw(...)
        # Build a small daytime internal-gains profile (0.2 kW at night → 0.4 kW mid-day)
        hours = pd.Index(idx).hour.values if isinstance(idx, pd.DatetimeIndex) else np.zeros(n)
        G = 0.2 + 0.2 * ( (hours >= 9) & (hours <= 20) ).astype(float)  # 0.4 kW from 09–20


        # step through time
        for k in range(1, n):
            # thermostat with optional min-on/off guard
            desired_on = hp_on
            if Ti[k-1] < low:
                desired_on = True
            elif Ti[k-1] > high:
                desired_on = False

            # Enforce minimum ON/OFF if requested
            if self._min_period_guard(state_hist, hp_on, k, dt_min):
                desired_on = hp_on

            hp_on = desired_on
            state_hist[k] = hp_on

            # then in the loop, replace self.internal_gains_kw with G[k]
            Q_hp = self.q_rated_kw if hp_on else 0.0
            P[k]  = (Q_hp) if hp_on else self.p_off_kw
            heat_loss = self.ua_kw_per_c * (Ti[k-1] - T_out[k])
            dTi = (Q_hp + G[k] - heat_loss) / max(self.C_th_kwh_per_c, 1e-6) * dt_h
            Ti[k] = Ti[k-1] + dTi

        sP  = pd.Series(P,  index=idx, name="P_EH_kW")
        sTi = pd.Series(Ti, index=idx, name="Ti_C")

        return sP, sTi


@dataclass
class WeatherHotTub:
    name: str = "hot_tub_weather"

    target_c: float = 38.0       # water temp during use
    idle_c: float = 32.0         # keep-warm temperature
    heater_kw: float = 3.0       # heater power
    water_l: float = 800.0       # typical 600–1200 L
    ua_kw_per_c: float = 0.02    # heat loss coefficient

    # NEW: ambient handling
    indoor_ambient_c: float = 21.0
    use_outdoor_for_ambient: bool = False  # False for hot tub, True for pool

    sessions: list | None = None  # [{ "start": time, "duration_min": int }]

    def series_kw(self, idx: pd.DatetimeIndex, tout_minute: pd.Series):
        """Simulate hot-tub / pool heater power for one day."""
        if self.sessions is None:
            self.sessions = []

        if len(idx) == 0:
            return (
                pd.Series(dtype=float, index=idx, name="P_hot_tub_kW"),
                pd.Series(dtype=float, index=idx, name="T_water_C"),
            )

        # time base
        n = len(idx)
        if n > 1:
            dt_min = (idx[1] - idx[0]).total_seconds() / 60.0
        else:
            dt_min = 1.0
        dt_h = dt_min / 60.0

        # ambient (indoor or outdoor)
        if self.use_outdoor_for_ambient and tout_minute is not None:
            tout = pd.Series(tout_minute, index=idx).astype(float)
            T_amb = tout.values
        else:
            T_amb = np.full(n, float(self.indoor_ambient_c), dtype=float)

        # thermal capacity [kWh/°C]
        C_kwh_per_c = max(self.water_l * 1.16 / 1000.0, 1e-6)

        # minutes from midnight
        rel_min = idx.hour * 60 + idx.minute

        # --------------------------------------------------
        # 1) Build in_use mask (as before)
        # --------------------------------------------------
        in_use = np.zeros(n, dtype=bool)
        for sess in self.sessions:
            start = sess.get("start")
            dur = sess.get("duration_min", 0)
            if start is None or dur <= 0:
                continue
            s_min = start.hour * 60 + start.minute
            e_min = min(s_min + dur, 1440)
            mask = (rel_min >= s_min) & (rel_min < e_min)
            in_use |= mask

        # --------------------------------------------------
        # 2) Estimate preheat time from idle → target
        # --------------------------------------------------
        deltaT = max(self.target_c - self.idle_c, 0.0)
        T_amb_ref = float(np.mean(T_amb))
        if deltaT <= 0:
            preheat_min = 0.0
        else:
            # net heating power near idle
            q_net = self.heater_kw - self.ua_kw_per_c * (self.idle_c - T_amb_ref)
            if q_net <= 0.0:
                # heater too weak → no meaningful preheat estimate
                preheat_min = 0.0
            else:
                dTdt_h = q_net / C_kwh_per_c            # °C per hour
                preheat_min = 60.0 * deltaT / max(dTdt_h, 1e-6)  # minutes

        # --------------------------------------------------
        # 3) Preheat mask before each session
        # --------------------------------------------------
        preheat_mask = np.zeros(n, dtype=bool)
        for sess in self.sessions:
            start = sess.get("start")
            dur = sess.get("duration_min", 0)
            if start is None or dur <= 0 or preheat_min <= 0:
                continue
            s_min = start.hour * 60 + start.minute
            # start preheat this many minutes before session
            start_ph = max(int(round(s_min - preheat_min)), 0)
            mask_ph = (rel_min >= start_ph) & (rel_min < s_min)
            preheat_mask |= mask_ph

        # --------------------------------------------------
        # 4) Simulate
        # --------------------------------------------------
        T = np.zeros(n, dtype=float)
        P = np.zeros(n, dtype=float)
        T[0] = self.idle_c
        heater_on = False
        hyst = 0.4  # hysteresis around setpoint

        for k in range(1, n):
            # decide setpoint
            if in_use[k] or preheat_mask[k]:
                setpoint = self.target_c
            else:
                setpoint = self.idle_c

            # hysteresis around setpoint
            if T[k - 1] < setpoint - hyst:
                heater_on = True
            elif T[k - 1] > setpoint + hyst:
                heater_on = False

            q_in = self.heater_kw if heater_on else 0.0
            P[k] = q_in

            # thermal balance vs ambient
            dT = (q_in - self.ua_kw_per_c * (T[k - 1] - T_amb[k])) * dt_h / C_kwh_per_c
            T[k] = T[k - 1] + dT

        sP = pd.Series(P, index=idx, name="P_tub_kW")
        sT = pd.Series(T, index=idx, name="T_water_C")
        return sP, sT


from dataclasses import dataclass
import numpy as np
import pandas as pd

@dataclass
class DHWTank:
    name: str = "dhw_tank"

    volume_l: float = 200.0
    t_set_c: float = 50.0
    hyst_band_c: float = 5.0
    ua_kw_per_c: float = 0.02
    p_el_kw: float = 2.0
    p_off_kw: float = 0.01

    T_cold_c: float = 10.0
    T_amb_c: float = 20.0
    Ti0_c: float = 50.0

    min_on_min: int = 0
    min_off_min: int = 0

    usage_level: str = "Medium"  # "Low" / "Medium" / "High"

    # ---- constants ----
    _KWH_PER_C_PER_L: float = 0.001163  # 1 L water ≈ 0.001163 kWh/°C

    def _C_kwh_per_c(self) -> float:
        return max(self.volume_l * self._KWH_PER_C_PER_L, 1e-6)

    def _dt_minutes(self, idx: pd.DatetimeIndex) -> float:
        # You said it's always 1 min. We enforce that, but keep a safe fallback.
        if len(idx) < 2:
            return 1.0
        dt_min = (idx[1] - idx[0]).total_seconds() / 60.0
        # hard guard: if it isn't ~1 minute, still run but don't silently lie
        if not (0.9 <= dt_min <= 1.1):
            raise ValueError(f"DHW tank expects 1-min index, got dt_min={dt_min:.3f}")
        return 1.0

    def _min_period_guard(self, state_hist: np.ndarray, state: bool, t: int, dt_min: float) -> bool:
        if self.min_on_min <= 0 and self.min_off_min <= 0:
            return False
        run = 0
        i = t - 1
        while i >= 0 and state_hist[i] == state:
            run += 1
            i -= 1
        held_min = run * dt_min
        if state and self.min_on_min > 0:
            return held_min < self.min_on_min
        if (not state) and self.min_off_min > 0:
            return held_min < self.min_off_min
        return False

    def _build_draw_profile_lpm(self, idx: pd.DatetimeIndex) -> np.ndarray:
        n = len(idx)
        draw_lpm = np.zeros(n, dtype=float)
        if n == 0:
            return draw_lpm

        if self.usage_level == "Low":
            events = [(7, 30, 40.0), (21, 30, 40.0)]
        elif self.usage_level == "High":
            events = [(7, 45, 60.0), (12, 20, 30.0), (19, 45, 80.0)]
        else:
            events = [(7, 30, 50.0), (19, 30, 60.0)]

        minutes_of_day = idx.hour * 60 + idx.minute
        for start_hour, dur_min, vol_L in events:
            start_min = start_hour * 60
            end_min = start_min + dur_min
            mask = (minutes_of_day >= start_min) & (minutes_of_day < end_min)
            if mask.any():
                n_steps = int(mask.sum())
                if n_steps > 0:
                    # IMPORTANT: this is L per MINUTE step (because dt is 1 min)
                    draw_per_step_L = vol_L / float(n_steps)  # liters each minute
                    draw_lpm[mask] += draw_per_step_L  # since dt=1min, L/min == L/step
        return draw_lpm

    def build_Q_draw_external_kw(self, idx: pd.DatetimeIndex, *, T_use_c: float = 45.0) -> pd.Series:
        """
        External/service DHW thermal demand (kWth), independent of tank temperature.
        Computed from draw schedule + fixed service temperature T_use_c.
        """
        n = len(idx)
        if n == 0:
            return pd.Series(dtype=float, index=idx, name="Q_draw_th_kW")

        dt_min = self._dt_minutes(idx)  # enforces 1 min
        dt_h = dt_min / 60.0

        draw_lpm = self._build_draw_profile_lpm(idx)  # L/min (== L/step here)
        V_draw_L = draw_lpm * dt_min  # liters in this step (== draw_lpm)

        dT = max(0.0, float(T_use_c) - float(self.T_cold_c))
        E_draw_kwh = V_draw_L * self._KWH_PER_C_PER_L * dT
        Q_draw_kw = E_draw_kwh / max(dt_h, 1e-9)
        return pd.Series(Q_draw_kw, index=idx, name="Q_draw_th_kW")

    def series_kw(
        self,
        idx: pd.DatetimeIndex,
        tout_c: pd.Series | None = None,
        q_extra_kw: float | np.ndarray | pd.Series = 0.0,
        t_cap_c: float | None = None,
    ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        """
        Tank simulation driven by:
          - heater thermostat
          - losses to ambient
          - exogenous volume draw schedule (mixing)
          - optional extra heat q_extra_kw (FC waste heat)

        Returns:
          Q_heater_th_kW, T_tank_C, Q_extra_used_kW, Q_extra_spill_kW
        """
        n = len(idx)
        if n == 0:
            z = pd.Series(dtype=float, index=idx)
            return (
                z.rename("Q_heater_th_kW"),
                z.rename("T_tank_C"),
                z.rename("Q_extra_used_kW"),
                z.rename("Q_extra_spill_kW"),
            )

        # normalize q_extra to ndarray
        if isinstance(q_extra_kw, pd.Series):
            q_ex = q_extra_kw.reindex(idx).fillna(0.0).to_numpy(dtype=float)
        elif isinstance(q_extra_kw, (np.ndarray, list, tuple)):
            q_ex = np.asarray(q_extra_kw, dtype=float)
            if len(q_ex) != n:
                raise ValueError("q_extra_kw must have same length as idx")
        else:
            q_ex = np.full(n, float(q_extra_kw), dtype=float)
        q_ex = np.maximum(q_ex, 0.0)

        dt_min = self._dt_minutes(idx)  # enforces 1 min
        dt_h = dt_min / 60.0

        T_amb = np.full(n, float(self.T_amb_c), dtype=float)
        draw_lpm = self._build_draw_profile_lpm(idx)  # exogenous volume schedule
        C = float(self._C_kwh_per_c())

        T = np.zeros(n, dtype=float)
        Q_heater = np.zeros(n, dtype=float)
        q_used = np.zeros(n, dtype=float)
        q_spill = np.zeros(n, dtype=float)
        state_hist = np.zeros(n, dtype=bool)

        Tmin = float(self.t_set_c - self.hyst_band_c / 2.0)
        Tmax = float(self.t_set_c + self.hyst_band_c / 2.0)
        if Tmax <= Tmin:
            Tmax = Tmin + 1.0

        cap = float(t_cap_c) if t_cap_c is not None else Tmax
        cap = max(cap, Tmax)

        # init
        T[0] = float(self.Ti0_c)
        heater_on = bool(T[0] < Tmin)
        state_hist[0] = heater_on
        Q_heater[0] = float(self.p_el_kw) if heater_on else 0.0
        q_used[0] = 0.0
        q_spill[0] = float(q_ex[0])

        for k in range(1, n):
            # thermostat
            if T[k - 1] < Tmin:
                desired_on = True
            elif T[k - 1] >= Tmax:
                desired_on = False
            else:
                desired_on = heater_on

            if self._min_period_guard(state_hist, heater_on, k, dt_min):
                desired_on = heater_on

            heater_on = bool(desired_on)
            state_hist[k] = heater_on

            Qh = float(self.p_el_kw) if heater_on else 0.0
            Q_loss = float(self.ua_kw_per_c * (T[k - 1] - T_amb[k]))

            # extra heat absorption with cap (pre-draw)
            Q_in_full = Qh + float(q_ex[k])
            T_pre_full = T[k - 1] + ((Q_in_full - Q_loss) / C) * dt_h

            if T_pre_full > cap:
                dT_allow = max(0.0, cap - T[k - 1])
                Q_net_allow = (dT_allow / max(dt_h, 1e-9)) * C
                Q_in_allow = Q_net_allow + Q_loss

                q_extra_used = max(0.0, Q_in_allow - Qh)
                q_extra_used = min(q_extra_used, float(q_ex[k]))

                q_used[k] = q_extra_used
                q_spill[k] = float(q_ex[k]) - q_extra_used

                Q_in_used = Qh + q_extra_used
                T_pre = min(cap, T[k - 1] + ((Q_in_used - Q_loss) / C) * dt_h)
            else:
                q_used[k] = float(q_ex[k])
                q_spill[k] = 0.0
                T_pre = T_pre_full

            # physical mixing (volume draw) AFTER heating
            if draw_lpm[k] > 0.0:
                V_draw = float(draw_lpm[k] * dt_min)  # liters this step (== draw_lpm)
                f = min(V_draw / max(self.volume_l, 1e-9), 0.9)
                T[k] = (1.0 - f) * T_pre + f * float(self.T_cold_c)
            else:
                T[k] = T_pre

            Q_heater[k] = Qh

        return (
            pd.Series(Q_heater, index=idx, name="Q_heater_th_kW"),
            pd.Series(T, index=idx, name="T_tank_C"),
            pd.Series(q_used, index=idx, name="Q_extra_used_kW"),
            pd.Series(q_spill, index=idx, name="Q_extra_spill_kW"),
        )
