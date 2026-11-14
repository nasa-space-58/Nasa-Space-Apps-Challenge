"""R13 shared engine for additional quantum application benchmarks.

Every application supplies labelled binary decision variables, an independent
validator built from its original input, a HiGHS MILP of the original rules
and a QUBO. This module provides the exact and MILP references, the QUBO
audit, the shared repair, the large-neighbourhood search whose subproblems are
sampled by local QAOA (noiseless or the R10 illustrative noise model), uniform
random or exact sub-enumeration, the pre-registered go/no-go rule and the
quantum execution gate. No hardware or provider job is submitted and no quantum
advantage is claimed; see docs/quantum-applications.md.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Annotated, Literal

import numpy as np
from pydantic import Field

from shared.hybrid import Contract, NonNegative, Positive

from .hybrid_benchmark import Decision, NoiseModel, Timing

SCHEMA = "r13-application-benchmark-v1"
MILP_SOLVER = "r13-highs-milp-v1"
HYBRID_SOLVER = "r13-lns-qaoa-v1"
APPLICATIONS = (
    "comm_slots",
    "science_activities",
    "relay_selection",
    "rover_routes",
    "robust_plans",
)
Application = Literal[
    "comm_slots",
    "science_activities",
    "relay_selection",
    "rover_routes",
    "robust_plans",
]
Sampler = Literal["qaoa", "qaoa_noise", "random", "exact"]
Method = Literal["milp", "exact", "lns_qaoa", "lns_random", "lns_exact"]
ExecutionMode = Literal["classical", "noiseless_simulator", "noise_model"]
Probability = Annotated[float, Field(strict=True, ge=0, le=1)]
Seed = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
SUBPROBLEM_MAX_QUBITS = 8  # Statevector cap shared with H2 qaoa_statevector.
EXACT_MAX_VARIABLES = 16
UNBALANCED_LAMBDA_1 = 0.96  # Montanez-Barrera et al. 2023, unbalanced penalization.
UNBALANCED_LAMBDA_2 = 0.0371
NO_ADVANTAGE_STATEMENT = (
    "Local statevector simulation only (some runs through an illustrative noise "
    "model); no hardware or provider job was submitted and no quantum advantage "
    "is claimed."
)


# ------------------------------------------------------------ instance model


@dataclass(frozen=True)
class Evaluation:
    """Independent validator verdict for one full assignment.

    `violation` is an exact nonnegative measure that is zero exactly when the
    assignment is feasible; `objective` is maximized and exact.
    """

    feasible: bool
    violation: Fraction
    objective: Fraction
    metrics: Mapping[str, float | int | None]
    issues: tuple[str, ...] = ()
    violating_variables: tuple[int, ...] = ()


@dataclass(frozen=True)
class Qubo:
    """Minimized energy c + sum l_i x_i + sum_{i<j} q_ij x_i x_j.

    `exact` declares that every feasible assignment has energy -objective and
    every infeasible assignment lies strictly above the optimum energy.
    """

    constant: float
    linear: tuple[float, ...]
    quadratic: Mapping[tuple[int, int], float]
    exact: bool
    encoding: str


@dataclass(frozen=True)
class MilpModel:
    """Maximize objective . z; the first `decision_count` columns are the bits.

    Each row is (sparse coefficients by column, lower bound, upper bound).
    """

    objective: tuple[float, ...]
    integrality: tuple[int, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    rows: tuple[tuple[Mapping[int, float], float, float], ...]
    decision_count: int


@dataclass(frozen=True)
class ApplicationInstance:
    """One benchmark instance of one application."""

    application: str
    instance_id: str
    variable_labels: tuple[str, ...]
    evaluate: Callable[[tuple[int, ...]], Evaluation]
    qubo: Qubo
    milp: MilpModel
    input_fingerprint: str
    source_note: str
    rejections: tuple[str, ...] = field(default=())


# ------------------------------------------------------------ records


class Reference(Contract):
    """Exact or MILP reference solution, always revalidated."""

    method: Literal["exact", "milp"]
    status: Literal["optimal", "infeasible", "time_limit"]
    bits: tuple[int, ...] | None
    objective: str | None
    objective_value: float | None
    assignments_checked: int | None = None
    validator_calls: int
    timing: Timing


class QuboAudit(Contract):
    """Full-enumeration comparison of QUBO energies with the validator."""

    variables: int
    assignments: int
    feasible_assignments: int
    feasible_energy_matches_objective: bool
    max_feasible_energy_error: NonNegative | None
    ground_state_feasible: bool
    ground_state_optimal: bool
    infeasible_below_optimum: int


class RepairResult(Contract):
    """Raw assignment kept separate from the repaired one."""

    raw_bits: tuple[int, ...]
    raw_feasible: bool
    raw_violation: str
    bits: tuple[int, ...]
    feasible: bool
    violation: str
    objective: str | None
    repair_moves: int
    improvement_moves: int
    validator_calls: int
    budget_exhausted: bool


class SearchStep(Contract):
    """One bounded neighbourhood of the classical outer search."""

    iteration: int
    free_variables: tuple[int, ...]
    selection_reason: Literal["violation", "neighbourhood"]
    qubits: int
    shots: int
    raw_feasible_fraction: Probability
    proposal_bits: tuple[int, ...]
    proposal_violation: str
    proposal_objective: str | None
    accepted: bool


class SearchResult(Contract):
    """Outer search whose neighbourhoods are solved by one sampler."""

    sampler: Sampler
    execution_mode: ExecutionMode
    seed: Seed
    status: Literal["feasible", "infeasible"]
    bits: tuple[int, ...]
    objective: str | None
    initial: RepairResult
    final: RepairResult
    steps: tuple[SearchStep, ...]
    samples_drawn: int
    raw_feasible_rate: Probability | None
    max_qubits: int
    cx_gates: int
    validator_calls: int
    hardware_jobs: Literal[0] = 0
    timing: Timing


class SearchConfig(Contract):
    """Pre-registered budgets (R10 values)."""

    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 2
    evaluations: Annotated[int, Field(strict=True, ge=1, le=512)] = 160
    shots: Annotated[int, Field(strict=True, ge=1, le=8192)] = 512
    max_free_variables: Annotated[
        int, Field(strict=True, ge=1, le=SUBPROBLEM_MAX_QUBITS)
    ] = 8
    steps: Annotated[int, Field(strict=True, ge=0, le=64)] = 8
    candidates_per_step: Annotated[int, Field(strict=True, ge=1, le=64)] = 8
    repair_moves: Annotated[int, Field(strict=True, ge=0, le=1024)] = 64
    noise: NoiseModel = NoiseModel()


class MethodRun(Contract):
    """One solver run on one instance, with its application metrics."""

    method: Method
    execution_mode: ExecutionMode
    seed: int | None = None
    status: Literal["feasible", "infeasible"]
    bits: tuple[int, ...] | None
    objective: str | None
    objective_value: float | None
    optimum_gap_value: NonNegative | None
    metrics: dict[str, float | int | None]
    raw_feasible_rate: Probability | None = None
    raw_samples: int | None = None
    steps: int | None = None
    accepted_steps: int | None = None
    max_qubits: int | None = None
    cx_gates: int | None = None
    validator_calls: int
    hardware_jobs: Literal[0] = 0
    timing: Timing


class InstanceRecord(Contract):
    application: Application
    split: Literal["dev", "held_out"]
    seed: int
    instance_id: str
    input_fingerprint: str
    variables: int
    qubo_exact: bool
    qubo_encoding: str
    audit: QuboAudit | None
    optimum_source: Literal["exact", "milp", "none"]
    optimum: str | None
    optimum_value: float | None
    rejections: tuple[str, ...]
    source_note: str
    methods: tuple[MethodRun, ...]


class ApplicationCriteria(Contract):
    """Pre-registered R10 rule applied per application on held-out data."""

    application: Application
    hybrid_method: Literal["lns_qaoa"] = "lns_qaoa"
    hybrid_mode: Literal["noiseless_simulator"] = "noiseless_simulator"
    random_method: Literal["lns_random"] = "lns_random"
    classical_method: Literal["milp"] = "milp"
    min_feasible_run_fraction: Probability = 1.0
    max_mean_gap_excess: NonNegative = 0.0
    max_runtime_ratio: Positive = 1.0


class ExecutionPolicy(Contract):
    """Which applications may execute QAOA; default is the classical MILP."""

    quantum_enabled: dict[str, bool]
    retained_solver: dict[str, str]
    reasons: dict[str, str]


class PolicyResult(Contract):
    application: str
    solver: str
    execution_mode: ExecutionMode
    quantum_executed: bool
    status: Literal["feasible", "infeasible"]
    bits: tuple[int, ...] | None
    objective: str | None
    reason: str


class ApplicationSplit(Contract):
    application: Application
    dev_seeds: tuple[Seed, ...] = (101, 102)
    held_out_seeds: tuple[Seed, ...] = (201, 202, 203, 204)


class BenchmarkConfig(Contract):
    applications: tuple[ApplicationSplit, ...]
    qaoa_seeds: tuple[Seed, ...] = (17, 23, 42)
    search: SearchConfig = SearchConfig()
    exact_max_variables: Annotated[
        int, Field(strict=True, ge=1, le=EXACT_MAX_VARIABLES)
    ] = EXACT_MAX_VARIABLES
    milp_time_limit_s: Positive = 30.0


class BenchmarkReport(Contract):
    schema_version: Literal["r13-application-benchmark-v1"] = SCHEMA
    config: BenchmarkConfig
    config_fingerprint: str
    instances: tuple[InstanceRecord, ...]
    decisions: tuple[Decision, ...]
    policy: ExecutionPolicy
    environment: dict[str, str]
    statement: str


Builder = Callable[[int], ApplicationInstance]


# ------------------------------------------------------------ public functions


def qubo_energy(qubo: Qubo, bits: tuple[int, ...]) -> float:
    """Energy of one full assignment."""
    raise NotImplementedError


def subproblem_energies(
    qubo: Qubo, bits: tuple[int, ...], free: tuple[int, ...]
) -> np.ndarray:
    """Energies over 2^len(free) sub-assignments (little-endian over `free`)
    with every other variable fixed at `bits`."""
    raise NotImplementedError


def unbalanced_penalty(
    weights: Mapping[int, float], capacity: float, strength: float
) -> tuple[float, dict[int, float], dict[tuple[int, int], float]]:
    """QUBO terms of strength*(-l1*h + l2*h^2), h = 1 - sum(w_i/C)*x_i."""
    raise NotImplementedError


def solve_exact(
    instance: ApplicationInstance, *, max_variables: int = EXACT_MAX_VARIABLES
) -> Reference:
    """Enumerate every assignment through the validator (n <= max_variables)."""
    raise NotImplementedError


def solve_milp(
    instance: ApplicationInstance, *, time_limit_s: float = 30.0
) -> Reference:
    """HiGHS optimum of the original rules, revalidated by the validator."""
    raise NotImplementedError


def audit_qubo(
    instance: ApplicationInstance, *, max_variables: int = EXACT_MAX_VARIABLES
) -> QuboAudit:
    """Compare QUBO energies with the validator on every assignment."""
    raise NotImplementedError


def repair(
    instance: ApplicationInstance, bits: tuple[int, ...], *, max_moves: int = 64
) -> RepairResult:
    """Steepest descent on (violation, -objective): flips, then swaps."""
    raise NotImplementedError


def subproblem_search(
    instance: ApplicationInstance,
    *,
    sampler: Sampler,
    seed: int,
    config: SearchConfig = SearchConfig(),
) -> SearchResult:
    """Classical outer search with bounded sampled neighbourhoods."""
    raise NotImplementedError


def run_instance(
    instance: ApplicationInstance,
    *,
    split: Literal["dev", "held_out"],
    seed: int,
    config: BenchmarkConfig,
) -> InstanceRecord:
    """All methods on one instance with metrics and optimum gaps."""
    raise NotImplementedError


def decide_application(
    instances: tuple[InstanceRecord, ...], criteria: ApplicationCriteria
) -> Decision:
    """Pre-registered go/no-go on held-out records of one application."""
    raise NotImplementedError


def execution_policy(decisions: tuple[Decision, ...]) -> ExecutionPolicy:
    """Enable QAOA only for applications with a held-out GO decision."""
    raise NotImplementedError


def solve_application(
    instance: ApplicationInstance,
    policy: ExecutionPolicy,
    *,
    config: SearchConfig = SearchConfig(),
    seed: int = 17,
    time_limit_s: float = 30.0,
) -> PolicyResult:
    """Run the solver the policy allows; MILP unless the application is GO."""
    raise NotImplementedError


def run_benchmark(
    config: BenchmarkConfig, builders: Mapping[str, Builder]
) -> BenchmarkReport:
    """Dev and held-out instances, decisions and the resulting policy."""
    raise NotImplementedError


def config_fingerprint(config: BenchmarkConfig) -> str:
    """SHA-256 of the canonical configuration JSON."""
    raise NotImplementedError
