"""R4 powered descent trajectories (scenario S6).

Planar two-body model: spherical Moon, point-mass gravity (JPL GM), no mascons,
J2 or lunar rotation. Phase structure and gates follow NASA TN D-6846 (Apollo 11
premission profile): descent orbit insertion (Hohmann to 50 000 ft above the
site), coast, braking to high gate, approach to low gate, terminal descent.
Each powered phase uses closed-loop ZEM/ZEV guidance to its gate state and the
TN D-6846 throttle rule. Terrain clearance along the path is not checked.

Planar frame: origin at the Moon's centre, the site on +y at radius
R + terrain height, motion toward +x. Downrange is the arc length on the site
sphere (negative uprange); altitude is measured above the site sphere.
"""

import json
import math
from pathlib import Path

import numpy as np
import rasterio
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from rasterio.warp import transform

from app.descent_dynamics import advance_burn, zem_zev_acceleration
from app.horizon import _validate_dataset
from app.terrain_safety import _cached_dem
from shared.constants import (
    FOOT_M,
    MOON_GM_M3_S2,
    MOON_REFERENCE_RADIUS_M,
    NAUTICAL_MILE_M,
    POUND_FORCE_N,
    POUND_MASS_KG,
    STANDARD_GRAVITY_M_S2,
)

PROFILES_FILE = Path(__file__).resolve().parents[2] / "shared/vehicle_profiles.json"
DEFAULT_ORBIT_ALTITUDE_KM = 111.12  # 60 nautical miles (TN D-6846, Figure 1)
PDI_ALTITUDE_M = 50000 * FOOT_M  # TN D-6846: PDI near 50 000 ft above the site
GUIDANCE_HOLD_S = 2.0
MIN_PHASE_DURATION_S = 10.0  # well above the guidance hold window  # hold the last command as t_go -> 0 (singular gains)
PDI_RANGE_BOUNDS_M = (300e3, 700e3)
SEARCH_STEP_S = 0.5
FINAL_STEP_S = 0.1
MAX_SAMPLES = 400
ASSUMPTIONS = (
    "Spherical Moon with point-mass gravity (GM 4902.800118 km^3/s^2, JPL DE440); no mascons or J2.",
    "Planar motion in the orbit plane; lunar rotation (about 0.4 m/s at 85 deg S) is neglected.",
    "Initial orbit is circular; descent orbit insertion is an impulsive Hohmann burn to 50 000 ft above the site.",
    "Gates and phase durations follow the Apollo 11 premission profile (NASA TN D-6846, Table I).",
    "Closed-loop ZEM/ZEV guidance to each gate, not the Apollo P63/P64 flight software; no hover or redesignation.",
    "Engine: constant specific impulse; TN D-6846 throttle rule (10-60 % of design thrust, else fixed throttle position).",
    "Plane changes are impulsive in the initial orbit, before descent orbit insertion.",
    "Terrain clearance along the descent path is not checked.",
)


class DescentError(ValueError):
    """Invalid descent inputs or unusable terrain."""


class VehicleProfile(BaseModel):
    """SI vehicle parameters at powered descent initiation."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    profile_id: str = "custom"
    name: str = "Custom vehicle"
    mass_at_pdi_kg: float = Field(gt=100, le=100000)
    usable_propellant_kg: float = Field(gt=0, le=90000)
    max_thrust_n: float = Field(gt=0, le=1e6)
    design_thrust_n: float = Field(gt=0, le=1e6)
    min_throttle_fraction: float = Field(ge=0, lt=1)
    max_throttle_fraction: float = Field(gt=0, le=1)
    isp_s: float = Field(gt=50, le=500)


class OrbitSettings(BaseModel):
    """Initial circular orbit: altitude above the reference sphere and inclination."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    altitude_km: float = Field(default=DEFAULT_ORBIT_ALTITUDE_KM, ge=30, le=500)
    inclination_deg: float = Field(default=90.0, ge=0, le=180)


