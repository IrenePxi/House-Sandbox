import numpy as np
import pandas as pd
from scipy.optimize import minimize
def normalize_fc_cfg(fc_cfg: dict) -> dict:
    fc = dict(fc_cfg or {})

    # --- power keys ---
    # UI defaults: p_min_kw, p_rated_kw
    # solver expects: Pmin_W, Prated_W
    if "Pmin_W" not in fc:
        if "p_min_kw" in fc:
            fc["Pmin_W"] = float(fc["p_min_kw"]) * 1000.0
        elif "Pmin_kW" in fc:
            fc["Pmin_W"] = float(fc["Pmin_kW"]) * 1000.0
        else:
            fc["Pmin_W"] = 1900.0  # last-resort fallback

    if "Prated_W" not in fc:
        if "p_rated_kw" in fc:
            fc["Prated_W"] = float(fc["p_rated_kw"]) * 1000.0
        elif "Prated_kW" in fc:
            fc["Prated_W"] = float(fc["Prated_kW"]) * 1000.0
        else:
            fc["Prated_W"] = 4860.0  # last-resort fallback

    # --- min on/off ---
    fc["min_on_min"]  = int(fc.get("min_on_min", 60))
    fc["min_off_min"] = int(fc.get("min_off_min", 60))

    # --- heat priority normalization ---
    hp = str(fc.get("heat_priority", "dhw")).strip().lower()
    if hp in ("dhw", "hotwater", "hot_water"):
        fc["heat_priority_norm"] = "dhw"
    elif hp in ("space", "space_heating"):
        fc["heat_priority_norm"] = "space"
    elif "then" in hp or "→" in hp or "->" in hp:
        fc["heat_priority_norm"] = "dhw_then_space"
    else:
        # already in your internal tokens? keep if valid
        if hp in ("dhw_then_space",):
            fc["heat_priority_norm"] = hp
        else:
            fc["heat_priority_norm"] = "dhw"

    return fc

def fc_cost_rate_dkk_per_h(P_fc_kw: float, price_ch3oh: float) -> float:
    # replicate your fc_cost_fun logic but return DKK/h at given P
    P_W = P_fc_kw * 1000.0
    p = [0.001812, 0.003538, -0.004421, -0.009001,
         0.003244, 0.007644, 0.02274, 0.3901]
    x = (P_W - 3557.0) / 890.0
    y = np.polyval(p, x)  # your fitted factor
    price_fc_dkk_per_kwh = y * price_ch3oh         # your assumption
    return P_fc_kw * price_fc_dkk_per_kwh          # (kW)*(DKK/kWh)=DKK/h


def fc_cost_fun(Pfc_W, Price_ch3oh, Elec_price, Mini_Pfc_W, Maxi_Pfc_W):
    """
    Your original cost function, slightly cleaned:
    Pfc_W: scalar (W), numpy array shape (1,) from scipy is also ok.
    Price_ch3oh: methanol price (same units as before).
    Elec_price: electricity price at this slot (DKK/kWh or same as before).
    Returns scalar objective value.
    """
    # Ensure scalar
    if isinstance(Pfc_W, (np.ndarray, list, tuple)):
        Pfc = float(Pfc_W[0])
    else:
        Pfc = float(Pfc_W)

    # Polynomial coefficients (same as your MATLAB-based fit)
    p = [0.001812, 0.003538, -0.004421, -0.009001,
         0.003244, 0.007644, 0.02274, 0.3901]

    # Normalization (same as before)
    x = (Pfc - 3557.0) / 890.0
    y = np.polyval(p, x)  # dimensionless factor

    # If outside bounds, just return 0 (though bounds should prevent this)
    if not (Mini_Pfc_W <= Pfc <= Maxi_Pfc_W):
        return 0.0

    # Objective (same structure as before)
    return (y * Price_ch3oh - Elec_price) * Pfc

