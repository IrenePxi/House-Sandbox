import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Ensure we can import from the project root
sys.path.append(os.getcwd())

import services.P3_ems_globalopt_service as ems_opt

def test_pulp_fallback():
    print("Testing PuLP fallback...")
    
    # Create sample data
    idx = pd.date_range("2025-01-01 00:00", periods=24, freq="1h")
    load = pd.Series(1.0, index=idx)
    pv = pd.Series(0.0, index=idx)
    tout = pd.Series(5.0, index=idx)
    price = pd.Series(2.0, index=idx)
    
    batt_cfg = {
        "E_kWh": 10.0,
        "soc_init": 50.0,
        "soc_min": 10.0,
        "soc_max": 90.0,
        "P_ch_max_kW": 3.0,
        "P_dis_max_kW": 3.0,
        "eta_ch": 0.95,
        "eta_dis": 0.95
    }
    
    fc_cfg = {
        "Pmin_W": 1900.0,
        "Prated_W": 4860.0,
        "min_on_min": 60,
        "min_off_min": 60,
        "price_ch3oh": 10.0,
        "use_waste_heat": True,
        "heat_priority": "dhw"
    }
    
    space_cfg = {"ua_kw_per_c": 0.25, "C_th_kwh_per_c": 3.0}
    dhw_cfg = {"C_th_kwh_per_c": 6.0, "ua_kw_per_c": 0.08}

    print("\n--- Running solve_stepA with pulp ---")
    try:
        res_a = ems_opt.solve_stepA_relaxed_global_opt(
            idx=idx,
            load_nonthermal_kw=load,
            pv_avail_kw=pv,
            price_el=price,
            tout_c=tout,
            batt_cfg=batt_cfg,
            fc_cfg=fc_cfg,
            space_cfg=space_cfg,
            dhw_cfg=dhw_cfg,
            solver_backend="pulp",
            enable_fc=True,
            enable_batt=True
        )
        print("solve_stepA (pulp) SUCCESS")
        print(f"Grid import sum: {res_a['p_grid_kw'].sum():.2f}")
    except Exception as e:
        print(f"solve_stepA (pulp) FAILED: {e}")
        import traceback
        traceback.print_exc()

    print("\n--- Running solve_stepC with pulp ---")
    try:
        # Mock p_fc_kw from A or just use zeros
        p_fc = pd.Series(0.0, index=idx)
        res_c = ems_opt.solve_stepC_grid_batt_with_fixed_fc(
            idx=idx,
            load_kw=load,
            pv_avail_kw=pv,
            p_fc_kw=p_fc,
            price_el=price,
            batt_cfg=batt_cfg,
            dt_h=1.0,
            solver_backend="pulp"
        )
        print("solve_stepC (pulp) SUCCESS")
        print(f"Grid import sum: {res_c['grid_import_kw'].sum():.2f}")
    except Exception as e:
        print(f"solve_stepC (pulp) FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    if not ems_opt._HAS_PULP:
        print("PuLP not installed. Please install it to run these tests.")
    else:
        test_pulp_fallback()