class Tolerances(BaseModel):
    """Touchdown success limits."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    position_m: float = Field(default=5.0, gt=0, le=1000)
    vertical_speed_mps: float = Field(default=1.0, gt=0, le=10)
    horizontal_speed_mps: float = Field(default=0.5, gt=0, le=10)


class DescentRequest(BaseModel):
    """Site (MOON_ME degrees), vehicle profile ID or custom profile, orbit and limits."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    profile_id: str = "apollo-lm-11"
    custom_profile: VehicleProfile | None = None
    orbit: OrbitSettings = Field(default_factory=OrbitSettings)
    tolerances: Tolerances = Field(default_factory=Tolerances)
    allow_plane_change: bool = False


def _document(path: Path) -> dict:
    return json.loads(Path(path).read_bytes().decode("utf-8"))


def load_profiles(path: Path = PROFILES_FILE) -> dict[str, VehicleProfile]:
    """Sourced profiles converted from document units to SI."""
    profiles = {}
    for row in _document(path)["profiles"]:
        profiles[row["profile_id"]] = VehicleProfile(
            profile_id=row["profile_id"],
            name=row["name"],
            mass_at_pdi_kg=row["mass_at_pdi_lb"] * POUND_MASS_KG,
            usable_propellant_kg=row["usable_propellant_lb"] * POUND_MASS_KG,
            max_thrust_n=row["max_thrust_lbf"] * POUND_FORCE_N,
            design_thrust_n=row["design_thrust_lbf"] * POUND_FORCE_N,
            min_throttle_fraction=row["min_throttle_fraction"],
            max_throttle_fraction=row["max_throttle_fraction"],
            isp_s=row["isp_s"],
        )
    return profiles


def apollo11_reference(path: Path = PROFILES_FILE) -> dict:
    """TN D-6846 Table I reference values in their published units."""
    return _document(path)["apollo11_reference"]


def hohmann_deorbit(orbit_radius_m: float, periapsis_radius_m: float) -> dict:
    """Delta-v (m/s), periapsis speed (m/s) and coast time (s) of the transfer.

    A retrograde impulse at the circular orbit lowers periapsis; ValueError when
    the periapsis is not strictly below the orbit or not positive.
    """
    if not 0 < periapsis_radius_m < orbit_radius_m:
        raise ValueError("Periapsis must be positive and below the initial orbit")
    a = (orbit_radius_m + periapsis_radius_m) / 2
    circular = math.sqrt(MOON_GM_M3_S2 / orbit_radius_m)
    apoapsis = math.sqrt(MOON_GM_M3_S2 * (2 / orbit_radius_m - 1 / a))
    periapsis = math.sqrt(MOON_GM_M3_S2 * (2 / periapsis_radius_m - 1 / a))
    return {
        "delta_v_mps": circular - apoapsis,
        "periapsis_speed_mps": periapsis,
        "coast_s": math.pi * math.sqrt(a**3 / MOON_GM_M3_S2),
        "circular_speed_mps": circular,
    }


def plane_change(latitude_deg: float, inclination_deg: float, speed_mps: float) -> dict:
    """Required plane rotation (deg) and impulsive delta-v (m/s) to overfly the site.

    A ground track reaches latitudes up to min(i, 180 - i); beyond that the
    plane must rotate by the difference: delta-v = 2 v sin(delta_i / 2).
    """
    reach = min(inclination_deg, 180 - inclination_deg)
    required = max(0.0, abs(latitude_deg) - reach)
    return {
        "required_deg": required,
        "delta_v_mps": 2 * speed_mps * math.sin(math.radians(required) / 2),
    }


def engine_thrust(command_n: float, profile: VehicleProfile) -> tuple[float, bool]:
    """Apply the TN D-6846 throttle rule; return (thrust N, saturated).

    Commands above the throttleable band use the fixed throttle position (maximum
    thrust); commands below the minimum are raised to it.
    """
    if command_n < 0 or not math.isfinite(command_n):
        raise ValueError("Thrust command must be a non-negative finite force")
    if command_n > profile.max_throttle_fraction * profile.design_thrust_n:
        return profile.max_thrust_n, command_n > profile.max_thrust_n
    floor = profile.min_throttle_fraction * profile.design_thrust_n
    if command_n < floor:
        return floor, False
    return command_n, False


def _state(site_radius_m, downrange_m, altitude_m, horizontal_mps, vertical_mps):
    angle = downrange_m / site_radius_m
    radius = site_radius_m + altitude_m
    up = np.array([math.sin(angle), math.cos(angle)])
    east = np.array([up[1], -up[0]])
    return radius * up, horizontal_mps * east + vertical_mps * up


