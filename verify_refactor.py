import sys
import os

# Ensure we can import from current dir
sys.path.append(os.getcwd())

try:
    print("Importing models...")
    from models.schemas import DeviceConfig, SimulationContext
    print("Models imported.")

    print("Importing services...")
    from services import simulation_service
    print("Services imported.")

    # Test basic simulation
    print("Testing basic simulation...")
    from datetime import date, time
    
    ctx = SimulationContext(selected_day=date(2025, 1, 10))
    cfg = DeviceConfig(power_kw=1.0, duration_min=60, start=time(12,0))
    
    # Test simulate_device
    prof = simulation_service.simulate_device("elec_fixed:test", cfg, ctx)
    print(f"Profile generated with shape: {prof.shape}, Sum: {prof.sum()}")
    
    if prof.sum() > 0:
        print("SUCCESS: Simulation produced power.")
    else:
        print("WARNING: Simulation produced 0 power (might be intended based on schedule?)")

    print("Verification complete.")

except Exception as e:
    print(f"FAILED: {e}")
    import traceback
    traceback.print_exc()
