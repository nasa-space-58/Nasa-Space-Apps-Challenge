"""H1 static mission-option planning. No physics, event replay or quantum calls."""

from typing import Literal

from shared.hybrid import (
    Candidate,
    Contract,
    Demand,
    Identifier,
    NonNegative,
    Number,
    Plan,
    Scenario,
    Site,
    TimeWindow,
)


class CandidateInput(Contract):
    """Explicit site/time alternative; all metrics and demands supplied by caller."""

    mission_id: Identifier
    site: Site
    window: TimeWindow
    demands: tuple[Demand, ...] = ()
    metrics: dict[Identifier, Number | None]
    source_ids: tuple[Identifier, ...]


class Issue(Contract):
    """A constraint or unsupported-input explanation; quantities retain their unit."""

    code: str
    message: str
    option_ids: tuple[str, ...] = ()
    mission_id: str | None = None
    resource_id: str | None = None
    window: TimeWindow | None = None
    required: str | None = None
    capacity: str | None = None
    unit: str | None = None


class ValidationReport(Contract):
    """Evidence against original static rules, separate from H0's unvalidated Plan."""

    status: Literal["feasible", "infeasible", "incomplete", "unsupported"]
    input_fingerprint: str
    selected_option_ids: tuple[str, ...]
    issues: tuple[Issue, ...] = ()
    score: Number | None = None
    exact_score: str | None = None
    metric_totals: dict[str, Number] = {}


class SolverResult(Contract):
    """Counts cover only evaluated assignments; optimality requires complete search."""

    status: Literal[
        "optimal", "infeasible", "incomplete", "unsupported", "limit_reached", "proposal"
    ]
    solver_name: str
    input_fingerprint: str
    total_combinations: int
    evaluated_combinations: int = 0
    feasible_count: int = 0
    infeasible_count: int = 0
    search_complete: bool = False
    optimal_plan_count: int | None = None
    plan: Plan | None = None
    validation: ValidationReport | None = None
    issues: tuple[Issue, ...] = ()
    failure_counts: dict[str, int] = {}
    elapsed_s: NonNegative = 0
    max_evaluations: int | None = None


def generate_candidates(inputs: tuple[CandidateInput, ...]) -> tuple[Candidate, ...]:
    """Assign stable content IDs to explicit options; reject empty/duplicate input.

    Return H0 candidates, preserving MOON_ME sites, UTC windows, units and sources.
    Does not filter feasibility or infer metrics. Raises ValueError for invalid rows.
    """
    raise NotImplementedError


def validate_plan(plan: Plan, scenario: Scenario) -> ValidationReport:
    """Check H0 identity, selection, deadlines, resources and exclusions.

    Return static feasibility with explanations and normalized dimensionless score.
    Reject malformed or stale input with ValueError; report events as unsupported.
    Unknown selected metrics/capacities are incomplete, never assumed feasible.
    """
    raise NotImplementedError


def solve_exact(scenario: Scenario, *, max_evaluations: int = 10_000) -> SolverResult:
    """Enumerate bounded assignments; maximize H0 weighted normalized objectives.

    Return an incumbent and counts, with optimality only after full enumeration.
    Missing candidate metrics/used capacities stop search conservatively. Events are
    unsupported. Positive integer evaluation limit required; invalid input raises
    ValueError. Exact decimal-rational comparisons; ID order resolves equal scores.
    """
    raise NotImplementedError


def select_independent(scenario: Scenario) -> SolverResult:
    """Pick each mission's highest score without joint constraints, then validate.

    Return a proposal, including conflicts; never claim global optimality.
    Missing inputs/events block selection. Invalid input raises ValueError.
    """
    raise NotImplementedError
