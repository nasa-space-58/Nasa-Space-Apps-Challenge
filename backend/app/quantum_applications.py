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

import hashlib
import itertools
import json
import math
import platform
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from fractions import Fraction
from time import perf_counter
from typing import Annotated, Literal

import numpy as np
import pydantic
import scipy
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.optimize import Bounds, LinearConstraint, milp
from shared.hybrid import Contract, NonNegative, Positive

from .hybrid_benchmark import (
    CriterionResult,
    Decision,
    NoiseModel,
    Timing,
    apply_noise,
    tune_qaoa,
)

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


class InputContract(BaseModel):
    """Strict frozen R13 application input that keeps the given element order.

    Unlike shared.hybrid.Contract it never re-sorts collections (H0 sorts
    fields such as `candidates`), because R13 variable order follows input
    order. Timestamps must be explicit UTC.
    """

    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, frozen=True, validate_default=True
    )

    @model_validator(mode="after")
    def explicit_utc(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, datetime) and value.utcoffset() != timedelta(0):
                raise ValueError(f"{name} must use explicit UTC (Z or +00:00)")
        return self


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


# ------------------------------------------------------------ helpers


def _check_bits(bits, n) -> tuple[int, ...]:
    bits = tuple(bits)
    if len(bits) != n:
        raise ValueError(f"Assignment has {len(bits)} bits, expected {n}")
    for value in bits:
        if isinstance(value, bool) or value not in (0, 1):
            raise ValueError("Assignment bits must be 0 or 1")
    return tuple(int(v) for v in bits)


def _key(evaluation: Evaluation) -> tuple[Fraction, Fraction]:
    """Lexicographic search key: lower violation, then higher objective."""
    return (Fraction(evaluation.violation), -Fraction(evaluation.objective))


def _text(value) -> str | None:
    return None if value is None else str(Fraction(value))


def _all_bits(n: int) -> np.ndarray:
    indices = np.arange(2**n, dtype=np.int64)
    return ((indices[:, None] >> np.arange(n)) & 1).astype(float)


def _energies(qubo: Qubo, matrix: np.ndarray) -> np.ndarray:
    """Vectorized energies of rows of a 0/1 matrix."""
    values = np.full(matrix.shape[0], float(qubo.constant))
    values += matrix @ np.asarray(qubo.linear, dtype=float)
    for (i, j), q in qubo.quadratic.items():
        values += q * matrix[:, i] * matrix[:, j]
    return values


def _environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pydantic": pydantic.VERSION,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def _metrics(evaluation: Evaluation | None) -> dict[str, float | int | None]:
    if evaluation is None:
        return {}
    clean = {}
    for name, value in evaluation.metrics.items():
        if value is None or isinstance(value, (bool, int)):
            clean[name] = None if value is None else int(value)
        else:
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"Metric {name} is not finite")
            clean[name] = value
    return clean


# ------------------------------------------------------------ energies


def qubo_energy(qubo: Qubo, bits: tuple[int, ...]) -> float:
    """Energy of one full assignment."""
    bits = _check_bits(bits, len(qubo.linear))
    value = float(qubo.constant)
    value += sum(w for w, b in zip(qubo.linear, bits) if b)
    value += sum(q for (i, j), q in qubo.quadratic.items() if bits[i] and bits[j])
    return value


def subproblem_energies(
    qubo: Qubo, bits: tuple[int, ...], free: tuple[int, ...]
) -> np.ndarray:
    """Energies over 2^len(free) sub-assignments (little-endian over `free`)
    with every other variable fixed at `bits`."""
    n = len(qubo.linear)
    bits = _check_bits(bits, n)
    free = tuple(int(i) for i in free)
    if not free:
        raise ValueError("A neighbourhood needs at least one free variable")
    if len(set(free)) != len(free):
        raise ValueError("Free variables must be distinct")
    if any(not 0 <= i < n for i in free):
        raise ValueError("Free variable index out of range")
    if len(free) > SUBPROBLEM_MAX_QUBITS:
        raise ValueError(
            f"At most {SUBPROBLEM_MAX_QUBITS} free variables are supported"
        )
    position = {v: p for p, v in enumerate(free)}
    constant = float(qubo.constant)
    linear = np.zeros(len(free))
    pairs: dict[tuple[int, int], float] = {}
    for i, w in enumerate(qubo.linear):
        if i in position:
            linear[position[i]] += w
        elif bits[i]:
            constant += w
    for (i, j), q in qubo.quadratic.items():
        fi, fj = i in position, j in position
        if fi and fj:
            key = tuple(sorted((position[i], position[j])))
            pairs[key] = pairs.get(key, 0.0) + q
        elif fi and bits[j]:
            linear[position[i]] += q
        elif fj and bits[i]:
            linear[position[j]] += q
        elif not fi and not fj and bits[i] and bits[j]:
            constant += q
    matrix = _all_bits(len(free))
    values = constant + matrix @ linear
    for (a, b), q in pairs.items():
        values += q * matrix[:, a] * matrix[:, b]
    return values


