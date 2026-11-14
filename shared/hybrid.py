"""H0 versioned, solver-neutral mission contracts. No optimizer or flight model."""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$", max_length=128)]
Number = Annotated[float, Field(strict=True)]
NonNegative = Annotated[float, Field(strict=True, ge=0)]
Positive = Annotated[float, Field(strict=True, gt=0)]
Revision = Annotated[int, Field(strict=True, ge=0)]
Vector3 = tuple[Number, Number, Number]
Sigma3 = tuple[NonNegative, NonNegative, NonNegative]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]


class Contract(BaseModel):
    """Reject extra fields and non-finite values; validate without network access."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True, validate_default=True)

    @model_validator(mode="after")
    def check_contract(self):
        raise NotImplementedError


class TimeWindow(Contract):
    """Half-open [start, end) interval in explicit UTC."""

    start_utc: datetime
    end_utc: datetime


class Source(Contract):
    """Origin and identity of observations, calculations or declared assumptions."""

    source_id: Identifier
    kind: Literal["observed", "derived", "synthetic", "assumption"]
    description: Annotated[str, Field(strict=True, min_length=1)]
    locator: str | None = None
    version: str | None = None
    sha256: Digest | None = None
    analysis_id: str | None = None
    parent_source_ids: tuple[Identifier, ...] = ()


class Site(Contract):
    """MOON_ME surface angles; no implicit terrain height or safety claim."""

    site_id: Identifier
    latitude_deg: Annotated[float, Field(strict=True, ge=-90, le=90)]
    longitude_deg_east: Annotated[float, Field(strict=True, ge=0, lt=360)]
    reference_frame: Literal["MOON_ME"] = "MOON_ME"


class Mission(Contract):
    """V1 requires exactly one candidate per mission, by its UTC deadline."""

    mission_id: Identifier
    vehicle_id: Identifier
    deadline_utc: datetime
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class Resource(Contract):
    """Concurrent channel capacity or cumulative joule budget over availability."""

    resource_id: Identifier
    mode: Literal["concurrent", "budget"]
    unit: Literal["channel", "J"]
    capacity: NonNegative | None
    availability: tuple[TimeWindow, ...]
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class Demand(Contract):
    """Constant channel demand or total joules consumed within a candidate interval."""

    resource_id: Identifier
    amount: NonNegative
    unit: Literal["channel", "J"]
    window: TimeWindow


class Candidate(Contract):
    """A proposed option; structural validity does not certify feasibility."""

    option_id: Identifier
    mission_id: Identifier
    site: Site
    window: TimeWindow
    demands: tuple[Demand, ...] = ()
    metrics: dict[Identifier, Number | None]
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class Objective(Contract):
    """Weighted normalized metric: sum(weight * signed value / scale)."""

    metric: Identifier
    unit: Literal["score", "s", "J", "kg"]
    direction: Literal["maximize", "minimize"]
    weight: Positive
    scale: Positive


class Incompatibility(Contract):
    """At most one of these candidate IDs may be selected (pairwise exclusion)."""

    constraint_id: Identifier
    kind: Literal["at_most_one"] = "at_most_one"
    option_ids: tuple[Identifier, ...] = Field(min_length=2)


class StateSnapshot(Contract):
    """Moon-centered rotating MOON_ME Cartesian components; units in field names."""

    state_id: Identifier
    vehicle_id: Identifier
    revision: Revision
    epoch_utc: datetime
    record_kind: Literal["ground_truth", "observation", "estimate"]
    reference_frame: Literal["MOON_ME"] = "MOON_ME"
    position_m: Vector3 | None = None
    velocity_mps: Vector3 | None = None
    total_mass_kg: Positive | None = None
    propellant_kg: NonNegative | None = None
    available_thrust_n: NonNegative | None = None
    battery_energy_j: NonNegative | None = None
    battery_capacity_j: Positive | None = None
    position_std_m: Sigma3 | None = None
    sensor_id: Identifier | None = None
    sensor_health: Literal["nominal", "degraded", "lost", "unknown"] | None = None
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class ResourceOutage(Contract):
    kind: Literal["resource_outage"]
    resource_id: Identifier
    window: TimeWindow


class ThrustLoss(Contract):
    kind: Literal["thrust_loss"]
    vehicle_id: Identifier
    available_thrust_n: NonNegative


class SensorDegradation(Contract):
    kind: Literal["sensor_degradation"]
    vehicle_id: Identifier
    sensor_id: Identifier
    position_std_m: Sigma3


class Event(Contract):
    """An event declaration, not an executed state transition."""

    event_id: Identifier
    epoch_utc: datetime
    sequence: Revision
    payload: Annotated[ResourceOutage | ThrustLoss | SensorDegradation, Field(discriminator="kind")]
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class Scenario(Contract):
    """V1 benchmark input for both solver families; no feasibility assertion."""

    schema_version: Literal[1] = 1
    scenario_id: Identifier
    revision: Revision
    description: Annotated[str, Field(strict=True, min_length=1)]
    seed: Annotated[int, Field(strict=True, ge=0)]
    window: TimeWindow
    selection_rule: Literal["exactly_one_per_mission"] = "exactly_one_per_mission"
    missions: tuple[Mission, ...] = Field(min_length=1)
    candidates: tuple[Candidate, ...] = Field(min_length=1)
    resources: tuple[Resource, ...] = ()
    objectives: tuple[Objective, ...] = Field(min_length=1)
    incompatibilities: tuple[Incompatibility, ...] = ()
    sources: tuple[Source, ...] = Field(min_length=1)
    states: tuple[StateSnapshot, ...] = ()
    events: tuple[Event, ...] = ()


class Plan(Contract):
    """Unvalidated selection; H1 independently checks original physical constraints."""

    schema_version: Literal[1] = 1
    plan_id: Identifier
    scenario_id: Identifier
    scenario_revision: Revision
    input_fingerprint: Digest
    state_revisions: dict[Identifier, Revision]
    selected_option_ids: tuple[Identifier, ...] = Field(min_length=1)
    execution_mode: Literal["classical", "noiseless_simulator", "noise_model", "hardware"]
    solver_name: Annotated[str, Field(strict=True, min_length=1)]
    validation_status: Literal["unvalidated"] = "unvalidated"


def canonical_json(scenario: Scenario) -> str:
    """Return canonical V1 JSON; normalize unordered collections, UTC and numbers."""
    raise NotImplementedError


def fingerprint(scenario: Scenario) -> str:
    """SHA-256 of canonical scenario JSON including state, provenance and events."""
    raise NotImplementedError


def load_scenario(path: str | Path) -> Scenario:
    """Load local JSON, raising ValueError for invalid data and OSError for I/O."""
    raise NotImplementedError


def save_scenario(scenario: Scenario, path: str | Path) -> None:
    """Write canonical UTF-8 JSON; no network or directory creation."""
    raise NotImplementedError


def validate_plan_references(plan: Plan, scenario: Scenario) -> None:
    """Check problem identity and referenced IDs, not selection feasibility."""
    raise NotImplementedError
