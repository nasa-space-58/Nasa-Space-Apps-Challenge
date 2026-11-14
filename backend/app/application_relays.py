"""R13 application 3: relay-location selection after geometry/capacity checks.

Positions are in a site-centred local tangent frame (east/north/up, m) with a
spherical-surface drop d^2/(2R). Horizon masks use the Task 2 azimuth grid and
are applied conservatively at every distance. A relay is credited only with
relay equipment, range, horizon clearance, its own Earth visibility and enough
link capacity. Synthetic planning benchmark; not a validated relay site.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from shared.hybrid import Contract, Identifier, NonNegative, Positive

from .quantum_applications import ApplicationInstance

APPLICATION = "relay_selection"
FRAME = "site-centred-local-tangent-enu"
Slot = Annotated[int, Field(strict=True, ge=0, le=47)]
RejectionCode = Literal["no_relay_equipment", "no_credited_link", "capacity_exceeded"]


class HorizonMask(Contract):
    """Horizon elevation (deg) at azimuths 0, step, 2*step, ... (clockwise from north)."""

    azimuth_step_deg: Annotated[float, Field(strict=True, gt=0, le=90)]
    elevations_deg: tuple[Annotated[float, Field(ge=-90, le=90)], ...]

    @model_validator(mode="after")
    def closed_grid(self):
        return self  # Skeleton: validation added in the implementation.


class RelaySite(Contract):
    site_id: Identifier
    east_m: float
    north_m: float
    up_m: float
    observer_height_m: NonNegative = 2.0
    horizon: HorizonMask
    demand_slots: tuple[Slot, ...]
    direct_earth_slots: tuple[Slot, ...] = ()


class RelayCandidate(Contract):
    relay_id: Identifier
    east_m: float
    north_m: float
    up_m: float
    mast_height_m: NonNegative
    has_relay_equipment: bool
    capacity_links: Annotated[int, Field(strict=True, ge=1, le=8)]
    max_range_m: Positive
    earth_visible_slots: tuple[Slot, ...]


class RelayProblem(Contract):
    problem_id: Identifier
    start_utc: AwareDatetime
    slot_s: Annotated[int, Field(strict=True, ge=60, le=86400)]
    slot_count: Annotated[int, Field(strict=True, ge=1, le=48)]
    frame: Literal["site-centred-local-tangent-enu"] = FRAME
    sites: tuple[RelaySite, ...] = Field(min_length=1)
    candidates: tuple[RelayCandidate, ...] = Field(min_length=1)
    select_count: Annotated[int, Field(strict=True, ge=1, le=8)]
    clearance_margin_deg: NonNegative = 0.5
    resilience_weight: NonNegative = 0.5
    source_note: str = (
        "synthetic R13 planning benchmark; not a validated relay site or link budget"
    )

    @model_validator(mode="after")
    def consistent(self):
        return self  # Skeleton: validation added in the implementation.


class RelayLink(Contract):
    relay_id: Identifier
    site_id: Identifier
    range_m: NonNegative
    azimuth_deg: float
    elevation_deg: float
    horizon_deg: float
    line_of_sight: bool
    in_range: bool
    credited_slots: tuple[Slot, ...]


class RelayRejection(Contract):
    relay_id: Identifier
    code: RejectionCode
    detail: str


class RelayValidation(Contract):
    accepted_relay_ids: tuple[Identifier, ...]
    rejections: tuple[RelayRejection, ...]
    links: tuple[RelayLink, ...]
    demand_points: tuple[tuple[Identifier, int], ...]


def horizon_at(mask: HorizonMask, azimuth_deg: float) -> float:
    """Linear interpolation with azimuth wrap-around."""
    raise NotImplementedError


def link_geometry(site: RelaySite, relay: RelayCandidate) -> tuple[float, float, float]:
    """(range_m, azimuth_deg, elevation_deg) of the relay seen from the site."""
    raise NotImplementedError


def validate_relay_geometry(problem: RelayProblem) -> RelayValidation:
    """Credited links and coded rejections before any selection."""
    raise NotImplementedError


def relay_metrics(
    problem: RelayProblem, validation: RelayValidation, selected: tuple[str, ...]
) -> dict:
    """Coverage, single-failure resilience, double coverage and longest gaps."""
    raise NotImplementedError


def generate_relays(
    seed: int,
    *,
    sites: int = 3,
    candidates: int = 8,
    slots: int = 8,
    select_count: int = 2,
    start_utc: datetime | None = None,
) -> RelayProblem:
    """Seeded synthetic instance (random.Random)."""
    raise NotImplementedError


def build_relays(problem: RelayProblem) -> ApplicationInstance:
    """Validated candidates become the variables; raises ValueError when fewer
    than `select_count` candidates are validated."""
    raise NotImplementedError