def _sub_terms(qubo: Qubo, bits, free) -> tuple[int, int]:
    """(nonzero Z terms, ZZ pairs) of the restricted QUBO for gate estimates."""
    position = {v: p for p, v in enumerate(free)}
    z = np.zeros(len(free))
    pairs: dict[tuple[int, int], float] = {}
    effective = np.array([qubo.linear[v] for v in free], dtype=float)
    for (i, j), q in qubo.quadratic.items():
        fi, fj = i in position, j in position
        if fi and fj:
            key = tuple(sorted((position[i], position[j])))
            pairs[key] = pairs.get(key, 0.0) + q
        elif fi and bits[j]:
            effective[position[i]] += q
        elif fj and bits[i]:
            effective[position[j]] += q
    # x = (1 - Z)/2: Z_i gets -l_i/2 - sum_j q_ij/4; ZZ_ij gets q_ij/4.
    z -= effective / 2
    for (a, b), q in pairs.items():
        z[a] -= q / 4
        z[b] -= q / 4
    zz = sum(1 for q in pairs.values() if abs(q) > 1e-12)
    return int(np.sum(np.abs(z) > 1e-12)) + zz, zz


def unbalanced_penalty(
    weights: Mapping[int, float], capacity: float, strength: float
) -> tuple[float, dict[int, float], dict[tuple[int, int], float]]:
    """QUBO terms of strength*(-l1*h + l2*h^2), h = 1 - sum(w_i/C)*x_i."""
    if not math.isfinite(capacity) or capacity <= 0:
        raise ValueError("Capacity must be positive and finite")
    if not math.isfinite(strength) or strength < 0:
        raise ValueError("Penalty strength must be nonnegative and finite")
    l1, l2 = UNBALANCED_LAMBDA_1, UNBALANCED_LAMBDA_2
    scaled = {int(i): float(w) / capacity for i, w in weights.items() if w}
    constant = strength * (-l1 + l2)
    linear = {
        i: strength * (l1 * a - 2 * l2 * a + l2 * a * a) for i, a in scaled.items()
    }
    quadratic = {}
    items = sorted(scaled.items())
    for (i, a), (j, b) in itertools.combinations(items, 2):
        quadratic[(i, j)] = strength * 2 * l2 * a * b
    return constant, linear, quadratic


# ------------------------------------------------------------ references


def solve_exact(
    instance: ApplicationInstance, *, max_variables: int = EXACT_MAX_VARIABLES
) -> Reference:
    """Enumerate every assignment through the validator (n <= max_variables)."""
    started = perf_counter()
    n = len(instance.variable_labels)
    if n > max_variables or n > EXACT_MAX_VARIABLES:
        raise ValueError(f"Exact enumeration is limited to {max_variables} variables")
    best_bits, best = None, None
    calls = 0
    for bits in itertools.product((0, 1), repeat=n):
        evaluation = instance.evaluate(bits)
        calls += 1
        if evaluation.feasible and (best is None or evaluation.objective > best):
            best_bits, best = tuple(bits), Fraction(evaluation.objective)
    elapsed = perf_counter() - started
    return Reference(
        method="exact",
        status="optimal" if best_bits is not None else "infeasible",
        bits=best_bits,
        objective=_text(best),
        objective_value=None if best is None else float(best),
        assignments_checked=calls,
        validator_calls=calls,
        timing=Timing(validation_s=elapsed, total_s=elapsed),
    )


