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
from shared.constants import MOON_REFERENCE_RADIUS_M, STANDARD_GRAVITY_M_S2

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
    "Events change the model: thrust_loss scales available thrust, sensor_degradation changes noise/health (degraded without values = 5x the range-scaled noise; explicit values are used unscaled), propellant_leak removes mass instantly, comm_outage removes ground uplink, obstacle_detected adds a synthetic hazard disk.",
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


class TwinFlight:
    """Step-wise twin flight: the engine behind `fly_twin`, also driven by R12.

    State is advanced one integration step at a time so that a replanner can
    stop between steps, read the estimated or true state and retarget.
    """

    def __init__(
        self,
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
    ):
        self.profile = profile
        self.sensor = sensor
        self.step_s = step_s
        self.battery = battery or BatterySettings()
        parsed = [
            e if isinstance(e, BaseModel) else _EVENTS.validate_python(e)
            for e in events
        ]
        self.pending = [
            parsed[i]
            for i in sorted(range(len(parsed)), key=lambda i: (parsed[i].t_s, i))
        ]
        self.rng = np.random.default_rng(seed)
        self.bias = sensor.thrust_noise_fraction * float(self.rng.standard_normal())
        self.gain = sensor.gain
        self.ve = profile.isp_s * STANDARD_GRAVITY_M_S2
        self.dry_kg = profile.mass_at_pdi_kg - profile.usable_propellant_kg
        self.site_radius_m = site_radius_m
        self.position, self.velocity = _state(
            site_radius_m, -pdi_range_m, PDI_ALTITUDE_M, periapsis_speed_mps, 0.0
        )
        self.est_p, self.est_v = self.position.copy(), self.velocity.copy()
        self.mass = profile.mass_at_pdi_kg
        self.clock = 0.0
        self.exhausted = False
        self.health = "nominal"
        self.pos_sigma = sensor.position_sigma_m
        self.speed_sigma = sensor.speed_sigma_mps
        self.sigma_factor = 1.0
        self.sigma_fixed = False
        self.engine = profile
        self.revision = 0
        self.comm_until = None
        self.est_std = estimate_sigma(sensor.position_sigma_m, self.gain)
        self.dependencies = tuple(plan_dependencies)
        self.log: list[dict] = []
        self.obstacles: list[dict] = []
        self.invalidated: list[str] = []
        self.rows: list[dict] = []
        self.max_error = 0.0
        self.record_every = record_every
        self.gates = gate_states(site_radius_m)
        self.gate_index = 0
        self.phases: list[dict] = []
        self.command = np.zeros(2)
        self.thrust = 0.0
        self.step_index = 0
        self.target_down_m = 0.0
        self.target_cross_m = 0.0
        self._open_phase()

    # -- phases -------------------------------------------------------------
    def _open_phase(self):
        self.phase_start = self.clock
        self.elapsed = 0.0
        self.phase_dv = 0.0
        self.phase_mass = self.mass
        self.saturated_s = 0.0

    def _close_phase(self):
        end = _describe(self.position, self.velocity, self.site_radius_m)
        self.phases.append(
            {
                "name": self.gates[self.gate_index]["name"],
                "start_s": self.phase_start,
                "end_s": self.clock,
                "delta_v_mps": self.phase_dv,
                "propellant_kg": self.phase_mass - self.mass,
                "end_altitude_m": end["altitude_m"],
                "saturated_s": self.saturated_s,
            }
        )

    @property
    def done(self) -> bool:
        return self.gate_index >= len(self.gates)

    @property
    def phase(self) -> str | None:
        return None if self.done else self.gates[self.gate_index]["name"]

    def remaining_gates(self) -> list[dict]:
        """Current gate with its remaining time, then the later gates."""
        if self.done:
            return []
        current = dict(self.gates[self.gate_index])
        current["duration_s"] = current["duration_s"] - self.elapsed
        return [current] + [dict(g) for g in self.gates[self.gate_index + 1 :]]

    # -- events -------------------------------------------------------------
    def has_due_events(self) -> bool:
        return bool(self.pending) and self.clock >= self.pending[0].t_s - 1e-9

    def apply_due(self) -> list:
        applied = []
        while self.has_due_events():
            event = self.pending.pop(0)
            applied.append(event)
            self.revision += 1
            clock = self.clock
            self.log.append(
                {
                    "t_s": clock,
                    "kind": "event_applied",
                    "event": event.model_dump(),
                    "state_revision": self.revision,
                }
            )
            covers = True
            if event.kind == "thrust_loss":
                fraction = event.available_fraction
                self.engine = (
                    self.profile
                    if fraction == 1.0
                    else self.profile.model_copy(
                        update={
                            "max_thrust_n": self.profile.max_thrust_n * fraction,
                            "design_thrust_n": self.profile.design_thrust_n * fraction,
                        }
                    )
                )
            elif event.kind == "sensor_degradation":
                self.health = event.health
                explicit = (
                    event.position_sigma_m is not None
                    or event.speed_sigma_mps is not None
                )
                self.sigma_fixed = explicit
                self.sigma_factor = (
                    DEGRADED_SIGMA_FACTOR
                    if event.health == "degraded" and not explicit
                    else 1.0
                )
                self.pos_sigma = (
                    event.position_sigma_m
                    if event.position_sigma_m is not None
                    else self.sensor.position_sigma_m
                )
                self.speed_sigma = (
                    event.speed_sigma_mps
                    if event.speed_sigma_mps is not None
                    else self.sensor.speed_sigma_mps
                )
            elif event.kind == "propellant_leak":
                self.mass -= event.leaked_kg
                if self.mass < self.dry_kg:
                    self.mass, self.exhausted = self.dry_kg, True
            elif event.kind == "comm_outage":
                self.comm_until = clock + event.duration_s
            elif event.kind == "obstacle_detected":
                down = event.down_m - self.target_down_m
                cross = event.cross_m - self.target_cross_m
                covers = math.hypot(down, cross) < event.radius_m
                self.obstacles.append(
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
            if dependency in self.dependencies and covers:
                if dependency not in self.invalidated:
                    self.invalidated.append(dependency)
                self.log.append(
                    {
                        "t_s": clock,
                        "kind": "plan_invalidated",
                        "event_kind": event.kind,
                        "dependency": dependency,
                        "plan_revision": self.revision,
                        "reason": f"{event.kind.replace('_', ' ')} changed {dependency.replace('_', ' ')}, which the active plan depends on",
                    }
                )
            else:
                self.log.append(
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
        return applied

    # -- sensing ------------------------------------------------------------
    def _sigmas(self) -> tuple[float, float]:
        if self.sigma_fixed:
            return self.pos_sigma, self.speed_sigma
        altitude = float(np.linalg.norm(self.position)) - self.site_radius_m
        pos_sigma, speed_sigma = effective_sigmas(
            self.pos_sigma, self.speed_sigma, altitude, self.sensor
        )
        return pos_sigma * self.sigma_factor, speed_sigma * self.sigma_factor

    def battery_energy(self) -> float:
        return max(
            self.battery.initial_energy_j - self.battery.load_w * self.clock, 0.0
        )

    def _row(self, phase, observation, thrust):
        radius = self.site_radius_m
        return {
            "t_s": self.clock,
            "phase": phase,
            "truth": _streams(self.position, self.velocity, radius),
            "observation": (
                None
                if observation is None
                else _streams(observation[0], observation[1], radius)
            ),
            "estimate": _streams(self.est_p, self.est_v, radius),
            "estimate_error_m": float(np.linalg.norm(self.position - self.est_p)),
            "estimate_std_m": self.est_std,
            "mass_kg": self.mass,
            "propellant_kg": max(self.mass - self.dry_kg, 0.0),
            "thrust_n": thrust,
            "available_thrust_n": self.engine.max_thrust_n,
            "battery_energy_j": self.battery_energy(),
            "sensor_health": self.health,
            "ground_uplink": self.comm_until is None or self.clock >= self.comm_until,
            "target_down_m": self.target_down_m,
            "target_cross_m": self.target_cross_m,
            "record_label": "simulated",
        }

    # -- flight -------------------------------------------------------------
    def step(self) -> None:
        """Apply due events, observe, correct, guide and integrate one step."""
        self.apply_due()
        gate = self.gates[self.gate_index]
        duration = gate["duration_s"]
        step = min(self.step_s, duration - self.elapsed)
        pos_sigma, speed_sigma = self._sigmas()
        observation = observe(
            self.position, self.velocity, self.health, pos_sigma, speed_sigma, self.rng
        )
        if observation is not None:
            self.est_p = correct(self.est_p, observation[0], self.gain)
            self.est_v = correct(self.est_v, observation[1], self.gain)
            self.est_std = estimate_sigma(pos_sigma, self.gain)
        else:
            self.est_std += estimate_sigma(speed_sigma, self.gain) * step
        self.max_error = max(
            self.max_error, float(np.linalg.norm(self.position - self.est_p))
        )
        time_to_go = duration - self.elapsed
        if time_to_go > GUIDANCE_HOLD_S:
            self.command = zem_zev_acceleration(
                self.est_p,
                self.est_v,
                gate["position_m"],
                gate["velocity_mps"],
                time_to_go,
                _gravity(self.est_p),
            )
        norm = float(np.linalg.norm(self.command))
        mass = self.mass
        thrust, saturated = engine_thrust(mass * norm, self.engine)
        if mass <= self.dry_kg:
            thrust, self.exhausted = 0.0, True
        self.thrust = thrust
        direction = self.command / norm if norm > 0 else np.array([0.0, 1.0])
        if self.step_index % self.record_every == 0:
            self.rows.append(self._row(gate["name"], observation, thrust))
        actual = thrust * (1.0 + self.bias)
        flow = actual / self.ve
        self.position, self.velocity = _rk4(
            self.position, self.velocity, mass, actual, direction, flow, step
        )
        self.est_p, self.est_v = _rk4(
            self.est_p, self.est_v, mass, thrust, direction, thrust / self.ve, step
        )
        new_mass = mass - flow * step
        if new_mass < self.dry_kg:
            new_mass, self.exhausted = self.dry_kg, True
        if new_mass < mass:
            self.phase_dv += self.ve * math.log(mass / new_mass)
        self.mass = new_mass
        self.saturated_s += step if saturated else 0.0
        self.elapsed += step
        self.clock += step
        self.step_index += 1
        if self.elapsed >= duration - 1e-9:
            self._close_phase()
            self.gate_index += 1
            if not self.done:
                self._open_phase()

    # -- replanning hooks (R12) --------------------------------------------
    def estimated_state(self) -> dict:
        """What the vehicle believes: estimate, known mass and engine."""
        return {
            "position_m": self.est_p.copy(),
            "velocity_mps": self.est_v.copy(),
            "mass_kg": self.mass,
            "engine": self.engine,
            "estimate_std_m": self.est_std,
            "revision": self.revision,
            "t_s": self.clock,
            "site_radius_m": self.site_radius_m,
        }

    def true_state(self) -> dict:
        """Ground truth for independent validation (never used by guidance)."""
        return self.estimated_state() | {
            "position_m": self.position.copy(),
            "velocity_mps": self.velocity.copy(),
        }

    def retarget(
        self,
        down_m: float,
        cross_m: float,
        target_height_m: float,
        lateral_propellant_kg: float,
        reason: str,
    ) -> None:
        """Move the target by (down, cross) metres from the current one.

        Downrange: the planar state is rotated about the Moon's centre so the
        R4 gates now lead to the new target at its own height. Crossrange:
        the analytic lateral divert propellant is removed now (conservative).
        """
        radius = MOON_REFERENCE_RADIUS_M + target_height_m
        angle = down_m / radius
        c, s = math.cos(angle), math.sin(angle)
        rotation = np.array([[c, -s], [s, c]])
        self.position = rotation @ self.position
        self.velocity = rotation @ self.velocity
        self.est_p = rotation @ self.est_p
        self.est_v = rotation @ self.est_v
        self.site_radius_m = radius
        self.gates = gate_states(radius)
        self.command = np.zeros(2)
        self.mass -= lateral_propellant_kg
        if self.mass < self.dry_kg:
            self.mass, self.exhausted = self.dry_kg, True
        self.target_down_m += down_m
        self.target_cross_m += cross_m
        for obstacle in self.obstacles:
            obstacle["covers_target"] = (
                math.hypot(
                    obstacle["down_m"] - self.target_down_m,
                    obstacle["cross_m"] - self.target_cross_m,
                )
                < obstacle["radius_m"]
            )
        self.revision += 1
        self.invalidated = []
        self.log.append(
            {
                "t_s": self.clock,
                "kind": "retargeted",
                "down_m": down_m,
                "cross_m": cross_m,
                "target_height_m": target_height_m,
                "lateral_propellant_kg": lateral_propellant_kg,
                "plan_revision": self.revision,
                "reason": reason,
            }
        )

    def note(self, entry: dict) -> None:
        """Append a replanning record to the deterministic log."""
        self.log.append({"t_s": self.clock} | entry)

    # -- touchdown ----------------------------------------------------------
    def finish(self) -> dict:
        pos_sigma, speed_sigma = self._sigmas()
        observation = observe(
            self.position, self.velocity, self.health, pos_sigma, speed_sigma, self.rng
        )
        if observation is not None:
            self.est_p = correct(self.est_p, observation[0], self.gain)
            self.est_v = correct(self.est_v, observation[1], self.gain)
        final_error = float(np.linalg.norm(self.position - self.est_p))
        self.max_error = max(self.max_error, final_error)
        self.rows.append(self._row("landed", observation, self.thrust))
        described = _describe(self.position, self.velocity, self.site_radius_m)
        final = {
            "position_error_m": float(
                np.linalg.norm(self.position - np.array([0.0, self.site_radius_m]))
            ),
            "vertical_speed_mps": described["vertical_speed_mps"],
            "horizontal_speed_mps": described["horizontal_speed_mps"],
            "mass_kg": self.mass,
        }
        remaining = max(self.mass - self.dry_kg, 0.0)
        self.log.append(
            {
                "t_s": self.clock,
                "kind": "touchdown",
                "final": final,
                "propellant_remaining_kg": remaining,
                "state_revision": self.revision,
            }
        )
        for event in self.pending:
            self.log.append(
                {
                    "t_s": event.t_s,
                    "kind": "event_not_reached",
                    "event": event.model_dump(),
                }
            )
        energy = self.battery_energy()
        return {
            "rows": self.rows,
            "final": final,
            "propellant_exhausted": self.exhausted,
            "propellant_remaining_kg": remaining,
            "phases": self.phases,
            "log": self.log,
            "obstacles": self.obstacles,
            "max_estimate_error_m": self.max_error,
            "final_estimate_error_m": final_error,
            "plan": {
                "depends_on": list(self.dependencies),
                "valid": not self.invalidated,
                "invalidated_by": self.invalidated,
                "revision": self.revision,
            },
            "target_offset_m": {
                "down_m": self.target_down_m,
                "cross_m": self.target_cross_m,
            },
            "duration_s": self.clock,
            "battery_energy_j": energy,
            "battery_empty": energy <= 0.0,
            "engine_bias_fraction": self.bias,
            "log_hash": canonical_hash(self.log),
        }


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
    flight = TwinFlight(
        profile,
        site_radius_m,
        pdi_range_m,
        periapsis_speed_mps,
        sensor,
        events,
        seed,
        step_s,
        battery,
        plan_dependencies,
        record_every,
    )
    while not flight.done:
        flight.step()
    return flight.finish()


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
    """Keep ordinary twin runs on the current R9 implementation.

    Only terrain recovery uses the isolated stepwise TwinFlight engine. The
    archived run serializer adds recovery-only fields, so it must not replace
    the existing public twin output contract.
    """
    from app.twin import TwinRequest as PublicTwinRequest, run_twin as public_run

    return public_run(PublicTwinRequest.model_validate(request.model_dump()), terrain_height_m)


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
