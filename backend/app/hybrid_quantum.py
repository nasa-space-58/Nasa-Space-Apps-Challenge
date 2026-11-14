"""H2 audited QUBO and compact-register QAOA; local noiseless simulation only.

QAOA: Farhi, Goldstone, Gutmann (2014), https://arxiv.org/abs/1411.4028.
The compact diagonal Hamiltonian may contain higher-than-quadratic Z terms.
"""

import hashlib
import json
from fractions import Fraction
from itertools import combinations, product
from time import perf_counter
import math
from typing import Literal

import numpy as np

from shared.hybrid import Contract, Plan, Scenario, fingerprint
from .hybrid_planning import solve_exact, validate_plan


class QuadraticTerm(Contract):
    left: int
    right: int
    coefficient: str


class QuboModel(Contract):
    input_fingerprint: str
    option_ids: tuple[str, ...]
    mission_groups: tuple[tuple[int, ...], ...]
    offset: str
    diagonal: tuple[str, ...]
    quadratic: tuple[QuadraticTerm, ...]
    penalty: str
    forbidden_options: tuple[int, ...]
    conflict_pairs: tuple[tuple[int, int], ...]


class MappingAudit(Contract):
    binary_assignments: int
    one_choice_assignments: int
    feasible_assignments: int
    optimal_assignments: int
    ground_energy: str
    ground_matches_classical: bool


class CompactModel(Contract):
    qubo: QuboModel
    logical_qubits: int
    one_hot_masks: tuple[int, ...]
    normalized_energies: tuple[float, ...]
    energy_shift: float
    energy_scale: float
    z_terms: tuple[tuple[int, float], ...]
    cx_per_cost_layer: int
    max_z_locality: int


class QuantumSample(Contract):
    basis_index: int
    count: int
    probability: float
    selected_option_ids: tuple[str, ...]
    feasible: bool
    optimal: bool
    score: float | None


class QaoaResult(Contract):
    execution_mode: Literal["noiseless_simulator"] = "noiseless_simulator"
    input_fingerprint: str
    logical_qubits: int
    qubo_variables: int
    depth: int
    shots: int
    seed: int
    objective_evaluations: int
    gammas: tuple[float, ...]
    betas: tuple[float, ...]
    expected_normalized_energy: float
    feasible_probability: float
    optimal_probability: float
    uniform_feasible_probability: float
    uniform_optimal_probability: float
    sampled_feasible_rate: float
    sampled_optimal_rate: float
    best_sampled_plan: Plan | None
    samples: tuple[QuantumSample, ...]
    cx_gate_estimate: int
    rz_gate_estimate: int
    rx_gate_count: int
    max_z_locality: int
    elapsed_s: float
    hardware_jobs: Literal[0] = 0
    repair_applied: Literal[False] = False


def compile_qubo(scenario: Scenario, *, max_variables: int = 12) -> QuboModel:
    """Build exact rational one-hot QUBO after exhaustive constraint consistency.

    Support static complete instances whose hard rules are exactly unary/pairwise
    on one-choice assignments. Raise ValueError for unsupported inputs, higher-order
    resource constraints, or more than max_variables (hard cap 12) binary variables.
    """
    _integer_budget(max_variables, "max_variables", 1, 12)
    scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
    if len(scenario.candidates) > max_variables:
        raise ValueError("QUBO variable budget exceeded")
    reference = solve_exact(scenario, max_evaluations=4096)
    if reference.status in ("incomplete", "unsupported", "limit_reached"):
        raise ValueError(f"QUBO input {reference.status}: {reference.issues}")
    candidates = tuple(sorted(scenario.candidates, key=lambda c: c.option_id))
    ids = tuple(c.option_id for c in candidates)
    groups = tuple(
        tuple(i for i, c in enumerate(candidates) if c.mission_id == m.mission_id)
        for m in sorted(scenario.missions, key=lambda m: m.mission_id)
    )
    digest = fingerprint(scenario)
    scores = tuple(
        sum(
            (
                Fraction(str(o.weight))
                * Fraction(str(c.metrics[o.metric]))
                / Fraction(str(o.scale))
                * (1 if o.direction == "maximize" else -1)
                for o in scenario.objectives
            ),
            Fraction(0),
        )
        for c in candidates
    )
    penalty = 1 + sum(abs(score) for score in scores)
    forbidden = []
    for i, oid in enumerate(ids):
        report = validate_plan(_plan((oid,), scenario, digest), scenario)
        if _has_hard_violation(report):
            forbidden.append(i)
    conflicts = []
    for i, j in combinations(range(len(ids)), 2):
        if (
            i in forbidden
            or j in forbidden
            or candidates[i].mission_id == candidates[j].mission_id
        ):
            continue
        report = validate_plan(_plan((ids[i], ids[j]), scenario, digest), scenario)
        if _has_hard_violation(report):
            conflicts.append((i, j))
    # Pair constraints cannot express every capacity/budget constraint. Prove
    # equivalence on the whole bounded choice domain rather than approximating it.
    for choice in product(*groups):
        predicted = not (set(choice) & set(forbidden)) and not any(
            i in choice and j in choice for i, j in conflicts
        )
        report = validate_plan(
            _plan(tuple(ids[i] for i in choice), scenario, digest), scenario
        )
        if predicted != (report.status == "feasible"):
            raise ValueError(
                "Resource constraint requires higher-order or slack-variable mapping"
            )
    diagonal = [
        -score - penalty + (penalty if i in forbidden else 0)
        for i, score in enumerate(scores)
    ]
    pairs = {}
    for group in groups:
        for i, j in combinations(group, 2):
            pairs[(i, j)] = 2 * penalty
    for pair in conflicts:
        pairs[pair] = pairs.get(pair, Fraction(0)) + penalty
    return QuboModel(
        input_fingerprint=digest,
        option_ids=ids,
        mission_groups=groups,
        offset=str(penalty * len(groups)),
        diagonal=tuple(str(v) for v in diagonal),
        quadratic=tuple(
            QuadraticTerm(left=i, right=j, coefficient=str(v))
            for (i, j), v in sorted(pairs.items())
        ),
        penalty=str(penalty),
        forbidden_options=tuple(forbidden),
        conflict_pairs=tuple(conflicts),
    )


