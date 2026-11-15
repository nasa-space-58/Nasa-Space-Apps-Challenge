"""R11 multi-mission campaign contracts. Explicit inputs only; no solver.

All timestamps are explicit UTC, site angles are MOON_ME degrees, and every
physical quantity carries its unit in its name. Geometry, station calendars,
station capacity, link data rates and link exclusivity are separate inputs:
none of them is inferred from another. Nothing here is a DSN booking, a link
budget, a validated landing site or a NASA mission plan.
"""

import hashlib
import json
from datetime import datetime
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from shared.hybrid import (
    Contract,
    Identifier,
    NonNegative,
    Positive,
    Site,
    Source,
    TimeWindow,
    _normalize,
)

MissionType = Literal["crewed_lander", "robotic_lander", "rover", "relay_lander"]
SunState = Literal["full", "partial", "dark", "boundary", "unknown"]
GeometryState = Literal["available", "unavailable", "unknown"]
Priority = Annotated[int, Field(strict=True, ge=1, le=5)]
Count = Annotated[int, Field(strict=True, ge=0)]
Fraction01 = Annotated[float, Field(strict=True, ge=0, le=1)]
Degrees = Annotated[float, Field(strict=True, ge=0, le=90)]


class SunInterval(Contract):
    """Solar-disk state over a half-open UTC interval (from Task 3 results)."""

    window: TimeWindow
    state: SunState


class GeometryInterval(Contract):
    """Geometric line-of-sight state over a half-open UTC interval."""

    window: TimeWindow
    state: GeometryState


class TerrainEvidence(Contract):
    """Landing-area slope evidence (R3 terrain safety); null means unknown."""

    max_landing_slope_deg: Degrees | None
    unknown_landing_fraction: Fraction01
    hazard_distance_m: NonNegative | None = None
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class DescentEvidence(Contract):
    """Descent propellant evidence for one vehicle at one site (R4/R5)."""

    vehicle_id: Identifier
    feasible: bool
    propellant_used_kg: NonNegative | None
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class StationGeometry(Contract):
    """Earth-and-station geometric visibility from a site (R2 per-station result)."""

    station_id: Identifier
    intervals: tuple[GeometryInterval, ...]


class SiteEvidence(Contract):
    """Validated evidence for one site. Gaps inside the window are unknown."""

    site: Site
    evidence_window: TimeWindow
    terrain: TerrainEvidence
    sunlight: tuple[SunInterval, ...]
    station_geometry: tuple[StationGeometry, ...] = ()
    descents: tuple[DescentEvidence, ...] = ()
    source_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_evidence(self):
        _check_site_evidence(self)
        return self


class VehicleModel(Contract):
    """Explicit vehicle planning assumptions; no power or battery physics."""

    vehicle_id: Identifier
    usable_propellant_kg: Positive
    propellant_reserve_kg: NonNegative
    max_landing_slope_deg: Annotated[float, Field(strict=True, gt=0, le=45)]
    max_unknown_landing_fraction: Fraction01
    solar_power_w: NonNegative
    initial_energy_j: NonNegative
    energy_reserve_j: NonNegative
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class Requirements(Contract):
    """Surface operation duration, consumed energy and data that must be delivered."""

    surface_duration_s: Positive
    energy_j: NonNegative
    downlink_bits: NonNegative


class MissionPolicy(Contract):
    """Whether the campaign may cancel or defer this mission, and at what cost."""

    cancel_allowed: bool
    cancel_penalty_score: NonNegative = 0
    defer_allowed: bool
    max_defer_s: NonNegative = 0
    defer_penalty_score: NonNegative = 0
    defer_penalty_score_per_h: NonNegative = 0

    @model_validator(mode="after")
    def check_policy(self):
        _check_policy(self)
        return self


class Dependency(Contract):
    """Successor lands at least min_lag_s after the predecessor lands."""

    predecessor_mission_id: Identifier
    kind: Literal["landed_before_landing"] = "landed_before_landing"
    min_lag_s: NonNegative


class RelayService(Contract):
    """Relay equipment on a vehicle that can forward other missions' data."""

    capacity_links: Annotated[int, Field(strict=True, ge=1)]
    max_range_m: Positive
    hop_rate_bps: Positive


class MissionSpec(Contract):
    """Mission type, priority, deadline, dependencies, resources and policy."""

    mission_id: Identifier
    vehicle_id: Identifier
    mission_type: MissionType
    priority: Priority
    landing_window: TimeWindow
    deadline_utc: datetime
    site_ids: tuple[Identifier, ...] = Field(min_length=1)
    science_score_by_site: dict[Identifier, NonNegative]
    requirements: Requirements
    policy: MissionPolicy
    dependencies: tuple[Dependency, ...] = ()
    requires_exclusive_link: bool = False
    relay_terminal: bool = False
    relay_service: RelayService | None = None
    source_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_mission(self):
        _check_mission(self)
        return self


