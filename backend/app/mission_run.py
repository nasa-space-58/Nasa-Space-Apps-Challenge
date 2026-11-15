"""Mission run: physics-gated mission outcome with injectable fault scenarios.

A mission run replays one landing attempt through ordered gates, each computed
from existing physics rather than from a random failure rate:

1. descent: the R4 planar powered descent (ZEM/ZEV guidance, TN D-6846 gates)
   flown again with time-activated vehicle faults (thrust loss, propellant leak,
   specific-impulse loss). Ground contact is detected on the site sphere and
   judged against touchdown speed limits.
2. touchdown terrain: the R3 Horn slope of the cached LOLA 80 m DEM at the
   actual touchdown point, shifted by guidance miss and navigation bias along an
   explicit approach azimuth (the planar model has no MOON_ME heading).
3. surface power: an ideal battery propagated exactly over the analysis' sunlit,
   partial and dark intervals with constant loads and surface faults.
4. communication: Earth-centre visibility from the analysis, minus fault
   outages, against a first-contact deadline and a maximum contact gap.

Unknown sunlight or Earth intervals are bracketed by pessimistic and optimistic
propagations, so an outcome that depends on them is reported as unknown, never
guessed. The run is a deterministic educational simulation: it is not landing
safety certification, a battery or thermal model, a DSN booking or a link budget.
"""

import json
import math
from bisect import bisect_right
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Callable, Literal, get_args

import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.descent import (
    GUIDANCE_HOLD_S,
    PDI_ALTITUDE_M,
    DescentError,
    DescentRequest,
    VehicleProfile,
    _describe,
    _gravity,
    _state,
    engine_thrust,
    gate_states,
    load_profiles,
    plan_descent,
    terrain_height_m,
)
from app.descent_dynamics import advance_burn, zem_zev_acceleration
from app.terrain_safety import SafetySettings, TerrainSafetyError, terrain_safety
from app.visibility import stamp, utc
from shared.constants import MOON_REFERENCE_RADIUS_M

SCENARIOS_FILE = Path(__file__).resolve().parents[2] / "shared/mission_faults.json"

# Apollo Experience Report - Lunar Module Landing Gear Subsystem (NASA TN D-6850,
# 1972): the gear was designed for touchdown up to 10 ft/s vertical and 4 ft/s
# horizontal velocity. Used as configurable structural limits, not certification.
DEFAULT_CRASH_VERTICAL_MPS = 3.048
DEFAULT_CRASH_HORIZONTAL_MPS = 1.2192
# Soft-landing goal, matching the R4 touchdown tolerances.
DEFAULT_SOFT_VERTICAL_MPS = 1.0
DEFAULT_SOFT_HORIZONTAL_MPS = 0.5
# End-of-schedule altitudes at or below this count as contact (TN D-6846: the
# LM probes signal contact about 5.6 ft above the surface; 1 m is conservative).
CONTACT_ALTITUDE_M = 1.0
# Bounded post-schedule descent when the vehicle is still airborne.
EXTENSION_LIMIT_S = 600.0
EXTENSION_DESCENT_RATE_MPS = 0.9144  # TN D-6846 touchdown rate, 3 ft/s
EXTENSION_GAIN_PER_S = 0.5
# Touchdowns farther than this from the target are reported as a warning.
TARGET_MISS_WARNING_M = 50.0
MAX_DESCENT_SAMPLES = 400
MAX_FAULTS = 12

ASSUMPTIONS = (
    "Descent: R4 planar point-mass model and ZEM/ZEV guidance; faults act only from their activation time and are known to no guidance logic.",
    "A propellant leak removes mass without producing thrust; the leak's momentum is neglected.",
    "Ground contact is evaluated on the site sphere; terrain along the descent path is not checked.",
    "Touchdown point: along-track guidance miss along the explicit approach azimuth plus navigation bias, on the 1737.4 km sphere.",
    "Tip-over: Horn slope of the 80 m LOLA pixel containing the touchdown point against the slope limit; lander-scale boulders are not resolved.",
    "Power: ideal battery (no efficiency, temperature or ageing), full array output in full sun, a fixed fraction in partial sun, none in darkness.",
    "Communication: geometric Earth-centre visibility only; no DSN schedule, antenna pointing or link budget.",
    "Unknown Sun/Earth intervals are bracketed by pessimistic and optimistic runs; a result that depends on them is unknown.",
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# --- Faults ---------------------------------------------------------------


class EngineThrustLoss(Contract):
    """From activation, thrust is capped at a fraction of maximum thrust."""

    kind: Literal["engine_thrust_loss"] = "engine_thrust_loss"
    start_after_pdi_s: float = Field(ge=0, le=1200)
    thrust_fraction: float = Field(gt=0, lt=1)


class PropellantLeak(Contract):
    """Propellant is lost at a constant rate during the leak window."""

    kind: Literal["propellant_leak"] = "propellant_leak"
    start_after_pdi_s: float = Field(ge=0, le=1200)
    leak_rate_kg_s: float = Field(gt=0, le=50)
    duration_s: float = Field(gt=0, le=1200)


class IspDegradation(Contract):
    """From activation, specific impulse is reduced to a fraction of nominal."""

    kind: Literal["isp_degradation"] = "isp_degradation"
    start_after_pdi_s: float = Field(ge=0, le=1200)
    isp_fraction: float = Field(gt=0.3, lt=1)


class NavigationBias(Contract):
    """The navigation estimate is offset; guidance lands that far from the target."""

    kind: Literal["navigation_bias"] = "navigation_bias"
    offset_m: float = Field(gt=0, le=5000)
    bearing_deg: float = Field(ge=0, lt=360)


class LandingDelay(Contract):
    """Wave-off: the landing happens later inside the analysis interval."""

    kind: Literal["landing_delay"] = "landing_delay"
    delay_h: float = Field(gt=0, le=720)


class BatteryCapacityLoss(Contract):
    """From activation, a fraction of battery capacity (and its charge) is lost."""

    kind: Literal["battery_capacity_loss"] = "battery_capacity_loss"
    start_after_landing_h: float = Field(ge=0, le=720)
    capacity_fraction_lost: float = Field(gt=0, lt=1)


class PowerLoadIncrease(Contract):
    """An additional constant load, e.g. a stuck-on heater, for a window."""

    kind: Literal["power_load_increase"] = "power_load_increase"
    start_after_landing_h: float = Field(ge=0, le=720)
    duration_h: float = Field(gt=0, le=720)
    extra_load_w: float = Field(gt=0, le=5000)


class SolarArrayDegradation(Contract):
    """From activation, array output is reduced, e.g. by dust deposition."""

    kind: Literal["solar_array_degradation"] = "solar_array_degradation"
    start_after_landing_h: float = Field(ge=0, le=720)
    output_fraction: float = Field(ge=0, lt=1)


class CommunicationOutage(Contract):
    """No Earth contact is possible during the outage window."""

    kind: Literal["communication_outage"] = "communication_outage"
    start_after_landing_h: float = Field(ge=0, le=720)
    duration_h: float = Field(gt=0, le=720)


Fault = Annotated[
    EngineThrustLoss
    | PropellantLeak
    | IspDegradation
    | NavigationBias
    | LandingDelay
    | BatteryCapacityLoss
    | PowerLoadIncrease
    | SolarArrayDegradation
    | CommunicationOutage,
    Field(discriminator="kind"),
]
DESCENT_FAULTS = ("engine_thrust_loss", "propellant_leak", "isp_degradation")


class FaultScenario(Contract):
    """A curated, named fault combination from shared/mission_faults.json."""

    scenario_id: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9-]+$")
    summary: str = Field(min_length=1, max_length=300)
    faults: tuple[Fault, ...] = Field(max_length=MAX_FAULTS)