def solve_milp(
    instance: ApplicationInstance, *, time_limit_s: float = 30.0
) -> Reference:
    """HiGHS optimum of the original rules, revalidated by the validator."""
    started = perf_counter()
    model = instance.milp
    n = len(instance.variable_labels)
    if model.decision_count != n:
        raise ValueError("MILP decision columns must match the variables")
    columns = len(model.objective)
    constraints = []
    if model.rows:
        matrix = np.zeros((len(model.rows), columns))
        lower = np.empty(len(model.rows))
        upper = np.empty(len(model.rows))
        for r, (coefficients, lb, ub) in enumerate(model.rows):
            for c, value in coefficients.items():
                matrix[r, c] += value
            lower[r], upper[r] = lb, ub
        constraints.append(LinearConstraint(matrix, lower, upper))
    prepared = perf_counter()
    result = milp(
        c=-np.asarray(model.objective, dtype=float),
        integrality=np.asarray(model.integrality),
        bounds=Bounds(np.asarray(model.lower), np.asarray(model.upper)),
        constraints=constraints,
        options={"time_limit": float(time_limit_s), "mip_rel_gap": 0.0, "disp": False},
    )
    solved = perf_counter()
    calls = 0
    bits = None
    objective = None
    if result.x is not None:
        raw = np.asarray(result.x[:n])
        if np.any(np.abs(raw - np.round(raw)) > 1e-6):
            raise RuntimeError("MILP returned fractional decision values")
        bits = tuple(int(round(v)) for v in raw)
        evaluation = instance.evaluate(bits)
        calls += 1
        if not evaluation.feasible:
            raise RuntimeError(
                "MILP incumbent rejected by the validator: "
                + "; ".join(evaluation.issues)
            )
        objective = Fraction(evaluation.objective)
        if result.status == 0:
            milp_value = float(np.asarray(model.objective) @ result.x)
            if abs(milp_value - float(objective)) > 1e-6 * (1 + abs(float(objective))):
                raise RuntimeError(
                    f"MILP objective {milp_value} disagrees with validator {float(objective)}"
                )
    if result.status == 0:
        status = "optimal"
    elif result.status == 2:
        status = "infeasible"
    elif result.status == 1:
        status = "time_limit"
    else:
        raise RuntimeError(f"HiGHS failed: {result.message}")
    finished = perf_counter()
    return Reference(
        method="milp",
        status=status,
        bits=bits,
        objective=_text(objective),
        objective_value=None if objective is None else float(objective),
        validator_calls=calls,
        timing=Timing(
            preparation_s=prepared - started,
            sampling_s=solved - prepared,
            validation_s=finished - solved,
            total_s=finished - started,
        ),
    )


def audit_qubo(
    instance: ApplicationInstance, *, max_variables: int = EXACT_MAX_VARIABLES
) -> QuboAudit:
    """Compare QUBO energies with the validator on every assignment."""
    n = len(instance.variable_labels)
    if n > max_variables or n > EXACT_MAX_VARIABLES:
        raise ValueError(f"QUBO audit is limited to {max_variables} variables")
    matrix = _all_bits(n)
    energies = _energies(instance.qubo, matrix)
    feasible = np.zeros(len(energies), dtype=bool)
    objectives = [Fraction(0)] * len(energies)
    for index, row in enumerate(matrix.astype(int)):
        evaluation = instance.evaluate(tuple(int(v) for v in row))
        feasible[index] = evaluation.feasible
        objectives[index] = Fraction(evaluation.objective)
    scale = 1.0 + float(np.max(np.abs(energies)))
    tolerance = 1e-9 * scale
    if not feasible.any():
        return QuboAudit(
            variables=n,
            assignments=len(energies),
            feasible_assignments=0,
            feasible_energy_matches_objective=False,
            max_feasible_energy_error=None,
            ground_state_feasible=False,
            ground_state_optimal=False,
            infeasible_below_optimum=0,
        )
    errors = [abs(energies[i] + float(objectives[i])) for i in np.flatnonzero(feasible)]
    error = float(max(errors))
    optimum = max(objectives[i] for i in np.flatnonzero(feasible))
    optimal = [i for i in np.flatnonzero(feasible) if objectives[i] == optimum]
    optimum_energy = min(energies[i] for i in optimal)
    ground = np.flatnonzero(energies <= energies.min() + tolerance)
    ground_feasible = bool(feasible[ground].all())
    return QuboAudit(
        variables=n,
        assignments=len(energies),
        feasible_assignments=int(feasible.sum()),
        feasible_energy_matches_objective=error <= 1e-7 * scale,
        max_feasible_energy_error=error,
        ground_state_feasible=ground_feasible,
        ground_state_optimal=ground_feasible
        and all(objectives[i] == optimum for i in ground),
        infeasible_below_optimum=int(
            np.sum(~feasible & (energies < optimum_energy - tolerance))
        ),
    )


# ------------------------------------------------------------ repair


