# services/P3_ems_globalopt_service.py
# services/P3_ems_globalopt_service.py
from __future__ import annotations

import numpy as np
import pandas as pd

from core.FCcontrol import fc_cost_rate_dkk_per_h
from core.FCcontrol import normalize_fc_cfg  # we'll add this
try:
    import gurobipy as gp
    from gurobipy import GRB
    _HAS_GUROBI = True
except Exception:
    _HAS_GUROBI = False

try:
    import pulp
    _HAS_PULP = True
except ImportError:
    _HAS_PULP = False

def _pulp_add_pwl(prob: pulp.LpProblem, x_var: pulp.LpVariable, y_var: pulp.LpVariable, x_pts: list[float], y_pts: list[float], name: str):
    """
    Add Piecewise Linear (PWL) constraint to PuLP using SOS2 (Special Ordered Sets of type 2).
    """
    n = len(x_pts)
    indices = range(n)
    
    # Weights for each breakpoint
    weights = [pulp.LpVariable(f"w_{name}_{i}", lowBound=0, upBound=1) for i in indices]
    
    # 1. Sum of weights = 1
    prob += pulp.lpSum(weights) == 1, f"sum_w_{name}"
    
    # 2. X = sum(weights * x_pts)
    prob += pulp.lpSum(weights[i] * x_pts[i] for i in indices) == x_var, f"x_link_{name}"
    
    # 3. Y = sum(weights * y_pts)
    prob += pulp.lpSum(weights[i] * y_pts[i] for i in indices) == y_var, f"y_link_{name}"
    
    # 4. SOS2: at most two weights non-zero, and MUST be adjacent
    # PuLP's SOS2 support depends on the solver. 
    # For a general MILP implementation, we use binary variables.
    z = [pulp.LpVariable(f"z_{name}_{i}", cat=pulp.LpBinary) for i in range(n - 1)]
    prob += pulp.lpSum(z) == 1, f"one_interval_{name}"
    
    for i in indices:
        if i == 0:
            prob += weights[i] <= z[0], f"w_bound_{name}_{i}"
        elif i == n - 1:
            prob += weights[i] <= z[n - 2], f"w_bound_{name}_{i}"
        else:
            prob += weights[i] <= z[i-1] + z[i], f"w_bound_{name}_{i}"



def _get_configured_pulp_solver():
    """
    Returns a configured PuLP solver optimized for Cloud execution.
    Prioritizes HiGHS (if available) with 60s time limit and 1% gap.
    Falls back to CBC with same limits.
    """
    available = pulp.listSolvers(onlyAvailable=True)
    
    # 1. HiGHS (Preferred)
    if 'HiGHS' in available:
        # PuLP 2.8+ supports these standard args for HiGHS
        # If older pulp, it might ignore them, but usually safe.
        return pulp.getSolver('HiGHS', timeLimit=60.0, gapRel=0.01, msg=False)
    
    # 2. CBC (Fallback)
    # PuliP_CBC_CMD supports timeLimit (sec) and gapRel (ratio)
    return pulp.PULP_CBC_CMD(timeLimit=60, gapRel=0.01, msg=False)


def _as_series(x, idx, name=None, fill=0.0):
    if x is None:
        return pd.Series(fill, index=idx, name=name)
    if isinstance(x, pd.Series):
        return x.reindex(idx).fillna(fill).rename(name or x.name)
    if isinstance(x, (np.ndarray, list, tuple)):
        a = np.asarray(x, dtype=float)
        if len(a) != len(idx):
            raise ValueError(f"{name or 'series'} length mismatch with idx")
        return pd.Series(a, index=idx, name=name)
    return pd.Series(float(x), index=idx, name=name)


def _make_breakpoints(x0: float, x1: float, n: int) -> np.ndarray:
    n = int(max(2, n))
    return np.linspace(float(x0), float(x1), n)