def gate_states(
    site_radius_m: float,
    approach_s: float | None = None,
    terminal_s: float | None = None,
) -> list[dict]:
    """Apollo 11 gate targets as planar states relative to the site (TN D-6846).

    R5 may override the approach and terminal-descent durations (s); the gate
    states themselves stay at the Apollo 11 values.
    """
    for value in (approach_s, terminal_s):
        if value is not None and not (
            math.isfinite(value) and value >= MIN_PHASE_DURATION_S
        ):
            raise ValueError(
                f"Phase durations must be at least {MIN_PHASE_DURATION_S:g} s"
            )
    ref = apollo11_reference()
    high, low, touch = ref["high_gate"], ref["low_gate"], ref["touchdown"]
    high_horizontal = math.sqrt(
        high["inertial_velocity_fps"] ** 2 - high["altitude_rate_fps"] ** 2
    )
    low_range = (
        low["altitude_ft"] * FOOT_M / math.tan(math.radians(low["glide_angle_deg"]))
    )
    targets = [
        (
            "braking",
            high["time_s"],
            _state(
                site_radius_m,
                -high["range_nmi"] * NAUTICAL_MILE_M,
                high["altitude_ft"] * FOOT_M,
                high_horizontal * FOOT_M,
                high["altitude_rate_fps"] * FOOT_M,
            ),
        ),
        (
            "approach",
            approach_s if approach_s is not None else low["time_s"] - high["time_s"],
            _state(
                site_radius_m,
                -low_range,
                low["altitude_ft"] * FOOT_M,
                low["horizontal_velocity_fps"] * FOOT_M,
                low["altitude_rate_fps"] * FOOT_M,
            ),
        ),
        (
            "terminal",
            terminal_s if terminal_s is not None else touch["time_s"] - low["time_s"],
            _state(site_radius_m, 0.0, 0.0, 0.0, touch["altitude_rate_fps"] * FOOT_M),
        ),
    ]
    return [
        {
            "name": name,
            "duration_s": float(duration),
            "position_m": position,
            "velocity_mps": velocity,
        }
        for name, duration, (position, velocity) in targets
    ]


def _gravity(position):
    return -MOON_GM_M3_S2 * position / np.linalg.norm(position) ** 3


def _describe(position, velocity, site_radius_m):
    radius = np.linalg.norm(position)
    up = position / radius
    vertical = float(velocity @ up)
    horizontal = float(np.linalg.norm(velocity - vertical * up))
    angle = math.atan2(position[0], position[1])
    return {
        "downrange_m": float(angle * site_radius_m),
        "altitude_m": float(radius - site_radius_m),
        "vertical_speed_mps": vertical,
        "horizontal_speed_mps": horizontal,
        "speed_mps": float(np.linalg.norm(velocity)),
    }


