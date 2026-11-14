"""H0 versioned, solver-neutral mission contracts. No optimizer or flight model."""

import hashlib
import json
from datetime import datetime, timedelta
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[
    str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$", max_length=128)
]
Number = Annotated[float, Field(strict=True)]
NonNegative = Annotated[float, Field(strict=True, ge=0)]
Positive = Annotated[float, Field(strict=True, gt=0)]
Revision = Annotated[int, Field(strict=True, ge=0)]
Vector3 = tuple[Number, Number, Number]
Sigma3 = tuple[NonNegative, NonNegative, NonNegative]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]


class Contract(BaseModel):
    """Reject extra fields and non-finite values; validate without network access."""

    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, frozen=True, validate_default=True
    )

    @model_validator(mode="before")
    @classmethod
    def check_raw_types(cls, data):
        if isinstance(data, dict):
            if "schema_version" in data and type(data["schema_version"]) is not int:
                raise ValueError("schema_version must be an integer")
            for key, value in data.items():
                if key.endswith("_utc") and not isinstance(value, (str, datetime)):
                    raise ValueError(f"{key} must be an explicit UTC timestamp")
                if isinstance(value, str) and not value.strip():
                    raise ValueError(f"{key} must not be blank")
        return data

    @model_validator(mode="after")
    def check_contract(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, datetime) and value.utcoffset() != timedelta(0):
                raise ValueError(f"{name} must use explicit UTC (Z or +00:00)")
        _validate_contract(self)
        # Set-like collections have deterministic in-memory order as well as JSON order.
        for name in type(self).model_fields:
            value = getattr(self, name)
            if name in _UNORDERED_FIELDS and isinstance(value, tuple):
                object.__setattr__(
                    self, name, tuple(sorted(value, key=_collection_key))
                )
        return self


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
    payload: Annotated[
        ResourceOutage | ThrustLoss | SensorDegradation, Field(discriminator="kind")
    ]
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
    execution_mode: Literal[
        "classical", "noiseless_simulator", "noise_model", "hardware"
    ]
    solver_name: Annotated[str, Field(strict=True, min_length=1)]
    validation_status: Literal["unvalidated"] = "unvalidated"