def solve_stepA_relaxed_global_opt(
    *,
    idx: pd.DatetimeIndex,

    # ---- ELECTRIC inputs ----
    load_nonthermal_kw: pd.Series,     # kW, excludes space/dhw/leisure electric (those are modeled below)
    pv_avail_kw: pd.Series,            # kW
    price_el: pd.Series | None,        # DKK/kWh

    # ---- THERMAL driving data ----
    tout_c: pd.Series,                # °C, outdoor temperature
    dhw_draw_th_kw: pd.Series | None = None,   # kWth, hot water usage draw (can be 0)
    leisure_el_kw: pd.Series | None = None,    # kW electric leisure load (kept as its own category, fixed here)

    # ---- CONFIGS ----
    batt_cfg: dict | None = None,
    fc_cfg: dict | None = None,
    space_cfg: dict | None = None,
    dhw_cfg: dict | None = None,

    dt_h: float = 1.0/60.0,
    objective: str = "cost",  # only "cost" supported here

    enable_fc: bool = True,
    enable_batt: bool = True,

    # PWL resolution
    n_fc_pwl: int = 12,
    n_fc_heat_pwl: int = 12,

    solver_backend: str | None = None,  # "gurobi" or "pulp"

) -> dict:
    """
    Step A v2 (benchmark):
      - strict electric balance (no dump, no export)
      - PV can be curtailed
      - FC has min power + min on/off
      - FC fuel cost uses fc_cost_rate_dkk_per_h(P_fc_kw, price_ch3oh) via PWL
      - Space + DHW are relaxed thermal states (continuous), so no thermostat cycling
      - Leisure stays as its own category (passed as fixed electric series for now)
      - FC waste heat can go to DHW / Space / Spill depending on UI option

    Returns (Series):
      p_grid_kw, p_pv_used_kw, p_bat_kw, soc_kwh
      p_fc_kw, k_fc
      p_space_el_kw, p_dhw_el_kw, p_leisure_el_kw, p_thermal_el_kw
      Ti_C, Ttank_C
      q_fc_avail_kw, q_fc_to_dhw_kw, q_fc_to_space_kw, q_fc_spill_kw
    """
    if solver_backend is None:
        if _HAS_GUROBI:
            solver_backend = "gurobi"
        elif _HAS_PULP:
            solver_backend = "pulp"
        else:
            raise RuntimeError("No solver backend (gurobi or pulp) available.")

    if solver_backend == "gurobi" and not _HAS_GUROBI:
        raise RuntimeError("Gurobi backend requested but gurobipy not available.")
    if solver_backend == "pulp" and not _HAS_PULP:
        raise RuntimeError("PuLP backend requested but pulp not available.")

    if objective != "cost":
        raise ValueError("Step A supports only objective='cost' for now.")

    n = len(idx)
    if n == 0:
        raise ValueError("idx is empty")

    batt_cfg = batt_cfg or {}
    fc_cfg = normalize_fc_cfg(fc_cfg)

    space_cfg = space_cfg or {}
    dhw_cfg = dhw_cfg or {}

    enable_fc = bool(enable_fc) and (fc_cfg is not None)
    enable_batt = bool(enable_batt) and (batt_cfg is not None) and float(batt_cfg.get("E_kWh", 0.0)) > 0.0

    # --------------------------
    # align / sanitize inputs
    # --------------------------
    L = _as_series(load_nonthermal_kw, idx, "load_nonthermal_kw", 0.0).clip(lower=0.0)
    PV = _as_series(pv_avail_kw, idx, "pv_avail_kw", 0.0).clip(lower=0.0)
    Tout = _as_series(tout_c, idx, "Tout_C", 5.0)

    if price_el is None:
        c_el = pd.Series(0.0, index=idx, name="price_el")
    else:
        c_el = _as_series(price_el, idx, "price_el", 0.0).clip(lower=0.0)

    Q_dhw_use = _as_series(dhw_draw_th_kw, idx, "dhw_draw_th_kw", 0.0).clip(lower=0.0)
    P_leisure = _as_series(leisure_el_kw, idx, "p_leisure_el_kw", 0.0).clip(lower=0.0)

    # --------------------------
    # battery params
    # --------------------------
    if enable_batt:
        E_kWh = float(batt_cfg.get("E_kWh", 0.0))
        soc0_pct = float(batt_cfg.get("soc_init", 60.0))
        soc_min_pct = float(batt_cfg.get("soc_min", 10.0))
        soc_max_pct = float(batt_cfg.get("soc_max", 100.0))
        soc0 = (soc0_pct/100.0) * E_kWh
        soc_min = (soc_min_pct/100.0) * E_kWh
        soc_max = (soc_max_pct/100.0) * E_kWh
        P_ch_max = float(batt_cfg.get("P_ch_max_kW", 0.0))
        P_dis_max = float(batt_cfg.get("P_dis_max_kW", 0.0))
        eta_ch = float(batt_cfg.get("eta_ch", 1.0))
        eta_dis = float(batt_cfg.get("eta_dis", 1.0))
        eta_ch = max(1e-6, min(1.0, eta_ch))
        eta_dis = max(1e-6, min(1.0, eta_dis))
    else:
        E_kWh = soc0 = soc_min = soc_max = 0.0
        P_ch_max = P_dis_max = 0.0
        eta_ch = eta_dis = 1.0

    # --------------------------
    # FC params
    # --------------------------
    if enable_fc:
        Pmin_W = float(fc_cfg["Pmin_W"])
        Prated_W = float(fc_cfg["Prated_W"])
        heat_priority = str(fc_cfg.get("heat_priority_norm", "dhw"))
        min_on_min = int(fc_cfg["min_on_min"])
        min_off_min = int(fc_cfg["min_off_min"])

        P_fc_min = Pmin_W / 1000.0
        P_fc_max = Prated_W / 1000.0

        dt_min = max(1, int(round(dt_h * 60)))
        N_on = max(1, int(round(min_on_min / dt_min)))
        N_off = max(1, int(round(min_off_min / dt_min)))

        price_ch3oh = float(fc_cfg.get("price_ch3oh", fc_cfg.get("Price_ch3oh", 0.0)))

        use_heat = bool(fc_cfg.get("use_waste_heat", False))
    else:
        P_fc_min = P_fc_max = 0.0
        N_on = N_off = 1
        price_ch3oh = 0.0
        use_heat = False
        heat_priority = "dhw"

    # --------------------------
    # relaxed thermal params (space)
    # --------------------------
    # Defaults: choose safe values if missing (but you should pass real ones)
    t_in_min = float(space_cfg.get("t_min_c", 20.0))
    t_in_max = float(space_cfg.get("t_max_c", 22.0))
    if t_in_max <= t_in_min:
        t_in_max = t_in_min + 0.5

    UA = float(space_cfg.get("ua_kw_per_c", 0.25))  # kW/°C
    Cth = float(space_cfg.get("C_th_kwh_per_c", 3.0))  # kWh/°C
    Ti0 = float(space_cfg.get("Ti0_c", 0.5*(t_in_min+t_in_max)))

    # capacities
    Q_sp_hp_max = float(space_cfg.get("hp_q_th_kw", 8.0)) * float(space_cfg.get("hp_n", 1))
    Q_sp_eh_max = float(space_cfg.get("eh_q_th_kw", 6.0)) * float(space_cfg.get("eh_n", 1))

    # COP for space HP (simple linear bounded curve like your thermal_share.py cop_simple)
    # Keep it deterministic + consistent.
    tout_vals = Tout.to_numpy(dtype=float)
    cop_sp = 2.0 + (tout_vals + 7.0) * (1.0/14.0)
    cop_sp = np.clip(cop_sp, 1.4, 3.8)

    # --------------------------
    # relaxed thermal params (DHW tank)
    # --------------------------
    t_tank_min = float(dhw_cfg.get("t_min_c", 45.0))
    t_tank_max = float(dhw_cfg.get("t_max_c", 55.0))
    if t_tank_max <= t_tank_min:
        t_tank_max = t_tank_min + 1.0
    Ttank0 = float(dhw_cfg.get("T0_c", 0.5*(t_tank_min+t_tank_max)))

    # simple tank model params (you can refine later)
    C_tank = float(dhw_cfg.get("C_th_kwh_per_c", 6.0))   # kWh/°C
    UA_tank = float(dhw_cfg.get("ua_kw_per_c", 0.08))    # kW/°C
    T_amb = float(dhw_cfg.get("T_amb_c", 21.0))

    Q_dhw_hp_max = float(dhw_cfg.get("hp_q_th_kw", 1.5))   # kWth
    Q_dhw_eh_max = float(dhw_cfg.get("eh_q_th_kw", float(dhw_cfg.get("p_el_kw", 2.0))))  # kWth

    cop_dhw_val = float(dhw_cfg.get("cop_dhw", 2.5))
    cop_dhw_val = max(1.0, cop_dhw_val)  # keep sane

    # --------------------------
    # Build model
    # --------------------------
    if solver_backend == "gurobi":
        m = gp.Model("stepA_relaxed_global_v2")
        m.Params.OutputFlag = 1          # show log while debugging
        m.Params.TimeLimit = 30          # seconds (choose 30–120)
        m.Params.MIPGap = 0.005          # 0.5% gap is usually plenty for EMS
        m.Params.MIPFocus = 1            # focus on finding feasible solutions fast
        m.Params.Heuristics = 0.2
        m.Params.RINS = 10
    else:
        m = pulp.LpProblem("stepA_relaxed_global_v2", pulp.LpMinimize)


    # ---- Electric vars ----
    if solver_backend == "gurobi":
        p_grid = m.addVars(n, lb=0.0, name="p_grid")
        p_pv_used = m.addVars(n, lb=0.0, name="p_pv_used")
    else:
        p_grid = [pulp.LpVariable(f"p_grid_{t}", lowBound=0) for t in range(n)]
        p_pv_used = [pulp.LpVariable(f"p_pv_used_{t}", lowBound=0) for t in range(n)]

    # Battery vars (split charge/discharge for efficiency)
    if enable_batt:
        if solver_backend == "gurobi":
            p_ch = m.addVars(n, lb=0.0, ub=P_ch_max, name="p_bat_ch")
            p_dis = m.addVars(n, lb=0.0, ub=P_dis_max, name="p_bat_dis")
            soc = m.addVars(n, lb=soc_min, ub=soc_max, name="soc")
            # reporting var (signed): +dis, -ch
            p_bat = m.addVars(n, lb=-P_ch_max, ub=P_dis_max, name="p_bat")
        else:
            p_ch = [pulp.LpVariable(f"p_bat_ch_{t}", lowBound=0, upBound=P_ch_max) for t in range(n)]
            p_dis = [pulp.LpVariable(f"p_bat_dis_{t}", lowBound=0, upBound=P_dis_max) for t in range(n)]
            soc = [pulp.LpVariable(f"soc_{t}", lowBound=soc_min, upBound=soc_max) for t in range(n)]
            p_bat = [pulp.LpVariable(f"p_bat_{t}", lowBound=-P_ch_max, upBound=P_dis_max) for t in range(n)]
    else:
        p_ch = p_dis = soc = p_bat = None

    # Fuel cell vars
    if enable_fc:
        if solver_backend == "gurobi":
            p_fc = m.addVars(n, lb=0.0, ub=P_fc_max, name="p_fc")
            k_fc = m.addVars(n, vtype=GRB.BINARY, name="k_fc")
            u_on = m.addVars(n, vtype=GRB.BINARY, name="u_on")
            d_off = m.addVars(n, vtype=GRB.BINARY, name="d_off")

            # PWL fuel cost rate (DKK/h)
            c_fc_rate = m.addVars(n, lb=0.0, name="c_fc_rate_dkk_per_h")

            # PWL FC heat availability (kWth), only used if heat reuse enabled
            q_fc_avail = m.addVars(n, lb=0.0, name="q_fc_avail_th_kw")
        else:
            p_fc = [pulp.LpVariable(f"p_fc_{t}", lowBound=0, upBound=P_fc_max) for t in range(n)]
            k_fc = [pulp.LpVariable(f"k_fc_{t}", cat=pulp.LpBinary) for t in range(n)]
            u_on = [pulp.LpVariable(f"u_on_{t}", cat=pulp.LpBinary) for t in range(n)]
            d_off = [pulp.LpVariable(f"d_off_{t}", cat=pulp.LpBinary) for t in range(n)]
            c_fc_rate = [pulp.LpVariable(f"c_fc_rate_{t}", lowBound=0) for t in range(n)]
            q_fc_avail = [pulp.LpVariable(f"q_fc_avail_{t}", lowBound=0) for t in range(n)]
    else:
        p_fc = k_fc = u_on = d_off = None
        c_fc_rate = None
        q_fc_avail = None

    # ---- Thermal vars: Space ----
    if solver_backend == "gurobi":
        Ti = m.addVars(n, lb=t_in_min, ub=t_in_max, name="Ti_C")
        q_sp_hp = m.addVars(n, lb=0.0, ub=max(0.0, Q_sp_hp_max), name="q_sp_hp_th_kw")
        q_sp_eh = m.addVars(n, lb=0.0, ub=max(0.0, Q_sp_eh_max), name="q_sp_eh_th_kw")

        # ---- Thermal vars: DHW ----
        Ttank = m.addVars(n, lb=t_tank_min, ub=t_tank_max, name="Ttank_C")
        q_dhw_hp = m.addVars(n, lb=0.0, ub=max(0.0, Q_dhw_hp_max), name="q_dhw_hp_th_kw")
        q_dhw_eh = m.addVars(n, lb=0.0, ub=max(0.0, Q_dhw_eh_max), name="q_dhw_eh_th_kw")

        # FC heat routing
        q_fc_to_dhw = m.addVars(n, lb=0.0, name="q_fc_to_dhw_th_kw")
        q_fc_to_sp = m.addVars(n, lb=0.0, name="q_fc_to_sp_th_kw")
        q_fc_spill = m.addVars(n, lb=0.0, name="q_fc_spill_th_kw")
    else:
        Ti = [pulp.LpVariable(f"Ti_{t}", lowBound=t_in_min, upBound=t_in_max) for t in range(n)]
        q_sp_hp = [pulp.LpVariable(f"q_sp_hp_{t}", lowBound=0, upBound=max(0.0, Q_sp_hp_max)) for t in range(n)]
        q_sp_eh = [pulp.LpVariable(f"q_sp_eh_{t}", lowBound=0, upBound=max(0.0, Q_sp_eh_max)) for t in range(n)]
        Ttank = [pulp.LpVariable(f"Ttank_{t}", lowBound=t_tank_min, upBound=t_tank_max) for t in range(n)]
        q_dhw_hp = [pulp.LpVariable(f"q_dhw_hp_{t}", lowBound=0, upBound=max(0.0, Q_dhw_hp_max)) for t in range(n)]
        q_dhw_eh = [pulp.LpVariable(f"q_dhw_eh_{t}", lowBound=0, upBound=max(0.0, Q_dhw_eh_max)) for t in range(n)]
        q_fc_to_dhw = [pulp.LpVariable(f"q_fc_to_dhw_{t}", lowBound=0) for t in range(n)]
        q_fc_to_sp = [pulp.LpVariable(f"q_fc_to_sp_{t}", lowBound=0) for t in range(n)]
        q_fc_spill = [pulp.LpVariable(f"q_fc_spill_{t}", lowBound=0) for t in range(n)]

    # --------------------------
    # Constraints
    # --------------------------
    for t in range(n):
        # PV cap
        if solver_backend == "gurobi":
            m.addConstr(p_pv_used[t] <= float(PV.iloc[t]), name=f"pv_cap[{t}]")
        else:
            m += p_pv_used[t] <= float(PV.iloc[t]), f"pv_cap_{t}"

        # Battery linking + SOC
        if enable_batt:
            if solver_backend == "gurobi":
                m.addConstr(p_bat[t] == p_dis[t] - p_ch[t], name=f"bat_link[{t}]")
                if t == 0:
                    m.addConstr(soc[t] == soc0 + (eta_ch * p_ch[t] - (1.0/eta_dis) * p_dis[t]) * dt_h, name="soc0")
                else:
                    m.addConstr(soc[t] == soc[t-1] + (eta_ch * p_ch[t] - (1.0/eta_dis) * p_dis[t]) * dt_h, name=f"soc[{t}]")
            else:
                m += p_bat[t] == p_dis[t] - p_ch[t], f"bat_link_{t}"
                if t == 0:
                    m += soc[t] == soc0 + (eta_ch * p_ch[t] - (1.0/eta_dis) * p_dis[t]) * dt_h, "soc0"
                else:
                    m += soc[t] == soc[t-1] + (eta_ch * p_ch[t] - (1.0/eta_dis) * p_dis[t]) * dt_h, f"soc_{t}"

        # FC min/max when on
        if enable_fc:
            if solver_backend == "gurobi":
                m.addConstr(p_fc[t] <= P_fc_max * k_fc[t], name=f"fc_up[{t}]")
                m.addConstr(p_fc[t] >= P_fc_min * k_fc[t], name=f"fc_low[{t}]")
            else:
                m += p_fc[t] <= P_fc_max * k_fc[t], f"fc_up_{t}"
                m += p_fc[t] >= P_fc_min * k_fc[t], f"fc_low_{t}"

        # FC heat availability and routing
        if enable_fc:
            if use_heat:
                # routing sum = avail
                if solver_backend == "gurobi":
                    m.addConstr(q_fc_to_dhw[t] + q_fc_to_sp[t] + q_fc_spill[t] == q_fc_avail[t], name=f"fc_heat_split[{t}]")
                else:
                    m += q_fc_to_dhw[t] + q_fc_to_sp[t] + q_fc_spill[t] == q_fc_avail[t], f"fc_heat_split_{t}"
            else:
                # no reuse -> all 0
                if solver_backend == "gurobi":
                    m.addConstr(q_fc_to_dhw[t] == 0.0, name=f"fc_heat0_dhw[{t}]")
                    m.addConstr(q_fc_to_sp[t] == 0.0, name=f"fc_heat0_sp[{t}]")
                    m.addConstr(q_fc_spill[t] == 0.0, name=f"fc_heat0_spill[{t}]")
                    m.addConstr(q_fc_avail[t] == 0.0, name=f"fc_heat0_av[{t}]")
                else:
                    m += q_fc_to_dhw[t] == 0.0, f"fc_heat0_dhw_{t}"
                    m += q_fc_to_sp[t] == 0.0, f"fc_heat0_sp_{t}"
                    m += q_fc_spill[t] == 0.0, f"fc_heat0_spill_{t}"
                    m += q_fc_avail[t] == 0.0, f"fc_heat0_av_{t}"
        else:
            if solver_backend == "gurobi":
                m.addConstr(q_fc_to_dhw[t] == 0.0, name=f"fc_heat0_dhw_no[{t}]")
                m.addConstr(q_fc_to_sp[t] == 0.0, name=f"fc_heat0_sp_no[{t}]")
                m.addConstr(q_fc_spill[t] == 0.0, name=f"fc_heat0_spill_no[{t}]")
            else:
                m += q_fc_to_dhw[t] == 0.0, f"fc_heat0_dhw_no_{t}"
                m += q_fc_to_sp[t] == 0.0, f"fc_heat0_sp_no_{t}"
                m += q_fc_spill[t] == 0.0, f"fc_heat0_spill_no_{t}"

        # Apply UI routing restrictions (keep consistent with Page 2)
        if use_heat:
            if heat_priority in ("dhw", "hotwater", "hot_water"):
                if solver_backend == "gurobi":
                    m.addConstr(q_fc_to_sp[t] == 0.0, name=f"prio_dhw_only[{t}]")
                else:
                    m += q_fc_to_sp[t] == 0.0, f"prio_dhw_only_{t}"
            elif heat_priority in ("space", "space_heating"):
                if solver_backend == "gurobi":
                    m.addConstr(q_fc_to_dhw[t] == 0.0, name=f"prio_space_only[{t}]")
                else:
                    m += q_fc_to_dhw[t] == 0.0, f"prio_space_only_{t}"

        # ---- Space thermal dynamics ----
        q_loss_sp = UA * (Ti[t] - float(Tout.iloc[t]))
        if t == 0:
            if solver_backend == "gurobi":
                m.addConstr(Ti[t] == Ti0, name="Ti0")
            else:
                m += Ti[t] == Ti0, "Ti0"
        else:
            Q_in_sp = q_sp_hp[t] + q_sp_eh[t] + q_fc_to_sp[t]
            if solver_backend == "gurobi":
                m.addConstr(Ti[t] == Ti[t-1] + ((Q_in_sp - q_loss_sp) / Cth) * dt_h, name=f"Ti_dyn[{t}]")
            else:
                m += Ti[t] == Ti[t-1] + ((Q_in_sp - q_loss_sp) / Cth) * dt_h, f"Ti_dyn_{t}"

        # ---- DHW tank dynamics ----
        q_loss_tank = UA_tank * (Ttank[t] - T_amb)
        if t == 0:
            if solver_backend == "gurobi":
                m.addConstr(Ttank[t] == Ttank0, name="Ttank0")
            else:
                m += Ttank[t] == Ttank0, "Ttank0"
        else:
            Q_in_dhw = q_dhw_hp[t] + q_dhw_eh[t] + q_fc_to_dhw[t]
            if solver_backend == "gurobi":
                m.addConstr(Ttank[t] == Ttank[t-1] + ((Q_in_dhw - float(Q_dhw_use.iloc[t]) - q_loss_tank) / C_tank) * dt_h, name=f"Ttank_dyn[{t}]")
            else:
                m += Ttank[t] == Ttank[t-1] + ((Q_in_dhw - float(Q_dhw_use.iloc[t]) - q_loss_tank) / C_tank) * dt_h, f"Ttank_dyn_{t}"

        # ---- Strict electric balance ----
        p_space_el = (1.0 / float(cop_sp[t])) * q_sp_hp[t] + q_sp_eh[t]
        p_dhw_el = (1.0 / float(cop_dhw_val)) * q_dhw_hp[t] + q_dhw_eh[t]
        demand = float(L.iloc[t]) + float(P_leisure.iloc[t]) + p_space_el + p_dhw_el

        supply = p_grid[t] + p_pv_used[t]
        if enable_fc:
            supply += p_fc[t]
        if enable_batt:
            supply += p_bat[t]

        if solver_backend == "gurobi":
            m.addConstr(supply == demand, name=f"balance[{t}]")
        else:
            m += supply == demand, f"balance_{t}"

    # End SOC >= start SOC (same as your previous choice)
    if enable_batt and n > 1:
        if solver_backend == "gurobi":
            m.addConstr(soc[n-1] >= soc0, name="soc_end_ge_start")
        else:
            m += soc[n-1] >= soc0, "soc_end_ge_start"

    # FC commitment logic (k0 = 0)
    if enable_fc:
        if solver_backend == "gurobi":
            m.addConstr(d_off[0] == 0.0, name="d0_zero")
            m.addConstr(k_fc[0] == u_on[0], name="k0_u0")
            for t in range(1, n):
                m.addConstr(k_fc[t] - k_fc[t-1] == u_on[t] - d_off[t], name=f"commit[{t}]")
            for t in range(n):
                end_on = min(n-1, t + N_on - 1)
                m.addConstr(gp.quicksum(k_fc[i] for i in range(t, end_on+1)) >= (end_on - t + 1) * u_on[t], name=f"min_on[{t}]")
                end_off = min(n-1, t + N_off - 1)
                m.addConstr(gp.quicksum(1 - k_fc[i] for i in range(t, end_off+1)) >= (end_off - t + 1) * d_off[t], name=f"min_off[{t}]")
        else:
            m += d_off[0] == 0.0, "d0_zero"
            m += k_fc[0] == u_on[0], "k0_u0"
            for t in range(1, n):
                m += k_fc[t] - k_fc[t-1] == u_on[t] - d_off[t], f"commit_{t}"
            for t in range(n):
                end_on = min(n-1, t + N_on - 1)
                m += pulp.lpSum(k_fc[i] for i in range(t, end_on+1)) >= (end_on - t + 1) * u_on[t], f"min_on_{t}"
                end_off = min(n-1, t + N_off - 1)
                m += pulp.lpSum(1 - k_fc[i] for i in range(t, end_off+1)) >= (end_off - t + 1) * d_off[t], f"min_off_{t}"

        # ---- PWL: FC fuel cost rate ----
        x_fc = _make_breakpoints(0.0, P_fc_max, n_fc_pwl)
        y_cost = np.array([float(fc_cost_rate_dkk_per_h(float(x), price_ch3oh)) for x in x_fc], dtype=float)
        y_cost = np.maximum(y_cost, 0.0)

        for t in range(n):
            if solver_backend == "gurobi":
                m.addGenConstrPWL(p_fc[t], c_fc_rate[t], x_fc.tolist(), y_cost.tolist(), name=f"pwl_fc_cost[{t}]")
            else:
                _pulp_add_pwl(m, p_fc[t], c_fc_rate[t], x_fc.tolist(), y_cost.tolist(), f"pwl_fc_cost_{t}")

        # ---- PWL: FC available heat ----
        x_h = _make_breakpoints(0.0, P_fc_max, n_fc_heat_pwl)
        y_heat = None
        try:
            from core.FCheat import FCSimpleModel, FCSimpleModelCfg
            eta_recovery = float(fc_cfg.get("eta_recovery", 1.0))
            model = FCSimpleModel(FCSimpleModelCfg(eta_recovery=eta_recovery))
            y_heat = np.array([float(model.compute_from_power(float(x)).get("q_fc_kw", 0.0)) for x in x_h], dtype=float)
        except Exception:
            heat_ratio = float(fc_cfg.get("heat_ratio_kWth_per_kWel", 1.0))
            y_heat = np.array([float(x) * heat_ratio for x in x_h], dtype=float)

        y_heat = np.maximum(y_heat, 0.0)
        for t in range(n):
            if solver_backend == "gurobi":
                m.addGenConstrPWL(p_fc[t], q_fc_avail[t], x_h.tolist(), y_heat.tolist(), name=f"pwl_fc_heat[{t}]")
            else:
                _pulp_add_pwl(m, p_fc[t], q_fc_avail[t], x_h.tolist(), y_heat.tolist(), f"pwl_fc_heat_{t}")

    # --------------------------
    # Objective
    # --------------------------
    if solver_backend == "gurobi":
        obj = gp.LinExpr()
        for t in range(n):
            obj += p_grid[t] * float(c_el.iloc[t]) * dt_h
            if enable_fc:
                obj += c_fc_rate[t] * dt_h
        m.setObjective(obj, GRB.MINIMIZE)
        m.optimize()
    else:
        obj = pulp.lpSum(p_grid[t] * float(c_el.iloc[t]) * dt_h for t in range(n))
        if enable_fc:
            obj += pulp.lpSum(c_fc_rate[t] * dt_h for t in range(n))
        m.setObjective(obj)
        
        # Use optimized solver config
        solver = _get_configured_pulp_solver()
        m.solve(solver)

    # --------------------------
    # Check status & Extract results
    # --------------------------
    if solver_backend == "gurobi":
        ok_status = (GRB.OPTIMAL, GRB.SUBOPTIMAL, GRB.TIME_LIMIT)
        if m.Status not in ok_status:
            raise RuntimeError(f"Step A failed. Gurobi status: {m.Status}")
        if m.Status == GRB.TIME_LIMIT and m.SolCount == 0:
            raise RuntimeError("Step A hit TIME_LIMIT and found NO feasible solution.")

        def _get_val(v, t): return v[t].X
    else:
        # PuLP: Check status. If NotSolved (0), it might be a TimeLimit with a feasible solution.
        # We allow Optimal (1) or NotSolved (0) if we can read values.
        if m.status not in [pulp.LpStatusOptimal, pulp.LpStatusNotSolved]:
            raise RuntimeError(f"Step A failed. PuLP status: {pulp.LpStatus[m.status]}")
        
        # Double check if we actually have values
        # If objective is None, then we really failed
        if pulp.value(m.objective) is None:
             raise RuntimeError(f"Step A failed (No solution found). PuLP status: {pulp.LpStatus[m.status]}")

        def _get_val(v, t): 
            val = pulp.value(v[t])
            return float(val) if val is not None else 0.0

    out = {
        "p_grid_kw": pd.Series([_get_val(p_grid, t) for t in range(n)], index=idx, name="p_grid_kw"),
        "p_pv_used_kw": pd.Series([_get_val(p_pv_used, t) for t in range(n)], index=idx, name="p_pv_used_kw"),
    }

    if enable_batt:
        out["p_bat_kw"] = pd.Series([_get_val(p_bat, t) for t in range(n)], index=idx, name="p_bat_kw")
        out["soc_kwh"] = pd.Series([_get_val(soc, t) for t in range(n)], index=idx, name="soc_kwh")
        out["p_bat_ch_kw"] = pd.Series([_get_val(p_ch, t) for t in range(n)], index=idx, name="p_bat_ch_kw")
        out["p_bat_dis_kw"] = pd.Series([_get_val(p_dis, t) for t in range(n)], index=idx, name="p_bat_dis_kw")
    else:
        out["p_bat_kw"] = pd.Series(0.0, index=idx, name="p_bat_kw")
        out["soc_kwh"] = pd.Series(np.nan, index=idx, name="soc_kwh")

    if enable_fc:
        out["p_fc_kw"] = pd.Series([_get_val(p_fc, t) for t in range(n)], index=idx, name="p_fc_kw")
        out["k_fc"] = pd.Series([_get_val(k_fc, t) for t in range(n)], index=idx, name="k_fc")
        out["fc_cost_rate_dkk_per_h"] = pd.Series([_get_val(c_fc_rate, t) for t in range(n)], index=idx, name="fc_cost_rate_dkk_per_h")
        out["q_fc_avail_kw"] = pd.Series([_get_val(q_fc_avail, t) for t in range(n)], index=idx, name="q_fc_avail_kw")
        out["q_fc_to_dhw_kw"] = pd.Series([_get_val(q_fc_to_dhw, t) for t in range(n)], index=idx, name="q_fc_to_dhw_kw")
        out["q_fc_to_space_kw"] = pd.Series([_get_val(q_fc_to_sp, t) for t in range(n)], index=idx, name="q_fc_to_space_kw")
        out["q_fc_spill_kw"] = pd.Series([_get_val(q_fc_spill, t) for t in range(n)], index=idx, name="q_fc_spill_kw")
    else:
        out["p_fc_kw"] = pd.Series(0.0, index=idx, name="p_fc_kw")
        out["k_fc"] = pd.Series(0.0, index=idx, name="k_fc")
        out["q_fc_avail_kw"] = pd.Series(0.0, index=idx, name="q_fc_avail_kw")
        out["q_fc_to_dhw_kw"] = pd.Series(0.0, index=idx, name="q_fc_to_dhw_kw")
        out["q_fc_to_space_kw"] = pd.Series(0.0, index=idx, name="q_fc_to_space_kw")
        out["q_fc_spill_kw"] = pd.Series(0.0, index=idx, name="q_fc_spill_kw")

    # Thermal outputs (electric)
    p_space_el = []
    p_dhw_el = []
    for t in range(n):
        p_space_el.append((_get_val(q_sp_hp, t) / float(cop_sp[t])) + _get_val(q_sp_eh, t))
        p_dhw_el.append((_get_val(q_dhw_hp, t) / float(cop_dhw_val)) + _get_val(q_dhw_eh, t))

    out["p_space_el_kw"] = pd.Series(p_space_el, index=idx, name="p_space_el_kw")
    out["p_dhw_el_kw"] = pd.Series(p_dhw_el, index=idx, name="p_dhw_el_kw")
    out["p_leisure_el_kw"] = P_leisure.rename("p_leisure_el_kw")
    out["p_thermal_el_kw"] = (out["p_space_el_kw"] + out["p_dhw_el_kw"] + out["p_leisure_el_kw"]).rename("p_thermal_el_kw")

    out["Ti_C"] = pd.Series([_get_val(Ti, t) for t in range(n)], index=idx, name="Ti_C")
    out["Ttank_C"] = pd.Series([_get_val(Ttank, t) for t in range(n)], index=idx, name="Ttank_C")

    return out



