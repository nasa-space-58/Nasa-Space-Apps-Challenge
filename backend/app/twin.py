"""R9 descent digital twin: truth, observations and estimates with events (S13).

The twin flies the R4 descent (planar point-mass Moon, ZEM/ZEV guidance
through the Apollo 11 gates) and keeps three state streams, matching the H0
`record_kind` vocabulary:

- ground_truth: the RK4 integration of the actual vehicle;
- observation: truth plus seeded Gaussian noise from a navigation sensor whose
  health is nominal, degraded or lost (no observation);
- estimate: a constant-gain predictor-corrector. Prediction uses the commanded
  acceleration; actual thrust carries a seeded random error unknown to the
  estimator, so the estimate drifts when the sensor is lost.

Guidance flies on the estimate, not the truth. Timestamped events change the
model itself: thrust_loss (available thrust), sensor_degradation (sensor
noise/health), propellant_leak (instant mass loss), comm_outage (no ground
uplink window) and obstacle_detected (a synthetic hazard near the target).
Every applied event, state revision and plan invalidation is appended to a
deterministic log whose SHA-256 identifies the run. The twin never replans;
R12 does.

Planar frame: as in R4 (origin at the Moon's centre, site on +y). Mass is
assumed known to the estimator (propellant gauging); battery energy uses a
constant load (planning assumption).
"""

import hashlib
import json
import math
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.descent import (
    GUIDANCE_HOLD_S,
    PDI_ALTITUDE_M,
    DescentError,
    DescentRequest,
    OrbitSettings,
    Tolerances,
    VehicleProfile,
    _describe,
    _gravity,
    _state,
    engine_thrust,
    gate_states,
    load_profiles,
    plan_descent,
    terrain_height_m,
    zem_zev_acceleration,
)
from app.fuel_optimization import (
    DEFAULT_RESERVE_DELTA_V_MPS,
    _reason,
    reserve_propellant_kg,
)
from shared.constants import STANDARD_GRAVITY_M_S2

DEFAULT_STEP_S = 0.2
MAX_EVENTS = 20
MAX_TIMELINE_ROWS = 400
DEPENDENCIES = (
    "available_thrust",
    "navigation_sensor",
    "propellant",
    "target_hazard",
    "ground_uplink",
)
DEFAULT_PLAN_DEPENDENCIES = (
    "available_thrust",
    "navigation_sensor",
    "propellant",
    "target_hazard",
)
EVENT_DEPENDENCY = {
    "thrust_loss": "available_thrust",
    "sensor_degradation": "navigation_sensor",
    "propellant_leak": "propellant",
    "obstacle_detected": "target_hazard",
    "comm_outage": "ground_uplink",
}


class TwinError(ValueError):
    """Invalid twin inputs or a plan the R4 model rejects."""


class SensorSettings(BaseModel):
    """Navigation sensor and estimator (planning assumptions, editable).

    thrust_noise_fraction is the standard deviation of a constant engine
    performance bias drawn once per run and unknown to the estimator.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    position_sigma_m: float = Field(default=5.0, ge=0, le=5000)
    speed_sigma_mps: float = Field(default=0.1, ge=0, le=50)
    gain: float = Field(default=0.05, gt=0, le=1)
    thrust_noise_fraction: float = Field(default=0.005, ge=0, le=0.1)
    range_fraction: float = Field(default=0.002, ge=0, le=0.1)
    floor_position_m: float = Field(default=0.1, ge=0, le=100)


class BatterySettings(BaseModel):
    """Constant-load battery (planning assumption)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    initial_energy_j: float = Field(default=5.0e7, gt=0, le=1e10)
    load_w: float = Field(default=2000.0, ge=0, le=1e5)


