"""Fuel-limited planar point-mass dynamics shared by R4 and H4."""

import math
from dataclasses import dataclass

import numpy as np

from shared.constants import MOON_GM_M3_S2, STANDARD_GRAVITY_M_S2


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
    p, v, force = (
        np.asarray(x, dtype=float).copy()
        for x in (position_m, velocity_mps, thrust_vector_n)
    )
    if any(x.shape != (2,) or not np.isfinite(x).all() for x in (p, v, force)):
        raise ValueError("Dynamics require finite planar vectors")
    values = (mass_kg, dry_mass_kg, isp_s, dt_s, gm_m3_s2)
    if not all(math.isfinite(x) for x in values):
        raise ValueError("Dynamics require finite scalars")
    if not (mass_kg >= dry_mass_kg > 0 and isp_s > 0 and dt_s > 0 and gm_m3_s2 >= 0):
        raise ValueError("Invalid mass, specific impulse, step or gravity")
    if np.linalg.norm(p) <= 0:
        raise ValueError("Position must be outside the gravity singularity")
    magnitude = float(np.linalg.norm(force))
    exhaust_mps = isp_s * STANDARD_GRAVITY_M_S2
    flow_kg_s = magnitude / exhaust_mps
    fuel_kg = mass_kg - dry_mass_kg
    burn_s = min(dt_s, fuel_kg / flow_kg_s) if flow_kg_s > 0 else 0.0
    final_mass = max(dry_mass_kg, mass_kg - flow_kg_s * burn_s)

    def integrate(p, v, mass, duration, force, flow):
        # Limit fractional mass loss to 0.5% per RK4 substep. In ordinary R4
        # steps this is one evaluation; high-thrust fuel-boundary probes split.
        count = max(1, math.ceil(flow * duration / (0.005 * final_mass)))
        if count > 10000:
            raise ValueError("Burn exceeds bounded integration work")
        h = duration / count

        def acceleration(position, m):
            radius = float(np.linalg.norm(position))
            if radius <= 0 or not math.isfinite(radius):
                raise ValueError("Gravity singularity or non-finite trajectory")
            return -gm_m3_s2 * position / radius**3 + force / m

        for _ in range(count):
            middle_mass = max(dry_mass_kg, mass - flow * h / 2)
            end_mass = max(dry_mass_kg, mass - flow * h)
            a1 = acceleration(p, mass)
            v2 = v + h / 2 * a1
            a2 = acceleration(p + h / 2 * v, middle_mass)
            v3 = v + h / 2 * a2
            a3 = acceleration(p + h / 2 * v2, middle_mass)
            v4 = v + h * a3
            a4 = acceleration(p + h * v3, end_mass)
            p = p + h / 6 * (v + 2 * v2 + 2 * v3 + v4)
            v = v + h / 6 * (a1 + 2 * a2 + 2 * a3 + a4)
            mass = end_mass
        return p, v

    if burn_s > 0:
        p, v = integrate(p, v, mass_kg, burn_s, force, flow_kg_s)
    coast_s = dt_s - burn_s
    if coast_s > 0:
        p, v = integrate(p, v, final_mass, coast_s, np.zeros(2), 0.0)
    if not np.isfinite(p).all() or not np.isfinite(v).all():
        raise ValueError("Non-finite trajectory")
    return BurnStep(
        p,
        v,
        final_mass,
        burn_s,
        coast_s,
        exhaust_mps * math.log(mass_kg / final_mass),
        final_mass <= dry_mass_kg,
    )


def zem_zev_acceleration(
    position_m,
    velocity_mps,
    target_position_m,
    target_velocity_mps,
    time_to_go_s,
    gravity_mps2,
):
    """Return the R4 zero-effort-miss/velocity thrust acceleration."""
    if not math.isfinite(time_to_go_s) or time_to_go_s <= 0:
        raise ValueError("Time to go must be positive and finite")
    t = time_to_go_s
    zem = target_position_m - (
        position_m + velocity_mps * t + 0.5 * gravity_mps2 * t**2
    )
    zev = target_velocity_mps - (velocity_mps + gravity_mps2 * t)
    return 6 * zem / t**2 - 2 * zev / t