def stepB_project_real_thermal(
    *,
    idx: pd.DatetimeIndex,
    context,
    cfgs: dict,
    simulate_device_fn,
    simulate_dhw_with_extra_debug_fn,
    fc_power_kw: pd.Series,
    fc_cfg: dict,
) -> dict:
    """
    Step B: keep FC schedule fixed, compute available FC heat, re-run real thermostats
    to get actual electric DHW + space profiles, plus heat used/spilled.

    Returns:
      p_dhw_el_kw, p_space_el_kw, q_fc_avail_kw, q_dhw_used_kw, q_dhw_spill_kw, q_to_space_kw
    """
    # --- sanitize inputs ---
    if fc_power_kw is None:
        fc_power_kw = pd.Series(0.0, index=idx)

    p_fc = fc_power_kw.reindex(idx).fillna(0.0).clip(lower=0.0)

    use_heat = bool(fc_cfg.get("use_waste_heat", False))
    prio = str(fc_cfg.get("heat_priority", "dhw_then_space")).lower().strip()

    # --- compute FC heat availability ---
    q_fc_avail = pd.Series(0.0, index=idx, name="q_fc_avail_kw")

    if use_heat:
        try:
            from core.FCheat import FCSimpleModel, FCSimpleModelCfg
            eta_recovery = float(fc_cfg.get("eta_recovery", 1.0))
            model = FCSimpleModel(FCSimpleModelCfg(eta_recovery=eta_recovery))
            q_list = [float(model.compute_from_power(float(p)).get("q_fc_kw", 0.0)) for p in p_fc.values]
            q_fc_avail = pd.Series(q_list, index=idx, name="q_fc_avail_kw").clip(lower=0.0)
        except Exception:
            heat_ratio = float(fc_cfg.get("heat_ratio_kWth_per_kWel", 1.0))
            q_fc_avail = (p_fc * heat_ratio).rename("q_fc_avail_kw").clip(lower=0.0)

    # --- prepare configs (dict -> DeviceConfig) ---
    from models.schemas import DeviceConfig

    cfg_dhw = cfgs.get("thermal:dhw")
    if isinstance(cfg_dhw, dict):
        cfg_dhw = DeviceConfig.from_dict(cfg_dhw)

    cfg_space = cfgs.get("thermal:space_heat")
    if isinstance(cfg_space, dict):
        cfg_space = DeviceConfig.from_dict(cfg_space)

    # --- defaults ---
    p_dhw_el    = pd.Series(0.0, index=idx, name="p_dhw_el_kw")
    p_space_el  = pd.Series(0.0, index=idx, name="p_space_el_kw")
    q_dhw_used  = pd.Series(0.0, index=idx, name="q_dhw_used_kw")
    q_dhw_spill = pd.Series(0.0, index=idx, name="q_dhw_spill_kw")
    q_to_space  = pd.Series(0.0, index=idx, name="q_to_space_kw")

    # --- if no heat reuse: re-sim with zero extra and return ---
    if not use_heat:
        if cfg_dhw is not None:
            p_dhw_el = simulate_device_fn("thermal:dhw", cfg_dhw, context, q_extra_kw=0.0)
            p_dhw_el = p_dhw_el.reindex(idx).fillna(0.0).rename("p_dhw_el_kw")
        if cfg_space is not None:
            p_space_el = simulate_device_fn("thermal:space_heat", cfg_space, context, q_extra_kw=0.0)
            p_space_el = p_space_el.reindex(idx).fillna(0.0).rename("p_space_el_kw")

        return {
            "q_fc_avail_kw": q_fc_avail,
            "p_dhw_el_kw": p_dhw_el,
            "p_space_el_kw": p_space_el,
            "q_dhw_used_kw": q_dhw_used,
            "q_dhw_spill_kw": q_dhw_spill,
            "q_to_space_kw": q_to_space,
        }

    # --- heat reuse cases ---
    if prio in ("dhw", "dhw_then_space"):
        # send all available heat to DHW first
        if cfg_dhw is not None:
            p_dhw_el, _, q_dhw_used, q_dhw_spill, _q_draw = simulate_dhw_with_extra_debug_fn(
                cfg_dhw, context, q_extra_kw=q_fc_avail
            )
            p_dhw_el    = p_dhw_el.reindex(idx).fillna(0.0).rename("p_dhw_el_kw")
            q_dhw_used  = q_dhw_used.reindex(idx).fillna(0.0).clip(lower=0.0).rename("q_dhw_used_kw")
            q_dhw_spill = q_dhw_spill.reindex(idx).fillna(0.0).clip(lower=0.0).rename("q_dhw_spill_kw")
        else:
            # no DHW device: everything becomes spill
            q_dhw_spill = q_fc_avail.reindex(idx).fillna(0.0).rename("q_dhw_spill_kw")

        if prio == "dhw_then_space":
            q_to_space = q_dhw_spill.rename("q_to_space_kw")
            if cfg_space is not None:
                p_space_el = simulate_device_fn("thermal:space_heat", cfg_space, context, q_extra_kw=q_to_space)
                p_space_el = p_space_el.reindex(idx).fillna(0.0).rename("p_space_el_kw")
        else:
            # dhw only
            q_to_space = pd.Series(0.0, index=idx, name="q_to_space_kw")
            if cfg_space is not None:
                p_space_el = simulate_device_fn("thermal:space_heat", cfg_space, context, q_extra_kw=0.0)
                p_space_el = p_space_el.reindex(idx).fillna(0.0).rename("p_space_el_kw")

    elif prio == "space":
        q_to_space = q_fc_avail.reindex(idx).fillna(0.0).rename("q_to_space_kw")

        if cfg_space is not None:
            p_space_el = simulate_device_fn("thermal:space_heat", cfg_space, context, q_extra_kw=q_to_space)
            p_space_el = p_space_el.reindex(idx).fillna(0.0).rename("p_space_el_kw")

        if cfg_dhw is not None:
            p_dhw_el = simulate_device_fn("thermal:dhw", cfg_dhw, context, q_extra_kw=0.0)
            p_dhw_el = p_dhw_el.reindex(idx).fillna(0.0).rename("p_dhw_el_kw")

    else:
        # unknown -> default to dhw_then_space
        if cfg_dhw is not None:
            p_dhw_el, _, q_dhw_used, q_dhw_spill, _q_draw = simulate_dhw_with_extra_debug_fn(
                cfg_dhw, context, q_extra_kw=q_fc_avail
            )
            p_dhw_el    = p_dhw_el.reindex(idx).fillna(0.0).rename("p_dhw_el_kw")
            q_dhw_used  = q_dhw_used.reindex(idx).fillna(0.0).clip(lower=0.0).rename("q_dhw_used_kw")
            q_dhw_spill = q_dhw_spill.reindex(idx).fillna(0.0).clip(lower=0.0).rename("q_dhw_spill_kw")
        else:
            q_dhw_spill = q_fc_avail.reindex(idx).fillna(0.0).rename("q_dhw_spill_kw")

        q_to_space = q_dhw_spill.rename("q_to_space_kw")
        if cfg_space is not None:
            p_space_el = simulate_device_fn("thermal:space_heat", cfg_space, context, q_extra_kw=q_to_space)
            p_space_el = p_space_el.reindex(idx).fillna(0.0).rename("p_space_el_kw")

    return {
        "q_fc_avail_kw": q_fc_avail,
        "p_dhw_el_kw": p_dhw_el,
        "p_space_el_kw": p_space_el,
        "q_dhw_used_kw": q_dhw_used,
        "q_dhw_spill_kw": q_dhw_spill,
        "q_to_space_kw": q_to_space,
    }