def _repair(instance, bits, max_moves):
    """Shared repair core returning (RepairResult, final evaluation)."""
    current = tuple(bits)
    evaluation = instance.evaluate(current)
    calls = 1
    raw = evaluation
    n = len(current)
    repair_moves = improvement_moves = 0
    exhausted = False
    while True:
        best_bits, best_eval = None, None

        def consider(candidate):
            nonlocal best_bits, best_eval, calls
            result = instance.evaluate(candidate)
            calls += 1
            if _key(result) < _key(evaluation) and (
                best_eval is None or _key(result) < _key(best_eval)
            ):
                best_bits, best_eval = candidate, result

        for i in range(n):
            flipped = list(current)
            flipped[i] ^= 1
            consider(tuple(flipped))
        if best_bits is None:
            ones = [i for i in range(n) if current[i]]
            zeros = [j for j in range(n) if not current[j]]
            for i in ones:
                for j in zeros:
                    swapped = list(current)
                    swapped[i], swapped[j] = 0, 1
                    consider(tuple(swapped))
        if best_bits is None:
            break
        if repair_moves + improvement_moves >= max_moves:
            exhausted = True
            break
        if evaluation.feasible:
            improvement_moves += 1
        else:
            repair_moves += 1
        current, evaluation = best_bits, best_eval
    result = RepairResult(
        raw_bits=tuple(bits),
        raw_feasible=raw.feasible,
        raw_violation=_text(raw.violation),
        bits=current,
        feasible=evaluation.feasible,
        violation=_text(evaluation.violation),
        objective=_text(evaluation.objective) if evaluation.feasible else None,
        repair_moves=repair_moves,
        improvement_moves=improvement_moves,
        validator_calls=calls,
        budget_exhausted=exhausted,
    )
    return result, evaluation


def repair(
    instance: ApplicationInstance, bits: tuple[int, ...], *, max_moves: int = 64
) -> RepairResult:
    """Steepest descent on (violation, -objective): flips, then swaps."""
    if isinstance(max_moves, bool) or not isinstance(max_moves, int) or max_moves < 0:
        raise ValueError("max_moves must be a nonnegative integer")
    bits = _check_bits(bits, len(instance.variable_labels))
    return _repair(instance, bits, max_moves)[0]


# ------------------------------------------------------------ search


SAMPLER_MODES = {
    "qaoa": "noiseless_simulator",
    "qaoa_noise": "noise_model",
    "random": "classical",
    "exact": "classical",
}


def _adjacency(qubo: Qubo, n: int) -> list[dict[int, float]]:
    adjacency: list[dict[int, float]] = [{} for _ in range(n)]
    for (i, j), q in qubo.quadratic.items():
        weight = abs(q)
        if weight > 0:
            adjacency[i][j] = max(adjacency[i].get(j, 0.0), weight)
            adjacency[j][i] = max(adjacency[j].get(i, 0.0), weight)
    return adjacency


def _neighbourhood(n, evaluation, adjacency, size, rng):
    violating = sorted(set(evaluation.violating_variables))
    reason = "violation" if violating else "neighbourhood"
    if n <= size:
        return tuple(range(n)), reason
    if violating:
        chosen = violating[:size]
    else:
        chosen = [int(rng.integers(n))]
    while len(chosen) < size:
        weights: dict[int, float] = {}
        for i in chosen:
            for j, w in adjacency[i].items():
                if j not in chosen:
                    weights[j] = max(weights.get(j, 0.0), w)
        if weights:
            chosen.append(min(weights, key=lambda j: (-weights[j], j)))
        else:
            remaining = [j for j in range(n) if j not in chosen]
            chosen.append(int(remaining[int(rng.integers(len(remaining)))]))
    return tuple(sorted(chosen)), reason