class ThrustLoss(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["thrust_loss"]
    t_s: float = Field(ge=0, le=3600)
    available_fraction: float = Field(gt=0, le=1)


class SensorDegradation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["sensor_degradation"]
    t_s: float = Field(ge=0, le=3600)
    health: Literal["nominal", "degraded", "lost"]
    position_sigma_m: float | None = Field(default=None, ge=0, le=5000)
    speed_sigma_mps: float | None = Field(default=None, ge=0, le=50)


class PropellantLeak(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["propellant_leak"]
    t_s: float = Field(ge=0, le=3600)
    leaked_kg: float = Field(gt=0, le=20000)


class CommOutage(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["comm_outage"]
    t_s: float = Field(ge=0, le=3600)
    duration_s: float = Field(gt=0, le=3600)


class ObstacleDetected(BaseModel):
    """Synthetic hazard: a disk at (down, cross) metres from the target."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["obstacle_detected"]
    t_s: float = Field(ge=0, le=3600)
    down_m: float = Field(ge=-20000, le=20000)
    cross_m: float = Field(ge=-20000, le=20000)
    radius_m: float = Field(gt=0, le=5000)


TwinEvent = Annotated[
    ThrustLoss | SensorDegradation | PropellantLeak | CommOutage | ObstacleDetected,
    Field(discriminator="kind"),
]


class TwinRequest(BaseModel):
    """R4 plan inputs, sensor/battery assumptions, seeded events."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    profile_id: str = "apollo-lm-11"
    custom_profile: VehicleProfile | None = None
    orbit: OrbitSettings = Field(default_factory=OrbitSettings)
    tolerances: Tolerances = Field(default_factory=Tolerances)
    reserve_delta_v_mps: float = Field(
        default=DEFAULT_RESERVE_DELTA_V_MPS, ge=0, le=500
    )
    seed: int = Field(ge=0, le=2**32 - 1, strict=True)
    step_s: float = Field(default=DEFAULT_STEP_S, ge=0.05, le=1.0)
    sensor: SensorSettings = Field(default_factory=SensorSettings)
    battery: BatterySettings = Field(default_factory=BatterySettings)
    events: list[TwinEvent] = Field(default_factory=list, max_length=MAX_EVENTS)
    plan_dependencies: list[
        Literal[
            "available_thrust",
            "navigation_sensor",
            "propellant",
            "target_hazard",
            "ground_uplink",
        ]
    ] = Field(default_factory=lambda: list(DEFAULT_PLAN_DEPENDENCIES))


DEGRADED_SIGMA_FACTOR = 5.0
ASSUMPTIONS = (
    "The twin flies the R4 model: planar point-mass Moon, ZEM/ZEV guidance through the Apollo 11 gates, RK4 at the chosen step.",
    "Guidance uses the estimated state; the true state is integrated separately (ground truth vs observation vs estimate).",
    "Navigation observations add independent Gaussian noise to downrange/altitude position and horizontal/vertical speed; sizes are planning assumptions.",
    "Sensor noise is range-scaled (radar/lidar-like): the configured sigma is a cap, reduced to range_fraction x altitude near the surface, never below a floor.",
    "The estimator is a constant-gain predictor-corrector: prediction with the commanded acceleration, correction toward each observation.",
    "Actual thrust carries a constant engine performance bias drawn once per run and unknown to the estimator; without observations the estimate drifts.",
    "Mass is known to the estimator (propellant gauging). Battery energy uses a constant load; no thermal or power model.",
    "Events change the model: thrust_loss scales available thrust, sensor_degradation changes noise/health (degraded without values = 5x noise), propellant_leak removes mass instantly, comm_outage removes ground uplink, obstacle_detected adds a synthetic hazard disk.",
    "The twin does not replan: an invalidated plan keeps flying so its consequences are visible (R12 replans).",
    "All telemetry is simulated; obstacles are synthetic; the vehicle profile and terrain height keep their sources.",
)
_EVENTS = TypeAdapter(TwinEvent)


def estimate_sigma(observation_sigma: float, gain: float) -> float:
    """Steady-state error of a constant-gain filter on white noise:
    sigma * sqrt(K / (2 - K))."""
    return float(observation_sigma) * math.sqrt(gain / (2.0 - gain))


def correct(estimate, observation, gain: float):
    """(1 - K) * estimate + K * observation, elementwise (K = 1 copies exactly)."""
    if gain == 1.0:
        return np.array(observation, dtype=float, copy=True)
    return (1.0 - gain) * np.asarray(estimate, dtype=float) + gain * np.asarray(
        observation, dtype=float
    )


def _local_axes(position):
    up = np.asarray(position, dtype=float) / np.linalg.norm(position)
    east = np.array([up[1], -up[0]])
    return up, east


def observe(
    position_m,
    velocity_mps,
    health: str,
    position_sigma_m: float,
    speed_sigma_mps: float,
    rng: np.random.Generator,
):
    """Noisy (position, velocity) in the planar frame, or None when lost.

    Noise is drawn along the local up and downrange directions. Zero sigma
    returns exact copies. Always draws the same number of variates per call
    (even when lost) so that later draws do not depend on sensor health.
    """
    noise = rng.standard_normal(4)
    if health == "lost":
        return None
    position = np.array(position_m, dtype=float, copy=True)
    velocity = np.array(velocity_mps, dtype=float, copy=True)
    if position_sigma_m > 0:
        up, east = _local_axes(position)
        position = position + position_sigma_m * (noise[0] * east + noise[1] * up)
    if speed_sigma_mps > 0:
        up, east = _local_axes(position_m)
        velocity = velocity + speed_sigma_mps * (noise[2] * east + noise[3] * up)
    return position, velocity


def effective_sigmas(
    position_sigma_m: float,
    speed_sigma_mps: float,
    altitude_m: float,
    sensor: SensorSettings,
) -> tuple[float, float]:
    """Range-scaled noise: radar/lidar errors shrink near the surface.

    Position sigma is min(configured, max(floor, range_fraction * altitude));
    speed sigma scales by the same ratio. range_fraction = 0 disables scaling.
    """
    if position_sigma_m <= 0 or sensor.range_fraction <= 0:
        return position_sigma_m, speed_sigma_mps
    scaled = min(
        position_sigma_m,
        max(sensor.floor_position_m, sensor.range_fraction * max(altitude_m, 0.0)),
    )
    return scaled, speed_sigma_mps * scaled / position_sigma_m


def canonical_hash(value) -> str:
    """SHA-256 of canonical JSON (sorted keys, no NaN)."""
    text = json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _streams(position, velocity, site_radius_m) -> dict:
    described = _describe(np.asarray(position), np.asarray(velocity), site_radius_m)
    return {
        "downrange_m": described["downrange_m"],
        "altitude_m": described["altitude_m"],
        "vertical_speed_mps": described["vertical_speed_mps"],
        "horizontal_speed_mps": described["horizontal_speed_mps"],
    }


def _rk4(position, velocity, mass, thrust, direction, flow, step):
    def rate(p, v, m):
        return v, _gravity(p) + thrust * direction / m, -flow

    k1 = rate(position, velocity, mass)
    k2 = rate(
        position + step / 2 * k1[0],
        velocity + step / 2 * k1[1],
        mass + step / 2 * k1[2],
    )
    k3 = rate(
        position + step / 2 * k2[0],
        velocity + step / 2 * k2[1],
        mass + step / 2 * k2[2],
    )
    k4 = rate(position + step * k3[0], velocity + step * k3[1], mass + step * k3[2])
    return (
        position + step / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]),
        velocity + step / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]),
    )