def qubo_energy(model: QuboModel, bits: tuple[int, ...]) -> Fraction:
    """Return exact dimensionless QUBO energy; reject wrong length/non-binary bits."""
    if len(bits) != len(model.option_ids) or any(
        type(bit) is not int or bit not in (0, 1) for bit in bits
    ):
        raise ValueError(
            "Assignment must contain one integer binary bit per QUBO variable"
        )
    return (
        Fraction(model.offset)
        + sum((Fraction(v) * bit for v, bit in zip(model.diagonal, bits)), Fraction(0))
        + sum(
            (
                Fraction(term.coefficient) * bits[term.left] * bits[term.right]
                for term in model.quadratic
            ),
            Fraction(0),
        )
    )


def audit_mapping(model: QuboModel, scenario: Scenario) -> MappingAudit:
    """Compare every binary energy/ground state against the original H1 rules.

    Raise ValueError for stale or inconsistent models. Bound to 12 variables.
    """
    expected = compile_qubo(scenario)
    if model != expected:
        raise ValueError("QUBO model is stale or inconsistent with original input")
    n = len(model.option_ids)
    ground = None
    ground_masks = []
    feasible_energies = {}
    one_choice_count = 0
    for mask in range(1 << n):
        assignment = _bits(mask, n)
        energy = qubo_energy(model, assignment)
        if ground is None or energy < ground:
            ground, ground_masks = energy, [mask]
        elif energy == ground:
            ground_masks.append(mask)
        if any(
            sum(assignment[i] for i in group) != 1 for group in model.mission_groups
        ):
            continue
        one_choice_count += 1
        ids = tuple(oid for oid, bit in zip(model.option_ids, assignment) if bit)
        report = validate_plan(_plan(ids, scenario, model.input_fingerprint), scenario)
        if report.status == "feasible":
            if energy != -Fraction(report.exact_score):
                raise ValueError("Feasible QUBO energy differs from original objective")
            feasible_energies[mask] = energy
    optimum = min(feasible_energies.values()) if feasible_energies else None
    optimal_masks = {
        mask for mask, value in feasible_energies.items() if value == optimum
    }
    matches = bool(optimal_masks) and set(ground_masks) == optimal_masks
    if optimal_masks and not matches:
        raise ValueError("QUBO ground states differ from original optimal plans")
    return MappingAudit(
        binary_assignments=1 << n,
        one_choice_assignments=one_choice_count,
        feasible_assignments=len(feasible_energies),
        optimal_assignments=len(optimal_masks),
        ground_energy=str(ground),
        ground_matches_classical=matches,
    )