def subproblem_search(
    instance: ApplicationInstance,
    *,
    sampler: Sampler,
    seed: int,
    config: SearchConfig = SearchConfig(),
) -> SearchResult:
    """Classical outer search with bounded sampled neighbourhoods."""
    if sampler not in SAMPLER_MODES:
        raise ValueError(f"Unknown sampler {sampler!r}")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("Seed must be an integer in [0, 2^32)")
    config = SearchConfig.model_validate(config.model_dump())
    started = perf_counter()
    n = len(instance.variable_labels)
    if n == 0:
        raise ValueError("Instance has no decision variables")
    timing = dict(preparation_s=0.0, tuning_s=0.0, sampling_s=0.0, validation_s=0.0)
    mark = perf_counter()
    initial, evaluation = _repair(instance, (0,) * n, config.repair_moves)
    repair_s = perf_counter() - mark
    calls = initial.validator_calls
    current = initial.bits
    adjacency = _adjacency(instance.qubo, n)
    neighbourhood_rng = np.random.default_rng(np.random.SeedSequence([seed, 0]))
    steps = []
    shots_total = feasible_shots = 0
    max_qubits = cx_total = 0
    for iteration in range(config.steps):
        mark = perf_counter()
        free, reason = _neighbourhood(
            n, evaluation, adjacency, config.max_free_variables, neighbourhood_rng
        )
        energies = subproblem_energies(instance.qubo, current, free)
        k = len(free)
        max_qubits = max(max_qubits, k)
        timing["preparation_s"] += perf_counter() - mark
        rng = np.random.default_rng(np.random.SeedSequence([seed, 1, iteration]))
        size = 2**k

        def full(index):
            bits = list(current)
            for position, variable in enumerate(free):
                bits[variable] = (index >> position) & 1
            return tuple(bits)

        if sampler == "exact":
            counts = np.ones(size, dtype=np.int64)
            shots = size
        else:
            mark = perf_counter()
            if sampler == "random":
                probabilities = np.full(size, 1.0 / size)
            else:
                tuning = tune_qaoa(
                    tuple(float(e) for e in energies),
                    depth=config.depth,
                    evaluations=config.evaluations,
                    seed=int(rng.integers(2**32)),
                )
                probabilities = np.asarray(tuning.probabilities)
                rz, zz = _sub_terms(instance.qubo, current, free)
                cx_total += config.depth * 2 * zz
            timing["tuning_s"] += perf_counter() - mark
            mark = perf_counter()
            if sampler == "qaoa_noise":
                probabilities = apply_noise(
                    probabilities,
                    noise=config.noise,
                    cx_gates=config.depth * 2 * zz,
                    single_qubit_gates=config.depth * (rz + k),
                )
            probabilities = probabilities / probabilities.sum()
            counts = rng.multinomial(config.shots, probabilities)
            shots = config.shots
            timing["sampling_s"] += perf_counter() - mark
        mark = perf_counter()
        evaluated = {}
        feasible_count = 0
        for index in np.flatnonzero(counts):
            result = instance.evaluate(full(int(index)))
            calls += 1
            evaluated[int(index)] = result
            if result.feasible:
                feasible_count += int(counts[index])
        ranked = sorted(evaluated, key=lambda i: (-int(counts[i]), i))
        candidates = (
            ranked if sampler == "exact" else ranked[: config.candidates_per_step]
        )
        proposal_index = min(candidates, key=lambda i: (_key(evaluated[i]), i))
        proposal = evaluated[proposal_index]
        accepted = _key(proposal) < _key(evaluation)
        proposal_bits = full(proposal_index)
        timing["validation_s"] += perf_counter() - mark
        steps.append(
            SearchStep(
                iteration=iteration,
                free_variables=free,
                selection_reason=reason,
                qubits=k,
                shots=shots,
                raw_feasible_fraction=feasible_count / shots,
                proposal_bits=proposal_bits,
                proposal_violation=_text(proposal.violation),
                proposal_objective=_text(proposal.objective),
                accepted=accepted,
            )
        )
        shots_total += shots
        feasible_shots += feasible_count
        if accepted:
            current, evaluation = proposal_bits, proposal
    mark = perf_counter()
    final, evaluation = _repair(instance, current, config.repair_moves)
    repair_s += perf_counter() - mark
    calls += final.validator_calls
    total = perf_counter() - started
    return SearchResult(
        sampler=sampler,
        execution_mode=SAMPLER_MODES[sampler],
        seed=seed,
        status="feasible" if final.feasible else "infeasible",
        bits=final.bits,
        objective=final.objective,
        initial=initial,
        final=final,
        steps=tuple(steps),
        samples_drawn=shots_total,
        raw_feasible_rate=feasible_shots / shots_total if shots_total else None,
        max_qubits=max_qubits,
        cx_gates=cx_total,
        validator_calls=calls,
        timing=Timing(**timing, repair_s=repair_s, total_s=total),
    )


# ------------------------------------------------------------ benchmark records


METHOD_OF = {
    "qaoa": "lns_qaoa",
    "qaoa_noise": "lns_qaoa",
    "random": "lns_random",
    "exact": "lns_exact",
}


def _gap(objective, optimum):
    if objective is None or optimum is None:
        return None
    return max(0.0, float(Fraction(optimum) - Fraction(objective)))