# --- Settings -------------------------------------------------------------


class TouchdownLimits(Contract):
    """Soft-landing goal and structural crash limits (m/s)."""

    soft_vertical_mps: float = Field(default=DEFAULT_SOFT_VERTICAL_MPS, gt=0, le=10)
    soft_horizontal_mps: float = Field(default=DEFAULT_SOFT_HORIZONTAL_MPS, gt=0, le=10)
    crash_vertical_mps: float = Field(default=DEFAULT_CRASH_VERTICAL_MPS, gt=0, le=20)
    crash_horizontal_mps: float = Field(
        default=DEFAULT_CRASH_HORIZONTAL_MPS, gt=0, le=20
    )


class PowerSystem(Contract):
    """Illustrative lander power system (not a sourced NASA vehicle)."""

    battery_capacity_wh: float = Field(default=4000.0, gt=0, le=200000)
    initial_state_of_charge: float = Field(default=0.9, gt=0, le=1)
    solar_array_power_w: float = Field(default=900.0, ge=0, le=50000)
    partial_sun_output_fraction: float = Field(default=0.5, ge=0, le=1)
    base_load_w: float = Field(default=350.0, gt=0, le=20000)


class CommunicationRules(Contract):
    """Touchdown confirmation deadline and longest tolerated silence (hours)."""

    first_contact_deadline_h: float = Field(default=1.0, gt=0, le=240)
    max_contact_gap_h: float = Field(default=72.0, gt=0, le=720)


class MissionRunRequest(Contract):
    """Vehicle, optional curated scenario plus custom faults, and gate settings."""

    profile_id: str = "apollo-lm-11"
    scenario_id: str | None = Field(default=None, max_length=60)
    faults: tuple[Fault, ...] = Field(default=(), max_length=MAX_FAULTS)
    approach_azimuth_deg: float = Field(default=0.0, ge=0, lt=360)
    touchdown: TouchdownLimits = Field(default_factory=TouchdownLimits)
    terrain: SafetySettings = Field(default_factory=SafetySettings)
    power: PowerSystem = Field(default_factory=PowerSystem)
    communication: CommunicationRules = Field(default_factory=CommunicationRules)


class MissionRunError(ValueError):
    """Invalid run inputs, e.g. an unknown scenario or a delay past the window."""


# --- Functions ------------------------------------------------------------

GATES = ("descent", "touchdown_terrain", "power", "communication")
FAULT_PHASES = {
    "engine_thrust_loss": "descent",
    "propellant_leak": "descent",
    "isp_degradation": "descent",
    "navigation_bias": "descent",
    "landing_delay": "landing",
}
DISCLAIMER = (
    "Deterministic educational mission simulation from sampled physics and "
    "illustrative vehicle settings; not landing-safety certification, a power "
    "or thermal qualification, a DSN booking or a link budget."
)
HOUR = timedelta(hours=1)
SUN_STATES = {"full", "partial", "dark", "unknown", "boundary"}
EARTH_STATES = {"visible", "blocked", "unknown", "boundary"}


def load_scenarios(path: Path = SCENARIOS_FILE) -> dict[str, FaultScenario]:
    """Curated scenarios by ID; ValueError on duplicates or invalid entries."""
    scenarios = {}
    for row in _document(path)["scenarios"]:
        scenario = FaultScenario.model_validate(row)
        if scenario.scenario_id in scenarios:
            raise ValueError(f"Duplicate fault scenario {scenario.scenario_id}")
        scenarios[scenario.scenario_id] = scenario
    return scenarios


def effective_faults(
    request: MissionRunRequest, scenarios: dict[str, FaultScenario]
) -> tuple:
    """Scenario faults followed by custom faults; MissionRunError on unknown ID,
    more than MAX_FAULTS in total or more than one landing delay."""
    faults = ()
    if request.scenario_id is not None:
        if request.scenario_id not in scenarios:
            raise MissionRunError(f"Unknown fault scenario {request.scenario_id}")
        faults = tuple(scenarios[request.scenario_id].faults)
    faults += tuple(request.faults)
    if len(faults) > MAX_FAULTS:
        raise MissionRunError(f"At most {MAX_FAULTS} faults can be combined")
    if sum(f.kind == "landing_delay" for f in faults) > 1:
        raise MissionRunError("At most one landing delay can be applied")
    return faults


def destination_point(
    latitude_deg: float,
    longitude_deg_east: float,
    distance_m: float,
    bearing_deg: float,
    radius_m: float,
) -> tuple[float, float]:
    """Great-circle destination (MOON_ME degrees, longitude in [0, 360))."""
    if distance_m == 0:
        return float(latitude_deg), float(longitude_deg_east) % 360.0
    phi, lam = math.radians(latitude_deg), math.radians(longitude_deg_east)
    delta, theta = distance_m / radius_m, math.radians(bearing_deg)
    sin_phi = math.sin(phi) * math.cos(delta) + math.cos(phi) * math.sin(
        delta
    ) * math.cos(theta)
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi)))
    lam2 = lam + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi),
        math.cos(delta) - math.sin(phi) * math.sin(phi2),
    )
    return math.degrees(phi2), math.degrees(lam2) % 360.0


