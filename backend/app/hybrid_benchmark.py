"""R10 classical-versus-hybrid benchmark: repair, subproblem search and go/no-go.

All quantum results are local statevector simulations, optionally passed through
an explicitly illustrative noise model. No provider or hardware job is submitted.
Physical modelling, candidate generation and final validation remain classical.
"""

import hashlib
import json
import math
import platform
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from time import perf_counter
from typing import Annotated, Literal, get_args

import numpy as np
import pydantic
import scipy
from pydantic import Field

from shared.hybrid import Contract, NonNegative, Plan, Positive, Scenario, fingerprint

from .hybrid_planning import (
    ValidationReport,
    _blocked_status,
    _blockers,
    _evaluate,
    solve_exact,
    validate_plan,
)
from .hybrid_quantum import qaoa_statevector
from .hybrid_scaling import (
    MILP_SOLVER_NAME,
    InstanceSpec,
    MilpResult,
    ResourceProfile,
    ScalingPoint,
    _indices_energy,
    _integer_range,
    _tables,
    _walsh_hadamard,
    _widths,
    build_pairwise_model,
    compact_energies,
    generate_instance,
    resource_profile,
    scaling_sweep,
    solve_milp,
)

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
REPAIR_SOLVER = "r10-repair-v1"
SOLVER_NAMES = {
    "qaoa": "r10-qaoa-direct-v1",
    "random": "r10-random-v1",
    "annealing": "r10-annealing-v1",
}
PROBABILITY_SUM_TOLERANCE = 1e-9
SUBPROBLEM_MAX_QUBITS = 8  # Statevector cap shared with H2 qaoa_statevector.
# Geometric annealing schedule in normalized (dimensionless) energy units.
ANNEALING_START_TEMPERATURE = 1.0
ANNEALING_END_TEMPERATURE = 0.01
UNKNOWN_CODES = frozenset({"unknown_metric", "unknown_capacity", "unapplied_events"})
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
    values = _rational_diagonal(energies)
    qubits = len(values).bit_length() - 1
    terms = [
        mask for mask, value in enumerate(_walsh_hadamard(values)) if mask and value
    ]
    return PauliResources(
        qubits=qubits,
        z_terms=len(terms),
        max_z_locality=max((mask.bit_count() for mask in terms), default=0),
        cx_per_layer=sum(2 * (mask.bit_count() - 1) for mask in terms),
        rz_per_layer=len(terms),
        rx_per_layer=qubits,
    )