def _reference_run(reference: Reference, instance, optimum) -> MethodRun:
    evaluation = instance.evaluate(reference.bits) if reference.bits else None
    feasible = evaluation is not None and evaluation.feasible
    return MethodRun(
        method=reference.method,
        execution_mode="classical",
        status="feasible" if feasible else "infeasible",
        bits=reference.bits,
        objective=reference.objective if feasible else None,
        objective_value=reference.objective_value if feasible else None,
        optimum_gap_value=_gap(reference.objective, optimum) if feasible else None,
        metrics=_metrics(evaluation),
        validator_calls=reference.validator_calls,
        timing=reference.timing,
    )


def _search_run(result: SearchResult, instance, optimum) -> MethodRun:
    feasible = result.status == "feasible"
    evaluation = instance.evaluate(result.bits)
    return MethodRun(
        method=METHOD_OF[result.sampler],
        execution_mode=result.execution_mode,
        seed=result.seed,
        status=result.status,
        bits=result.bits if feasible else None,
        objective=result.objective if feasible else None,
        objective_value=float(Fraction(result.objective)) if feasible else None,
        optimum_gap_value=_gap(result.objective, optimum) if feasible else None,
        metrics=_metrics(evaluation),
        raw_feasible_rate=result.raw_feasible_rate,
        raw_samples=result.samples_drawn,
        steps=len(result.steps),
        accepted_steps=sum(step.accepted for step in result.steps),
        max_qubits=result.max_qubits,
        cx_gates=result.cx_gates,
        validator_calls=result.validator_calls,
        timing=result.timing,
    )


def run_instance(
    instance: ApplicationInstance,
    *,
    split: Literal["dev", "held_out"],
    seed: int,
    config: BenchmarkConfig,
) -> InstanceRecord:
    """All methods on one instance with metrics and optimum gaps."""
    n = len(instance.variable_labels)
    milp_ref = solve_milp(instance, time_limit_s=config.milp_time_limit_s)
    exact_ref = None
    audit = None
    if n <= config.exact_max_variables:
        exact_ref = solve_exact(instance, max_variables=config.exact_max_variables)
        audit = audit_qubo(instance, max_variables=config.exact_max_variables)
        if (exact_ref.status == "optimal") != (milp_ref.status == "optimal") or (
            exact_ref.status == "optimal"
            and Fraction(exact_ref.objective) != Fraction(milp_ref.objective)
        ):
            raise RuntimeError(
                f"MILP and exact enumeration disagree on {instance.instance_id}"
            )
    if exact_ref is not None and exact_ref.status == "optimal":
        source, optimum = "exact", exact_ref.objective
    elif milp_ref.status == "optimal":
        source, optimum = "milp", milp_ref.objective
    else:
        source, optimum = "none", None
    methods = [_reference_run(milp_ref, instance, optimum)]
    if exact_ref is not None:
        methods.append(_reference_run(exact_ref, instance, optimum))
    for qaoa_seed in config.qaoa_seeds:
        for sampler in ("qaoa", "qaoa_noise", "random", "exact"):
            result = subproblem_search(
                instance, sampler=sampler, seed=qaoa_seed, config=config.search
            )
            methods.append(_search_run(result, instance, optimum))
    return InstanceRecord(
        application=instance.application,
        split=split,
        seed=seed,
        instance_id=instance.instance_id,
        input_fingerprint=instance.input_fingerprint,
        variables=n,
        qubo_exact=instance.qubo.exact,
        qubo_encoding=instance.qubo.encoding,
        audit=audit,
        optimum_source=source,
        optimum=optimum,
        optimum_value=None if optimum is None else float(Fraction(optimum)),
        rejections=tuple(instance.rejections),
        source_note=instance.source_note,
        methods=tuple(methods),
    )


# ------------------------------------------------------------ decisions


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _difference(left, right):
    return None if left is None or right is None else left - right