def compact_model(model: QuboModel, *, max_qubits: int = 6) -> CompactModel:
    """Encode power-of-two options per mission, without auxiliary variables.

    Return a normalized diagonal Hamiltonian and logical all-to-all gate estimates.
    More-than-quadratic Z terms are explicit; routing/transpilation is not estimated.
    Raise ValueError outside 1..max_qubits (hard cap 8) or for non-power-of-two groups.
    """
    _integer_budget(max_qubits, "max_qubits", 1, 8)
    if len(model.option_ids) > 12:
        raise ValueError("QUBO variable budget exceeded")
    widths = []
    for group in model.mission_groups:
        size = len(group)
        if size < 1 or size & (size - 1):
            raise ValueError(
                "Compact encoding requires power-of-two options per mission"
            )
        widths.append(size.bit_length() - 1)
    qubits = sum(widths)
    if not 1 <= qubits <= max_qubits:
        raise ValueError("Compact qubit budget exceeded or no decision qubit is needed")
    masks = []
    for basis in range(1 << qubits):
        offset = mask = 0
        for group, width in zip(model.mission_groups, widths):
            choice = (basis >> offset) & ((1 << width) - 1)
            mask |= 1 << group[choice]
            offset += width
        masks.append(mask)
    energies = np.array(
        [
            float(qubo_energy(model, _bits(mask, len(model.option_ids))))
            for mask in masks
        ]
    )
    if not np.isfinite(energies).all():
        raise ValueError("Hamiltonian energies exceed finite range")
    shift = float(np.mean(energies))
    scale = float(np.max(np.abs(energies - shift))) or 1.0
    normalized = (energies - shift) / scale
    coefficients = normalized.copy()
    step = 1
    # Walsh-Hadamard transform: each mask represents a product of Pauli Zs.
    while step < len(coefficients):
        for start in range(0, len(coefficients), 2 * step):
            left = coefficients[start : start + step].copy()
            right = coefficients[start + step : start + 2 * step].copy()
            coefficients[start : start + step] = left + right
            coefficients[start + step : start + 2 * step] = left - right
        step *= 2
    coefficients /= len(coefficients)
    terms = tuple(
        (mask, float(value))
        for mask, value in enumerate(coefficients)
        if abs(value) > 1e-12
    )
    cx = sum(2 * (mask.bit_count() - 1) for mask, _ in terms if mask)
    return CompactModel(
        qubo=model,
        logical_qubits=qubits,
        one_hot_masks=tuple(masks),
        normalized_energies=tuple(float(v) for v in normalized),
        energy_shift=shift,
        energy_scale=scale,
        z_terms=terms,
        cx_per_cost_layer=cx,
        max_z_locality=max((mask.bit_count() for mask, _ in terms), default=0),
    )


def qaoa_statevector(energies, gammas, betas) -> np.ndarray:
    """Return exp(-i beta sum X) exp(-i gamma C) layers acting on uniform |+>.

    Little-endian basis indexing; finite dimensionless energies/angles required.
    Support 1..8 qubits and 1..3 layers; invalid input raises ValueError.
    """
    energies = np.asarray(energies, dtype=float)
    gammas, betas = np.asarray(gammas, dtype=float), np.asarray(betas, dtype=float)
    if (
        energies.ndim != 1
        or len(energies) < 2
        or len(energies) > 256
        or len(energies) & (len(energies) - 1)
    ):
        raise ValueError("Energy vector must describe 1..8 qubits")
    if (
        gammas.ndim != 1
        or betas.ndim != 1
        or len(gammas) != len(betas)
        or not 1 <= len(gammas) <= 3
    ):
        raise ValueError("Matching gamma/beta arrays for 1..3 layers required")
    if not all(np.isfinite(v).all() for v in (energies, gammas, betas)):
        raise ValueError("Energies and angles must be finite")
    state = np.ones(len(energies), dtype=complex) / math.sqrt(len(energies))
    indices = np.arange(len(energies))
    for gamma, beta in zip(gammas, betas):
        with np.errstate(over="ignore", invalid="ignore"):
            phase = gamma * energies
        if not np.isfinite(phase).all():
            raise ValueError("Phase angles exceed finite range")
        state *= np.exp(-1j * phase)
        for qubit in range(len(energies).bit_length() - 1):
            state = (
                math.cos(beta) * state
                - 1j * math.sin(beta) * state[indices ^ (1 << qubit)]
            )
    return state