def canonical_json(scenario: Scenario) -> str:
    """Return canonical V1 JSON; normalize unordered collections, UTC and numbers."""
    checked = Scenario.model_validate(scenario.model_dump(mode="python"))
    return json.dumps(
        _normalize(checked.model_dump(mode="json")),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def fingerprint(scenario: Scenario) -> str:
    """SHA-256 of canonical scenario JSON including state, provenance and events."""
    return hashlib.sha256(canonical_json(scenario).encode("utf-8")).hexdigest()


def load_scenario(path: str | Path) -> Scenario:
    """Load local JSON, raising ValueError for invalid data and OSError for I/O."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"Non-finite JSON number: {value}")

    raw = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=pairs,
        parse_constant=invalid_constant,
    )
    return Scenario.model_validate(raw)


def save_scenario(scenario: Scenario, path: str | Path) -> None:
    """Write canonical UTF-8 JSON; no network or directory creation."""
    Path(path).write_text(canonical_json(scenario) + "\n", encoding="utf-8")


def validate_plan_references(plan: Plan, scenario: Scenario) -> None:
    """Check problem identity and referenced IDs, not selection feasibility."""
    scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
    plan = Plan.model_validate(plan.model_dump(mode="python"))
    if (
        plan.scenario_id != scenario.scenario_id
        or plan.scenario_revision != scenario.revision
        or plan.input_fingerprint != fingerprint(scenario)
    ):
        raise ValueError("Plan input identity does not match scenario")
    if plan.state_revisions != {s.state_id: s.revision for s in scenario.states}:
        raise ValueError("Plan state revisions do not match scenario")
    known = {c.option_id for c in scenario.candidates}
    if not set(plan.selected_option_ids) <= known:
        raise ValueError("Plan refers to unknown option IDs")


# These collections are sets semantically. Vector coordinates are never sorted.
_UNORDERED_FIELDS = frozenset(
    {
        "missions",
        "candidates",
        "resources",
        "objectives",
        "incompatibilities",
        "sources",
        "states",
        "events",
        "availability",
        "demands",
        "source_ids",
        "parent_source_ids",
        "option_ids",
    }
)


def _collection_key(value):
    identity = {
        Mission: "mission_id",
        Candidate: "option_id",
        Resource: "resource_id",
        Source: "source_id",
        Objective: "metric",
        Incompatibility: "constraint_id",
        StateSnapshot: "state_id",
        Event: "event_id",
    }.get(type(value))
    if identity is not None:
        return getattr(value, identity)
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        _normalize(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _normalize(value, field=""):
    if isinstance(value, dict):
        return {key: _normalize(item, key) for key, item in value.items()}
    if isinstance(value, list):
        items = [_normalize(item) for item in value]
        if field in _UNORDERED_FIELDS:
            items.sort(
                key=lambda item: json.dumps(
                    item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
            )
        return items
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")


def _contained(inner: TimeWindow, outer: TimeWindow):
    return outer.start_utc <= inner.start_utc < inner.end_utc <= outer.end_utc


def _validate_contract(model):
    if hasattr(model, "source_ids"):
        _unique(model.source_ids, "source references")
    if isinstance(model, TimeWindow):
        if model.start_utc >= model.end_utc:
            raise ValueError("TimeWindow requires start_utc < end_utc")
    elif isinstance(model, Source):
        _unique(model.parent_source_ids, "source parents")
        if model.kind == "observed" and not (
            model.locator and (model.version or model.sha256 or model.analysis_id)
        ):
            raise ValueError(
                "Observed sources require locator and version, checksum or analysis ID"
            )
        if model.kind == "derived" and not (model.parent_source_ids and model.version):
            raise ValueError("Derived sources require parents and model version")
    elif isinstance(model, Resource):
        if (model.mode, model.unit) not in (("concurrent", "channel"), ("budget", "J")):
            raise ValueError("Resource mode and unit do not match")
        ordered = sorted(model.availability, key=lambda window: window.start_utc)
        if any(a.end_utc > b.start_utc for a, b in zip(ordered, ordered[1:])):
            raise ValueError("Resource availability windows overlap")
    elif isinstance(model, Candidate):
        for demand in model.demands:
            if not _contained(demand.window, model.window):
                raise ValueError("Demand must be contained in candidate window")
    elif isinstance(model, Incompatibility):
        _unique(model.option_ids, "incompatibility option IDs")
    elif isinstance(model, StateSnapshot):
        _validate_state(model)
    elif isinstance(model, Event):
        if (
            isinstance(model.payload, ResourceOutage)
            and model.epoch_utc != model.payload.window.start_utc
        ):
            raise ValueError("Outage starts at the event epoch")
    elif isinstance(model, Scenario):
        _validate_scenario(model)
    elif isinstance(model, Plan):
        _unique(model.selected_option_ids, "selected option IDs")


def _validate_state(state):
    measurements = (
        state.position_m,
        state.velocity_mps,
        state.total_mass_kg,
        state.propellant_kg,
        state.available_thrust_n,
        state.battery_energy_j,
    )
    if all(value is None for value in measurements):
        raise ValueError("State must contain at least one known measurement")
    if (
        state.total_mass_kg is not None
        and state.propellant_kg is not None
        and state.propellant_kg > state.total_mass_kg
    ):
        raise ValueError("Propellant exceeds total mass")
    if (
        state.battery_capacity_j is not None
        and state.battery_energy_j is not None
        and state.battery_energy_j > state.battery_capacity_j
    ):
        raise ValueError("Stored battery energy exceeds capacity")
    if state.record_kind == "observation":
        if state.sensor_id is None or state.sensor_health is None:
            raise ValueError("Observation requires sensor identity and health")
    elif state.sensor_id is not None or state.sensor_health is not None:
        raise ValueError("Sensor identity and health belong to observations")
    if state.record_kind == "ground_truth" and state.position_std_m is not None:
        raise ValueError("Ground truth cannot carry estimated measurement uncertainty")
    if state.position_std_m is not None and state.position_m is None:
        raise ValueError("Position uncertainty requires a position")


def _validate_scenario(scenario):
    collections = (
        (scenario.missions, "mission_id"),
        (scenario.candidates, "option_id"),
        (scenario.resources, "resource_id"),
        (scenario.objectives, "metric"),
        (scenario.incompatibilities, "constraint_id"),
        (scenario.sources, "source_id"),
        (scenario.states, "state_id"),
        (scenario.events, "event_id"),
    )
    for items, field in collections:
        _unique([getattr(item, field) for item in items], field)
    missions = {m.mission_id: m for m in scenario.missions}
    vehicles = {m.vehicle_id for m in scenario.missions}
    resources = {r.resource_id: r for r in scenario.resources}
    sources = {s.source_id for s in scenario.sources}
    candidates = {c.option_id for c in scenario.candidates}
    metrics = {o.metric for o in scenario.objectives}
    for items in (
        scenario.missions,
        scenario.candidates,
        scenario.resources,
        scenario.states,
        scenario.events,
    ):
        for item in items:
            if not set(item.source_ids) <= sources:
                raise ValueError("Unknown source reference")
    graph = {
        source.source_id: set(source.parent_source_ids) for source in scenario.sources
    }
    for parents in graph.values():
        if not parents <= sources:
            raise ValueError("Unknown source parent")
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ValueError("Cyclic source lineage") from exc
    represented = set()
    for candidate in scenario.candidates:
        if candidate.mission_id not in missions:
            raise ValueError("Candidate refers to unknown mission")
        represented.add(candidate.mission_id)
        if not _contained(candidate.window, scenario.window):
            raise ValueError("Candidate lies outside scenario window")
        if set(candidate.metrics) != metrics:
            raise ValueError(
                "Candidate metrics must match objective names; use null for unknown values"
            )
        for demand in candidate.demands:
            if demand.resource_id not in resources:
                raise ValueError("Demand refers to unknown resource")
            if demand.unit != resources[demand.resource_id].unit:
                raise ValueError("Demand unit does not match resource")
    if represented != set(missions):
        raise ValueError("Every mission must have at least one candidate")
    for mission in scenario.missions:
        if (
            not scenario.window.start_utc
            <= mission.deadline_utc
            <= scenario.window.end_utc
        ):
            raise ValueError("Mission deadline lies outside scenario window")
    for resource in scenario.resources:
        if any(
            not _contained(window, scenario.window) for window in resource.availability
        ):
            raise ValueError("Resource availability lies outside scenario window")
    for constraint in scenario.incompatibilities:
        if not set(constraint.option_ids) <= candidates:
            raise ValueError("Incompatibility refers to unknown option")
    for state in scenario.states:
        if state.vehicle_id not in vehicles:
            raise ValueError("State refers to unknown vehicle")
        if not scenario.window.start_utc <= state.epoch_utc <= scenario.window.end_utc:
            raise ValueError("State epoch lies outside scenario window")
    _unique([event.sequence for event in scenario.events], "event sequence")
    ordered = sorted(scenario.events, key=lambda event: event.sequence)
    if any(a.epoch_utc > b.epoch_utc for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Event sequence must follow chronological order")
    for event in scenario.events:
        if not scenario.window.start_utc <= event.epoch_utc < scenario.window.end_utc:
            raise ValueError("Event epoch lies outside scenario window")
        if isinstance(event.payload, ResourceOutage):
            if event.payload.resource_id not in resources:
                raise ValueError("Outage refers to unknown resource")
            if not _contained(event.payload.window, scenario.window):
                raise ValueError("Outage lies outside scenario window")
        elif event.payload.vehicle_id not in vehicles:
            raise ValueError("Event refers to unknown vehicle")