def touchdown_offset(
    downrange_m: float, approach_azimuth_deg: float, faults: tuple
) -> tuple[float, float]:
    """(distance m, bearing deg from north) of the touchdown from the target.

    Along-track miss (positive = overshoot) lies along the approach azimuth;
    navigation biases add as local east/north vectors.
    """
    azimuth = math.radians(approach_azimuth_deg)
    east, north = downrange_m * math.sin(azimuth), downrange_m * math.cos(azimuth)
    for fault in faults:
        if fault.kind == "navigation_bias":
            bearing = math.radians(fault.bearing_deg)
            east += fault.offset_m * math.sin(bearing)
            north += fault.offset_m * math.cos(bearing)
    distance = math.hypot(east, north)
    if distance == 0:
        return 0.0, 0.0
    return distance, math.degrees(math.atan2(east, north)) % 360.0


def _overlap_s(start_s, end_s, window_start_s, window_end_s) -> float:
    return max(0.0, min(end_s, window_end_s) - max(start_s, window_start_s))


def _sphere_crossing(p0, p1, radius) -> float:
    """Fraction f in [0, 1] where |p0 + f (p1 - p0)| = radius (p0 outside)."""
    d = p1 - p0
    a, b, c = float(d @ d), 2 * float(p0 @ d), float(p0 @ p0) - radius**2
    root = math.sqrt(max(b * b - 4 * a * c, 0.0))
    # Numerically stable smaller root of a f^2 + b f + c = 0 with c > 0, b < 0.
    f = 2 * c / (-b + root) if -b + root > 0 else 1.0
    return max(0.0, min(1.0, f))


def fly_faulted_descent(
    profile: VehicleProfile,
    site_radius_m: float,
    pdi_range_m: float,
    periapsis_speed_mps: float,
    faults: tuple,
    dt_s: float = 0.1,
) -> dict:
    """Fly the R4 gate schedule from PDI with time-activated vehicle faults.

    Without faults this matches simulate_powered_descent step for step. The run
    stops at ground contact (interpolated inside the step), extends a bounded
    controlled descent when still airborne after the schedule, and returns
    samples, events, contact state, propellant use and exhaustion.
    """
    vehicle = [f for f in faults if f.kind in DESCENT_FAULTS]
    dry_kg = profile.mass_at_pdi_kg - profile.usable_propellant_kg
    position, velocity = _state(
        site_radius_m, -pdi_range_m, PDI_ALTITUDE_M, periapsis_speed_mps, 0.0
    )
    state = dict(mass=float(profile.mass_at_pdi_kg), clock=0.0, burned=0.0, leaked=0.0)
    samples, events, announced = [], [], set()
    result = dict(contact=None, exhausted_at=None)

    def describe(p, v):
        return _describe(p, v, site_radius_m)

    def record(phase, p, v, thrust, mass, t):
        d = describe(p, v)
        samples.append(
            {
                "t_s": t,
                "phase": phase,
                "altitude_m": d["altitude_m"],
                "downrange_m": d["downrange_m"],
                "vertical_speed_mps": d["vertical_speed_mps"],
                "horizontal_speed_mps": d["horizontal_speed_mps"],
                "thrust_n": thrust,
                "mass_kg": mass,
            }
        )

    def touch(p, v, mass, t):
        d = describe(p, v)
        result["contact"] = {
            "t_s": t,
            "altitude_m": d["altitude_m"],
            "downrange_m": d["downrange_m"],
            "vertical_speed_mps": d["vertical_speed_mps"],
            "horizontal_speed_mps": d["horizontal_speed_mps"],
            "speed_mps": d["speed_mps"],
            "mass_kg": mass,
        }
        record("landed", p, v, 0.0, mass, t)
        events.append(
            {
                "t_s": t,
                "kind": "contact",
                "detail": f"Ground contact at {abs(d['vertical_speed_mps']):.2f} m/s vertical and {d['horizontal_speed_mps']:.2f} m/s horizontal.",
            }
        )

    def step(command, phase, step_s):
        nonlocal position, velocity
        clock, mass = state["clock"], state["mass"]
        thrust_fraction, isp_fraction, leak_rate = 1.0, 1.0, []
        for index, fault in enumerate(vehicle):
            if fault.kind == "propellant_leak":
                overlap = _overlap_s(
                    clock,
                    clock + step_s,
                    fault.start_after_pdi_s,
                    fault.start_after_pdi_s + fault.duration_s,
                )
                if overlap > 0:
                    leak_rate.append(fault.leak_rate_kg_s * overlap)
                active = overlap > 0
            else:
                active = clock >= fault.start_after_pdi_s - 1e-9
                if active and fault.kind == "engine_thrust_loss":
                    thrust_fraction = min(thrust_fraction, fault.thrust_fraction)
                if active and fault.kind == "isp_degradation":
                    isp_fraction = min(isp_fraction, fault.isp_fraction)
            if active and index not in announced:
                announced.add(index)
                events.append(
                    {
                        "t_s": max(clock, fault.start_after_pdi_s),
                        "kind": "fault_activated",
                        "fault": fault.kind,
                        "detail": f"Fault {fault.kind} active.",
                    }
                )
        norm = float(np.linalg.norm(command))
        thrust, _ = engine_thrust(mass * norm, profile)
        if thrust_fraction < 1:
            thrust = min(thrust, thrust_fraction * profile.max_thrust_n)
        if mass <= dry_kg:
            thrust = 0.0
        direction = command / norm if norm > 0 else np.array([0.0, 1.0])
        record(phase, position, velocity, thrust, mass, clock)
        advanced = advance_burn(
            position,
            velocity,
            mass,
            dry_kg,
            thrust * direction,
            profile.isp_s * isp_fraction,
            step_s,
        )
        new_mass = advanced.mass_kg
        state["burned"] += mass - new_mass
        leak = sum(leak_rate)
        if leak > 0:
            leak = min(leak, new_mass - dry_kg)
            state["leaked"] += leak
            new_mass = max(dry_kg, new_mass - leak)
        if result["exhausted_at"] is None and mass > dry_kg and new_mass <= dry_kg:
            at = clock + (advanced.burn_s if advanced.burn_s < step_s else step_s)
            altitude = describe(advanced.position_m, advanced.velocity_mps)
            result["exhausted_at"] = {"t_s": at, "altitude_m": altitude["altitude_m"]}
            events.append(
                {
                    "t_s": at,
                    "kind": "propellant_exhausted",
                    "detail": f"Propellant exhausted {altitude['altitude_m']:.0f} m above the site; the engine stops.",
                }
            )
        start_radius = float(np.linalg.norm(position))
        end_radius = float(np.linalg.norm(advanced.position_m))
        if start_radius > site_radius_m >= end_radius:
            f = _sphere_crossing(position, advanced.position_m, site_radius_m)
            p = position + f * (advanced.position_m - position)
            v = velocity + f * (advanced.velocity_mps - velocity)
            m = mass + f * (new_mass - mass)
            position, velocity = p, v
            state.update(mass=m, clock=clock + f * step_s)
            touch(p, v, m, clock + f * step_s)
            return True
        position, velocity = advanced.position_m, advanced.velocity_mps
        state.update(mass=new_mass, clock=clock + step_s)
        return False

    landed = False
    command = np.zeros(2)
    for gate in gate_states(site_radius_m):
        elapsed, duration = 0.0, gate["duration_s"]
        while elapsed < duration - 1e-9 and not landed:
            step_s = min(dt_s, duration - elapsed)
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
            landed = step(command, gate["name"], step_s)
            elapsed += step_s
        if landed:
            break
    if not landed:
        altitude = describe(position, velocity)["altitude_m"]
        if altitude <= CONTACT_ALTITUDE_M:
            touch(position, velocity, state["mass"], state["clock"])
            landed = True
        else:
            events.append(
                {
                    "t_s": state["clock"],
                    "kind": "schedule_end_airborne",
                    "detail": f"Guidance schedule ended {altitude:.0f} m above the site; continuing a bounded controlled descent.",
                }
            )
            extended = 0.0
            while extended < EXTENSION_LIMIT_S - 1e-9 and not landed:
                up = position / np.linalg.norm(position)
                desired = -EXTENSION_DESCENT_RATE_MPS * up
                command = -_gravity(position) + EXTENSION_GAIN_PER_S * (
                    desired - velocity
                )
                landed = step(command, "extension", dt_s)
                extended += dt_s
            if not landed:
                events.append(
                    {
                        "t_s": state["clock"],
                        "kind": "no_contact",
                        "detail": f"No ground contact within {EXTENSION_LIMIT_S:.0f} s after the guidance schedule.",
                    }
                )
    return {
        "contact": result["contact"],
        "exhausted_at": result["exhausted_at"],
        "events": sorted(events, key=lambda e: e["t_s"]),
        "samples": samples,
        "propellant_used_kg": state["burned"],
        "propellant_leaked_kg": state["leaked"],
        "propellant_remaining_kg": state["mass"] - dry_kg,
        "mass_kg": state["mass"],
        "final_position_m": np.asarray(position, dtype=float).tolist(),
        "final_velocity_mps": np.asarray(velocity, dtype=float).tolist(),
    }