def decide_application(
    instances: tuple[InstanceRecord, ...], criteria: ApplicationCriteria
) -> Decision:
    """Pre-registered go/no-go on held-out records of one application."""
    criteria = ApplicationCriteria.model_validate(criteria.model_dump())
    held = [
        i
        for i in instances
        if i.application == criteria.application
        and i.split == "held_out"
        and i.optimum_value is not None
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
    label = f"{criteria.hybrid_method}:{criteria.hybrid_mode}"

    def gaps(records):
        return _mean([r.optimum_gap_value for r in records if r.status == "feasible"])

    results = []
    feasible = (
        sum(r.status == "feasible" for r in hybrid) / len(hybrid) if hybrid else None
    )
    results.append(
        CriterionResult(
            name="validated_feasibility",
            passed=feasible is not None
            and feasible >= criteria.min_feasible_run_fraction,
            measured=feasible,
            threshold=criteria.min_feasible_run_fraction,
            explanation=f"Fraction of held-out {label} runs whose returned assignment "
            "passes the independent validator.",
        )
    )
    excess = _difference(gaps(hybrid), gaps(classical))
    results.append(
        CriterionResult(
            name="objective_quality",
            passed=excess is not None and excess <= criteria.max_mean_gap_excess,
            measured=excess,
            threshold=criteria.max_mean_gap_excess,
            explanation=f"Mean optimum gap of {label} minus mean gap of "
            f"{criteria.classical_method}; lower is better.",
        )
    )
    raw = _difference(
        _mean([r.raw_feasible_rate for r in hybrid]),
        _mean([r.raw_feasible_rate for r in baseline]),
    )
    results.append(
        CriterionResult(
            name="raw_feasibility_vs_random",
            passed=raw is not None and raw > 0,
            measured=raw,
            threshold=0.0,
            explanation=f"Mean raw feasible sample rate of {label} minus "
            f"{criteria.random_method}; must be positive.",
        )
    )
    versus = _difference(gaps(hybrid), gaps(baseline))
    results.append(
        CriterionResult(
            name="repaired_quality_vs_random",
            passed=versus is not None and versus <= 0,
            measured=versus,
            threshold=0.0,
            explanation=f"Mean final optimum gap of {label} minus "
            f"{criteria.random_method} under the same outer search and repair.",
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
            explanation=f"Mean end-to-end time of {label} (preparation, tuning, "
            "sampling, validation and repair; no provider queue) divided by "
            f"{criteria.classical_method}.",
        )
    )
    go = all(r.passed for r in results)
    return Decision(
        application=criteria.application,
        verdict="go" if go else "no_go",
        criteria=tuple(results),
        retained_solver=HYBRID_SOLVER if go else MILP_SOLVER,
        statement=NO_ADVANTAGE_STATEMENT
        + " The decision covers only the held-out synthetic instances and the "
        "recorded compute budget.",
    )


def execution_policy(decisions: tuple[Decision, ...]) -> ExecutionPolicy:
    """Enable QAOA only for applications with a held-out GO decision."""
    by_application: dict[str, Decision] = {}
    for decision in decisions:
        if decision.application not in APPLICATIONS:
            raise ValueError(f"Unknown application {decision.application!r}")
        if decision.application in by_application:
            raise ValueError(f"Duplicate decision for {decision.application}")
        by_application[decision.application] = decision
    enabled, retained, reasons = {}, {}, {}
    for application in APPLICATIONS:
        decision = by_application.get(application)
        if decision is None:
            enabled[application] = False
            retained[application] = MILP_SOLVER
            reasons[application] = "No held-out decision; quantum execution disabled."
        elif decision.verdict == "go":
            enabled[application] = True
            retained[application] = HYBRID_SOLVER
            reasons[application] = (
                "Held-out GO: every pre-registered criterion passed; QAOA runs with "
                "validator-checked output and MILP fallback."
            )
        else:
            failed = ", ".join(c.name for c in decision.criteria if not c.passed)
            enabled[application] = False
            retained[application] = MILP_SOLVER
            reasons[application] = f"Held-out NO-GO (failed: {failed}); MILP retained."
    return ExecutionPolicy(
        quantum_enabled=enabled, retained_solver=retained, reasons=reasons
    )


def _milp_result(instance, time_limit_s, quantum_executed, reason) -> PolicyResult:
    reference = solve_milp(instance, time_limit_s=time_limit_s)
    return PolicyResult(
        application=instance.application,
        solver=MILP_SOLVER,
        execution_mode="classical",
        quantum_executed=quantum_executed,
        status="feasible" if reference.bits is not None else "infeasible",
        bits=reference.bits,
        objective=reference.objective,
        reason=reason,
    )


def solve_application(
    instance: ApplicationInstance,
    policy: ExecutionPolicy,
    *,
    config: SearchConfig = SearchConfig(),
    seed: int = 17,
    time_limit_s: float = 30.0,
) -> PolicyResult:
    """Run the solver the policy allows; MILP unless the application is GO."""
    if not policy.quantum_enabled.get(instance.application, False):
        reason = policy.reasons.get(
            instance.application, "No held-out decision; quantum execution disabled."
        )
        return _milp_result(instance, time_limit_s, False, reason)
    result = subproblem_search(instance, sampler="qaoa", seed=seed, config=config)
    if result.status == "feasible" and instance.evaluate(result.bits).feasible:
        return PolicyResult(
            application=instance.application,
            solver=HYBRID_SOLVER,
            execution_mode="noiseless_simulator",
            quantum_executed=True,
            status="feasible",
            bits=result.bits,
            objective=result.objective,
            reason="Held-out GO; QAOA search output passed the independent validator.",
        )
    return _milp_result(
        instance,
        time_limit_s,
        True,
        "QAOA search returned no validated assignment; classical MILP fallback.",
    )


# ------------------------------------------------------------ runner


def config_fingerprint(config: BenchmarkConfig) -> str:
    """SHA-256 of the canonical configuration JSON."""
    text = json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_benchmark(
    config: BenchmarkConfig, builders: Mapping[str, Builder]
) -> BenchmarkReport:
    """Dev and held-out instances, decisions and the resulting policy."""
    config = BenchmarkConfig.model_validate(config.model_dump())
    missing = [
        s.application for s in config.applications if s.application not in builders
    ]
    if missing:
        raise ValueError(f"No instance builder for: {', '.join(missing)}")
    records = []
    decisions = []
    for split in config.applications:
        prints: dict[str, set[str]] = {"dev": set(), "held_out": set()}
        for name, seeds in (
            ("dev", split.dev_seeds),
            ("held_out", split.held_out_seeds),
        ):
            for seed in seeds:
                instance = builders[split.application](seed)
                if instance.application != split.application:
                    raise ValueError(
                        f"Builder for {split.application} returned {instance.application}"
                    )
                prints[name].add(instance.input_fingerprint)
                records.append(
                    run_instance(instance, split=name, seed=seed, config=config)
                )
        if prints["dev"] & prints["held_out"]:
            raise ValueError(
                f"{split.application}: a held-out instance equals a dev instance"
            )
        decisions.append(
            decide_application(
                tuple(records), ApplicationCriteria(application=split.application)
            )
        )
    return BenchmarkReport(
        config=config,
        config_fingerprint=config_fingerprint(config),
        instances=tuple(records),
        decisions=tuple(decisions),
        policy=execution_policy(tuple(decisions)),
        environment=_environment(),
        statement=NO_ADVANTAGE_STATEMENT
        + " Instances are synthetic planning benchmarks, not NASA plans, DSN "
        "bookings, validated relay sites or certified landing/traverse plans.",
    )


# ------------------------------------------------------------ application helpers


def rational(value) -> Fraction:
    """Exact rational of a float's shortest decimal representation."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{value!r} is not a finite number")
    return Fraction(repr(float(value))) if isinstance(value, float) else Fraction(value)


def model_fingerprint(model: Contract) -> str:
    """SHA-256 of a contract's canonical JSON."""
    text = json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def penalty_strength(coefficients) -> float:
    """Pre-registered A = 1 + 2 * sum |objective coefficients|."""
    return 1.0 + 2.0 * float(sum(abs(float(c)) for c in coefficients))


class QuboBuilder:
    """Accumulates constant, linear and upper-triangular quadratic terms."""

    def __init__(self, n: int):
        self.n = n
        self.constant = 0.0
        self.linear = [0.0] * n
        self.quadratic: dict[tuple[int, int], float] = {}

    def add_linear(self, i: int, value: float) -> None:
        self.linear[i] += float(value)

    def add_pair(self, i: int, j: int, value: float) -> None:
        if i == j:
            self.linear[i] += float(value)  # x^2 = x for binaries.
            return
        key = (min(i, j), max(i, j))
        self.quadratic[key] = self.quadratic.get(key, 0.0) + float(value)

    def add_unbalanced(
        self, weights: Mapping[int, float], capacity: float, strength: float
    ):
        constant, linear, quadratic = unbalanced_penalty(weights, capacity, strength)
        self.constant += constant
        for i, value in linear.items():
            self.add_linear(i, value)
        for (i, j), value in quadratic.items():
            self.add_pair(i, j, value)

    def build(self, *, exact: bool, encoding: str) -> Qubo:
        return Qubo(
            constant=self.constant,
            linear=tuple(self.linear),
            quadratic={k: v for k, v in self.quadratic.items() if v != 0.0},
            exact=exact,
            encoding=encoding,
        )


def milp_model(
    objective, rows, *, decision_count: int, integrality=None, lower=None, upper=None
) -> MilpModel:
    columns = len(objective)
    return MilpModel(
        objective=tuple(float(v) for v in objective),
        integrality=tuple(integrality or (1,) * columns),
        lower=tuple(lower or (0.0,) * columns),
        upper=tuple(upper or (1.0,) * columns),
        rows=tuple(rows),
        decision_count=decision_count,
    )
