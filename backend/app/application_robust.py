"""R13 application 5: robust plan selection after R6.

Two landers each choose one of their candidate plans. Every plan is flown
through R6 `run_monte_carlo` with the same seed and run count, so scenario s is
the same seeded error draw for every plan and both landers (explicit
common-cause assumption). Finite Monte Carlo counts are not certified
probabilities. See docs/quantum-applications.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import Field, model_validator

from shared.hybrid import Contract, Identifier, NonNegative, Positive

from .quantum_applications import ApplicationInstance
from .risk import LocalSlopeMap

APPLICATION = "robust_plans"


class PlanOption(Contract):
    plan_id: Identifier
    orbit_altitude_km: Annotated[float, Field(ge=30, le=500)]
    approach_azimuth_deg: Annotated[float, Field(ge=0, lt=360)]


class RobustPlan(Contract):
    """Per-scenario R6 outcome of one plan."""

    plan_id: Identifier
    orbit_altitude_km: float
    approach_azimuth_deg: float
    success: tuple[bool, ...]
    miss_m: tuple[NonNegative, ...]
    r6_result_id: str


class LanderPlans(Contract):
    lander_id: Identifier
    site_id: Identifier
    plans: tuple[RobustPlan, ...] = Field(min_length=1)


class RobustProblem(Contract):
    problem_id: Identifier
    runs: Annotated[int, Field(strict=True, ge=10, le=300)]
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
    landers: tuple[LanderPlans, LanderPlans]
    cost_limit_m: Positive
    cost_weight: NonNegative = 1.0
    cost_scale_m: Positive = 1000.0
    source_note: str = (
        "R6 seeded Monte Carlo on synthetic slope maps; common-cause scenarios"
    )

    @model_validator(mode="after")
    def consistent(self):
        raise NotImplementedError


@dataclass(frozen=True)
class LanderSpec:
    """R6 inputs for one lander; `plans` vary the orbit and approach."""

    lander_id: str
    site_id: str
    latitude_deg: float
    longitude_deg_east: float
    terrain_height_m: float
    slope_map: LocalSlopeMap | None
    plans: tuple[PlanOption, ...]


def robust_problem_from_r6(
    problem_id: str,
    landers: tuple[LanderSpec, LanderSpec],
    *,
    runs: int,
    seed: int,
    cost_limit_m: float,
) -> RobustProblem:
    """Fly every plan through R6 with the same seed and run count."""
    raise NotImplementedError


def synthetic_slope_map(seed: int, *, size: int = 101, pixel_m: float = 80.0):
    """Seeded slope map (deg) with steep patches around a site at the origin."""
    raise NotImplementedError


def generate_robust(seed: int, *, runs: int = 24) -> RobustProblem:
    """Two seeded landers with four plans each, flown through R6."""
    raise NotImplementedError


def build_robust(problem: RobustProblem) -> ApplicationInstance:
    """Variables, validator, MILP and exact pairwise QUBO for one problem."""
    raise NotImplementedError