def _segments(intervals, start_utc, end_utc, allowed) -> list[tuple]:
    """Clip intervals to [start, end]; gaps become 'unknown'."""
    rows = []
    for row in intervals:
        if row["state"] not in allowed:
            raise ValueError(f"Unsupported interval state {row['state']}")
        a, b = max(utc(row["start_utc"]), start_utc), min(utc(row["end_utc"]), end_utc)
        if b > a:
            rows.append((a, b, row["state"]))
    rows.sort()
    filled, cursor = [], start_utc
    for a, b, value in rows:
        if a > cursor:
            filled.append((cursor, a, "unknown"))
        a = max(a, cursor)
        if b > a:
            filled.append((a, b, value))
            cursor = b
    if cursor < end_utc:
        filled.append((cursor, end_utc, "unknown"))
    return filled


def _fault_window(fault, start_utc):
    begin = start_utc + fault.start_after_landing_h * HOUR
    duration = getattr(fault, "duration_h", None)
    return begin, begin + duration * HOUR if duration is not None else None


def power_trace(
    sun_intervals: list[dict],
    start_utc: datetime,
    end_utc: datetime,
    power: PowerSystem,
    faults: tuple,
    unknown_generation: Literal["none", "full"],
) -> dict:
    """Exact piecewise-linear battery energy (Wh) from landing to end_utc.

    Returns the trace, minimum energy and the depletion time (None if never).
    """
    if end_utc <= start_utc:
        raise ValueError("Surface window must have positive duration")
    segments = _segments(sun_intervals, start_utc, end_utc, SUN_STATES)
    surface = [
        f
        for f in faults
        if f.kind
        in ("battery_capacity_loss", "power_load_increase", "solar_array_degradation")
    ]
    cuts = {start_utc, end_utc} | {s for s, _, _ in segments}
    for fault in surface:
        for moment in _fault_window(fault, start_utc):
            if moment is not None and start_utc < moment < end_utc:
                cuts.add(moment)
    cuts = sorted(cuts)
    starts = [s for s, _, _ in segments]
    applied = set()
    capacity = power.battery_capacity_wh
    energy = power.initial_state_of_charge * capacity

    def apply_losses(moment):
        nonlocal capacity, energy
        for index, fault in enumerate(surface):
            if fault.kind != "battery_capacity_loss" or index in applied:
                continue
            if _fault_window(fault, start_utc)[0] <= moment:
                applied.add(index)
                capacity *= 1 - fault.capacity_fraction_lost
                energy *= 1 - fault.capacity_fraction_lost

    apply_losses(start_utc)
    trace = [{"utc": stamp(start_utc), "energy_wh": energy, "capacity_wh": capacity}]
    minimum, depleted = energy, None
    for a, b in zip(cuts, cuts[1:]):
        apply_losses(a)
        sun = segments[bisect_right(starts, a) - 1][2]
        output = {
            "full": 1.0,
            "partial": power.partial_sun_output_fraction,
            "dark": 0.0,
        }.get(sun, 1.0 if unknown_generation == "full" else 0.0)
        array = 1.0
        load = power.base_load_w
        for fault in surface:
            begin, finish = _fault_window(fault, start_utc)
            if fault.kind == "solar_array_degradation" and begin <= a:
                array *= fault.output_fraction
            if fault.kind == "power_load_increase" and begin <= a < finish:
                load += fault.extra_load_w
        net_w = power.solar_array_power_w * output * array - load
        hours = (b - a) / HOUR
        if net_w < 0 and energy / -net_w <= hours:
            depleted = a + timedelta(hours=energy / -net_w)
            energy = 0.0
            minimum = 0.0
            trace.append(
                {"utc": stamp(depleted), "energy_wh": 0.0, "capacity_wh": capacity}
            )
            break
        energy = min(capacity, energy + net_w * hours)
        minimum = min(minimum, energy)
        trace.append({"utc": stamp(b), "energy_wh": energy, "capacity_wh": capacity})
    return {
        "trace": trace,
        "min_energy_wh": minimum,
        "depleted_at_utc": stamp(depleted) if depleted is not None else None,
        "unknown_generation": unknown_generation,
    }


