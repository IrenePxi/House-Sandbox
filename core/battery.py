def battery_step(soc: float, p_cmd_kw: float, dt_h: float, cfg: dict):
    """
    Returns (soc_next, p_actual_kw).
    p_actual_kw uses sign convention: +discharge, -charge.
    """
    E = float(cfg["E_kWh"])
    soc_min = float(cfg["soc_min"])*0.01
    soc_max = float(cfg["soc_max"])*0.01
    eta_ch  = float(cfg["eta_ch"])
    eta_dis = float(cfg["eta_dis"])
    Pch_max = float(cfg["P_ch_max_kW"])
    Pdis_max= float(cfg["P_dis_max_kW"])

    # clamp power command by limits
    p = float(p_cmd_kw)
    if p >= 0:
        p = min(p, Pdis_max)
    else:
        p = max(p, -Pch_max)

    # energy bounds
    E_now = soc * E
    E_min = soc_min * E
    E_max = soc_max * E

    if p < 0:  # charging
        # energy increase = |p| * eta_ch * dt
        dE = (-p) * eta_ch * dt_h
        if E_now + dE > E_max and dt_h > 0:
            dE = max(0.0, E_max - E_now)
            p = - (dE / (eta_ch * dt_h)) if eta_ch > 0 else 0.0
        E_next = E_now + dE

    else:      # discharging
        # energy decrease = p/eta_dis * dt
        dE = (p / eta_dis) * dt_h if eta_dis > 0 else 0.0
        if E_now - dE < E_min and dt_h > 0:
            dE = max(0.0, E_now - E_min)
            p = (dE * eta_dis / dt_h) if dt_h > 0 else 0.0
        E_next = E_now - dE

    soc_next = 0.0 if E <= 0 else (E_next / E)
    soc_next = min(max(soc_next, soc_min), soc_max)
    return soc_next, p