def simulate_powered_descent(
    profile: VehicleProfile,
    site_radius_m: float,
    pdi_range_m: float,
    periapsis_speed_mps: float,
    dt_s: float = 0.1,
    record: bool = True,
    approach_s: float | None = None,
    terminal_s: float | None = None,
) -> dict:
    """Integrate braking, approach and terminal phases with RK4.

    Thrust magnitude and direction are held over each step. Delta-v per step is
    the rocket-equation value ve ln(m0 / m1). Each fuel-limited step is split
    at exhaustion, with unpowered coast over the remainder.
    """
    dry_kg = profile.mass_at_pdi_kg - profile.usable_propellant_kg
    position, velocity = _state(
        site_radius_m, -pdi_range_m, PDI_ALTITUDE_M, periapsis_speed_mps, 0.0
    )
    mass = profile.mass_at_pdi_kg
    clock = 0.0
    exhausted = False
    phases, samples = [], []
    command = np.zeros(2)

    def sample(phase, thrust):
        samples.append(
            {
                "t_s": clock,
                "phase": phase,
                **_describe(position, velocity, site_radius_m),
                "thrust_n": thrust,
                "throttle_fraction": thrust / profile.design_thrust_n,
                "mass_kg": mass,
            }
        )

    for gate in gate_states(site_radius_m, approach_s, terminal_s):
        start, elapsed = clock, 0.0
        phase_dv = 0.0
        phase_mass = mass
        saturated_s = 0.0
        duration = gate["duration_s"]
        while elapsed < duration - 1e-9:
            step = min(dt_s, duration - elapsed)
            time_to_go = duration - elapsed
            if time_to_go > GUIDANCE_HOLD_S:
                command = zem_zev_acceleration(
                    position,
                    velocity,
                    gate["position_m"],
                    gate["velocity_mps"],
                    time_to_go,
                    _gravity(position),
                )
            norm = float(np.linalg.norm(command))
            thrust, saturated = engine_thrust(mass * norm, profile)
            if mass <= dry_kg:
                thrust, exhausted = 0.0, True
            direction = command / norm if norm > 0 else np.array([0.0, 1.0])
            if record:
                sample(gate["name"], thrust)
            advanced = advance_burn(
                position,
                velocity,
                mass,
                dry_kg,
                thrust * direction,
                profile.isp_s,
                step,
            )
            position, velocity, mass = (
                advanced.position_m,
                advanced.velocity_mps,
                advanced.mass_kg,
            )
            exhausted = exhausted or advanced.propellant_exhausted
            phase_dv += advanced.delta_v_mps
            saturated_s += advanced.burn_s if saturated else 0.0
            elapsed += step
            clock += step
        end = _describe(position, velocity, site_radius_m)
        phases.append(
            {
                "name": gate["name"],
                "start_s": start,
                "end_s": clock,
                "delta_v_mps": phase_dv,
                "propellant_kg": phase_mass - mass,
                "target_position_error_m": float(
                    np.linalg.norm(position - gate["position_m"])
                ),
                "target_velocity_error_mps": float(
                    np.linalg.norm(velocity - gate["velocity_mps"])
                ),
                "end_altitude_m": end["altitude_m"],
                "end_downrange_m": end["downrange_m"],
                "end_speed_mps": end["speed_mps"],
                "saturated_s": saturated_s,
            }
        )
    if record:
        # Touchdown state; the engine is still at the last applied thrust at contact.
        sample("landed", 0.0 if mass <= dry_kg else thrust)
    final = _describe(position, velocity, site_radius_m)
    site = np.array([0.0, site_radius_m])
    return {
        "phases": phases,
        "samples": samples,
        "final": {
            "position_error_m": float(np.linalg.norm(position - site)),
            "vertical_speed_mps": final["vertical_speed_mps"],
            "horizontal_speed_mps": final["horizontal_speed_mps"],
            "mass_kg": mass,
        },
        "propellant_exhausted": exhausted,
    }


def _search_range(profile, site_radius_m, periapsis_speed_mps) -> float:
    """Golden-section search for the PDI range minimizing powered delta-v."""

    def cost(range_m):
        run = simulate_powered_descent(
            profile,
            site_radius_m,
            range_m,
            periapsis_speed_mps,
            SEARCH_STEP_S,
            record=False,
        )
        dv = sum(p["delta_v_mps"] for p in run["phases"])
        return dv + 100.0 * run["final"]["position_error_m"]

    low, high = PDI_RANGE_BOUNDS_M
    ratio = (math.sqrt(5) - 1) / 2
    a, b = high - ratio * (high - low), low + ratio * (high - low)
    fa, fb = cost(a), cost(b)
    while high - low > 1000.0:
        if fa <= fb:
            high, b, fb = b, a, fa
            a = high - ratio * (high - low)
            fa = cost(a)
        else:
            low, a, fa = a, b, fb
            b = low + ratio * (high - low)
            fb = cost(b)
    return round((low + high) / 2, 3)