def contact_check(
    earth_intervals: list[dict],
    start_utc: datetime,
    end_utc: datetime,
    rules: CommunicationRules,
    faults: tuple,
    unknown_visible: bool,
) -> dict:
    """Contact windows after outages, first contact, longest gap and the time
    the first rule is violated (None if never)."""
    if end_utc <= start_utc:
        raise ValueError("Surface window must have positive duration")
    windows = []
    for a, b, value in _segments(earth_intervals, start_utc, end_utc, EARTH_STATES):
        if value == "visible" or (value in ("unknown", "boundary") and unknown_visible):
            if windows and windows[-1][1] == a:
                windows[-1][1] = b
            else:
                windows.append([a, b])
    for fault in faults:
        if fault.kind != "communication_outage":
            continue
        begin, finish = _fault_window(fault, start_utc)
        kept = []
        for a, b in windows:
            if a < begin:
                kept.append([a, min(b, begin)])
            if b > finish:
                kept.append([max(a, finish), b])
        windows = [w for w in kept if w[1] > w[0]]
    first = windows[0][0] if windows else None
    gaps = []
    cursor = start_utc
    for a, b in windows:
        gaps.append((cursor, a))
        cursor = b
    gaps.append((cursor, end_utc))
    longest = max(((b - a) / HOUR for a, b in gaps), default=0.0)
    candidates = []
    deadline = start_utc + rules.first_contact_deadline_h * HOUR
    if deadline <= end_utc and (first is None or first > deadline):
        candidates.append(
            (
                deadline,
                "first_contact_deadline",
                f"No Earth contact within {rules.first_contact_deadline_h:g} h of touchdown; landing cannot be confirmed.",
            )
        )
    limit = rules.max_contact_gap_h * HOUR
    if first is not None:
        for a, b in gaps[1:]:
            if b - a > limit:
                candidates.append(
                    (
                        a + limit,
                        "contact_gap",
                        f"No Earth contact for more than {rules.max_contact_gap_h:g} h after {stamp(a)}; the mission is declared lost.",
                    )
                )
                break
    violation = None
    if candidates:
        at, kind, detail = min(candidates)
        violation = {"kind": kind, "at_utc": stamp(at), "detail": detail}
    return {
        "windows": [{"start_utc": stamp(a), "end_utc": stamp(b)} for a, b in windows],
        "first_contact_utc": stamp(first) if first is not None else None,
        "longest_gap_h": longest,
        "violation": violation,
        "unknown_visible": unknown_visible,
    }


@lru_cache(maxsize=32)
def _nominal_plan(profile_id, site_id, latitude_deg, longitude_deg_east, height_m):
    return plan_descent(
        DescentRequest(
            site_id=site_id[:80] or "custom",
            latitude_deg=latitude_deg,
            longitude_deg_east=longitude_deg_east,
            profile_id=profile_id,
        ),
        height_m,
    )


def _gate(name, status, detail, code=None, **extra):
    return {"gate": name, "status": status, "code": code, "detail": detail, **extra}


def _not_reached(name):
    return _gate(
        name, "not_reached", "Not reached: the mission ended at an earlier gate."
    )


def _descent_gate(request, flight, plan, warnings):
    contact = flight["contact"]
    limits = request.touchdown
    metrics = {
        "propellant_margin_kg": flight["propellant_remaining_kg"],
        "nominal_propellant_margin_kg": plan["totals"]["propellant_margin_kg"],
        "propellant_leaked_kg": flight["propellant_leaked_kg"],
        "exhausted_at": flight["exhausted_at"],
        "contact": contact,
    }
    exhausted = flight["exhausted_at"]
    note = (
        f" Propellant ran out {exhausted['altitude_m']:.0f} m above the site at {exhausted['t_s']:.0f} s after PDI."
        if exhausted
        else ""
    )
    if contact is None:
        return _gate(
            "descent",
            "fail",
            "The vehicle did not reach the surface within the bounded descent." + note,
            "no_contact",
            metrics=metrics,
        )
    vertical = abs(contact["vertical_speed_mps"])
    horizontal = contact["horizontal_speed_mps"]
    if vertical > limits.crash_vertical_mps or horizontal > limits.crash_horizontal_mps:
        return _gate(
            "descent",
            "fail",
            f"Crash: ground contact at {vertical:.1f} m/s vertical and {horizontal:.1f} m/s horizontal exceeds the landing-gear limits ({limits.crash_vertical_mps:.2f} / {limits.crash_horizontal_mps:.2f} m/s)."
            + note,
            "crash",
            metrics=metrics,
        )
    own = []
    if vertical > limits.soft_vertical_mps or horizontal > limits.soft_horizontal_mps:
        own.append(
            (
                "hard_landing",
                f"Hard landing at {vertical:.2f} m/s vertical and {horizontal:.2f} m/s horizontal, inside the structural limits.",
            )
        )
    if exhausted:
        own.append(
            ("propellant_exhausted", "Landed without propellant reserve." + note)
        )
    warnings.extend({"gate": "descent", "code": c, "detail": d} for c, d in own)
    margin = flight["propellant_remaining_kg"]
    return _gate(
        "descent",
        "warn" if own else "pass",
        f"Touchdown at {vertical:.2f} m/s vertical with {margin:.0f} kg propellant remaining (nominal {plan['totals']['propellant_margin_kg']:.0f} kg).",
        own[0][0] if own else None,
        metrics=metrics,
    )


