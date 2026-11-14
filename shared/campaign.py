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
    raise NotImplementedError


def load_campaign(path: str | Path) -> CampaignProblem:
    """Load local JSON; ValueError for invalid data, OSError for I/O."""
    raise NotImplementedError


def _check_intervals(intervals, label):
    raise NotImplementedError


def _check_site_evidence(evidence):
    raise NotImplementedError


def _check_policy(policy):
    raise NotImplementedError


def _check_mission(mission):
    raise NotImplementedError


def _check_station(station):
    raise NotImplementedError


def _check_problem(problem):
    raise NotImplementedError


# Referenced by the implementation; kept importable in the skeleton.
_UNUSED = (hashlib, json, CycleError, TopologicalSorter, _normalize)