def plan_descent(request: DescentRequest, terrain_height_m: float) -> dict:
    """Choose the PDI range, simulate and report feasibility and phase budgets."""
    if request.custom_profile is not None:
        profile = request.custom_profile
    else:
        profiles = load_profiles()
        if request.profile_id not in profiles:
            raise DescentError(f"Unknown vehicle profile {request.profile_id}")
        profile = profiles[request.profile_id]
    site_radius = MOON_REFERENCE_RADIUS_M + terrain_height_m
    orbit_radius = MOON_REFERENCE_RADIUS_M + request.orbit.altitude_km * 1000
    periapsis_radius = site_radius + PDI_ALTITUDE_M
    if periapsis_radius >= orbit_radius:
        raise DescentError(
            "Initial orbit is not above the PDI altitude (50 000 ft over the site)"
        )
    deorbit = hohmann_deorbit(orbit_radius, periapsis_radius)
    turn = plane_change(
        request.latitude_deg,
        request.orbit.inclination_deg,
        deorbit["circular_speed_mps"],
    )
    ve = profile.isp_s * STANDARD_GRAVITY_M_S2
    needs_turn = turn["required_deg"] > 0
    applied = needs_turn and request.allow_plane_change
    turn_propellant = 0.0
    vehicle = profile
    if applied:
        start_mass = profile.mass_at_pdi_kg * math.exp(-turn["delta_v_mps"] / ve)
        turn_propellant = profile.mass_at_pdi_kg - start_mass
        vehicle = profile.model_copy(
            update={
                "mass_at_pdi_kg": start_mass,
                "usable_propellant_kg": max(
                    profile.usable_propellant_kg - turn_propellant, 0.0
                ),
            }
        )
    pdi_range = _search_range(vehicle, site_radius, deorbit["periapsis_speed_mps"])
    run = simulate_powered_descent(
        vehicle, site_radius, pdi_range, deorbit["periapsis_speed_mps"], FINAL_STEP_S
    )
    powered_dv = sum(p["delta_v_mps"] for p in run["phases"])
    powered_prop = sum(p["propellant_kg"] for p in run["phases"])
    used = turn_propellant + powered_prop
    final = run["final"]
    tol = request.tolerances
    factors = []
    if needs_turn and not applied:
        factors.append(
            {
                "factor": "plane_change",
                "detail": f"Orbit inclination {request.orbit.inclination_deg:.2f} deg never overflies latitude {request.latitude_deg:.4f} deg; a {turn['required_deg']:.2f} deg plane change ({turn['delta_v_mps']:.1f} m/s) is required.",
                "value": turn["required_deg"],
                "limit": 0.0,
                "unit": "deg",
            }
        )
    if run["propellant_exhausted"] or used > profile.usable_propellant_kg:
        factors.append(
            {
                "factor": "propellant",
                "detail": f"Descent needs {used:.0f} kg of propellant; {profile.usable_propellant_kg:.0f} kg is available at PDI.",
                "value": used,
                "limit": profile.usable_propellant_kg,
                "unit": "kg",
            }
        )
    misses = []
    if final["position_error_m"] > tol.position_m:
        misses.append(
            f"position error {final['position_error_m']:.1f} m > {tol.position_m:g} m"
        )
    if abs(final["vertical_speed_mps"]) > tol.vertical_speed_mps:
        misses.append(
            f"vertical speed {abs(final['vertical_speed_mps']):.2f} m/s > {tol.vertical_speed_mps:g} m/s"
        )
    if final["horizontal_speed_mps"] > tol.horizontal_speed_mps:
        misses.append(
            f"horizontal speed {final['horizontal_speed_mps']:.2f} m/s > {tol.horizontal_speed_mps:g} m/s"
        )
    if misses:
        saturated = sum(p["saturated_s"] for p in run["phases"])
        factors.append(
            {
                "factor": "touchdown_tolerance",
                "detail": "Touchdown outside tolerance: "
                + "; ".join(misses)
                + (f" (thrust saturated for {saturated:.0f} s)" if saturated else "")
                + ".",
                "value": final["position_error_m"],
                "limit": tol.position_m,
                "unit": "m",
            }
        )
    coast = deorbit["coast_s"]
    phases = [
        {
            "name": "coast",
            "start_s": 0.0,
            "end_s": coast,
            "delta_v_mps": 0.0,
            "propellant_kg": 0.0,
            "end_altitude_m": PDI_ALTITUDE_M,
            "end_downrange_m": -pdi_range,
            "end_speed_mps": deorbit["periapsis_speed_mps"],
        }
    ] + [
        {**p, "start_s": coast + p["start_s"], "end_s": coast + p["end_s"]}
        for p in run["phases"]
    ]
    samples = run["samples"]
    stride = max(1, math.ceil(len(samples) / (MAX_SAMPLES - 1)))
    kept = samples[::stride]
    if kept[-1] is not samples[-1]:
        kept.append(samples[-1])
    reference = apollo11_reference()["total_powered_delta_v_fps"]
    model_fps = powered_dv / FOOT_M
    return {
        "feasible": not factors,
        "limiting_factors": factors,
        "site": {
            "site_id": request.site_id,
            "latitude_deg": request.latitude_deg,
            "longitude_deg_east": request.longitude_deg_east,
            "terrain_height_m": terrain_height_m,
            "radius_m": site_radius,
        },
        "profile": profile.model_dump(),
        "orbit": request.orbit.model_dump(),
        "tolerances": tol.model_dump(),
        "deorbit": {
            "delta_v_mps": deorbit["delta_v_mps"],
            "periapsis_speed_mps": deorbit["periapsis_speed_mps"],
            "coast_s": coast,
            "pdi_altitude_m": terrain_height_m + PDI_ALTITUDE_M,
            "note": "For the Apollo profile, DOI propellant is already spent in the mass at PDI.",
        },
        "plane_change": {**turn, "applied": applied, "propellant_kg": turn_propellant},
        "pdi_range_m": pdi_range,
        "phases": phases,
        "totals": {
            "powered_delta_v_mps": powered_dv,
            "total_delta_v_mps": deorbit["delta_v_mps"]
            + (turn["delta_v_mps"] if applied else 0.0)
            + powered_dv,
            "duration_s": coast + run["phases"][-1]["end_s"],
            "propellant_used_kg": used,
            "propellant_margin_kg": profile.usable_propellant_kg - used,
            "final_mass_kg": final["mass_kg"],
        },
        "touchdown": {
            "position_error_m": final["position_error_m"],
            "vertical_speed_mps": final["vertical_speed_mps"],
            "horizontal_speed_mps": final["horizontal_speed_mps"],
        },
        "reference_comparison": {
            "apollo11_total_powered_delta_v_fps": reference,
            "model_powered_delta_v_fps": model_fps,
            "difference_percent": 100 * (model_fps - reference) / reference,
        },
        "samples": kept,
        "assumptions": list(ASSUMPTIONS),
    }


