"""R13 application 4: rover target and route selection after R8.

Route candidates come from R8 `plan_routes` at two mobility slope limits.
Excursions are out-and-back from the lander; energy is a stated planning
assumption (drive power and climb work), and the communication gap is the
sampled no-Earth share of the route duration. Synthetic terrain and sky; not a
certified traverse plan. See docs/quantum-applications.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Field, model_validator

from shared.hybrid import Contract, Identifier, NonNegative, Positive

from .quantum_applications import ApplicationInstance

APPLICATION = "rover_routes"
VARIANT_SLOPE_LIMITS_DEG = {"conservative": 12.0, "direct": 20.0}
RejectionCode = Literal[
    "outside_terrain", "target_impassable", "no_path", "comm_gap_exceeds_limit"
]


class RoverEnergyModel(Contract):
    """Planning assumption, not a measured rover model."""

    drive_power_w: Positive = 100.0
    rover_mass_kg: Positive = 300.0
    climb_efficiency: Annotated[float, Field(strict=True, gt=0, le=1)] = 0.5


class RoverCandidate(Contract):
    target_id: Identifier
    variant_id: Identifier
    slope_limit_deg: Positive
    length_m: Positive
    duration_s: Positive
    elevation_gain_m: NonNegative
    max_slope_deg: NonNegative
    energy_j: NonNegative
    comm_gap_s: NonNegative
    shadowed_fraction: Annotated[float, Field(ge=0, le=1)]
    route_result_id: str


class RouteRejection(Contract):
    target_id: Identifier
    variant_id: Identifier
    code: RejectionCode
    detail: str


class RoverProblem(Contract):
    problem_id: Identifier
    site_id: Identifier
    science_values: dict[Identifier, Positive]
    candidates: tuple[RoverCandidate, ...]
    rejections: tuple[RouteRejection, ...] = ()
    time_budget_s: Positive
    energy_budget_j: Positive
    max_comm_gap_s: NonNegative
    energy_weight: NonNegative = 1.0
    energy_scale_j: Positive = 1e7
    gap_weight: NonNegative = 1.0
    gap_scale_s: Positive = 36000.0
    source_note: str = (
        "R8 routes on synthetic terrain; out-and-back excursions; not a traverse plan"
    )

    @model_validator(mode="after")
    def consistent(self):
        raise NotImplementedError


def route_energy_j(
    duration_s: float, elevation_gain_m: float, model: RoverEnergyModel
) -> float:
    """Out-and-back energy: 2 * (P * duration + m * g_moon * climb / efficiency)."""
    raise NotImplementedError


def route_comm_gap_s(route: Mapping) -> float:
    """duration * blocked-Earth waypoints / waypoints of one R8 route."""
    raise NotImplementedError


def rover_problem_from_routes(
    problem_id: str,
    route_results: Mapping[str, dict],
    science_values: Mapping[str, float],
    *,
    time_budget_s: float,
    energy_budget_j: float,
    max_comm_gap_s: float,
    energy_model: RoverEnergyModel = RoverEnergyModel(),
) -> RoverProblem:
    """Candidates and coded rejections from R8 outputs keyed by variant id."""
    raise NotImplementedError


def generate_rover(seed: int, *, targets: int = 5) -> RoverProblem:
    """Seeded synthetic DEM and sky, routed by R8 for both variants."""
    raise NotImplementedError


def build_rover(problem: RoverProblem) -> ApplicationInstance:
    """Variables, validator, MILP and QUBO for one problem."""
    raise NotImplementedError
