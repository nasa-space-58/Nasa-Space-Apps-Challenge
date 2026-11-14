"""R13 application 2: science-activity selection and ordering.

One instrument, precedence rules, deadlines and an explicitly ideal battery
(unlimited, lossless storage; H3-style assumption) with per-slot solar input.
Synthetic planning benchmark. See docs/quantum-applications.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, Field, model_validator

from shared.hybrid import Contract, Identifier, NonNegative, Positive

from .quantum_applications import ApplicationInstance

APPLICATION = "science_activities"


class ScienceActivity(Contract):
    """Must finish by `deadline_slot` (exclusive end slot index)."""

    activity_id: Identifier
    science_value: Positive
    duration_slots: Annotated[int, Field(strict=True, ge=1, le=48)]
    energy_j: NonNegative
    earliest_slot: Annotated[int, Field(strict=True, ge=0, le=47)]
    deadline_slot: Annotated[int, Field(strict=True, ge=1, le=48)]


class Precedence(Contract):
    before_id: Identifier
    after_id: Identifier


class ScienceProblem(Contract):
    problem_id: Identifier
    start_utc: AwareDatetime
    slot_s: Annotated[int, Field(strict=True, ge=60, le=86400)]
    slot_count: Annotated[int, Field(strict=True, ge=1, le=48)]
    activities: tuple[ScienceActivity, ...] = Field(min_length=1)
    precedences: tuple[Precedence, ...] = ()
    initial_energy_j: NonNegative
    reserve_energy_j: NonNegative
    solar_input_j_per_slot: tuple[NonNegative, ...]
    lateness_weight_per_slot: NonNegative = 0.05
    source_note: str = (
        "synthetic R13 planning benchmark; ideal unlimited lossless battery"
    )

    @model_validator(mode="after")
    def consistent(self):
        return self  # Skeleton: validation added in the implementation.


def generate_science(
    seed: int,
    *,
    activities: int = 4,
    slots: int = 6,
    precedence_pairs: int = 1,
    start_utc: datetime | None = None,
) -> ScienceProblem:
    """Seeded synthetic instance (random.Random)."""
    raise NotImplementedError


def build_science(problem: ScienceProblem) -> ApplicationInstance:
    """Variables, validator, MILP and QUBO for one problem."""
    raise NotImplementedError