def tune_qaoa(
    energies: tuple, *, depth: int = 1, evaluations: int = 160, seed: int = 17
) -> QaoaTuning:
    """Normalize energies (mean shift, max-abs scale) and run H2's seeded loop.

    Uses SeedSequence(seed).spawn(2)[0] for tuning so that, on the same diagonal,
    probabilities reproduce app.hybrid_quantum.run_qaoa exactly.
    """
    _integer_range(depth, "depth", 1, 3)
    _integer_range(evaluations, "evaluations", 1, 512)
    _integer_range(seed, "seed", 0, 2**32 - 1)
    floats = np.array([float(v) for v in _rational_diagonal(energies)])
    shift = float(np.mean(floats))
    scale = float(np.max(np.abs(floats - shift))) or 1.0
    normalized = (floats - shift) / scale
    # Same seeded loop as app.hybrid_quantum.run_qaoa (uniform first, about half
    # random exploration, then single-coordinate incumbent perturbations).
    tuning_seed = np.random.SeedSequence(seed).spawn(2)[0]
    rng = np.random.default_rng(tuning_seed)
    best_angles = np.zeros(2 * depth)
    best_energy = math.inf
    best_probabilities = None
    for iteration in range(evaluations):
        if iteration == 0:
            angles = np.zeros(2 * depth)
        elif iteration < max(2, evaluations // 2):
            angles = rng.uniform(-np.pi, np.pi, 2 * depth)
        else:
            angles = best_angles.copy()
            remaining = (evaluations - iteration) / max(1, evaluations // 2)
            angles[rng.integers(2 * depth)] += rng.normal(0, 1.5 * remaining + 0.05)
        state = qaoa_statevector(normalized, angles[:depth], angles[depth:])
        probabilities = np.abs(state) ** 2
        probabilities /= probabilities.sum()
        energy = float(probabilities @ normalized)
        if energy < best_energy:
            best_energy, best_angles, best_probabilities = (
                energy,
                angles.copy(),
                probabilities.copy(),
            )
    return QaoaTuning(
        depth=depth,
        evaluations=evaluations,
        seed=seed,
        gammas=tuple(float(a) for a in best_angles[:depth]),
        betas=tuple(float(a) for a in best_angles[depth:]),
        expected_normalized_energy=best_energy,
        probabilities=tuple(float(p) for p in best_probabilities),
    )


def apply_noise(
    probabilities, *, noise: NoiseModel, cx_gates: int, single_qubit_gates: int
):
    """Return the noisy outcome distribution as a NumPy array summing to one.

    survival = (1-p1)^single * (1-p2)^cx; p = survival*ideal + (1-survival)*uniform,
    followed by an exact independent bit-flip channel on every qubit.
    """
    noise = NoiseModel.model_validate(noise.model_dump())
    for value, name in (
        (cx_gates, "cx_gates"),
        (single_qubit_gates, "single_qubit_gates"),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    p = np.array(probabilities, dtype=float)
    size = len(p) if p.ndim == 1 else 0
    if size < 2 or size & (size - 1):
        raise ValueError("Probabilities must describe at least one qubit")
    if not np.isfinite(p).all() or (p < 0).any():
        raise ValueError("Probabilities must be finite and nonnegative")
    if abs(math.fsum(p) - 1) > PROBABILITY_SUM_TOLERANCE:
        raise ValueError("Probabilities must sum to one")
    survival = (1 - noise.single_qubit_depolarizing) ** single_qubit_gates * (
        1 - noise.two_qubit_depolarizing
    ) ** cx_gates
    p = survival * p + (1 - survival) / size
    indices = np.arange(size)
    flip = noise.readout_flip
    for qubit in range(size.bit_length() - 1):
        p = (1 - flip) * p + flip * p[indices ^ (1 << qubit)]
    return p


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
    started = perf_counter()
    _integer_range(max_moves, "max_moves", 1, 256)
    return _repair(option_ids, _Context(scenario), max_moves, started)


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
    started = perf_counter()
    for value, name, upper in (
        (seed, "seed", 2**32 - 1),
        (depth, "depth", 3),
        (evaluations, "evaluations", 512),
        (shots, "shots", 8192),
        (candidate_budget, "candidate_budget", 64),
        (max_moves, "max_moves", 256),
        (annealing_steps, "annealing_steps", 4096),
        (max_qubits, "max_qubits", 8),
    ):
        _integer_range(value, name, 0 if name == "seed" else 1, upper)
    optimum = None if optimum_exact_score is None else Fraction(optimum_exact_score)
    context = _Context(scenario)
    model = build_pairwise_model(context.scenario)
    energies = compact_energies(model, max_qubits=max_qubits)
    decoder = _Decoder(model)
    if not any(decoder.pairwise_feasible(b) for b in range(len(energies))):
        raise ValueError("Original problem is infeasible; no quantum optimization run")
    resources = pauli_z_resources(energies)
    preparation_s = perf_counter() - started
    qubits = resources.qubits
    single_gates = qubits + depth * (resources.rz_per_layer + resources.rx_per_layer)
    seeds = np.random.SeedSequence(seed).spawn(5)
    records = []

    mark = perf_counter()
    tuning = tune_qaoa(energies, depth=depth, evaluations=evaluations, seed=seed)
    tuning_s = perf_counter() - mark
    ideal = np.array(tuning.probabilities)
    quantum = [("noiseless_simulator", ideal, seeds[1])]
    if noise is not None:
        noisy = apply_noise(
            ideal,
            noise=noise,
            cx_gates=depth * resources.cx_per_layer,
            single_qubit_gates=single_gates,
        )
        quantum.append(("noise_model", noisy, seeds[2]))
    for mode, probabilities, stream in quantum:
        mark = perf_counter()
        counts = np.random.default_rng(stream).multinomial(shots, probabilities)
        sampling_s = perf_counter() - mark
        order = sorted(
            (b for b in range(len(counts)) if counts[b]),
            key=lambda b: (-int(counts[b]), b),
        )
        records.append(
            _direct_record(
                "qaoa",
                mode,
                {b: int(counts[b]) for b in order},
                order[:candidate_budget],
                context,
                decoder,
                optimum,
                max_moves,
                seed=seed,
                samples=shots,
                timing=dict(
                    preparation_s=preparation_s,
                    tuning_s=tuning_s,
                    sampling_s=sampling_s,
                ),
                quantum=dict(
                    logical_qubits=qubits,
                    depth=depth,
                    evaluations=evaluations,
                    cx_gates=depth * resources.cx_per_layer,
                ),
            )
        )

    mark = perf_counter()
    draws = np.random.default_rng(seeds[3]).integers(0, len(energies), size=shots)
    sampling_s = perf_counter() - mark
    counts, order = {}, []
    for basis in (int(b) for b in draws):
        if basis not in counts:
            order.append(basis)
        counts[basis] = counts.get(basis, 0) + 1
    records.append(
        _direct_record(
            "random",
            "classical",
            counts,
            order[:candidate_budget],
            context,
            decoder,
            optimum,
            max_moves,
            seed=seed,
            samples=shots,
            timing=dict(preparation_s=preparation_s, sampling_s=sampling_s),
        )
    )

    mark = perf_counter()
    endpoints = _anneal(
        energies,
        decoder,
        candidate_budget,
        annealing_steps,
        np.random.default_rng(seeds[4]),
    )
    sampling_s = perf_counter() - mark
    counts, order = {}, []
    for basis in endpoints:
        if basis not in counts:
            order.append(basis)
        counts[basis] = counts.get(basis, 0) + 1
    records.append(
        _direct_record(
            "annealing",
            "classical",
            counts,
            order[:candidate_budget],
            context,
            decoder,
            optimum,
            max_moves,
            seed=seed,
            samples=candidate_budget,
            timing=dict(preparation_s=preparation_s, sampling_s=sampling_s),
        )
    )
    return tuple(records)


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
    started = perf_counter()
    if method not in ("qaoa", "exact", "random"):
        raise ValueError("method must be 'qaoa', 'exact' or 'random'")
    for value, name, lower, upper in (
        (seed, "seed", 0, 2**32 - 1),
        (iterations, "iterations", 1, 64),
        (subproblem_missions, "subproblem_missions", 1, 4),
        (depth, "depth", 1, 3),
        (evaluations, "evaluations", 1, 512),
        (shots, "shots", 1, 8192),
        (max_moves, "max_moves", 1, 256),
    ):
        _integer_range(value, name, lower, upper)
    context = _Context(scenario)
    model = build_pairwise_model(context.scenario)
    widths = _widths(model)
    if widths is None:
        raise ValueError(
            "Subproblem registers require power-of-two options per mission"
        )
    size = min(subproblem_missions, len(widths))
    if sum(sorted(widths, reverse=True)[:size]) > SUBPROBLEM_MAX_QUBITS:
        raise ValueError(f"Subproblems may exceed {SUBPROBLEM_MAX_QUBITS} qubits")
    mode = (
        "classical"
        if method != "qaoa"
        else ("noise_model" if noise is not None else "noiseless_simulator")
    )
    tables = _tables(model)
    groups = model.mission_groups
    incumbent = _greedy(model, tables)
    preparation_s = perf_counter() - started
    mark = perf_counter()
    initial = _repair(
        tuple(model.option_ids[i] for i in incumbent), context, max_moves, mark
    )
    repair_s = perf_counter() - mark
    chosen = set(initial.final_plan.selected_option_ids)
    incumbent = [next(i for i in g if model.option_ids[i] in chosen) for g in groups]
    current = context.evaluate(model.option_ids[i] for i in incumbent)
    calls = initial.validator_calls
    mission_of = {i: m for m, group in enumerate(groups) for i in group}
    mission_of_id = {model.option_ids[i]: m for i, m in mission_of.items()}
    weights = {}
    for i, j in model.conflict_pairs:
        a, b = mission_of[i], mission_of[j]
        weights[(a, b)] = weights.get((a, b), 0) + 1
        weights[(b, a)] = weights.get((b, a), 0) + 1
    rng = np.random.default_rng(np.random.SeedSequence([seed, 0]))
    steps, samples_drawn = [], 0
    tuning_s = sampling_s = validation_s = 0.0
    for iteration in range(1, iterations + 1):
        reason, missions = _select_missions(
            current, model, mission_of_id, weights, size, rng
        )
        local_widths = [widths[m] for m in missions]
        qubits = sum(local_widths)
        local_states = []
        for basis in range(1 << qubits):
            choice, offset = list(incumbent), 0
            for m, width in zip(missions, local_widths):
                choice[m] = groups[m][(basis >> offset) & ((1 << width) - 1)]
                offset += width
            local_states.append(tuple(choice))
        step_seed = int(np.random.SeedSequence([seed, iteration]).generate_state(1)[0])
        if method == "exact":
            counts = {basis: 1 for basis in range(len(local_states))}
        elif method == "random":
            mark = perf_counter()
            draws = np.random.default_rng(step_seed).integers(
                0, len(local_states), size=shots
            )
            counts = {}
            for basis in (int(b) for b in draws):
                counts[basis] = counts.get(basis, 0) + 1
            sampling_s += perf_counter() - mark
        else:
            local = tuple(_indices_energy(tables, list(s)) for s in local_states)
            mark = perf_counter()
            tuning = tune_qaoa(
                local, depth=depth, evaluations=evaluations, seed=step_seed
            )
            tuning_s += perf_counter() - mark
            mark = perf_counter()
            probabilities = np.array(tuning.probabilities)
            stream = np.random.SeedSequence(step_seed).spawn(3)
            if noise is not None:
                resources = pauli_z_resources(local)
                probabilities = apply_noise(
                    probabilities,
                    noise=noise,
                    cx_gates=depth * resources.cx_per_layer,
                    single_qubit_gates=qubits
                    + depth * (resources.rz_per_layer + resources.rx_per_layer),
                )
            sampled = np.random.default_rng(stream[2 if noise else 1]).multinomial(
                shots, probabilities
            )
            counts = {b: int(c) for b, c in enumerate(sampled) if c}
            sampling_s += perf_counter() - mark
        samples_drawn += sum(counts.values())
        mark = perf_counter()
        evaluated = {
            basis: context.evaluate(model.option_ids[i] for i in local_states[basis])
            for basis in counts
        }
        validation_s += perf_counter() - mark
        calls += len(evaluated)
        feasible_samples = sum(
            count for basis, count in counts.items() if evaluated[basis].feasible
        )
        best = min(counts, key=lambda b: evaluated[b].key)
        proposal = evaluated[best]
        accepted = proposal.key < current.key
        if accepted:
            incumbent, current = list(local_states[best]), proposal
        steps.append(
            SubproblemStep(
                iteration=iteration,
                mission_ids=tuple(model.mission_ids[m] for m in missions),
                selection_reason=reason,
                qubits=qubits,
                raw_feasible_fraction=feasible_samples / sum(counts.values()),
                proposal_option_ids=proposal.ids,
                proposal_violation=str(proposal.measure),
                proposal_exact_score=_text(proposal.score),
                accepted=accepted,
                incumbent_violation=str(current.measure),
                incumbent_exact_score=_text(current.score),
            )
        )
    plan = context.plan(current.ids, mode, f"r10-lns-{method}-v1")
    mark = perf_counter()
    validation = validate_plan(plan, context.scenario)
    validation_s += perf_counter() - mark
    total_s = perf_counter() - started
    return SearchResult(
        method=method,
        execution_mode=mode,
        status="feasible" if validation.status == "feasible" else "infeasible",
        seed=seed,
        iterations=iterations,
        subproblem_missions=subproblem_missions,
        initial=initial,
        steps=tuple(steps),
        plan=plan,
        validation=validation,
        samples_drawn=samples_drawn,
        validator_calls=calls,
        timing=Timing(
            preparation_s=preparation_s,
            tuning_s=tuning_s,
            sampling_s=sampling_s,
            validation_s=validation_s,
            repair_s=repair_s,
            total_s=max(
                total_s,
                preparation_s + tuning_s + sampling_s + validation_s + repair_s,
            ),
        ),
    )


def decide_go_no_go(
    instances: tuple[InstanceRecord, ...], criteria: GoNoGoCriteria
) -> Decision:
    """Apply a pre-registered rule to held-out records; retain MILP on no-go."""
    criteria = GoNoGoCriteria.model_validate(criteria.model_dump())
    held = [
        i
        for i in instances
        if i.split == "held_out" and i.optimum_exact_score is not None
    ]

    def runs(method, mode):
        return [
            r
            for i in held
            for r in i.methods
            if r.method == method and r.execution_mode == mode
        ]

    hybrid = runs(criteria.hybrid_method, criteria.hybrid_mode)
    classical = runs(criteria.classical_method, "classical")
    baseline = runs(criteria.random_method, "classical")
    hybrid_label = f"{criteria.hybrid_method}:{criteria.hybrid_mode}"
    results = []

    feasible = sum(_feasible(r) for r in hybrid) / len(hybrid) if hybrid else None
    results.append(
        CriterionResult(
            name="validated_feasibility",
            passed=feasible is not None
            and feasible >= criteria.min_feasible_run_fraction,
            measured=feasible,
            threshold=criteria.min_feasible_run_fraction,
            explanation=(
                f"Fraction of held-out {hybrid_label} runs whose returned plan "
                "passes the independent classical validator."
            ),
        )
    )
    excess = _difference(_mean_gap(hybrid), _mean_gap(classical))
    results.append(
        CriterionResult(
            name="objective_quality",
            passed=excess is not None and excess <= criteria.max_mean_gap_excess,
            measured=excess,
            threshold=criteria.max_mean_gap_excess,
            explanation=(
                f"Mean optimum gap of {hybrid_label} minus mean gap of "
                f"{criteria.classical_method}; lower is better."
            ),
        )
    )
    if criteria.require_raw_rate_above_random:
        raw = _difference(_mean_raw(hybrid), _mean_raw(baseline))
        results.append(
            CriterionResult(
                name="raw_feasibility_vs_random",
                passed=raw is not None and raw > 0,
                measured=raw,
                threshold=0.0,
                explanation=(
                    f"Mean raw (pre-repair) feasible sample rate of {hybrid_label} "
                    f"minus {criteria.random_method}; must be positive."
                ),
            )
        )
    else:
        results.append(
            CriterionResult(
                name="raw_feasibility_vs_random",
                passed=True,
                measured=None,
                threshold=None,
                explanation="Not required by this pre-registered rule.",
            )
        )
    versus = _difference(_mean_gap(hybrid), _mean_gap(baseline))
    results.append(
        CriterionResult(
            name="repaired_quality_vs_random",
            passed=versus is not None and versus <= 0,
            measured=versus,
            threshold=0.0,
            explanation=(
                f"Mean repaired optimum gap of {hybrid_label} minus "
                f"{criteria.random_method} under the same repair procedure."
            ),
        )
    )
    hybrid_s = _mean([r.timing.total_s for r in hybrid])
    classical_s = _mean([r.timing.total_s for r in classical])
    ratio = (
        hybrid_s / classical_s
        if hybrid_s is not None and classical_s is not None and classical_s > 0
        else None
    )
    results.append(
        CriterionResult(
            name="end_to_end_runtime",
            passed=ratio is not None and ratio <= criteria.max_runtime_ratio,
            measured=ratio,
            threshold=criteria.max_runtime_ratio,
            explanation=(
                f"Mean end-to-end time of {hybrid_label} (preparation, tuning, "
                "sampling, validation and repair; no provider queue) divided by "
                f"{criteria.classical_method}."
            ),
        )
    )
    go = all(result.passed for result in results)
    return Decision(
        application=criteria.application,
        verdict="go" if go else "no_go",
        criteria=tuple(results),
        retained_solver=hybrid_label if go else MILP_SOLVER_NAME,
        statement=NO_ADVANTAGE_STATEMENT
        + " The decision covers only the held-out synthetic instances and the"
        " recorded compute budget.",
    )


def run_benchmark(
    config: BenchmarkConfig, *, generated_utc: datetime
) -> BenchmarkReport:
    """Run every configured method on dev and held-out instances; decide go/no-go.

    Non-timing content is deterministic for a fixed config. Raise ValueError if
    dev and held-out specs overlap or generated_utc is not explicit UTC.
    """
    config = BenchmarkConfig.model_validate(config.model_dump())
    if not isinstance(
        generated_utc, datetime
    ) or generated_utc.utcoffset() != timedelta(0):
        raise ValueError("generated_utc must be an explicit UTC datetime")
    leaked = {_spec_key(s) for s in config.dev_specs} & {
        _spec_key(s) for s in config.held_out_specs
    }
    if leaked:
        raise ValueError(
            "Dev and held-out specs overlap; held-out cases must be unseen"
        )
    instances = tuple(
        _run_instance(split, spec, config)
        for split, specs in (
            ("dev", config.dev_specs),
            ("held_out", config.held_out_specs),
        )
        for spec in specs
    )
    scaling = (
        scaling_sweep(config.scaling_specs, milp_time_limit_s=config.milp_time_limit_s)
        if config.scaling_specs
        else ()
    )
    return BenchmarkReport(
        generated_utc=generated_utc,
        config=config,
        config_fingerprint=hashlib.sha256(
            json.dumps(config.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest(),
        environment={
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "pydantic": pydantic.__version__,
            "machine": platform.machine(),
        },
        instances=instances,
        aggregates=_aggregates(instances),
        scaling=scaling,
        decisions=tuple(decide_go_no_go(instances, c) for c in config.criteria),
    )


def _rational_diagonal(energies):
    values = []
    for value in energies:
        if isinstance(value, bool) or not isinstance(value, (int, float, Fraction)):
            raise ValueError(f"Energy {value!r} must be an int, float or Fraction")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Energies must be finite")
        values.append(Fraction(value))
    size = len(values)
    if size < 2 or size > 256 or size & (size - 1):
        raise ValueError("Diagonal must describe 1..8 qubits (power-of-two length)")
    return values


def _measure(report):
    """Exact violation severity of a validation report (0 means no hard issue)."""
    total = Fraction(0)
    for issue in report.issues:
        if issue.code in UNKNOWN_CODES:
            continue
        if issue.code in ("capacity", "budget"):
            total += Fraction(issue.required) - Fraction(issue.capacity)
        elif issue.code == "incompatibility":
            total += len(issue.option_ids) - 1
        else:
            total += 1
    return total


def _text(value):
    return None if value is None else str(value)


@dataclass(frozen=True)
class _Evaluation:
    ids: tuple
    measure: Fraction
    score: Fraction | None
    report: ValidationReport

    @property
    def feasible(self):
        return self.report.status == "feasible"

    @property
    def key(self):
        # Lower measure first, then higher exact score, then sorted option IDs.
        return (self.measure, -(self.score or Fraction(0)), self.ids)


class _Context:
    """One validated snapshot and fingerprint shared by many H1 evaluations."""

    def __init__(self, scenario):
        self.scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
        blockers = _blockers(self.scenario)
        if blockers:
            raise ValueError(
                f"Static complete input required ({_blocked_status(blockers)}): "
                + "; ".join(issue.message for issue in blockers)
            )
        self.digest = fingerprint(self.scenario)
        self.mission_ids = tuple(sorted(m.mission_id for m in self.scenario.missions))
        self.mission_of = {c.option_id: c.mission_id for c in self.scenario.candidates}
        self.options = {
            mission: tuple(
                sorted(
                    c.option_id
                    for c in self.scenario.candidates
                    if c.mission_id == mission
                )
            )
            for mission in self.mission_ids
        }

    def plan(self, ids, mode, solver):
        ids = tuple(sorted(ids))
        return Plan(
            plan_id="r10-"
            + hashlib.sha256(
                json.dumps([self.digest, ids, mode, solver]).encode()
            ).hexdigest(),
            scenario_id=self.scenario.scenario_id,
            scenario_revision=self.scenario.revision,
            input_fingerprint=self.digest,
            state_revisions={s.state_id: s.revision for s in self.scenario.states},
            selected_option_ids=ids,
            execution_mode=mode,
            solver_name=solver,
        )

    def evaluate(self, ids):
        # Same H1 constraint evaluator as validate_plan, without re-snapshotting.
        plan = self.plan(ids, "classical", "r10-evaluation")
        report = _evaluate(plan, self.scenario, self.digest)
        score = None if report.exact_score is None else Fraction(report.exact_score)
        return _Evaluation(plan.selected_option_ids, _measure(report), score, report)


def _repair(
    option_ids, context, max_moves, started, source=("classical", REPAIR_SOLVER)
):
    if type(option_ids) is not tuple or not option_ids:
        raise ValueError("A nonempty tuple of option IDs is required")
    current = {}
    for oid in option_ids:
        if oid not in context.mission_of:
            raise ValueError(f"Unknown option ID {oid!r}")
        mission = context.mission_of[oid]
        if mission in current:
            raise ValueError(f"Mission {mission} has more than one selected option")
        current[mission] = oid
    if set(current) != set(context.mission_ids):
        raise ValueError("Exactly one option per mission is required")
    raw = state = context.evaluate(current.values())
    calls, moves, exhausted = 1, [], False

    def neighbours():
        found = []
        for mission in context.mission_ids:
            for alternative in context.options[mission]:
                if alternative != current[mission]:
                    changed = dict(current, **{mission: alternative})
                    found.append(
                        (mission, alternative, context.evaluate(changed.values()))
                    )
        return found

    def move(phase, mission, alternative, evaluation):
        moves.append(
            Move(
                phase=phase,
                mission_id=mission,
                from_option_id=current[mission],
                to_option_id=alternative,
                violation_before=str(state.measure),
                violation_after=str(evaluation.measure),
                exact_score_before=_text(state.score),
                exact_score_after=_text(evaluation.score),
            )
        )
        current[mission] = alternative

    while state.measure > 0:
        if len(moves) >= max_moves:
            exhausted = True
            break
        options = neighbours()
        calls += len(options)
        if not options:
            break
        mission, alternative, evaluation = min(options, key=lambda o: o[2].key)
        if evaluation.measure >= state.measure:
            break
        move("repair", mission, alternative, evaluation)
        state = evaluation
    while state.feasible:
        if len(moves) >= max_moves:
            exhausted = True
            break
        options = neighbours()
        calls += len(options)
        better = [o for o in options if o[2].feasible and o[2].score > state.score]
        if not better:
            break
        mission, alternative, evaluation = min(better, key=lambda o: o[2].key)
        move("improve", mission, alternative, evaluation)
        state = evaluation
    mode, solver = source
    final_plan = (
        context.plan(state.ids, mode, solver)
        if not moves
        else context.plan(state.ids, "classical", f"{solver}+{REPAIR_SOLVER}")
    )
    final_validation = validate_plan(final_plan, context.scenario)
    raw_by_mission = {context.mission_of[oid]: oid for oid in raw.ids}
    return RepairOutcome(
        raw_option_ids=raw.ids,
        raw_status=raw.report.status,
        raw_violation_count=sum(
            issue.code not in UNKNOWN_CODES for issue in raw.report.issues
        ),
        raw_violation_measure=str(raw.measure),
        raw_exact_score=_text(raw.score),
        final_plan=final_plan,
        final_validation=final_validation,
        moves=tuple(moves),
        repair_moves=sum(m.phase == "repair" for m in moves),
        improvement_moves=sum(m.phase == "improve" for m in moves),
        changed_mission_ids=tuple(
            sorted(m for m in context.mission_ids if raw_by_mission[m] != current[m])
        ),
        exact_score_change=None
        if raw.score is None or state.score is None
        else str(state.score - raw.score),
        validator_calls=calls,
        budget_exhausted=exhausted,
        repair_failed=final_validation.status != "feasible",
        elapsed_s=perf_counter() - started,
    )


class _Decoder:
    """Little-endian compact basis index to one option per mission."""

    def __init__(self, model):
        self.model = model
        self.widths = _widths(model)
        self.offsets = [sum(self.widths[:m]) for m in range(len(self.widths))]
        self.tables = _tables(model)

    def indices(self, basis):
        return [
            group[(basis >> offset) & ((1 << width) - 1)]
            for group, offset, width in zip(
                self.model.mission_groups, self.offsets, self.widths
            )
        ]

    def ids(self, basis):
        return tuple(sorted(self.model.option_ids[i] for i in self.indices(basis)))

    def pairwise_feasible(self, basis):
        _, forbidden, conflicts, _ = self.tables
        chosen = self.indices(basis)
        return not (set(chosen) & forbidden) and not any(
            (min(a, b), max(a, b)) in conflicts
            for n, a in enumerate(chosen)
            for b in chosen[n + 1 :]
        )


def _anneal(energies, decoder, chains, steps, rng):
    floats = np.array([float(e) for e in energies])
    shift = float(np.mean(floats))
    scale = float(np.max(np.abs(floats - shift))) or 1.0
    normalized = (floats - shift) / scale
    movable = [m for m, width in enumerate(decoder.widths) if width]
    ratio = ANNEALING_END_TEMPERATURE / ANNEALING_START_TEMPERATURE
    endpoints = []
    for _ in range(chains):
        state = int(rng.integers(len(normalized)))
        for step in range(steps):
            if not movable:
                break
            temperature = ANNEALING_START_TEMPERATURE * ratio ** (
                step / max(1, steps - 1)
            )
            m = movable[int(rng.integers(len(movable)))]
            width, offset = decoder.widths[m], decoder.offsets[m]
            mask = ((1 << width) - 1) << offset
            current = (state & mask) >> offset
            new = int(rng.integers((1 << width) - 1))
            new += new >= current
            proposal = (state & ~mask) | (new << offset)
            delta = normalized[proposal] - normalized[state]
            if delta <= 0 or rng.random() < math.exp(-delta / temperature):
                state = proposal
        endpoints.append(state)
    return endpoints


def _direct_record(
    method,
    mode,
    counts,
    candidate_bases,
    context,
    decoder,
    optimum,
    max_moves,
    *,
    seed,
    samples,
    timing,
    quantum=None,
):
    mark = perf_counter()
    evaluated = {basis: context.evaluate(decoder.ids(basis)) for basis in counts}
    validation_s = perf_counter() - mark
    total = sum(counts.values())
    feasible_count = sum(c for b, c in counts.items() if evaluated[b].feasible)
    optimal_rate = (
        None
        if optimum is None
        else sum(
            c
            for b, c in counts.items()
            if evaluated[b].feasible and evaluated[b].score == optimum
        )
        / total
    )
    mark = perf_counter()
    solver = SOLVER_NAMES[method]
    outcomes = tuple(
        _repair(
            decoder.ids(b), context, max_moves, perf_counter(), source=(mode, solver)
        )
        for b in candidate_bases
    )
    repair_s = perf_counter() - mark
    finals = [o for o in outcomes if o.final_validation.status == "feasible"]
    best = (
        min(
            finals,
            key=lambda o: (
                -Fraction(o.final_validation.exact_score),
                o.final_plan.selected_option_ids,
            ),
        )
        if finals
        else None
    )
    raw_feasible = [o for o in outcomes if o.raw_status == "feasible"]
    timing = dict(timing, validation_s=validation_s, repair_s=repair_s)
    timing["total_s"] = math.fsum(timing.values())
    return MethodRecord(
        method=method,
        execution_mode=mode,
        seed=seed,
        samples=samples,
        raw_sample_feasible_rate=feasible_count / total,
        raw_sample_optimal_rate=optimal_rate,
        candidates=CandidateSetSummary(
            candidates=len(outcomes),
            raw_feasible=len(raw_feasible),
            repaired_feasible=len(finals),
            repair_failures=sum(o.repair_failed for o in outcomes),
            mean_repair_moves=float(np.mean([o.repair_moves for o in outcomes])),
            mean_improvement_moves=float(
                np.mean([o.improvement_moves for o in outcomes])
            ),
            validator_calls=sum(o.validator_calls for o in outcomes),
            best_raw_exact_score=_text(
                max((Fraction(o.raw_exact_score) for o in raw_feasible), default=None)
            ),
            best_final_exact_score=None
            if best is None
            else best.final_validation.exact_score,
        ),
        outcomes=outcomes,
        best_plan=None if best is None else best.final_plan,
        best_validation=None if best is None else best.final_validation,
        timing=Timing(**timing),
        **_gap_fields(None if best is None else best.final_validation, optimum),
        **(quantum or {}),
    )


def _gap_fields(validation, optimum):
    if validation is None or validation.status != "feasible":
        return dict(best_exact_score=None, optimum_gap=None, optimum_gap_value=None)
    score = Fraction(validation.exact_score)
    if optimum is None:
        return dict(
            best_exact_score=str(score), optimum_gap=None, optimum_gap_value=None
        )
    gap = optimum - score
    if gap < 0:
        raise RuntimeError("Validated plan exceeds the certified optimum")
    return dict(
        best_exact_score=str(score), optimum_gap=str(gap), optimum_gap_value=float(gap)
    )


def _greedy(model, tables):
    scores, forbidden, conflicts, _ = tables
    chosen = []
    for group in model.mission_groups:

        def key(i):
            clashes = sum((min(i, j), max(i, j)) in conflicts for j in chosen)
            return ((i in forbidden) + clashes, -scores[i], model.option_ids[i])

        chosen.append(min(group, key=key))
    return chosen


def _select_missions(current, model, mission_of_id, weights, size, rng):
    count = len(model.mission_groups)
    selected = []
    if not current.feasible:
        reason = "violation"
        index = {mission: m for m, mission in enumerate(model.mission_ids)}
        involved = set()
        for issue in current.report.issues:
            if issue.code in UNKNOWN_CODES:
                continue
            involved.update(mission_of_id[oid] for oid in issue.option_ids)
            if issue.mission_id is not None:
                involved.add(index[issue.mission_id])
        selected = [int(m) for m in rng.permutation(sorted(involved))][:size]
    else:
        reason = "conflict_neighbourhood"
    if not selected:
        selected = [int(rng.integers(count))]
    while len(selected) < size:
        remaining = [int(m) for m in rng.permutation(count) if m not in selected]
        selected.append(
            max(remaining, key=lambda m: sum(weights.get((m, s), 0) for s in selected))
        )
    return reason, sorted(selected)


def _feasible(record):
    return (
        record.best_validation is not None
        and record.best_validation.status == "feasible"
    )


def _mean(values):
    return float(np.mean(values)) if values else None


def _mean_gap(records):
    return _mean(
        [r.optimum_gap_value for r in records if r.optimum_gap_value is not None]
    )


def _mean_raw(records):
    return _mean(
        [
            r.raw_sample_feasible_rate
            for r in records
            if r.raw_sample_feasible_rate is not None
        ]
    )


def _difference(left, right):
    return None if left is None or right is None else left - right


def _spec_key(spec):
    return tuple(sorted((k, v) for k, v in spec.model_dump().items() if k != "family"))


def _reference_record(method, plan, scenario, elapsed_s, optimum):
    validation = validate_plan(plan, scenario)
    return MethodRecord(
        method=method,
        execution_mode="classical",
        best_plan=plan,
        best_validation=validation,
        timing=Timing(total_s=elapsed_s),
        **_gap_fields(validation, optimum),
    )


def _search_record(result, optimum, config):
    feasible = result.status == "feasible"
    steps = result.steps
    quantum = result.method == "qaoa"
    return MethodRecord(
        method=f"lns_{result.method}",
        execution_mode=result.execution_mode,
        seed=result.seed,
        samples=result.samples_drawn,
        raw_sample_feasible_rate=math.fsum(s.raw_feasible_fraction for s in steps)
        / len(steps),
        subproblem_steps=len(steps),
        accepted_steps=sum(s.accepted for s in steps),
        logical_qubits=max(s.qubits for s in steps) if quantum else None,
        depth=config.depth if quantum else None,
        evaluations=config.evaluations if quantum else None,
        best_plan=result.plan if feasible else None,
        best_validation=result.validation if feasible else None,
        timing=result.timing,
        **_gap_fields(result.validation if feasible else None, optimum),
    )


def _run_instance(split, spec, config):
    scenario = generate_instance(spec)
    profile = resource_profile(build_pairwise_model(scenario))
    milp_result = solve_milp(scenario, time_limit_s=config.milp_time_limit_s)
    exact = None
    if profile.assignments <= config.exact_max_assignments:
        exact = solve_exact(scenario, max_evaluations=config.exact_max_assignments)
    optimum, source = None, "none"
    if exact is not None and exact.status in ("optimal", "infeasible"):
        if milp_result.status != exact.status:
            raise RuntimeError("MILP and exact enumeration disagree on feasibility")
        if exact.status == "optimal":
            optimum, source = (
                Fraction(exact.validation.exact_score),
                "exact_enumeration",
            )
            if Fraction(milp_result.validation.exact_score) != optimum:
                raise RuntimeError("MILP optimum differs from exact enumeration")
    elif milp_result.status == "optimal":
        optimum, source = Fraction(milp_result.validation.exact_score), "milp"
    methods = []
    if optimum is not None:
        methods.append(
            _reference_record(
                "milp", milp_result.plan, scenario, milp_result.elapsed_s, optimum
            )
        )
        if source == "exact_enumeration":
            methods.append(
                _reference_record(
                    "exact", exact.plan, scenario, exact.elapsed_s, optimum
                )
            )
        qubits = profile.compact_qubits
        for seed in config.qaoa_seeds:
            if qubits is not None and 1 <= qubits <= config.direct_max_qubits:
                methods.extend(
                    compare_direct(
                        scenario,
                        seed=seed,
                        depth=config.depth,
                        evaluations=config.evaluations,
                        shots=config.shots,
                        candidate_budget=config.candidate_budget,
                        max_moves=config.max_moves,
                        annealing_steps=config.annealing_steps,
                        noise=config.noise,
                        optimum_exact_score=str(optimum),
                        max_qubits=config.direct_max_qubits,
                    )
                )
            for method, noise in (
                ("qaoa", None),
                ("qaoa", config.noise),
                ("exact", None),
                ("random", None),
            ):
                result = solve_subproblem_search(
                    scenario,
                    method=method,
                    seed=seed,
                    iterations=config.lns_iterations,
                    subproblem_missions=config.subproblem_missions,
                    depth=config.depth,
                    evaluations=config.evaluations,
                    shots=config.shots,
                    noise=noise,
                    max_moves=config.max_moves,
                )
                methods.append(_search_record(result, optimum, config))
    return InstanceRecord(
        split=split,
        spec=spec,
        scenario_id=scenario.scenario_id,
        input_fingerprint=fingerprint(scenario),
        profile=profile,
        exact_status=None if exact is None else exact.status,
        exact_elapsed_s=None if exact is None else exact.elapsed_s,
        milp=milp_result,
        optimum_exact_score=_text(optimum),
        optimum_source=source,
        methods=tuple(methods),
    )


def _aggregates(instances):
    methods = list(get_args(Method))
    modes = list(get_args(ExecutionMode))
    splits = ["dev", "held_out"]
    grouped = {}
    for instance in instances:
        if instance.optimum_exact_score is None:
            continue
        for record in instance.methods:
            key = (instance.split, record.method, record.execution_mode)
            grouped.setdefault(key, []).append((instance.input_fingerprint, record))
    result = []
    for key in sorted(
        grouped,
        key=lambda k: (splits.index(k[0]), methods.index(k[1]), modes.index(k[2])),
    ):
        entries = grouped[key]
        records = [r for _, r in entries]
        gaps = [r.optimum_gap_value for r in records if r.optimum_gap_value is not None]
        raws = [
            r.raw_sample_feasible_rate
            for r in records
            if r.raw_sample_feasible_rate is not None
        ]
        totals = [r.timing.total_s for r in records]
        result.append(
            MethodAggregate(
                split=key[0],
                method=key[1],
                execution_mode=key[2],
                runs=len(records),
                instances=len({fp for fp, _ in entries}),
                feasible_runs=sum(_feasible(r) for r in records),
                optimum_hits=sum(g == 0 for g in gaps),
                mean_gap=_mean(gaps),
                std_gap=float(np.std(gaps)) if gaps else None,
                max_gap=max(gaps) if gaps else None,
                mean_raw_feasible_rate=_mean(raws),
                std_raw_feasible_rate=float(np.std(raws)) if raws else None,
                mean_total_s=float(np.mean(totals)),
                std_total_s=float(np.std(totals)),
            )
        )
    return tuple(result)