def run_qaoa(
    scenario: Scenario,
    *,
    depth: int = 1,
    evaluations: int = 160,
    shots: int = 512,
    seed: int = 17,
    max_qubits: int = 6,
    on_iteration=None,
) -> QaoaResult:
    """Run bounded seeded classical tuning, then sample the noiseless QAOA state.

    Validate every sampled assignment with H1; report raw feasible/optimal rates,
    exact distribution probabilities and uniform baselines separately. No repair,
    provider API, noise, hardware, queue time or quantum advantage is claimed.
    Raise ValueError for invalid budgets or unsupported/infeasible problems.
    """
    started = perf_counter()
    _integer_budget(depth, "depth", 1, 3)
    _integer_budget(evaluations, "evaluations", 1, 512)
    _integer_budget(shots, "shots", 1, 8192)
    _integer_budget(seed, "seed", 0, 2**32 - 1)
    _integer_budget(max_qubits, "max_qubits", 1, 8)
    scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
    qubo = compile_qubo(scenario)
    audit = audit_mapping(qubo, scenario)
    if not audit.feasible_assignments:
        raise ValueError("Original problem is infeasible; no quantum optimization run")
    compact = compact_model(qubo, max_qubits=max_qubits)
    energies = np.array(compact.normalized_energies)
    tuning_seed, sampling_seed = np.random.SeedSequence(seed).spawn(2)
    rng = np.random.default_rng(tuning_seed)
    best_angles = np.zeros(2 * depth)
    best_energy = math.inf
    best_probabilities = None
    for iteration in range(evaluations):
        if iteration == 0:
            angles = np.zeros(2 * depth)  # Uniform baseline is an explicit candidate.
        elif iteration < max(2, evaluations // 2):
            angles = rng.uniform(-np.pi, np.pi, 2 * depth)
        else:
            # Bounded stochastic coordinate refinement of the incumbent energy.
            angles = best_angles.copy()
            remaining = (evaluations - iteration) / max(1, evaluations // 2)
            angles[rng.integers(2 * depth)] += rng.normal(0, 1.5 * remaining + 0.05)
        state = qaoa_statevector(energies, angles[:depth], angles[depth:])
        probabilities = np.abs(state) ** 2
        probabilities /= probabilities.sum()
        energy = float(probabilities @ energies)
        if energy < best_energy:
            best_energy, best_angles, best_probabilities = (
                energy,
                angles.copy(),
                probabilities.copy(),
            )
    counts = np.random.default_rng(sampling_seed).multinomial(shots, best_probabilities)
    samples = []
    best_plan = None
    best_score = None
    for basis, (mask, count, probability) in enumerate(
        zip(compact.one_hot_masks, counts, best_probabilities)
    ):
        ids = tuple(oid for i, oid in enumerate(qubo.option_ids) if (mask >> i) & 1)
        plan = _plan(
            ids, scenario, qubo.input_fingerprint, execution_mode="noiseless_simulator"
        )
        report = validate_plan(plan, scenario)
        feasible = report.status == "feasible"
        optimal = feasible and -Fraction(report.exact_score) == Fraction(
            audit.ground_energy
        )
        samples.append(
            QuantumSample(
                basis_index=basis,
                count=int(count),
                probability=float(probability),
                selected_option_ids=ids,
                feasible=feasible,
                optimal=optimal,
                score=report.score,
            )
        )
        if feasible and count:
            score = Fraction(report.exact_score)
            if (
                best_score is None
                or score > best_score
                or (score == best_score and ids < best_plan.selected_option_ids)
            ):
                best_plan, best_score = plan, score
    return QaoaResult(
        input_fingerprint=qubo.input_fingerprint,
        logical_qubits=compact.logical_qubits,
        qubo_variables=len(qubo.option_ids),
        depth=depth,
        shots=shots,
        seed=seed,
        objective_evaluations=evaluations,
        gammas=tuple(float(a) for a in best_angles[:depth]),
        betas=tuple(float(a) for a in best_angles[depth:]),
        expected_normalized_energy=best_energy,
        feasible_probability=sum(s.probability for s in samples if s.feasible),
        optimal_probability=sum(s.probability for s in samples if s.optimal),
        uniform_feasible_probability=sum(s.feasible for s in samples) / len(samples),
        uniform_optimal_probability=sum(s.optimal for s in samples) / len(samples),
        sampled_feasible_rate=sum(s.count for s in samples if s.feasible) / shots,
        sampled_optimal_rate=sum(s.count for s in samples if s.optimal) / shots,
        best_sampled_plan=best_plan,
        samples=tuple(samples),
        cx_gate_estimate=depth * compact.cx_per_cost_layer,
        rz_gate_estimate=depth * sum(mask != 0 for mask, _ in compact.z_terms),
        rx_gate_count=depth * compact.logical_qubits,
        max_z_locality=compact.max_z_locality,
        elapsed_s=perf_counter() - started,
    )


def _integer_budget(value, name, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer in [{lower}, {upper}]")


def _bits(mask, size):
    return tuple((mask >> i) & 1 for i in range(size))


def _has_hard_violation(report):
    return any(issue.code != "selection_count" for issue in report.issues)


def _plan(ids, scenario, digest, execution_mode="classical"):
    return Plan(
        plan_id="h2-"
        + hashlib.sha256(
            json.dumps([digest, sorted(ids), execution_mode]).encode()
        ).hexdigest(),
        scenario_id=scenario.scenario_id,
        scenario_revision=scenario.revision,
        input_fingerprint=digest,
        state_revisions={s.state_id: s.revision for s in scenario.states},
        selected_option_ids=tuple(sorted(ids)),
        execution_mode=execution_mode,
        solver_name="h2-qaoa-v1"
        if execution_mode == "noiseless_simulator"
        else "h2-mapping-audit",
    )