def solve_stepC_grid_batt_with_fixed_fc(
    *,
    idx: pd.DatetimeIndex,
    load_kw: pd.Series,
    pv_avail_kw: pd.Series,
    p_fc_kw: pd.Series,
    price_el: pd.Series | None,
    batt_cfg: dict | None,
    dt_h: float,
    objective: str = "cost",
    enforce_end_soc: str = "ge",  # "none" | "ge" | "eq"
    dump_penalty_dkk_per_kwh: float = 1000.0,
    solver_backend: str | None = None,
) -> dict:
    """
    Step C: optimize (grid, PV_used, battery) with FC fixed.
    No export. p_dump >= 0 absorbs oversupply (feasibility).
    Battery convention returned: p_bat > 0 discharge, p_bat < 0 charge.
    """
    if solver_backend is None:
        if _HAS_GUROBI:
            solver_backend = "gurobi"
        elif _HAS_PULP:
            solver_backend = "pulp"
        else:
            raise RuntimeError("No solver backend (gurobi or pulp) available.")

    n = len(idx)
    if n == 0:
        raise ValueError("idx is empty")
    if dt_h <= 0:
        raise ValueError("dt_h must be positive")

    # ---- align series ----
    load = load_kw.reindex(idx).fillna(0.0).to_numpy(dtype=float)
    pv_av = pv_avail_kw.reindex(idx).fillna(0.0).to_numpy(dtype=float)
    pfc = p_fc_kw.reindex(idx).fillna(0.0).to_numpy(dtype=float)

    load = np.maximum(load, 0.0)
    pv_av = np.maximum(pv_av, 0.0)
    pfc = np.maximum(pfc, 0.0)

    if price_el is None:
        price = np.zeros(n, dtype=float)
    else:
        price = price_el.reindex(idx).ffill().fillna(0.0).to_numpy(dtype=float)

    # ---- battery params ----
    batt_cfg = batt_cfg or {}
    E = float(batt_cfg.get("E_kWh", 0.0))

    if E <= 0:
        # no battery -> choose PV used (<= PV) to reduce grid, dump absorbs oversupply
        pv_used = np.minimum(pv_av, np.maximum(load - pfc, 0.0))
        dump = np.maximum(pv_used + pfc - load, 0.0)
        grid = np.maximum(load + dump - pv_used - pfc, 0.0)
        pv_unused = np.maximum(pv_av - pv_used, 0.0)
        return {
            "grid_import_kw": pd.Series(grid, index=idx, name="grid_import_kw"),
            "pv_used_kw": pd.Series(pv_used, index=idx, name="pv_used_kw"),
            "pv_unused_kw": pd.Series(pv_unused, index=idx, name="pv_unused_kw"),
            "p_bat_kw": pd.Series(np.zeros(n), index=idx, name="p_bat_kw"),
            "soc_kwh": pd.Series(np.nan * np.ones(n), index=idx, name="soc_kwh"),
            "dump_kw": pd.Series(dump, index=idx, name="dump_kw"),
            "soc_end_pct": 0.0,
        }

    P_ch_max = float(batt_cfg.get("P_ch_max_kW", batt_cfg.get("P_ch_max", 0.0)))
    P_dis_max = float(batt_cfg.get("P_dis_max_kW", batt_cfg.get("P_dis_max", 0.0)))

    soc0_pct = float(batt_cfg.get("soc_init", batt_cfg.get("soc0", 60.0)))
    soc0 = (soc0_pct / 100.0) * E

    soc_min_pct = float(batt_cfg.get("soc_min_pct", batt_cfg.get("soc_min", 0.0)))
    soc_max_pct = float(batt_cfg.get("soc_max_pct", batt_cfg.get("soc_max", 100.0)))
    soc_min = (soc_min_pct / 100.0) * E
    soc_max = (soc_max_pct / 100.0) * E

    eta_ch = float(batt_cfg.get("eta_ch", 1.0))
    eta_dis = float(batt_cfg.get("eta_dis", 1.0))
    eta_ch = max(1e-6, min(1.0, eta_ch))
    eta_dis = max(1e-6, min(1.0, eta_dis))

    # ---- model ----
    if solver_backend == "gurobi":
        m = gp.Model("stepC_fixed_fc")
        m.Params.OutputFlag = 0
        p_grid = m.addVars(n, lb=0.0, name="p_grid")
        p_pv   = m.addVars(n, lb=0.0, name="p_pv_used")
        p_ch   = m.addVars(n, lb=0.0, ub=P_ch_max, name="p_bat_ch")
        p_dis  = m.addVars(n, lb=0.0, ub=P_dis_max, name="p_bat_dis")
        p_bat  = m.addVars(n, lb=-P_ch_max, ub=P_dis_max, name="p_bat")
        soc    = m.addVars(n, lb=soc_min, ub=soc_max, name="soc")
        p_dump = m.addVars(n, lb=0.0, name="p_dump")
    else:
        m = pulp.LpProblem("stepC_fixed_fc", pulp.LpMinimize)
        p_grid = [pulp.LpVariable(f"p_grid_{t}", lowBound=0) for t in range(n)]
        p_pv   = [pulp.LpVariable(f"p_pv_{t}", lowBound=0) for t in range(n)]
        p_ch   = [pulp.LpVariable(f"p_ch_{t}", lowBound=0, upBound=P_ch_max) for t in range(n)]
        p_dis  = [pulp.LpVariable(f"p_dis_{t}", lowBound=0, upBound=P_dis_max) for t in range(n)]
        p_bat  = [pulp.LpVariable(f"p_bat_{t}", lowBound=-P_ch_max, upBound=P_dis_max) for t in range(n)]
        soc    = [pulp.LpVariable(f"soc_{t}", lowBound=soc_min, upBound=soc_max) for t in range(n)]
        p_dump = [pulp.LpVariable(f"p_dump_{t}", lowBound=0) for t in range(n)]

    # Constraints
    for t in range(n):
        if solver_backend == "gurobi":
            m.addConstr(p_bat[t] == p_dis[t] - p_ch[t], name=f"bat_link[{t}]")
            m.addConstr(p_pv[t] <= float(pv_av[t]), name=f"pv_cap[{t}]")
            m.addConstr(p_grid[t] + p_pv[t] + p_bat[t] + float(pfc[t]) == float(load[t]) + p_dump[t], name=f"balance[{t}]")
            if t == 0:
                m.addConstr(soc[t] == soc0 + (eta_ch * p_ch[t] - (1.0 / eta_dis) * p_dis[t]) * dt_h, name="soc0_update")
            else:
                m.addConstr(soc[t] == soc[t-1] + (eta_ch * p_ch[t] - (1.0 / eta_dis) * p_dis[t]) * dt_h, name=f"soc_dyn[{t}]")
        else:
            m += p_bat[t] == p_dis[t] - p_ch[t], f"bat_link_{t}"
            m += p_pv[t] <= float(pv_av[t]), f"pv_cap_{t}"
            m += p_grid[t] + p_pv[t] + p_bat[t] + float(pfc[t]) == float(load[t]) + p_dump[t], f"balance_{t}"
            if t == 0:
                m += soc[t] == soc0 + (eta_ch * p_ch[t] - (1.0 / eta_dis) * p_dis[t]) * dt_h, "soc0_update"
            else:
                m += soc[t] == soc[t-1] + (eta_ch * p_ch[t] - (1.0 / eta_dis) * p_dis[t]) * dt_h, f"soc_dyn_{t}"

    # end SOC constraint
    if enforce_end_soc == "ge":
        if solver_backend == "gurobi":
            m.addConstr(soc[n-1] >= soc0, name="soc_end_ge")
        else:
            m += soc[n-1] >= soc0, "soc_end_ge"
    elif enforce_end_soc == "eq":
        if solver_backend == "gurobi":
            m.addConstr(soc[n-1] == soc0, name="soc_end_eq")
        else:
            m += soc[n-1] == soc0, "soc_end_eq"

    # Objective
    if solver_backend == "gurobi":
        obj = gp.quicksum(p_grid[t] * float(price[t]) * dt_h for t in range(n))
        obj += dump_penalty_dkk_per_kwh * gp.quicksum(p_dump[t] * dt_h for t in range(n))
        m.setObjective(obj, GRB.MINIMIZE)
        m.optimize()
        if m.Status not in (GRB.OPTIMAL, GRB.SUBOPTIMAL):
            raise RuntimeError(f"Step C solve failed. Gurobi status: {m.Status}")
        def _get_val(v, t): return v[t].X
    else:
        obj = pulp.lpSum(p_grid[t] * float(price[t]) * dt_h for t in range(n))
        obj += dump_penalty_dkk_per_kwh * pulp.lpSum(p_dump[t] * dt_h for t in range(n))
        m.setObjective(obj)
        
        # Use optimized solver config
        solver = _get_configured_pulp_solver()
        m.solve(solver)
        if m.status not in [pulp.LpStatusOptimal, pulp.LpStatusNotSolved]:
            raise RuntimeError(f"Step C solve failed. PuLP status: {pulp.LpStatus[m.status]}")
        
        if pulp.value(m.objective) is None:
             raise RuntimeError(f"Step C failed (No solution found). PuLP status: {pulp.LpStatus[m.status]}")

        def _get_val(v, t): 
            val = pulp.value(v[t])
            return float(val) if val is not None else 0.0

    grid = np.array([_get_val(p_grid, t) for t in range(n)], dtype=float)
    pv_used = np.array([_get_val(p_pv, t) for t in range(n)], dtype=float)
    bat = np.array([_get_val(p_bat, t) for t in range(n)], dtype=float)
    soc_v = np.array([_get_val(soc, t) for t in range(n)], dtype=float)
    dump = np.array([_get_val(p_dump, t) for t in range(n)], dtype=float)

    pv_unused = np.maximum(pv_av - pv_used, 0.0)
    soc_end_pct = float(soc_v[-1] / E * 100.0) if E > 0 else 0.0

    return {
        "grid_import_kw": pd.Series(grid, index=idx, name="grid_import_kw"),
        "pv_used_kw": pd.Series(pv_used, index=idx, name="pv_used_kw"),
        "pv_unused_kw": pd.Series(pv_unused, index=idx, name="pv_unused_kw"),
        "p_bat_kw": pd.Series(bat, index=idx, name="p_bat_kw"),
        "soc_kwh": pd.Series(soc_v, index=idx, name="soc_kwh"),
        "dump_kw": pd.Series(dump, index=idx, name="dump_kw"),
        "soc_end_pct": soc_end_pct,
    }