def _terrain_gate(request, terrain_at, latitude_deg, longitude_deg_east, warnings):
    limit = request.terrain.slope_limit_deg
    try:
        safety = terrain_at(latitude_deg, longitude_deg_east)
    except TerrainSafetyError as exc:
        return _gate(
            "touchdown_terrain",
            "unknown",
            f"Touchdown terrain cannot be evaluated: {exc}",
            "outside_coverage",
            metrics=None,
        )
    slope = safety.get("site_slope_deg")
    area = safety.get("max_landing_slope_deg")
    if slope is None:
        return _gate(
            "touchdown_terrain",
            "unknown",
            "The DEM has no slope at the touchdown point.",
            "terrain_unknown",
            metrics=safety,
        )
    if slope > limit:
        return _gate(
            "touchdown_terrain",
            "fail",
            f"Tip-over: the touchdown slope {slope:.1f} deg exceeds the {limit:g} deg limit.",
            "tip_over",
            metrics=safety,
        )
    if area is None or area > limit:
        detail = (
            f"Touchdown slope {slope:.1f} deg is safe, but terrain within {request.terrain.landing_radius_m:g} m reaches {area:.1f} deg."
            if area is not None
            else f"Touchdown slope {slope:.1f} deg is safe; part of the landing area has no slope data."
        )
        code = "steep_terrain_nearby" if area is not None else "landing_area_unknown"
        warnings.append({"gate": "touchdown_terrain", "code": code, "detail": detail})
        return _gate("touchdown_terrain", "warn", detail, code, metrics=safety)
    return _gate(
        "touchdown_terrain",
        "pass",
        f"Touchdown slope {slope:.1f} deg is below the {limit:g} deg limit.",
        metrics=safety,
    )


