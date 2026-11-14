"""R13 application 1: communication-slot allocation on shared stations.

Synthetic planning benchmark shaped like R2/R11 inputs: station availability,
mission-station visibility and link rates are separate inputs. Not a DSN
booking. See docs/quantum-applications.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, Field, model_validator

from shared.hybrid import Contract, Identifier, NonNegative, Positive

from .quantum_applications import ApplicationInstance

Slot = Annotated[int, Field(strict=True, ge=0, le=47)]
APPLICATION = "comm_slots"


class CommStation(Contract):
    station_id: Identifier
    capacity_links: Annotated[int, Field(strict=True, ge=1, le=4)]
    available_slots: tuple[Slot, ...]


class CommVisibility(Contract):
    """Geometry evidence only; availability and capacity stay separate."""

    mission_id: Identifier
    station_id: Identifier
    visible_slots: tuple[Slot, ...]


class CommRate(Contract):
    mission_id: Identifier
    station_id: Identifier
    rate_bps: Positive


class CommRequest(Contract):
    request_id: Identifier
    mission_id: Identifier
    volume_bits: Positive
    ready_slot: Slot
    deadline_slot: Slot


class CommSlotProblem(Contract):
    problem_id: Identifier
    start_utc: AwareDatetime
    slot_s: Annotated[int, Field(strict=True, ge=60, le=86400)]
    slot_count: Annotated[int, Field(strict=True, ge=1, le=48)]
    stations: tuple[CommStation, ...] = Field(min_length=1)
    visibility: tuple[CommVisibility, ...]
    rates: tuple[CommRate, ...]
    requests: tuple[CommRequest, ...] = Field(min_length=1)
    bit_scale: Positive = 1e8
    latency_weight_per_slot: NonNegative = 0.05
    source_note: str = "synthetic R13 planning benchmark; not a DSN booking"

    @model_validator(mode="after")
    def consistent(self):
        return self  # Skeleton: validation added in the implementation.


def generate_comm_slots(
    seed: int,
    *,
    missions: int = 3,
    requests_per_mission: int = 2,
    stations: int = 2,
    slots: int = 6,
    capacity_links: int = 1,
    start_utc: datetime | None = None,
) -> CommSlotProblem:
    """Seeded synthetic instance (random.Random)."""
    raise NotImplementedError


def build_comm_slots(problem: CommSlotProblem) -> ApplicationInstance:
    """Variables, validator, MILP and QUBO for one problem."""
    raise NotImplementedError