def fly_twin(
    profile: VehicleProfile,
    site_radius_m: float,
    pdi_range_m: float,
    periapsis_speed_mps: float,
    sensor: SensorSettings,
    events: list,
    seed: int,
    step_s: float = DEFAULT_STEP_S,
    battery: BatterySettings | None = None,
    plan_dependencies: tuple[str, ...] = DEFAULT_PLAN_DEPENDENCIES,
    record_every: int = 1,
) -> dict:
    """Fly the R4 descent from PDI with estimate-driven guidance and events.

    Returns rows (every `record_every` steps plus touchdown), the final truth
    state, propellant exhaustion, phases, the event log (applied events,
    revisions, invalidations, touchdown), obstacles, max estimate error and
    `log_hash`. With zero sensor noise, gain 1, no thrust noise and no events
    it reproduces `fly_gates` exactly.
    """
    battery = battery or BatterySettings()
    parsed = [
        e if isinstance(e, BaseModel) else _EVENTS.validate_python(e) for e in events
    ]
    pending = [
        parsed[i] for i in sorted(range(len(parsed)), key=lambda i: (parsed[i].t_s, i))
    ]
    rng = np.random.default_rng(seed)
    bias = sensor.thrust_noise_fraction * float(rng.standard_normal())
    gain = sensor.gain
    ve = profile.isp_s * STANDARD_GRAVITY_M_S2
    dry_kg = profile.mass_at_pdi_kg - profile.usable_propellant_kg
    position, velocity = _state(
        site_radius_m, -pdi_range_m, PDI_ALTITUDE_M, periapsis_speed_mps, 0.0
    )
    est_p, est_v = position.copy(), velocity.copy()
    mass = profile.mass_at_pdi_kg
    state = {
        "clock": 0.0,
        "exhausted": False,
        "health": "nominal",
        "pos_sigma": sensor.position_sigma_m,
        "speed_sigma": sensor.speed_sigma_mps,
        "engine": profile,
        "revision": 0,
        "comm_until": None,
        "mass": mass,
    }
    est_std = estimate_sigma(sensor.position_sigma_m, gain)
    dependencies = tuple(plan_dependencies)
    log: list[dict] = []
    obstacles: list[dict] = []
    invalidated: list[str] = []
    rows: list[dict] = []
    max_error = 0.0

    def apply_due():
        while pending and state["clock"] >= pending[0].t_s - 1e-9:
            event = pending.pop(0)
            state["revision"] += 1
            clock = state["clock"]
            log.append(
                {
                    "t_s": clock,
                    "kind": "event_applied",
                    "event": event.model_dump(),
                    "state_revision": state["revision"],
                }
            )
            covers = True
            if event.kind == "thrust_loss":
                fraction = event.available_fraction
                state["engine"] = (
                    profile
                    if fraction == 1.0
                    else profile.model_copy(
                        update={
                            "max_thrust_n": profile.max_thrust_n * fraction,
                            "design_thrust_n": profile.design_thrust_n * fraction,
                        }
                    )
                )
            elif event.kind == "sensor_degradation":
                state["health"] = event.health
                factor = DEGRADED_SIGMA_FACTOR if event.health == "degraded" else 1.0
                state["pos_sigma"] = (
                    event.position_sigma_m
                    if event.position_sigma_m is not None
                    else sensor.position_sigma_m * factor
                )
                state["speed_sigma"] = (
                    event.speed_sigma_mps
                    if event.speed_sigma_mps is not None
                    else sensor.speed_sigma_mps * factor
                )
            elif event.kind == "propellant_leak":
                state["mass"] -= event.leaked_kg
                if state["mass"] < dry_kg:
                    state["mass"], state["exhausted"] = dry_kg, True
            elif event.kind == "comm_outage":
                state["comm_until"] = clock + event.duration_s
            elif event.kind == "obstacle_detected":
                covers = math.hypot(event.down_m, event.cross_m) < event.radius_m
                obstacles.append(
                    {
                        "t_s": clock,
                        "down_m": event.down_m,
                        "cross_m": event.cross_m,
                        "radius_m": event.radius_m,
                        "covers_target": covers,
                        "label": "synthetic",
                    }
                )
            dependency = EVENT_DEPENDENCY[event.kind]
            if dependency in dependencies and covers:
                if dependency not in invalidated:
                    invalidated.append(dependency)
                log.append(
                    {
                        "t_s": clock,
                        "kind": "plan_invalidated",
                        "event_kind": event.kind,
                        "dependency": dependency,
                        "plan_revision": state["revision"],
                        "reason": f"{event.kind.replace('_', ' ')} changed {dependency.replace('_', ' ')}, which the active plan depends on",
                    }
                )
            else:
                log.append(
                    {
                        "t_s": clock,
                        "kind": "no_plan_effect",
                        "event_kind": event.kind,
                        "dependency": dependency,
                        "reason": (
                            "obstacle does not cover the target"
                            if not covers
                            else f"the active plan does not depend on {dependency.replace('_', ' ')}"
                        ),
                    }
                )

    def battery_energy():
        return max(battery.initial_energy_j - battery.load_w * state["clock"], 0.0)

    def row(phase, observation, thrust):
        error = float(np.linalg.norm(position - est_p))
        comm_until = state["comm_until"]
        return {
            "t_s": state["clock"],
            "phase": phase,
            "truth": _streams(position, velocity, site_radius_m),
            "observation": (
                None
                if observation is None
                else _streams(observation[0], observation[1], site_radius_m)
            ),
            "estimate": _streams(est_p, est_v, site_radius_m),
            "estimate_error_m": error,
            "estimate_std_m": est_std,
            "mass_kg": state["mass"],
            "propellant_kg": max(state["mass"] - dry_kg, 0.0),
            "thrust_n": thrust,
            "available_thrust_n": state["engine"].max_thrust_n,
            "battery_energy_j": battery_energy(),
            "sensor_health": state["health"],
            "ground_uplink": comm_until is None or state["clock"] >= comm_until,
            "record_label": "simulated",
        }

    phases = []
    command = np.zeros(2)
    thrust = 0.0
    step_index = 0
    for gate in gate_states(site_radius_m):
        start, elapsed = state["clock"], 0.0
        phase_dv, phase_mass, saturated_s = 0.0, state["mass"], 0.0
        duration = gate["duration_s"]
        while elapsed < duration - 1e-9:
            apply_due()
            step = min(step_s, duration - elapsed)
            altitude = float(np.linalg.norm(position)) - site_radius_m
            pos_sigma, speed_sigma = effective_sigmas(
                state["pos_sigma"], state["speed_sigma"], altitude, sensor
            )
            observation = observe(
                position, velocity, state["health"], pos_sigma, speed_sigma, rng
            )
            if observation is not None:
                est_p = correct(est_p, observation[0], gain)
                est_v = correct(est_v, observation[1], gain)
                est_std = estimate_sigma(pos_sigma, gain)
            else:
                est_std += estimate_sigma(speed_sigma, gain) * step
            max_error = max(max_error, float(np.linalg.norm(position - est_p)))
            time_to_go = duration - elapsed
            if time_to_go > GUIDANCE_HOLD_S:
                command = zem_zev_acceleration(
                    est_p,
                    est_v,
                    gate["position_m"],
                    gate["velocity_mps"],
                    time_to_go,
                    _gravity(est_p),
                )
            norm = float(np.linalg.norm(command))
            mass = state["mass"]
            thrust, saturated = engine_thrust(mass * norm, state["engine"])
            if mass <= dry_kg:
                thrust, state["exhausted"] = 0.0, True
            direction = command / norm if norm > 0 else np.array([0.0, 1.0])
            if step_index % record_every == 0:
                rows.append(row(gate["name"], observation, thrust))
            actual = thrust * (1.0 + bias)
            flow = actual / ve
            position, velocity = _rk4(
                position, velocity, mass, actual, direction, flow, step
            )
            est_p, est_v = _rk4(
                est_p, est_v, mass, thrust, direction, thrust / ve, step
            )
            new_mass = mass - flow * step
            if new_mass < dry_kg:
                new_mass, state["exhausted"] = dry_kg, True
            if new_mass < mass:
                phase_dv += ve * math.log(mass / new_mass)
            state["mass"] = new_mass
            saturated_s += step if saturated else 0.0
            elapsed += step
            state["clock"] += step
            step_index += 1
        end = _describe(position, velocity, site_radius_m)
        phases.append(
            {
                "name": gate["name"],
                "start_s": start,
                "end_s": state["clock"],
                "delta_v_mps": phase_dv,
                "propellant_kg": phase_mass - state["mass"],
                "end_altitude_m": end["altitude_m"],
                "saturated_s": saturated_s,
            }
        )
    pos_sigma, speed_sigma = effective_sigmas(
        state["pos_sigma"],
        state["speed_sigma"],
        float(np.linalg.norm(position)) - site_radius_m,
        sensor,
    )
    observation = observe(
        position, velocity, state["health"], pos_sigma, speed_sigma, rng
    )
    if observation is not None:
        est_p = correct(est_p, observation[0], gain)
        est_v = correct(est_v, observation[1], gain)
    final_error = float(np.linalg.norm(position - est_p))
    max_error = max(max_error, final_error)
    rows.append(row("landed", observation, thrust))
    described = _describe(position, velocity, site_radius_m)
    final = {
        "position_error_m": float(
            np.linalg.norm(position - np.array([0.0, site_radius_m]))
        ),
        "vertical_speed_mps": described["vertical_speed_mps"],
        "horizontal_speed_mps": described["horizontal_speed_mps"],
        "mass_kg": state["mass"],
    }
    remaining = max(state["mass"] - dry_kg, 0.0)
    log.append(
        {
            "t_s": state["clock"],
            "kind": "touchdown",
            "final": final,
            "propellant_remaining_kg": remaining,
            "state_revision": state["revision"],
        }
    )
    for event in pending:
        log.append(
            {"t_s": event.t_s, "kind": "event_not_reached", "event": event.model_dump()}
        )
    return {
        "rows": rows,
        "final": final,
        "propellant_exhausted": state["exhausted"],
        "propellant_remaining_kg": remaining,
        "phases": phases,
        "log": log,
        "obstacles": obstacles,
        "max_estimate_error_m": max_error,
        "final_estimate_error_m": final_error,
        "plan": {
            "depends_on": list(dependencies),
            "valid": not invalidated,
            "invalidated_by": invalidated,
            "revision": state["revision"],
        },
        "duration_s": state["clock"],
        "battery_energy_j": battery_energy(),
        "battery_empty": battery_energy() <= 0.0,
        "engine_bias_fraction": bias,
        "log_hash": canonical_hash(log),
    }