def terrain_height_m(
    data_dir: Path, latitude_deg: float, longitude_deg_east: float
) -> float:
    """Height of the containing 80 m DEM pixel above the 1737.4 km sphere.

    FileNotFoundError means terrain must be acquired first; DescentError marks a
    site outside the cached raster or without data.
    """
    try:
        path = _cached_dem(data_dir)
    except FileNotFoundError:
        raise
    except ValueError as exc:
        raise DescentError(str(exc)) from exc
    with rasterio.open(path) as dataset:
        try:
            _validate_dataset(dataset)
        except ValueError as exc:
            raise DescentError(str(exc)) from exc
        x, y = transform(
            f"+proj=longlat +R={MOON_REFERENCE_RADIUS_M}",
            dataset.crs,
            [longitude_deg_east],
            [latitude_deg],
        )
        row, col = dataset.index(x[0], y[0])
        if not (0 <= row < dataset.height and 0 <= col < dataset.width):
            raise DescentError("Site is outside terrain coverage")
        value = dataset.read(
            1, window=rasterio.windows.Window(col, row, 1, 1), masked=True
        )[0, 0]
    if np.ma.is_masked(value) or not np.isfinite(value):
        raise DescentError("No terrain height at the selected site")
    return float(value)


def register_descent_routes(app, data_dir: Path) -> None:
    """GET /vehicle-profiles and POST /descents."""

    @app.get("/vehicle-profiles")
    def vehicle_profiles():
        document = _document(PROFILES_FILE)
        profiles = load_profiles()
        return {
            "profiles": [
                {
                    **profiles[row["profile_id"]].model_dump(),
                    "sources": row["sources"],
                    "urls": row["urls"],
                }
                for row in document["profiles"]
            ],
            "apollo11_reference": document["apollo11_reference"],
        }

    @app.post("/descents")
    def descents(body: DescentRequest):
        try:
            height = terrain_height_m(
                data_dir, body.latitude_deg, body.longitude_deg_east
            )
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except DescentError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return plan_descent(body, height)
        except DescentError as exc:
            raise HTTPException(422, str(exc)) from exc
