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

from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.descent import OrbitSettings, Tolerances, VehicleProfile
from app.fuel_optimization import DEFAULT_RESERVE_DELTA_V_MPS

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
    speed_sigma_mps: float = Field(default=0.5, ge=0, le=50)
    gain: float = Field(default=0.3, gt=0, le=1)
    thrust_noise_fraction: float = Field(default=0.005, ge=0, le=0.1)


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


def estimate_sigma(observation_sigma: float, gain: float) -> float:
    """Steady-state error of a constant-gain filter on white noise:
    sigma * sqrt(K / (2 - K))."""
    raise NotImplementedError


def correct(estimate, observation, gain: float):
    """(1 - K) * estimate + K * observation, elementwise (K = 1 copies exactly)."""
    raise NotImplementedError


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
    raise NotImplementedError


def canonical_hash(value) -> str:
    """SHA-256 of canonical JSON (sorted keys, no NaN)."""
    raise NotImplementedError


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
    raise NotImplementedError


def run_twin(request: TwinRequest, terrain_height_m: float) -> dict:
    """Event run plus a same-seed no-event baseline and their differences.

    Raises TwinError for an unknown vehicle or a plan the R4 model rejects.
    """
    raise NotImplementedError


def register_twin_routes(app, data_dir: Path) -> None:
    """POST /twin/runs: 409 without cached terrain, 422 for invalid input."""

    @app.post("/twin/runs")
    def twin_runs(body: TwinRequest):
        raise NotImplementedError