def run_mission(
    request: MissionRunRequest,
    analysis: dict,
    terrain_height_m: float,
    terrain_at: Callable[[float, float], dict],
    scenarios: dict[str, FaultScenario] | None = None,
) -> dict:
    """Evaluate all gates in order and report the first failure with its cause.

    `analysis` is a completed visibility analysis (request, sun, earth);
    `terrain_at(lat, lon)` returns R3 terrain safety for a touchdown point and
    may raise TerrainSafetyError when the point is outside coverage.
    """
    faults = effective_faults(
        request, scenarios if scenarios is not None else load_scenarios()
    )
    window = analysis["request"]
    site = window["site"]
    latitude, longitude = site["latitude_deg"], site["longitude_deg_east"]
    start, end = utc(window["start_utc"]), utc(window["end_utc"])
    delay_h = sum(f.delay_h for f in faults if f.kind == "landing_delay")
    scheduled = start + delay_h * HOUR
    if scheduled >= end:
        raise MissionRunError("The landing delay leaves no time in the analysis window")
    if request.profile_id not in load_profiles():
        raise MissionRunError(f"Unknown vehicle profile {request.profile_id}")
    try:
        plan = _nominal_plan(
            request.profile_id,
            str(site.get("site_id", "custom")),
            float(latitude),
            float(longitude),
            float(terrain_height_m),
        )
    except DescentError as exc:
        raise MissionRunError(str(exc)) from exc
    profile = load_profiles()[request.profile_id]
    coast = plan["deorbit"]["coast_s"]
    powered_s = plan["phases"][-1]["end_s"] - coast
    pdi = scheduled - timedelta(seconds=powered_s)
    warnings, events = (
        [],
        [
            {
                "utc": stamp(pdi),
                "gate": "descent",
                "kind": "pdi",
                "detail": "Powered descent initiation.",
            }
        ],
    )
    base = {
        "scenario_id": request.scenario_id,
        "faults": [f.model_dump(mode="json") for f in faults],
        "analysis_window": {"start_utc": stamp(start), "end_utc": stamp(end)},
        "scheduled_landing_utc": stamp(scheduled),
        "pdi_utc": stamp(pdi),
        "settings": request.model_dump(mode="json"),
        "assumptions": list(ASSUMPTIONS),
        "disclaimer": DISCLAIMER,
    }

    def finish(gates, failure, **parts):
        statuses = {g["status"] for g in gates}
        outcome = (
            "failure"
            if failure
            else "inconclusive"
            if "unknown" in statuses
            else "success_with_warnings"
            if warnings
            else "success"
        )
        events.sort(key=lambda e: (utc(e["utc"]), GATES.index(e["gate"])))
        return {
            "outcome": outcome,
            "failure": failure,
            "gates": gates,
            "warnings": warnings,
            "events": events,
            **base,
            **parts,
        }

    if not plan["feasible"]:
        detail = " ".join(f["detail"] for f in plan["limiting_factors"])
        gate = _gate("descent", "fail", detail, "nominal_plan_infeasible", metrics=None)
        failure = {
            "gate": "descent",
            "code": gate["code"],
            "detail": detail,
            "at_utc": stamp(pdi),
        }
        return finish(
            [gate, *map(_not_reached, GATES[1:])],
            failure,
            landing_utc=None,
            touchdown_site=None,
            descent=None,
            power=None,
            communication=None,
        )
    flight = fly_faulted_descent(
        profile,
        plan["site"]["radius_m"],
        plan["pdi_range_m"],
        plan["deorbit"]["periapsis_speed_mps"],
        faults,
    )
    for event in flight["events"]:
        events.append(
            {
                "utc": stamp(pdi + timedelta(seconds=event["t_s"])),
                "gate": "descent",
                "kind": event["kind"],
                "detail": event["detail"],
                **({"fault": event["fault"]} if "fault" in event else {}),
            }
        )
    samples = flight["samples"]
    stride = max(1, math.ceil(len(samples) / (MAX_DESCENT_SAMPLES - 1)))
    kept = samples[::stride]
    if kept[-1] is not samples[-1]:
        kept.append(samples[-1])
    descent = {
        "nominal_propellant_margin_kg": plan["totals"]["propellant_margin_kg"],
        "propellant_margin_kg": flight["propellant_remaining_kg"],
        "propellant_used_kg": flight["propellant_used_kg"],
        "propellant_leaked_kg": flight["propellant_leaked_kg"],
        "contact": flight["contact"],
        "exhausted_at": flight["exhausted_at"],
        "pdi_range_m": plan["pdi_range_m"],
        "nominal_powered_duration_s": powered_s,
        "samples": kept,
    }
    descent_gate = _descent_gate(request, flight, plan, warnings)
    contact = flight["contact"]
    if descent_gate["status"] == "fail":
        at = pdi + timedelta(seconds=(contact or {"t_s": samples[-1]["t_s"]})["t_s"])
        failure = {
            "gate": "descent",
            "code": descent_gate["code"],
            "detail": descent_gate["detail"],
            "at_utc": stamp(at),
        }
        return finish(
            [descent_gate, *map(_not_reached, GATES[1:])],
            failure,
            landing_utc=None,
            touchdown_site=None,
            descent=descent,
            power=None,
            communication=None,
        )
    landing = pdi + timedelta(seconds=contact["t_s"])
    offset_m, bearing = touchdown_offset(
        contact["downrange_m"], request.approach_azimuth_deg, faults
    )
    touch_lat, touch_lon = destination_point(
        latitude, longitude, offset_m, bearing, MOON_REFERENCE_RADIUS_M
    )
    touchdown_site = {
        "latitude_deg": touch_lat,
        "longitude_deg_east": touch_lon,
        "offset_m": offset_m,
        "bearing_deg": bearing,
        "target": {"latitude_deg": latitude, "longitude_deg_east": longitude},
    }
    if offset_m > TARGET_MISS_WARNING_M:
        detail = f"Touched down {offset_m:.0f} m from the target at bearing {bearing:.0f} deg."
        warnings.append({"gate": "descent", "code": "target_miss", "detail": detail})
        if descent_gate["status"] == "pass":
            descent_gate.update(status="warn", code="target_miss")
    terrain_gate = _terrain_gate(request, terrain_at, touch_lat, touch_lon, warnings)
    parts = dict(
        landing_utc=stamp(landing), touchdown_site=touchdown_site, descent=descent
    )
    if terrain_gate["status"] == "fail":
        events.append(
            {
                "utc": stamp(landing),
                "gate": "touchdown_terrain",
                "kind": "tip_over",
                "detail": terrain_gate["detail"],
            }
        )
        failure = {
            "gate": "touchdown_terrain",
            "code": "tip_over",
            "detail": terrain_gate["detail"],
            "at_utc": stamp(landing),
        }
        return finish(
            [
                descent_gate,
                terrain_gate,
                _not_reached("power"),
                _not_reached("communication"),
            ],
            failure,
            power=None,
            communication=None,
            **parts,
        )
    if landing >= end:
        gates = [
            descent_gate,
            terrain_gate,
            _gate(
                "power",
                "unknown",
                "No surface time remains in the analysis window.",
                "no_surface_window",
            ),
            _gate(
                "communication",
                "unknown",
                "No surface time remains in the analysis window.",
                "no_surface_window",
            ),
        ]
        return finish(gates, None, power=None, communication=None, **parts)
    for fault in faults:
        if FAULT_PHASES.get(fault.kind, "surface") == "surface":
            begin = landing + fault.start_after_landing_h * HOUR
            if begin < end:
                events.append(
                    {
                        "utc": stamp(begin),
                        "gate": "power"
                        if fault.kind != "communication_outage"
                        else "communication",
                        "kind": "fault_activated",
                        "fault": fault.kind,
                        "detail": f"Fault {fault.kind} active.",
                    }
                )
    sun, earth = analysis["sun"]["intervals"], analysis["earth"]["intervals"]
    low = power_trace(sun, landing, end, request.power, faults, "none")
    high = power_trace(sun, landing, end, request.power, faults, "full")
    worst = contact_check(earth, landing, end, request.communication, faults, False)
    best = contact_check(earth, landing, end, request.communication, faults, True)
    power_fail = (
        utc(low["depleted_at_utc"]) if high["depleted_at_utc"] is not None else None
    )
    power_unknown = low["depleted_at_utc"] is not None and power_fail is None
    comm_fail = (
        utc(worst["violation"]["at_utc"]) if best["violation"] is not None else None
    )
    comm_unknown = worst["violation"] is not None and comm_fail is None
    ends = [t for t in (power_fail, comm_fail) if t is not None]
    mission_end = min(ends) if ends else end
    if mission_end < end:
        # Report the battery only while the mission was alive.
        low = power_trace(sun, landing, mission_end, request.power, faults, "none")
    failure = None
    if power_fail is not None and power_fail == mission_end:
        detail = f"Battery depleted at {stamp(power_fail)}: generation could not cover the loads."
        if high["depleted_at_utc"] != low["depleted_at_utc"]:
            detail += f" With unknown sunlight treated as full sun it would last until {high['depleted_at_utc']}."
        power_gate = _gate(
            "power",
            "fail",
            detail,
            "battery_depleted",
            evaluated_until_utc=stamp(power_fail),
            metrics={"min_energy_wh": low["min_energy_wh"]},
        )
        failure = {
            "gate": "power",
            "code": "battery_depleted",
            "detail": detail,
            "at_utc": stamp(power_fail),
        }
    elif power_unknown and (
        comm_fail is None or utc(low["depleted_at_utc"]) < comm_fail
    ):
        power_gate = _gate(
            "power",
            "unknown",
            "Battery survival depends on sunlight intervals the analysis could not determine.",
            "sunlight_unknown",
            evaluated_until_utc=stamp(mission_end),
            metrics={"min_energy_wh": low["min_energy_wh"]},
        )
    else:
        power_gate = _gate(
            "power",
            "pass",
            f"Battery stayed above zero (minimum {low['min_energy_wh']:.0f} Wh) until {stamp(mission_end)}.",
            evaluated_until_utc=stamp(mission_end),
            metrics={"min_energy_wh": low["min_energy_wh"]},
        )
    if comm_fail is not None and comm_fail == mission_end and failure is None:
        detail = worst["violation"]["detail"]
        comm_gate = _gate(
            "communication",
            "fail",
            detail,
            worst["violation"]["kind"],
            evaluated_until_utc=stamp(comm_fail),
            metrics={"longest_gap_h": worst["longest_gap_h"]},
        )
        failure = {
            "gate": "communication",
            "code": worst["violation"]["kind"],
            "detail": detail,
            "at_utc": stamp(comm_fail),
        }
    elif comm_unknown and (
        power_fail is None or utc(worst["violation"]["at_utc"]) < power_fail
    ):
        comm_gate = _gate(
            "communication",
            "unknown",
            "Earth contact depends on visibility intervals the analysis could not determine.",
            "earth_visibility_unknown",
            evaluated_until_utc=stamp(mission_end),
            metrics={"longest_gap_h": worst["longest_gap_h"]},
        )
    else:
        comm_gate = _gate(
            "communication",
            "pass",
            f"Earth contact rules held until {stamp(mission_end)} (longest gap {worst['longest_gap_h']:.1f} h).",
            evaluated_until_utc=stamp(mission_end),
            metrics={"longest_gap_h": worst["longest_gap_h"]},
        )
    if failure is not None:
        events.append(
            {
                "utc": failure["at_utc"],
                "gate": failure["gate"],
                "kind": failure["code"],
                "detail": failure["detail"],
            }
        )
    else:
        events.append(
            {
                "utc": stamp(end),
                "gate": "communication",
                "kind": "window_end",
                "detail": "End of the analysed surface window.",
            }
        )
    events[:] = [
        e for e in events if utc(e["utc"]) <= mission_end or e["gate"] == "descent"
    ]
    return finish(
        [descent_gate, terrain_gate, power_gate, comm_gate],
        failure,
        power={**low, "optimistic_depleted_at_utc": high["depleted_at_utc"]},
        communication={**worst, "optimistic_violation": best["violation"]},
        mission_end_utc=stamp(mission_end),
        **parts,
    )


