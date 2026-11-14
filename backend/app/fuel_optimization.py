"""R5 fuel optimization across descent routes (scenario S7).

Generates route variants of the R4 descent (initial orbit altitude, approach
duration, terminal-descent duration), estimates propellant from descent orbit
insertion to touchdown, rejects routes that violate the touchdown propellant
reserve or the R4 vehicle limits, and reports the fuel-versus-safety
trade-off. The default reserve is the NASA TN D-6846 Table II contingency
allowance: landing-site redesignation 60 fps plus manual hover 85 fps.

Propellant is counted from DOI ignition. For a profile whose mass is given at
PDI (after DOI from the 60 n.mi. reference orbit), the DOI mass is backed out
with the rocket equation, so routes from different orbits share one budget.
"""

import math
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.descent import (
    FINAL_STEP_S,
    PDI_ALTITUDE_M,
    DescentError,
    Tolerances,
    VehicleProfile,
    _search_range,
    hohmann_deorbit,
    load_profiles,
    plane_change,
    simulate_powered_descent,
    terrain_height_m,
)
from shared.constants import (
    FOOT_M,
    MOON_GM_M3_S2,
    MOON_REFERENCE_RADIUS_M,
    NAUTICAL_MILE_M,
    STANDARD_GRAVITY_M_S2,
)

DEFAULT_RESERVE_DELTA_V_MPS = (60 + 85) * FOOT_M
DEFAULT_ORBIT_ALTITUDES_KM = tuple(n * NAUTICAL_MILE_M / 1000 for n in (40, 60, 80))
DEFAULT_APPROACH_DURATIONS_S = (60.0, 100.0, 140.0)
DEFAULT_TERMINAL_DURATIONS_S = (60.0, 108.0, 150.0)
REFERENCE_ORBIT_ALTITUDE_KM = 60 * NAUTICAL_MILE_M / 1000
REFERENCE_APPROACH_S = 100.0
REFERENCE_TERMINAL_S = 108.0
RESERVE_SOURCE = (
    "NASA TN D-6846, Table II: contingency allowances for landing-site "
    "redesignation (60 fps) and manual hover (85 fps)"
)
ASSUMPTIONS = (
    "Each route reuses the R4 model: planar point-mass Moon, Hohmann DOI, ZEM/ZEV guidance through the Apollo 11 gates.",
    "Variants change only the initial orbit altitude and the approach and terminal-descent durations; gate states stay at Apollo 11 values.",
    "Propellant is counted from DOI ignition; the DOI mass is backed out from the PDI mass for the 60 n.mi. reference orbit.",
    "The reserve is propellant left at touchdown sufficient for the reserve delta-v at the landing mass (rocket equation).",
    "Hover margin is the hover time the propellant above the reserve buys at the landing mass (thrust = weight, constant Isp).",
    "Assessment time (approach + terminal descent) is the time available to see and assess the landing area; it is a planning proxy for safety, not a probability of success.",
)