def _vehicle(request: TwinRequest) -> VehicleProfile:
    if request.custom_profile is not None:
        return request.custom_profile
    profiles = load_profiles()
    if request.profile_id not in profiles:
        raise TwinError(f"Unknown vehicle profile {request.profile_id}")
    return profiles[request.profile_id]


def _touchdown_check(run, request: TwinRequest, profile: VehicleProfile) -> dict:
    final, tol = run["final"], request.tolerances
    reserve = reserve_propellant_kg(
        final["mass_kg"], request.reserve_delta_v_mps, profile.isp_s
    )
    margin = run["propellant_remaining_kg"] - reserve
    reasons = []
    if run["propellant_exhausted"]:
        reasons.append(_reason("propellant", "Propellant ran out before touchdown."))
    elif margin < 0:
        reasons.append(
            _reason(
                "reserve",
                f"{run['propellant_remaining_kg']:.0f} kg left; the reserve needs {reserve:.0f} kg.",
                run["propellant_remaining_kg"],
                reserve,
                "kg",
            )
        )
    if final["position_error_m"] > tol.position_m:
        reasons.append(
            _reason(
                "position",
                f"Landed {final['position_error_m']:.1f} m from the target.",
                final["position_error_m"],
                tol.position_m,
                "m",
            )
        )
    if abs(final["vertical_speed_mps"]) > tol.vertical_speed_mps:
        reasons.append(
            _reason(
                "vertical_speed",
                f"Vertical speed {abs(final['vertical_speed_mps']):.2f} m/s at touchdown.",
                abs(final["vertical_speed_mps"]),
                tol.vertical_speed_mps,
                "m/s",
            )
        )
    if final["horizontal_speed_mps"] > tol.horizontal_speed_mps:
        reasons.append(
            _reason(
                "horizontal_speed",
                f"Horizontal speed {final['horizontal_speed_mps']:.2f} m/s at touchdown.",
                final["horizontal_speed_mps"],
                tol.horizontal_speed_mps,
                "m/s",
            )
        )
    if any(o["covers_target"] for o in run["obstacles"]):
        reasons.append(
            _reason(
                "obstacle", "The target lies inside a detected (synthetic) obstacle."
            )
        )
    return {"success": not reasons, "reasons": reasons, "reserve_margin_kg": margin}