def solve_fc_schedule_minute(prices_minute, Price_ch3oh, Pmin_W, Prated_W):
    """
    Solve FC optimal response at 1-minute resolution.
    prices_minute: np.array of size 1440 for one day.
    """
    prices = np.asarray(prices_minute, dtype=float)
    n = len(prices)
    opti_fc = np.zeros(n, dtype=float)
    fvals = np.zeros(n, dtype=float)

    # 1-minute threshold scaling
    dt_min = 1.0
    threshold = -50 * (dt_min / 60.0)   # = -0.8333

    for i, Elec_price in enumerate(prices):
        x0 = Pmin_W
        bounds = [(Pmin_W, Prated_W)]

        res = minimize(
            fc_cost_fun,
            x0=[x0],
            args=(Price_ch3oh, Elec_price, Pmin_W, Prated_W),
            method="SLSQP",
            bounds=bounds,
            options={'maxiter': 200, 'disp': False},
        )

        fval = float(res.fun)
        Popt = float(res.x[0])

        # Scaled threshold
        if fval >= threshold:
            opti_fc[i] = 0.0
        else:
            opti_fc[i] = Popt

        fvals[i] = fval

    return opti_fc, fvals

def smooth_fc_schedule(p_fc: pd.Series,
                       dt_min: int = 1,
                       min_on_min: int = 60,
                       min_off_min: int = 30) -> pd.Series:
    """
    Post-process FC power profile to enforce:
      - minimum ON time
      - minimum OFF time
    and remove short OFF gaps between ON periods.

    p_fc: minute-level FC power [kW] (index = DatetimeIndex)
    """
    if p_fc.empty:
        return p_fc

    on = p_fc > 0  # boolean

    # Group consecutive equal values (runs)
    groups = (on != on.shift(fill_value=on.iloc[0])).cumsum()

    on2 = on.copy()

    # First pass: kill too-short ON periods
    for g, idx_g in on.groupby(groups).groups.items():
        mask = groups == g
        is_on = on[mask].iloc[0]
        length_min = mask.sum() * dt_min

        if is_on and length_min < min_on_min:
            # too short ON → force OFF
            on2[mask] = False

    # Recompute groups after first pass
    groups2 = (on2 != on2.shift(fill_value=on2.iloc[0])).cumsum()

    # Second pass: fill too-short OFF gaps between ON periods
    for g, idx_g in on2.groupby(groups2).groups.items():
        mask = groups2 == g
        is_on = on2[mask].iloc[0]
        length_min = mask.sum() * dt_min

        if (not is_on) and length_min < min_off_min:
            # OFF gap, check neighbours
            left_on  = (g - 1 in groups2.values) and on2[groups2 == (g - 1)].iloc[0]
            right_on = (g + 1 in groups2.values) and on2[groups2 == (g + 1)].iloc[0]
            if left_on and right_on:
                # short OFF between two ON blocks → fill it
                on2[mask] = True

    # Build new power profile:
    p_new = p_fc.copy()

    # Force OFF where on2 is False
    p_new[~on2] = 0.0

    # For points we turned ON (gap-fill) but original was 0,
    # just use the previous non-zero value (or a constant).
    turned_on = on2 & (~on)
    p_new[turned_on] = p_new.where(p_new > 0).ffill()[turned_on]

    return p_new

def build_fc_ref_profile_kw(
    *,
    idx: pd.DatetimeIndex,
    elec_price: pd.Series,          # DKK/kWh (or your units)
    Price_ch3oh: float,
    Pmin_W: float,
    Prated_W: float,
    smooth: bool = True,
    min_on_min: int = 60,
    min_off_min: int = 30,
) -> pd.Series:
    """
    Returns minute-level FC reference power [kW], indexed by idx.
    """
    if len(idx) == 0:
        return pd.Series(dtype=float, index=idx, name="P_fc_ref_kW")

    # reindex prices to idx (minute)
    p = elec_price.reindex(idx, method="nearest").astype(float).fillna(0.0)

    # solve in W
    opt_w, _ = solve_fc_schedule_minute(
        prices_minute=p.values,
        Price_ch3oh=Price_ch3oh,
        Pmin_W=Pmin_W,
        Prated_W=Prated_W,
    )

    # convert to kW Series
    p_ref = pd.Series(opt_w / 1000.0, index=idx, name="P_fc_ref_kW").clip(lower=0.0)

    if smooth:
        p_ref = smooth_fc_schedule(p_ref, dt_min=1, min_on_min=min_on_min, min_off_min=min_off_min)
        p_ref.name = "P_fc_ref_kW"

    return p_ref