OUTLOOK_FINDING_KINDS = (
    "fault_activated",
    "fault_cleared",
    "first_contact",
    "first_contact_deadline",
    "contact_gap",
    "battery_depleted",
)
LIMITATION_CODES = (
    "earth_never_visible",
    "fault_not_reached",
    "fault_after_window",
    "fault_after_mission_end",
    "fault_exceeds_window",
)


def surface_outlook(
    sun_intervals: list[dict],
    earth_intervals: list[dict],
    landing_utc: datetime,
    end_utc: datetime,
    request: MissionRunRequest,
    faults: tuple,
) -> dict:
    """Autonomous surface evolution from touchdown to the window end.

    Unlike the verdict, which stops at the first failed gate, the lander keeps
    running without Earth contact. Power uses the pessimistic run (unknown
    sunlight generates nothing); it stops at battery depletion, and contact is
    evaluated only while the lander is alive. Returns
    {landing_utc, evaluated_until_utc,
     power: {trace, min_energy_wh, depleted_at_utc, optimistic_depleted_at_utc},
     communication: {windows, first_contact_utc, longest_gap_h},
     findings: [{utc, gate, kind, detail, fault?}]} with findings in time order
    and kinds from OUTLOOK_FINDING_KINDS. This is a what-if continuation; it
    never changes the verdict.
    """
    raise NotImplementedError


def mission_baseline(analysis: dict, request: MissionRunRequest, result: dict) -> dict:
    """Fault-free reference for the same analysis and vehicle, and the scenario delta.

    The baseline lands at the window start with no faults. Returns
    {landing_utc, propellant_margin_kg, touchdown_offset_m, min_energy_wh,
     depleted_at_utc, first_contact_utc,
     delta: {propellant_margin_kg, touchdown_offset_m, landing_delay_h,
             min_energy_wh, first_contact_shift_h}}.
    `result` is the scenario run with `surface_outlook` set; a delta is None
    when either side lacks the quantity (e.g. no touchdown after a crash).
    """
    raise NotImplementedError


def window_context(analysis: dict, result: dict, faults: tuple) -> dict:
    """Why scenarios may look alike in this analysis window.

    Returns {surface_window_h, earth_visible_h, sun_visible_h, verdict_utc,
    limitations: [{code, detail, fault?}]} with codes from LIMITATION_CODES:
    Earth never visible (no visible, boundary or unknown interval), and per
    surface fault: never reached (no touchdown), starting at or after the window
    end, starting at or after the verdict time, or still active at the window end.
    """
    raise NotImplementedError


def register_mission_run_routes(app, analyses, data_dir: Path) -> None:
    """GET /mission-faults and POST /analyses/{analysis_id}/mission-run."""

    @app.get("/mission-faults")
    def mission_faults():
        kinds = []
        for model in get_args(get_args(Fault)[0]):
            kind = model.model_fields["kind"].default
            schema = model.model_json_schema()
            schema["properties"].pop("kind")
            kinds.append(
                {
                    "kind": kind,
                    "phase": FAULT_PHASES.get(kind, "surface"),
                    "summary": (model.__doc__ or "").strip(),
                    "schema": schema,
                }
            )
        return {
            "scenarios": [s.model_dump(mode="json") for s in load_scenarios().values()],
            "fault_kinds": kinds,
            "defaults": MissionRunRequest().model_dump(mode="json"),
            "assumptions": list(ASSUMPTIONS),
            "disclaimer": DISCLAIMER,
        }

    @app.post("/analyses/{analysis_id}/mission-run")
    def mission_run(analysis_id: str, body: MissionRunRequest):
        job = analyses.get(analysis_id)
        if job is None:
            raise HTTPException(404, "Analysis not found")
        if job["status"] not in ("ready", "insufficient_data"):
            raise HTTPException(409, "Analysis is not complete")
        data = job["result"]["analysis"]
        site = data["request"]["site"]
        try:
            height = terrain_height_m(
                data_dir, site["latitude_deg"], site["longitude_deg_east"]
            )
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except DescentError as exc:
            raise HTTPException(422, str(exc)) from exc

        def terrain_at(latitude_deg, longitude_deg_east):
            return terrain_safety(
                data_dir, latitude_deg, longitude_deg_east, body.terrain
            )

        try:
            result = run_mission(body, data, height, terrain_at)
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except MissionRunError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"analysis_id": analysis_id, **result}


def _document(path: Path) -> dict:
    return json.loads(Path(path).read_bytes().decode("utf-8"))