def _downsample(rows: list[dict]) -> list[dict]:
    if len(rows) <= MAX_TIMELINE_ROWS:
        return rows
    stride = math.ceil(len(rows) / (MAX_TIMELINE_ROWS - 1))
    kept = rows[::stride]
    if kept[-1] is not rows[-1]:
        kept.append(rows[-1])
    return kept


def run_twin(request: TwinRequest, terrain_height_m: float) -> dict:
    """Event run plus a same-seed no-event baseline and their differences.

    Raises TwinError for an unknown vehicle or a plan the R4 model rejects.
    """
    profile = _vehicle(request)
    try:
        nominal = plan_descent(
            DescentRequest(
                site_id=request.site_id,
                latitude_deg=request.latitude_deg,
                longitude_deg_east=request.longitude_deg_east,
                profile_id=request.profile_id,
                custom_profile=request.custom_profile,
                orbit=request.orbit,
                tolerances=request.tolerances,
            ),
            terrain_height_m,
        )
    except (DescentError, ValueError) as exc:
        raise TwinError(str(exc)) from exc
    site_radius = nominal["site"]["radius_m"]
    arguments = (
        profile,
        site_radius,
        nominal["pdi_range_m"],
        nominal["deorbit"]["periapsis_speed_mps"],
        request.sensor,
    )
    common = dict(
        seed=request.seed,
        step_s=request.step_s,
        battery=request.battery,
        plan_dependencies=tuple(request.plan_dependencies),
    )
    run = fly_twin(*arguments, list(request.events), **common)
    baseline = fly_twin(*arguments, [], **common)
    run_check = _touchdown_check(run, request, profile)
    base_check = _touchdown_check(baseline, request, profile)
    differences = {
        key: run["final"][key] - baseline["final"][key]
        for key in ("position_error_m", "vertical_speed_mps", "horizontal_speed_mps")
    } | {
        key: run[key] - baseline[key]
        for key in ("propellant_remaining_kg", "duration_s", "max_estimate_error_m")
    }
    output = {
        "site": {
            "site_id": request.site_id,
            "latitude_deg": request.latitude_deg,
            "longitude_deg_east": request.longitude_deg_east,
            "terrain_height_m": float(terrain_height_m),
        },
        "settings": request.model_dump(mode="json"),
        "nominal": {
            "feasible": nominal["feasible"],
            "pdi_range_m": nominal["pdi_range_m"],
        },
        "run": {
            "timeline": _downsample(run["rows"]),
            "final": run["final"],
            "touchdown_check": run_check,
            "log": run["log"],
            "obstacles": run["obstacles"],
            "plan": run["plan"],
            "phases": run["phases"],
            "max_estimate_error_m": run["max_estimate_error_m"],
            "final_estimate_error_m": run["final_estimate_error_m"],
            "propellant_remaining_kg": run["propellant_remaining_kg"],
            "propellant_exhausted": run["propellant_exhausted"],
            "duration_s": run["duration_s"],
            "battery_energy_j": run["battery_energy_j"],
            "battery_empty": run["battery_empty"],
            "engine_bias_fraction": run["engine_bias_fraction"],
            "log_hash": run["log_hash"],
        },
        "baseline": {
            "final": baseline["final"],
            "touchdown_check": base_check,
            "propellant_remaining_kg": baseline["propellant_remaining_kg"],
            "max_estimate_error_m": baseline["max_estimate_error_m"],
            "duration_s": baseline["duration_s"],
            "log_hash": baseline["log_hash"],
        },
        "differences": differences,
        "assumptions": list(ASSUMPTIONS),
    }
    output["result_id"] = canonical_hash(output)
    return output


def register_twin_routes(app, data_dir: Path) -> None:
    """POST /twin/runs: 409 without cached terrain, 422 for invalid input."""

    @app.post("/twin/runs")
    def twin_runs(body: TwinRequest):
        try:
            height = terrain_height_m(
                data_dir, body.latitude_deg, body.longitude_deg_east
            )
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except DescentError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return run_twin(body, height)
        except TwinError as exc:
            raise HTTPException(422, str(exc)) from exc