class RouteRequest(BaseModel):
    """Site, vehicle, variant grid, propellant reserve and touchdown limits."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    profile_id: str = "apollo-lm-11"
    custom_profile: VehicleProfile | None = None
    inclination_deg: float = Field(default=90.0, ge=0, le=180)
    allow_plane_change: bool = False
    orbit_altitudes_km: list[float] = Field(
        default_factory=lambda: list(DEFAULT_ORBIT_ALTITUDES_KM),
        min_length=1,
        max_length=4,
    )
    approach_durations_s: list[float] = Field(
        default_factory=lambda: list(DEFAULT_APPROACH_DURATIONS_S),
        min_length=1,
        max_length=4,
    )
    terminal_durations_s: list[float] = Field(
        default_factory=lambda: list(DEFAULT_TERMINAL_DURATIONS_S),
        min_length=1,
        max_length=4,
    )
    reserve_delta_v_mps: float = Field(
        default=DEFAULT_RESERVE_DELTA_V_MPS, ge=0, le=500
    )
    tolerances: Tolerances = Field(default_factory=Tolerances)

    @field_validator("orbit_altitudes_km")
    @classmethod
    def bound_orbits(cls, values: list[float]) -> list[float]:
        if any(not 30 <= v <= 500 for v in values):
            raise ValueError("Orbit altitudes must be 30-500 km")
        return values

    @field_validator("approach_durations_s", "terminal_durations_s")
    @classmethod
    def bound_durations(cls, values: list[float]) -> list[float]:
        if any(not 20 <= v <= 400 for v in values):
            raise ValueError("Phase durations must be 20-400 s")
        return values


def _exhaust_velocity(isp_s: float) -> float:
    return isp_s * STANDARD_GRAVITY_M_S2


def mass_at_doi_kg(profile: VehicleProfile, reference_doi_delta_v_mps: float) -> float:
    """Back out the mass before DOI from the PDI mass (rocket equation)."""
    return profile.mass_at_pdi_kg * math.exp(
        reference_doi_delta_v_mps / _exhaust_velocity(profile.isp_s)
    )


def reserve_propellant_kg(
    landing_mass_kg: float, reserve_delta_v_mps: float, isp_s: float
) -> float:
    """Propellant that must remain at touchdown to fly the reserve delta-v."""
    if reserve_delta_v_mps < 0:
        raise ValueError("Reserve delta-v must be non-negative")
    return landing_mass_kg * (
        1 - math.exp(-reserve_delta_v_mps / _exhaust_velocity(isp_s))
    )


def hover_time_s(
    propellant_kg: float, landing_mass_kg: float, isp_s: float, gravity_mps2: float
) -> float:
    """Seconds of hover the given propellant buys at the landing mass.

    With thrust equal to weight, m(t) = m0 exp(-g t / ve), so
    t = ve / g * ln(m0 / (m0 - propellant)). Non-positive propellant gives 0.
    """
    if propellant_kg <= 0:
        return 0.0
    if propellant_kg >= landing_mass_kg:
        raise ValueError("Propellant cannot exceed the landing mass")
    return (
        _exhaust_velocity(isp_s)
        / gravity_mps2
        * math.log(landing_mass_kg / (landing_mass_kg - propellant_kg))
    )


def pareto_front(routes: list[dict]) -> list[str]:
    """IDs of feasible routes not dominated in (lower fuel, longer assessment).

    A route is dominated when another uses no more propellant and allows no less
    assessment time, and is strictly better in one. Sorted by fuel ascending.
    """
    feasible = [r for r in routes if r["feasible"]]
    front = []
    for r in feasible:
        dominated = any(
            o["propellant_used_kg"] <= r["propellant_used_kg"]
            and o["assessment_time_s"] >= r["assessment_time_s"]
            and (
                o["propellant_used_kg"] < r["propellant_used_kg"]
                or o["assessment_time_s"] > r["assessment_time_s"]
            )
            for o in feasible
        )
        if not dominated:
            front.append(r)
    front.sort(
        key=lambda r: (r["propellant_used_kg"], -r["assessment_time_s"], r["id"])
    )
    return [r["id"] for r in front]


def _reason(reason, detail, value=None, limit=None, unit=None) -> dict:
    return {
        "reason": reason,
        "detail": detail,
        "value": value,
        "limit": limit,
        "unit": unit,
    }


def _profile(request: RouteRequest) -> VehicleProfile:
    if request.custom_profile is not None:
        return request.custom_profile
    profiles = load_profiles()
    if request.profile_id not in profiles:
        raise DescentError(f"Unknown vehicle profile {request.profile_id}")
    return profiles[request.profile_id]


def optimize_routes(request: RouteRequest, terrain_height_m: float) -> dict:
    """Simulate the variant grid and classify, rank and explain each route."""
    profile = _profile(request)
    ve = _exhaust_velocity(profile.isp_s)
    site_radius = MOON_REFERENCE_RADIUS_M + terrain_height_m
    periapsis = site_radius + PDI_ALTITUDE_M
    reference_radius = MOON_REFERENCE_RADIUS_M + REFERENCE_ORBIT_ALTITUDE_KM * 1000
    if periapsis >= reference_radius:
        raise DescentError("Site is too high for the 60 n.mi. reference orbit")
    reference_doi = hohmann_deorbit(reference_radius, periapsis)["delta_v_mps"]
    doi_mass = mass_at_doi_kg(profile, reference_doi)
    budget = profile.usable_propellant_kg + (doi_mass - profile.mass_at_pdi_kg)
    gravity = MOON_GM_M3_S2 / site_radius**2
    tol = request.tolerances
    routes = []
    index = 0
    for altitude in request.orbit_altitudes_km:
        orbit_radius = MOON_REFERENCE_RADIUS_M + altitude * 1000
        combos = [
            (a, t)
            for a in request.approach_durations_s
            for t in request.terminal_durations_s
        ]
        if periapsis >= orbit_radius:
            for a, t in combos:
                index += 1
                routes.append(
                    {
                        "id": f"r{index:02d}",
                        "orbit_altitude_km": altitude,
                        "approach_s": a,
                        "terminal_s": t,
                        "assessment_time_s": a + t,
                        "feasible": False,
                        "rejection_reasons": [
                            _reason(
                                "orbit",
                                f"A {altitude:g} km orbit is not above the PDI altitude (50 000 ft over the site).",
                                altitude,
                                (periapsis - MOON_REFERENCE_RADIUS_M) / 1000,
                                "km",
                            )
                        ],
                    }
                )
            continue
        deorbit = hohmann_deorbit(orbit_radius, periapsis)
        turn = plane_change(
            request.latitude_deg,
            request.inclination_deg,
            deorbit["circular_speed_mps"],
        )
        needs_turn = turn["required_deg"] > 0
        applied = needs_turn and request.allow_plane_change
        start_mass = doi_mass * (math.exp(-turn["delta_v_mps"] / ve) if applied else 1)
        pdi_mass = start_mass * math.exp(-deorbit["delta_v_mps"] / ve)
        at_pdi = budget - (doi_mass - pdi_mass)
        if not applied and abs(deorbit["delta_v_mps"] - reference_doi) < 1e-12:
            vehicle = profile  # the reference orbit reproduces the R4 inputs exactly
        else:
            vehicle = profile.model_copy(
                update={
                    "mass_at_pdi_kg": pdi_mass,
                    "usable_propellant_kg": max(at_pdi, 0.0),
                }
            )
        pdi_range = _search_range(vehicle, site_radius, deorbit["periapsis_speed_mps"])
        for a, t in combos:
            index += 1
            run = simulate_powered_descent(
                vehicle,
                site_radius,
                pdi_range,
                deorbit["periapsis_speed_mps"],
                FINAL_STEP_S,
                record=False,
                approach_s=a,
                terminal_s=t,
            )
            final = run["final"]
            landing = final["mass_kg"]
            used = doi_mass - landing
            remaining = budget - used
            required = reserve_propellant_kg(
                landing, request.reserve_delta_v_mps, profile.isp_s
            )
            margin = remaining - required
            powered = sum(p["delta_v_mps"] for p in run["phases"])
            reasons = []
            if needs_turn and not applied:
                reasons.append(
                    _reason(
                        "plane_change",
                        f"Inclination {request.inclination_deg:g} deg never overflies latitude {request.latitude_deg:g} deg; a {turn['required_deg']:.2f} deg plane change ({turn['delta_v_mps']:.1f} m/s) is required.",
                        turn["required_deg"],
                        0.0,
                        "deg",
                    )
                )
            if run["propellant_exhausted"] or remaining < 0:
                reasons.append(
                    _reason(
                        "propellant",
                        f"Route needs {used:.0f} kg; {budget:.0f} kg is available from DOI.",
                        used,
                        budget,
                        "kg",
                    )
                )
            elif margin < 0:
                reasons.append(
                    _reason(
                        "reserve",
                        f"Only {remaining:.0f} kg remains at touchdown; the {request.reserve_delta_v_mps:.1f} m/s reserve needs {required:.0f} kg.",
                        remaining,
                        required,
                        "kg",
                    )
                )
            misses = []
            if final["position_error_m"] > tol.position_m:
                misses.append(f"position error {final['position_error_m']:.1f} m")
            if abs(final["vertical_speed_mps"]) > tol.vertical_speed_mps:
                misses.append(
                    f"vertical speed {abs(final['vertical_speed_mps']):.2f} m/s"
                )
            if final["horizontal_speed_mps"] > tol.horizontal_speed_mps:
                misses.append(
                    f"horizontal speed {final['horizontal_speed_mps']:.2f} m/s"
                )
            if misses:
                reasons.append(
                    _reason(
                        "touchdown_tolerance",
                        "Touchdown outside tolerance: " + "; ".join(misses) + ".",
                        final["position_error_m"],
                        tol.position_m,
                        "m",
                    )
                )
            routes.append(
                {
                    "id": f"r{index:02d}",
                    "orbit_altitude_km": altitude,
                    "approach_s": a,
                    "terminal_s": t,
                    "assessment_time_s": a + t,
                    "feasible": not reasons,
                    "rejection_reasons": reasons,
                    "propellant_used_kg": used,
                    "propellant_remaining_kg": remaining,
                    "reserve_required_kg": required,
                    "reserve_margin_kg": margin,
                    "hover_margin_s": hover_time_s(
                        margin, landing, profile.isp_s, gravity
                    ),
                    "landing_mass_kg": landing,
                    "doi_delta_v_mps": deorbit["delta_v_mps"],
                    "plane_change_delta_v_mps": turn["delta_v_mps"] if applied else 0.0,
                    "powered_delta_v_mps": powered,
                    "delta_v_mps": deorbit["delta_v_mps"]
                    + (turn["delta_v_mps"] if applied else 0.0)
                    + powered,
                    "duration_s": deorbit["coast_s"] + run["phases"][-1]["end_s"],
                    "pdi_range_m": pdi_range,
                    "saturated_s": sum(p["saturated_s"] for p in run["phases"]),
                    "touchdown": {
                        "position_error_m": final["position_error_m"],
                        "vertical_speed_mps": final["vertical_speed_mps"],
                        "horizontal_speed_mps": final["horizontal_speed_mps"],
                    },
                }
            )
    feasible = [r for r in routes if r["feasible"]]
    lowest = min(
        feasible, key=lambda r: (r["propellant_used_kg"], r["id"]), default=None
    )
    longest = min(
        feasible,
        key=lambda r: (-r["assessment_time_s"], r["propellant_used_kg"], r["id"]),
        default=None,
    )
    reference = next(
        (
            r
            for r in routes
            if abs(r["orbit_altitude_km"] - REFERENCE_ORBIT_ALTITUDE_KM) < 1e-6
            and r["approach_s"] == REFERENCE_APPROACH_S
            and r["terminal_s"] == REFERENCE_TERMINAL_S
        ),
        None,
    )
    return {
        "site": {
            "site_id": request.site_id,
            "latitude_deg": request.latitude_deg,
            "longitude_deg_east": request.longitude_deg_east,
            "terrain_height_m": terrain_height_m,
        },
        "profile": profile.model_dump(),
        "reserve": {
            "delta_v_mps": request.reserve_delta_v_mps,
            "source": RESERVE_SOURCE,
        },
        "mass_at_doi_kg": doi_mass,
        "propellant_at_doi_kg": budget,
        "routes": routes,
        "feasible_ids": [r["id"] for r in feasible],
        "rejected_ids": [r["id"] for r in routes if not r["feasible"]],
        "pareto_ids": pareto_front(routes),
        "lowest_fuel_id": lowest["id"] if lowest else None,
        "longest_assessment_id": longest["id"] if longest else None,
        "reference_route_id": reference["id"] if reference else None,
        "assumptions": list(ASSUMPTIONS),
    }


def register_fuel_routes(app, data_dir: Path) -> None:
    """POST /descents/optimize."""

    @app.post("/descents/optimize")
    def optimize(body: RouteRequest):
        try:
            height = terrain_height_m(
                data_dir, body.latitude_deg, body.longitude_deg_east
            )
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except DescentError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return optimize_routes(body, height)
        except DescentError as exc:
            raise HTTPException(422, str(exc)) from exc
