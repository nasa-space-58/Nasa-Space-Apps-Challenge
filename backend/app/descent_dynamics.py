"""Fuel-limited planar point-mass dynamics shared by R4 and H4."""

from dataclasses import dataclass

import numpy as np

from shared.constants import MOON_GM_M3_S2


@dataclass(frozen=True)
class BurnStep:
    position_m: np.ndarray
    velocity_mps: np.ndarray
    mass_kg: float
    burn_s: float
    coast_s: float
    delta_v_mps: float
    propellant_exhausted: bool


def advance_burn(
    position_m,
    velocity_mps,
    mass_kg,
    dry_mass_kg,
    thrust_vector_n,
    isp_s,
    dt_s,
    *,
    gm_m3_s2=MOON_GM_M3_S2,
) -> BurnStep:
    """Hold thrust, stop exactly at fuel exhaustion, then coast the remainder."""
    raise NotImplementedError


def zem_zev_acceleration(
    position_m,
    velocity_mps,
    target_position_m,
    target_velocity_mps,
    time_to_go_s,
    gravity_mps2,
):
    """Return the R4 zero-effort-miss/velocity thrust acceleration."""
    raise NotImplementedError