class GroundStation(Contract):
    """Station calendar and simultaneous-link capacity; null capacity is unknown."""

    station_id: Identifier
    capacity_links: Count | None
    availability: tuple[TimeWindow, ...]
    source_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_station(self):
        _check_station(self)
        return self


class LinkRate(Contract):
    """Assumed sustained data rate for a mission through a station (not a link budget)."""

    mission_id: Identifier
    station_id: Identifier
    rate_bps: Positive
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class RelayGeometry(Contract):
    """Line-of-sight evidence between a client site and a relay site."""

    client_site_id: Identifier
    relay_site_id: Identifier
    intervals: tuple[GeometryInterval, ...]
    source_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_relay_geometry(self):
        _check_intervals(self.intervals, "relay geometry")
        return self


class Preferences(Contract):
    """Weighted, normalized soft objectives; hard rules are never weighted."""

    priority_weights: tuple[Positive, Positive, Positive, Positive, Positive]
    science_weight: NonNegative
    science_scale_score: Positive
    fuel_weight: NonNegative
    fuel_scale_kg: Positive
    wait_weight: NonNegative
    wait_scale_s: Positive
    reserve_weight: NonNegative
    reserve_scale_kg: Positive


class CampaignProblem(Contract):
    """Complete R11 input: missions, evidence, stations, links and preferences."""

    schema_version: Literal[1] = 1
    campaign_id: Identifier
    revision: Annotated[int, Field(strict=True, ge=0)]
    description: Annotated[str, Field(strict=True, min_length=1)]
    window: TimeWindow
    slot_s: Annotated[int, Field(strict=True, ge=60, le=86400)]
    landing_step_s: Annotated[int, Field(strict=True, ge=60, le=86400)]
    missions: tuple[MissionSpec, ...] = Field(min_length=1)
    vehicles: tuple[VehicleModel, ...] = Field(min_length=1)
    sites: tuple[SiteEvidence, ...] = Field(min_length=1)
    stations: tuple[GroundStation, ...] = ()
    link_rates: tuple[LinkRate, ...] = ()
    relay_geometry: tuple[RelayGeometry, ...] = ()
    preferences: Preferences
    sources: tuple[Source, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_problem(self):
        _check_problem(self)
        return self


def campaign_fingerprint(problem: CampaignProblem) -> str:
    """SHA-256 of the canonical JSON of the complete campaign input."""
    checked = CampaignProblem.model_validate(problem.model_dump(mode="python"))
    text = json.dumps(
        _normalize(checked.model_dump(mode="json")),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_campaign(path: str | Path) -> CampaignProblem:
    """Load local JSON; ValueError for invalid data, OSError for I/O."""

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
    return CampaignProblem.model_validate(raw)


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")


def _inside(inner: TimeWindow, outer: TimeWindow) -> bool:
    return outer.start_utc <= inner.start_utc and inner.end_utc <= outer.end_utc


def _check_intervals(intervals, label):
    for a, b in zip(intervals, intervals[1:]):
        if a.window.end_utc > b.window.start_utc:
            raise ValueError(f"{label} intervals must be ordered and nonoverlapping")


def _sort(model, name, key):
    object.__setattr__(model, name, tuple(sorted(getattr(model, name), key=key)))


def _check_site_evidence(evidence):
    _sort(evidence, "station_geometry", lambda g: g.station_id)
    _sort(evidence, "descents", lambda d: d.vehicle_id)
    _check_intervals(evidence.sunlight, "Sunlight")
    _unique([g.station_id for g in evidence.station_geometry], "station geometry")
    _unique([d.vehicle_id for d in evidence.descents], "descent vehicle")
    rows = list(evidence.sunlight)
    for geometry in evidence.station_geometry:
        _check_intervals(geometry.intervals, f"Station {geometry.station_id} geometry")
        rows += geometry.intervals
    if any(not _inside(row.window, evidence.evidence_window) for row in rows):
        raise ValueError("Evidence intervals must lie inside the evidence window")


def _check_policy(policy):
    if not policy.cancel_allowed and policy.cancel_penalty_score != 0:
        raise ValueError("A cancellation penalty requires cancel_allowed")
    if policy.defer_allowed and policy.max_defer_s <= 0:
        raise ValueError("defer_allowed requires a positive max_defer_s")
    if not policy.defer_allowed and (
        policy.max_defer_s
        or policy.defer_penalty_score
        or policy.defer_penalty_score_per_h
    ):
        raise ValueError("Deferral limits and penalties require defer_allowed")


def _check_mission(mission):
    if mission.deadline_utc <= mission.landing_window.start_utc:
        raise ValueError("Mission deadline must follow the landing window start")
    _unique(mission.site_ids, "mission site IDs")
    if set(mission.science_score_by_site) != set(mission.site_ids):
        raise ValueError("science_score_by_site must name exactly the mission sites")
    predecessors = [d.predecessor_mission_id for d in mission.dependencies]
    _unique(predecessors, "mission dependencies")
    if mission.mission_id in predecessors:
        raise ValueError("A mission cannot depend on itself")
    _sort(mission, "site_ids", lambda value: value)
    _sort(mission, "dependencies", lambda d: d.predecessor_mission_id)


def _check_station(station):
    ordered = sorted(station.availability, key=lambda w: w.start_utc)
    if any(a.end_utc > b.start_utc for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Station availability windows overlap")


def _cited(problem):
    for item in (*problem.missions, *problem.vehicles, *problem.stations):
        yield item.source_ids
    for item in (*problem.link_rates, *problem.relay_geometry):
        yield item.source_ids
    for evidence in problem.sites:
        yield evidence.source_ids
        yield evidence.terrain.source_ids
        for descent in evidence.descents:
            yield descent.source_ids


def _check_problem(problem):
    # Set-like collections: canonical order in memory and in the fingerprint.
    _sort(problem, "missions", lambda m: m.mission_id)
    _sort(problem, "vehicles", lambda v: v.vehicle_id)
    _sort(problem, "sites", lambda s: s.site.site_id)
    _sort(problem, "stations", lambda s: s.station_id)
    _sort(problem, "link_rates", lambda r: (r.mission_id, r.station_id))
    _sort(problem, "relay_geometry", lambda g: (g.client_site_id, g.relay_site_id))
    _sort(problem, "sources", lambda s: s.source_id)
    window = problem.window
    duration_s = (window.end_utc - window.start_utc).total_seconds()
    if duration_s % problem.slot_s:
        raise ValueError("Campaign window must be a whole number of slots")
    missions = {m.mission_id: m for m in problem.missions}
    _unique([m.mission_id for m in problem.missions], "mission IDs")
    _unique([v.vehicle_id for v in problem.vehicles], "vehicle IDs")
    _unique([s.site.site_id for s in problem.sites], "site IDs")
    _unique([s.station_id for s in problem.stations], "station IDs")
    _unique([s.source_id for s in problem.sources], "source IDs")
    _unique([(r.mission_id, r.station_id) for r in problem.link_rates], "link rates")
    _unique(
        [(g.client_site_id, g.relay_site_id) for g in problem.relay_geometry],
        "relay geometry pairs",
    )
    vehicles = {v.vehicle_id for v in problem.vehicles}
    sites = {s.site.site_id for s in problem.sites}
    stations = {s.station_id for s in problem.stations}
    sources = {s.source_id for s in problem.sources}
    used = [m.vehicle_id for m in problem.missions]
    if len(used) != len(set(used)):
        raise ValueError("Each vehicle flies at most one mission")
    for mission in problem.missions:
        if mission.vehicle_id not in vehicles:
            raise ValueError(f"Mission {mission.mission_id} refers to unknown vehicle")
        if not set(mission.site_ids) <= sites:
            raise ValueError(f"Mission {mission.mission_id} refers to unknown site")
        if not _inside(mission.landing_window, window):
            raise ValueError("Mission landing window lies outside the campaign window")
        if mission.deadline_utc > window.end_utc:
            raise ValueError("Mission deadline lies after the campaign window")
        for dependency in mission.dependencies:
            if dependency.predecessor_mission_id not in missions:
                raise ValueError("Dependency refers to unknown mission")
    graph = {
        m.mission_id: {d.predecessor_mission_id for d in m.dependencies}
        for m in problem.missions
    }
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ValueError("Cyclic mission dependencies") from exc
    for station in problem.stations:
        if any(not _inside(w, window) for w in station.availability):
            raise ValueError("Station availability lies outside the campaign window")
    for rate in problem.link_rates:
        if rate.mission_id not in missions or rate.station_id not in stations:
            raise ValueError("Link rate refers to unknown mission or station")
    for geometry in problem.relay_geometry:
        if not {geometry.client_site_id, geometry.relay_site_id} <= sites:
            raise ValueError("Relay geometry refers to unknown site")
    for evidence in problem.sites:
        if not {g.station_id for g in evidence.station_geometry} <= stations:
            raise ValueError("Site station geometry refers to unknown station")
    for cited in _cited(problem):
        if not set(cited) <= sources:
            raise ValueError("Unknown source reference")
    lineage = {s.source_id: set(s.parent_source_ids) for s in problem.sources}
    for parents in lineage.values():
        if not parents <= sources:
            raise ValueError("Unknown source parent")
    try:
        tuple(TopologicalSorter(lineage).static_order())
    except CycleError as exc:
        raise ValueError("Cyclic source lineage") from exc
