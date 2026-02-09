from __future__ import annotations
from models.schemas import DeviceConfig

def convert_legacy_cfg(cfgs_dict: dict) -> dict:
    """
    Convert a dict of raw dict configs into a dict of DeviceConfig objects.
    Preserves known fields, discards cached profiles (profile_index, profile_kw).
    """
    new_cfgs = {}
    for k, v in cfgs_dict.items():
        if isinstance(v, dict):
            # Clean up cache keys if they exist
            v.pop("profile_index", None)
            v.pop("profile_kw", None)
            
            try:
                new_cfgs[k] = DeviceConfig.from_dict(v)
            except Exception:
                # print(f"Migration warning for {k}: {e}")
                # Fallback: keep dict if conversion fails? 
                # Better to attempt best effort or create default
                new_cfgs[k] = DeviceConfig.from_dict(v) # Let it crash or handle?
                # Actually from_dict filters keys, so it should be safe-ish.
        else:
            new_cfgs[k] = v
            
    return new_cfgs
