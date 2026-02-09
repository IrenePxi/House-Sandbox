# core/fc_heat.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple
import numpy as np


@dataclass(frozen=True)
class FCSimpleModelCfg:
    # Stack parameters
    N: int = 120  # number of cells (or stacks factor in your simplification)

    # Valid current range (A)
    I_min_A: float = 23.0
    I_max_A: float = 72.0
    I_step_A: float = 0.25  # resolution of lookup

    # VI curve coefficients (your polynomial)
    p: Tuple[float, ...] = (
        -0.151, 0.2238, 0.6455, -0.8475, -1.016,
        1.179, 0.3137, 0.11, -6.849, 80.63
    )

    # Simplified linear fits for methanol and heat/BoP
    # m_ch3oh = N * I * 6.9182e-08  [kg/s]
    k_methanol: float = 6.9182e-08

    # Qcoil = N * I * 1.0526 - Pfc  [W]
    k_qcoil: float = 1.0526

    # P_BoP = I*N*0.0463 - Pfc*0.0094  [W]
    k_bop_I: float = 0.0463
    k_bop_P: float = 0.0094

    # optional recovery efficiency (HX effectiveness, plumbing losses)
    eta_recovery: float = 1.0

    # safety clips
    clip_negative_heat: bool = True
    clip_negative_bop: bool = True


class FCSimpleModel:
    """
    Precomputes a P(I) lookup table so we can invert P->I quickly.
    """

    def __init__(self, cfg: FCSimpleModelCfg):
        self.cfg = cfg
        self._build_table()

    def _build_table(self) -> None:
        cfg = self.cfg
        I = np.arange(cfg.I_min_A, cfg.I_max_A + 1e-9, cfg.I_step_A)

        # I_mid = (Ifc - 34) / 19.92
        I_mid = (I - 34.0) / 19.92

        # Vfc = poly in I_mid (order 9 -> 10 coefficients)
        # np.polyval expects highest power first; your p is p1..p10 already highest->lowest
        V = np.polyval(np.array(cfg.p, dtype=float), I_mid)

        P = V * I  # W  (matches your script)

        # Ensure monotonic for inversion by sorting by P
        sort_idx = np.argsort(P)
        self.I_table = I[sort_idx]
        self.P_table = P[sort_idx]

    def invert_power_to_current(self, p_fc_w: float) -> float:
        p = float(max(p_fc_w, 0.0))

        # OFF state
        if p <= 1e-6:
            return 0.0

        Pmin = float(self.P_table[0])
        Pmax = float(self.P_table[-1])

        if p <= Pmin:
            return float(self.I_table[0])
        if p >= Pmax:
            return float(self.I_table[-1])

        return float(np.interp(p, self.P_table, self.I_table))

    def forward_from_current(self, i_a: float) -> Dict[str, float]:
        """
        Compute Pfc, Qcoil, methanol, BoP from current (A).
        """
        cfg = self.cfg
        i = float(i_a)

        # OFF state
        if i <= 0.0:
            return {
                "p_fc_w": 0.0,
                "q_coil_w": 0.0,
                "m_ch3oh_kg_s": 0.0,
                "p_bop_w": 0.0,
                "v_fc_v": 0.0,
                "i_fc_a": 0.0,
            }

        # Voltage from VI curve
        I_mid = (i - 34.0) / 19.92
        V = float(np.polyval(np.array(cfg.p, dtype=float), I_mid))
        Pfc_w = V * i

        # Methanol mass flow
        m_ch3oh_kg_s = cfg.N * i * cfg.k_methanol

        # Heat (coil)
        Qcoil_w = cfg.N * i * cfg.k_qcoil - Pfc_w
        if cfg.clip_negative_heat:
            Qcoil_w = max(0.0, Qcoil_w)

        # BoP
        P_bop_w = i * cfg.N * cfg.k_bop_I - Pfc_w * cfg.k_bop_P
        if cfg.clip_negative_bop:
            P_bop_w = max(0.0, P_bop_w)

        return {
            "p_fc_w": float(Pfc_w),
            "q_coil_w": float(Qcoil_w),
            "m_ch3oh_kg_s": float(m_ch3oh_kg_s),
            "p_bop_w": float(P_bop_w),
            "v_fc_v": float(V),
            "i_fc_a": float(i),
        }


    def compute_from_power(self, p_fc_kw: float) -> Dict[str, float]:
        """
        Main API for the app:
        input: p_fc_kw (kW electric, commanded/achieved)
        output: q_coil_kw (kW usable), m_ch3oh_kg_s, p_bop_kw, plus debug
        """
        p_fc_w_target = float(max(p_fc_kw, 0.0) * 1000.0)
        i = self.invert_power_to_current(p_fc_w_target)
        out = self.forward_from_current(i)

        # note: Pfc_w returned by forward_from_current may differ slightly from target due to interpolation
        q_coil_kw = self.cfg.eta_recovery * out["q_coil_w"] / 1000.0
        p_bop_kw = out["p_bop_w"] / 1000.0

        return {
            "q_fc_kw": float(q_coil_kw),
            "p_bop_kw": float(p_bop_kw),
            "m_ch3oh_kg_s": float(out["m_ch3oh_kg_s"]),
            "debug_p_fc_w": float(out["p_fc_w"]),
            "debug_v_fc_v": float(out["v_fc_v"]),
            "debug_i_fc_a": float(out["i_fc_a"]),
        }
