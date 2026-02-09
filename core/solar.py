
import pandas as pd
import pvlib
from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS

def pv_from_weather_modelchain_from_df(
    idx_min: pd.DatetimeIndex,
    dfh: pd.DataFrame,             # hourly weather: ghi, dni, dhi, temp, wind
    lat: float, lon: float, kwp: float,
    tilt_deg: float = 30.0, az_deg: float = 180.0,
    sys_loss_frac: float = 0.14,
    tz: str = "Europe/Copenhagen",
) -> pd.Series:
    if kwp <= 0:
        return pd.Series(0.0, index=idx_min, name="pv_kw")

    # ---- 1) Normalize index tz for ALL inputs (use tz-aware consistently) ----
    if not isinstance(dfh.index, pd.DatetimeIndex):
        dfh = dfh.copy()
        dfh.index = pd.to_datetime(dfh.index)

    if dfh.index.tz is None:
        dfh.index = dfh.index.tz_localize(tz)
    else:
        dfh.index = dfh.index.tz_convert(tz)

    times_h = dfh.index  # tz-aware hourly index

    # ---- 2) Solar position with the SAME tz-aware index ----
    loc    = pvlib.location.Location(lat, lon, tz=tz)
    solpos = loc.get_solarposition(times_h)
    zen    = solpos["apparent_zenith"].clip(0, 90)

    # ---- 3) Fill DNI/DHI from GHI if missing (ERBS) using the SAME index ----
    ghi = dfh["ghi"].astype(float)
    dni = dfh["dni"] if "dni" in dfh.columns else None
    dhi = dfh["dhi"] if "dhi" in dfh.columns else None

    needs_fill = (dni is None) or (dhi is None) \
                or (dni.isna().any() if dni is not None else False) \
                or (dhi.isna().any() if dhi is not None else False)

    if needs_fill:
        split = pvlib.irradiance.erbs(
            ghi=ghi.values, zenith=zen.values, datetime_or_doy=times_h
        )

        # DNI
        if dni is None:
            dfh["dni"] = pd.Series(split["dni"].values, index=times_h, dtype=float)
        else:
            dfh["dni"] = dni.astype(float).fillna(pd.Series(split["dni"].values, index=times_h))

        # DHI
        if dhi is None:
            dfh["dhi"] = pd.Series(split["dhi"].values, index=times_h, dtype=float)
        else:
            dfh["dhi"] = dhi.astype(float).fillna(pd.Series(split["dhi"].values, index=times_h))

    # ---- 4) POA with matched indices (no tz mix) ----
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt_deg, surface_azimuth=az_deg,
        dni=dfh["dni"].astype(float), ghi=ghi, dhi=dfh["dhi"].astype(float),
        solar_zenith=zen, solar_azimuth=solpos["azimuth"]
    )
    poa_global = poa["poa_global"].clip(lower=0.0)

    # ---- 5) ModelChain from POA; keep times tz-aware then drop tz at the end ----
    weather_poa = pd.DataFrame({
        "poa_global":  poa["poa_global"].clip(lower=0.0).astype(float),
        "poa_direct":  poa["poa_direct"].clip(lower=0.0).astype(float),
        "poa_diffuse": poa["poa_diffuse"].clip(lower=0.0).astype(float),
        "temp_air":    dfh["temp"].fillna(15.0).astype(float),
        "wind_speed":  dfh["wind"].fillna(2.0).astype(float),
    }, index=times_h)

    pdc0_w = float(kwp) * 1000.0
    temp_params = TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
    system = pvlib.pvsystem.PVSystem(
        arrays=[pvlib.pvsystem.Array(
            mount=pvlib.pvsystem.FixedMount(surface_tilt=tilt_deg, surface_azimuth=az_deg),
            module_parameters={"pdc0": pdc0_w, "gamma_pdc": -0.0045},
            temperature_model_parameters=temp_params,
        )],
        inverter_parameters={"pdc0": pdc0_w}
    )

    mc = pvlib.modelchain.ModelChain(
        system, loc,
        dc_model="pvwatts", ac_model="pvwatts",
        aoi_model="physical", spectral_model="no_loss",
    )
    mc.run_model_from_poa(weather_poa)

    # AC (W) → kW, apply system loss, then drop tz and interpolate to minutes
    ac_kw_h = (mc.results.ac / 1000.0) * (1.0 - float(sys_loss_frac))
    ac_kw_h = ac_kw_h.tz_convert(None)               # now tz-naive
    ac_kw_m = ac_kw_h.reindex(idx_min).interpolate().bfill().ffill()
    return ac_kw_m.rename("pv_kw")
