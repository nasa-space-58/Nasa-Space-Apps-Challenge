"""R10 classical-versus-hybrid benchmark: repair, subproblem search and go/no-go.

All quantum results are local statevector simulations, optionally passed through
an explicitly illustrative noise model. No provider or hardware job is submitted.
Physical modelling, candidate generation and final validation remain classical.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from shared.hybrid import Contract, NonNegative, Plan, Positive, Scenario

from .hybrid_planning import ValidationReport
from .hybrid_scaling import InstanceSpec, MilpResult, ResourceProfile, ScalingPoint

Probability = Annotated[float, Field(strict=True, ge=0, le=1)]
Seed = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
ExecutionMode = Literal["classical", "noiseless_simulator", "noise_model"]
Method = Literal[
    "exact",
    "milp",
    "qaoa",
    "random",
    "annealing",
    "lns_qaoa",
    "lns_exact",
    "lns_random",
]

REPORT_SCHEMA = "r10-benchmark-v1"
NO_ADVANTAGE_STATEMENT = (
    "Local simulator benchmark on synthetic instances. Quantum advantage is "
    "neither assumed nor claimed."
)


class NoiseModel(Contract):
    """Illustrative assumption (H7 values), not a calibrated device model.

    The ideal distribution is mixed with uniform by the circuit's global
    depolarizing survival probability, then independent readout flips apply.
    """

    label: Literal["illustrative-global-depolarizing-v1"] = (
        "illustrative-global-depolarizing-v1"
    )
    single_qubit_depolarizing: Probability = 0.001
    two_qubit_depolarizing: Probability = 0.01
    readout_flip: Probability = 0.02


class PauliResources(Contract):
    """Exact Pauli-Z expansion counts of a diagonal cost Hamiltonian."""

    qubits: int
    z_terms: int
    max_z_locality: int
    cx_per_layer: int
    rz_per_layer: int
    rx_per_layer: int


class QaoaTuning(Contract):
    """Seeded H2-style classical outer loop over an exact noiseless statevector."""

    depth: int
    evaluations: int
    seed: int
    gammas: tuple[float, ...]
    betas: tuple[float, ...]
    expected_normalized_energy: float
    probabilities: tuple[float, ...]


class Move(Contract):
    """One single-mission option change made by classical repair/improvement."""

    phase: Literal["repair", "improve"]
    mission_id: str
    from_option_id: str
    to_option_id: str
    violation_before: str
    violation_after: str
    exact_score_before: str | None
    exact_score_after: str | None


class RepairOutcome(Contract):
    """Raw candidate evidence kept separate from the classically repaired plan."""

    raw_option_ids: tuple[str, ...]
    raw_status: Literal["feasible", "infeasible", "incomplete", "unsupported"]
    raw_violation_count: int
    raw_violation_measure: str
    raw_exact_score: str | None
    final_plan: Plan
    final_validation: ValidationReport
    moves: tuple[Move, ...] = ()
    repair_moves: int
    improvement_moves: int
    changed_mission_ids: tuple[str, ...] = ()
    exact_score_change: str | None
    validator_calls: int
    budget_exhausted: bool
    repair_failed: bool
    elapsed_s: NonNegative


class Timing(Contract):
    """Measured monotonic durations; queue_s is None for local execution."""

    preparation_s: NonNegative = 0
    tuning_s: NonNegative = 0
    sampling_s: NonNegative = 0
    validation_s: NonNegative = 0
    repair_s: NonNegative = 0
    queue_s: NonNegative | None = None
    total_s: NonNegative = 0


class CandidateSetSummary(Contract):
    """Distinct raw candidates passed through the same repair/improvement."""

    candidates: int
    raw_feasible: int
    repaired_feasible: int
    repair_failures: int
    mean_repair_moves: NonNegative
    mean_improvement_moves: NonNegative
    validator_calls: int
    best_raw_exact_score: str | None
    best_final_exact_score: str | None


class MethodRecord(Contract):
    """One solver run on one instance; gaps are exact rationals versus optimum."""

    method: Method
    execution_mode: ExecutionMode
    seed: int | None = None
    samples: int | None = None
    raw_sample_feasible_rate: Probability | None = None
    raw_sample_optimal_rate: Probability | None = None
    candidates: CandidateSetSummary | None = None
    outcomes: tuple[RepairOutcome, ...] = ()
    subproblem_steps: int | None = None
    accepted_steps: int | None = None
    logical_qubits: int | None = None
    depth: int | None = None
    evaluations: int | None = None
    cx_gates: int | None = None
    best_plan: Plan | None = None
    best_validation: ValidationReport | None = None
    best_exact_score: str | None = None
    optimum_gap: str | None = None
    optimum_gap_value: NonNegative | None = None
    hardware_jobs: Literal[0] = 0
    timing: Timing


class SubproblemStep(Contract):
    """One bounded neighbourhood: chosen missions, raw proposals and acceptance."""

    iteration: int
    mission_ids: tuple[str, ...]
    selection_reason: Literal["violation", "conflict_neighbourhood"]
    qubits: int
    raw_feasible_fraction: Probability
    proposal_option_ids: tuple[str, ...]
    proposal_violation: str
    proposal_exact_score: str | None
    accepted: bool
    incumbent_violation: str
    incumbent_exact_score: str | None


class SearchResult(Contract):
    """Classical outer search whose neighbourhoods are solved by one method."""

    method: Literal["qaoa", "exact", "random"]
    execution_mode: ExecutionMode
    status: Literal["feasible", "infeasible"]
    seed: int
    iterations: int
    subproblem_missions: int
    initial: RepairOutcome
    steps: tuple[SubproblemStep, ...]
    plan: Plan
    validation: ValidationReport
    samples_drawn: int
    validator_calls: int
    hardware_jobs: Literal[0] = 0
    timing: Timing


class GoNoGoCriteria(Contract):
    """Pre-registered decision rule evaluated on held-out instances only."""

    application: Literal["direct_qaoa_selection", "lns_qaoa_subproblem"]
    hybrid_method: Literal["qaoa", "lns_qaoa"]
    hybrid_mode: Literal["noiseless_simulator", "noise_model"] = "noiseless_simulator"
    random_method: Literal["random", "lns_random"]
    classical_method: Literal["milp"] = "milp"
    min_feasible_run_fraction: Probability = 1.0
    max_mean_gap_excess: NonNegative = 0.0
    max_runtime_ratio: Positive = 1.0
    require_raw_rate_above_random: bool = True


class CriterionResult(Contract):
    name: str
    passed: bool
    measured: float | None
    threshold: float | None
    explanation: str


class Decision(Contract):
    application: str
    verdict: Literal["go", "no_go"]
    evaluated_split: Literal["held_out"] = "held_out"
    criteria: tuple[CriterionResult, ...]
    retained_solver: str
    statement: str


class MethodAggregate(Contract):
    """Mean/standard deviation over runs of one method within one split."""

    split: Literal["dev", "held_out"]
    method: Method
    execution_mode: ExecutionMode
    runs: int
    instances: int
    feasible_runs: int
    optimum_hits: int
    mean_gap: float | None
    std_gap: float | None
    max_gap: float | None
    mean_raw_feasible_rate: float | None
    std_raw_feasible_rate: float | None
    mean_total_s: float
    std_total_s: float


class InstanceRecord(Contract):
    split: Literal["dev", "held_out"]
    spec: InstanceSpec
    scenario_id: str
    input_fingerprint: str
    profile: ResourceProfile
    exact_status: str | None
    exact_elapsed_s: NonNegative | None
    milp: MilpResult
    optimum_exact_score: str | None
    optimum_source: Literal["exact_enumeration", "milp", "none"]
    methods: tuple[MethodRecord, ...]


class BenchmarkConfig(Contract):
    """Frozen compute budget; dev and held-out specs must not overlap."""

    dev_specs: tuple[InstanceSpec, ...] = Field(min_length=1)
    held_out_specs: tuple[InstanceSpec, ...] = Field(min_length=1)
    scaling_specs: tuple[InstanceSpec, ...] = ()
    qaoa_seeds: tuple[Seed, ...] = Field(min_length=1, max_length=10)
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    evaluations: Annotated[int, Field(strict=True, ge=1, le=512)] = 160
    shots: Annotated[int, Field(strict=True, ge=1, le=8192)] = 512
    candidate_budget: Annotated[int, Field(strict=True, ge=1, le=64)] = 8
    max_moves: Annotated[int, Field(strict=True, ge=1, le=256)] = 64
    annealing_steps: Annotated[int, Field(strict=True, ge=1, le=4096)] = 64
    lns_iterations: Annotated[int, Field(strict=True, ge=1, le=64)] = 8
    subproblem_missions: Annotated[int, Field(strict=True, ge=1, le=4)] = 3
    direct_max_qubits: Annotated[int, Field(strict=True, ge=1, le=8)] = 8
    exact_max_assignments: Annotated[int, Field(strict=True, ge=1, le=65536)] = 4096
    milp_time_limit_s: Annotated[float, Field(strict=True, gt=0, le=300)] = 10.0
    noise: NoiseModel = NoiseModel()
    criteria: tuple[GoNoGoCriteria, ...] = Field(min_length=1)


class BenchmarkReport(Contract):
    schema_id: Literal["r10-benchmark-v1"] = REPORT_SCHEMA
    generated_utc: datetime
    config: BenchmarkConfig
    config_fingerprint: str
    environment: dict[str, str]
    instances: tuple[InstanceRecord, ...]
    aggregates: tuple[MethodAggregate, ...]
    scaling: tuple[ScalingPoint, ...]
    decisions: tuple[Decision, ...]
    hardware_jobs: Literal[0] = 0
    statement: str = NO_ADVANTAGE_STATEMENT


def pauli_z_resources(energies: tuple) -> PauliResources:
    """Exact rational Walsh-Hadamard expansion of a 2^q diagonal (1..8 qubits).

    Parity-chain logical estimate: one RZ and 2(k-1) CX per k-body term, one RX
    per qubit. Raise ValueError for non-power-of-two or non-rational input.
    """
    raise NotImplementedError


def tune_qaoa(
    energies: tuple, *, depth: int = 1, evaluations: int = 160, seed: int = 17
) -> QaoaTuning:
    """Normalize energies (mean shift, max-abs scale) and run H2's seeded loop.

    Uses SeedSequence(seed).spawn(2)[0] for tuning so that, on the same diagonal,
    probabilities reproduce app.hybrid_quantum.run_qaoa exactly.
    """
    raise NotImplementedError


def apply_noise(
    probabilities, *, noise: NoiseModel, cx_gates: int, single_qubit_gates: int
):
    """Return the noisy outcome distribution as a NumPy array summing to one.

    survival = (1-p1)^single * (1-p2)^cx; p = survival*ideal + (1-survival)*uniform,
    followed by an exact independent bit-flip channel on every qubit.
    """
    raise NotImplementedError


def repair_and_improve(
    option_ids: tuple[str, ...], scenario: Scenario, *, max_moves: int = 64
) -> RepairOutcome:
    """Repair by steepest violation descent, then improve by steepest ascent.

    Violation measure (exact rational): capacity/budget excess required-capacity,
    1 per deadline/availability issue and (selected - 1) per at-most-one issue.
    Each step evaluates every single-mission option change and picks the minimum
    key (measure, -exact score, sorted selected IDs). Repair stops when the best
    neighbour does not lower the measure (repair_failed); improvement accepts only
    feasible neighbours with a strictly higher exact score. validator_calls counts
    evaluated assignments, including the raw one. Stopping at max_moves sets
    budget_exhausted. The raw candidate is recorded before any change. Exactly
    one known option per mission and a static complete scenario are required;
    otherwise ValueError.
    """
    raise NotImplementedError


def compare_direct(
    scenario: Scenario,
    *,
    seed: int,
    depth: int = 1,
    evaluations: int = 160,
    shots: int = 512,
    candidate_budget: int = 8,
    max_moves: int = 64,
    annealing_steps: int = 64,
    noise: NoiseModel | None = None,
    optimum_exact_score: str | None = None,
    max_qubits: int = 8,
) -> tuple[MethodRecord, ...]:
    """Run QAOA (noiseless and, if given, noise-model), random and annealing.

    Records are returned in that order. QAOA tunes noiselessly with tune_qaoa and
    samples with SeedSequence(seed).spawn(2)[1], reproducing run_qaoa counts; the
    noise-model record samples the same tuned state through apply_noise. Random
    draws `shots` uniform assignments; annealing runs candidate_budget seeded
    Metropolis chains of annealing_steps single-mission proposals on the compact
    energy. Each method passes up to candidate_budget distinct raw assignments
    (most frequent first, ties by basis index; random in draw order) through
    repair_and_improve. Raise ValueError for infeasible, unsupported or
    over-budget (qubits > max_qubits) problems.
    """
    raise NotImplementedError


def solve_subproblem_search(
    scenario: Scenario,
    *,
    method: Literal["qaoa", "exact", "random"],
    seed: int,
    iterations: int = 8,
    subproblem_missions: int = 3,
    depth: int = 1,
    evaluations: int = 160,
    shots: int = 512,
    noise: NoiseModel | None = None,
    max_moves: int = 64,
) -> SearchResult:
    """Classical large-neighbourhood search with bounded (<= 8 qubit) subproblems.

    The initial plan is a greedy construction passed through repair_and_improve.
    Each step fixes all other missions; only validated improvements are accepted.
    """
    raise NotImplementedError


def decide_go_no_go(
    instances: tuple[InstanceRecord, ...], criteria: GoNoGoCriteria
) -> Decision:
    """Apply a pre-registered rule to held-out records; retain MILP on no-go."""
    raise NotImplementedError


def run_benchmark(
    config: BenchmarkConfig, *, generated_utc: datetime
) -> BenchmarkReport:
    """Run every configured method on dev and held-out instances; decide go/no-go.

    Non-timing content is deterministic for a fixed config. Raise ValueError if
    dev and held-out specs overlap or generated_utc is not explicit UTC.
    """
    raise NotImplementedError
