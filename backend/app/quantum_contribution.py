"""Paired, local-only quantum contribution experiment; no provider imports.

One binary variable per native application choice. Sparse QUBO coefficients
are compiled analytically, not by enumerating a truth table. The bounded
statevector simulator still uses exponential memory and is explicitly counted.
"""

import math
from dataclasses import replace
from fractions import Fraction
from time import perf_counter
from typing import Annotated

import numpy as np
from pydantic import Field
from shared.hybrid import Contract

from .quantum_applications import solve_milp

METHODS = (
    "milp",
    "multistart",
    "annealing",
    "random",
    "qaoa",
    "warm_qaoa",
    "conflict_qaoa",
)
QUANTUM_METHODS = METHODS[4:]


class ContributionBudget(Contract):
    validator_calls: Annotated[int, Field(strict=True, ge=1, le=1024)] = 64
    energy_evaluations: Annotated[int, Field(strict=True, ge=1, le=100000)] = 2048
    wall_s: Annotated[float, Field(strict=True, gt=0, le=60)] = 3.0
    max_qubits: Annotated[int, Field(strict=True, ge=1, le=16)] = 12
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    tuning_evaluations: Annotated[int, Field(strict=True, ge=1, le=256)] = 24
    shots: Annotated[int, Field(strict=True, ge=1, le=8192)] = 256


def ising_coefficients(qubo):
    """Return constant, Z coefficients and ZZ coefficients using x=(1-Z)/2."""
    n = len(qubo.linear)
    if not n or not all(math.isfinite(x) for x in (qubo.constant, *qubo.linear)):
        raise ValueError("Finite, nonempty QUBO required")
    constant = float(qubo.constant) + sum(qubo.linear) / 2
    z = [-v / 2 for v in qubo.linear]
    zz = {}
    for (i, j), value in qubo.quadratic.items():
        if not 0 <= i < j < n or not math.isfinite(value):
            raise ValueError("QUBO requires finite upper-triangular pairs")
        constant += value / 4
        z[i] -= value / 4
        z[j] -= value / 4
        zz[i, j] = value / 4
    return constant, tuple(z), zz


def conflict_masks(instance):
    """Certify native binary at-most-one rows; reject all unsupported constraints."""
    model = instance.milp
    n = len(instance.variable_labels)
    if (
        len(model.objective) != n
        or model.decision_count != n
        or model.integrality != (1,) * n
        or model.lower != (0.0,) * n
        or model.upper != (1.0,) * n
    ):
        raise ValueError("Conflict mixer requires binary columns without auxiliaries")
    masks = [0] * n
    for row, lower, upper in model.rows:
        if (
            lower > 0
            or math.isnan(lower)
            or upper != 1
            or any(not 0 <= i < n or value != 1 for i, value in row.items())
        ):
            raise ValueError("Conflict mixer supports only native at-most-one rows")
        members = sum(1 << i for i in row)
        for i in row:
            masks[i] |= members ^ (1 << i)
    return tuple(masks)


class _LocalStatevector:
    """Local verification only; exponential basis materialization is reported."""

    def __init__(self, instance, variant, initial, max_qubits):
        self.n = len(instance.variable_labels)
        if variant not in QUANTUM_METHODS or not 1 <= self.n <= max_qubits <= 16:
            raise ValueError("Unsupported variant or statevector qubit cap")
        if len(initial) != self.n or any(
            type(b) is not int or b not in (0, 1) for b in initial
        ):
            raise ValueError("Initial decision bits have invalid shape or values")
        self.variant = variant
        self.masks = conflict_masks(instance) if variant == "conflict_qaoa" else None
        initial_index = sum(b << i for i, b in enumerate(initial))
        if self.masks and any(
            b and initial_index & mask for b, mask in zip(initial, self.masks)
        ):
            raise ValueError("Conflict-preserving mixer needs a feasible initial state")
        _, z, zz = ising_coefficients(instance.qubo)
        basis = np.arange(2**self.n, dtype=np.int64)
        self.energies = np.zeros(len(basis))
        # Bound from sparse coefficients, not min/max over exhaustive objective values.
        scale = sum(abs(v) for v in z) + sum(abs(v) for v in zz.values()) or 1.0
        for i, value in enumerate(z):
            self.energies += value / scale * (1 - 2 * ((basis >> i) & 1))
        for (i, j), value in zz.items():
            self.energies += (
                value / scale * (1 - 2 * (((basis >> i) ^ (basis >> j)) & 1))
            )
        self.pairs = []
        self.cos_theta = []
        self.initial = np.ones(len(basis), dtype=complex)
        for i, bit in enumerate(initial):
            zero = basis[(basis & (1 << i)) == 0]
            if self.masks:
                zero = zero[(zero & self.masks[i]) == 0]
            self.pairs.append((zero, zero | (1 << i)))
            p = 0.8 if bit else 0.2
            self.cos_theta.append(1 - 2 * p)
            if variant == "warm_qaoa":
                self.initial *= np.where(
                    (basis >> i) & 1, math.sqrt(p), math.sqrt(1 - p)
                )
        if variant == "qaoa":
            self.initial /= math.sqrt(len(basis))
        elif variant == "conflict_qaoa":
            self.initial[:] = 0
            self.initial[initial_index] = 1

    def probabilities(self, gammas, betas):
        if not 1 <= len(gammas) == len(betas) <= 3 or not all(
            math.isfinite(a) for a in (*gammas, *betas)
        ):
            raise ValueError("Finite matching angle vectors of depth 1..3 required")
        state = self.initial.copy()
        for gamma, beta in zip(gammas, betas):
            state *= np.exp(-1j * gamma * self.energies)
            c, s = math.cos(beta), math.sin(beta)
            for i, (zero, one) in enumerate(self.pairs):
                a, b = state[zero].copy(), state[one].copy()
                z = self.cos_theta[i] if self.variant == "warm_qaoa" else 0.0
                x = math.sqrt(1 - z * z)
                state[zero] = (c - 1j * s * z) * a - 1j * s * x * b
                state[one] = -1j * s * x * a + (c + 1j * s * z) * b
        probabilities = np.abs(state) ** 2
        return probabilities / probabilities.sum()


def quantum_distribution(instance, gammas, betas, *, variant, initial, max_qubits=12):
    """Return local noiseless probabilities in little-endian decision-bit order."""
    return _LocalStatevector(instance, variant, initial, max_qubits).probabilities(
        gammas, betas
    )


class _Deadline(Exception):
    pass


class _EnergyBudget(Exception):
    pass


class _Search:
    def __init__(self, instance, method, budget, seed):
        self.instance, self.method, self.budget = instance, method, budget
        self.start = perf_counter()
        self.deadline = self.start + budget.wall_s
        self.rng = np.random.default_rng(seed)
        self.calls = self.energy_calls = self.basis_work = 0
        self.validation_s = 0.0
        self.trace = []
        self.pool = {}
        self.counts = {}
        self.best = None
        self.best_value = None
        self.quantum_executed = False
        self.initial = None
        self.angles = None

    def check(self):
        if perf_counter() >= self.deadline:
            raise _Deadline

    def energy(self, bits):
        self.check()
        if self.energy_calls >= self.budget.energy_evaluations:
            raise _EnergyBudget
        self.energy_calls += 1
        q = self.instance.qubo
        return (
            q.constant
            + sum(v * b for v, b in zip(q.linear, bits))
            + sum(v * bits[i] * bits[j] for (i, j), v in q.quadratic.items())
        )

    def propose(self, bits):
        value = self.energy(bits)
        self.pool[bits] = value
        return value

    def descend(self, bits):
        value = self.propose(bits)
        while True:
            best, best_value = bits, value
            for i in range(len(bits)):
                trial = bits[:i] + (1 - bits[i],) + bits[i + 1 :]
                energy = self.propose(trial)
                if (energy, trial) < (best_value, best):
                    best, best_value = trial, energy
            if best_value >= value - 1e-12:
                return bits
            bits, value = best, best_value

    def validate(self, bits):
        self.check()
        mark = perf_counter()
        result = self.instance.evaluate(bits)
        self.calls += 1
        self.validation_s += perf_counter() - mark
        self.check()
        value = Fraction(result.objective)
        if result.feasible and (
            self.best_value is None
            or value > self.best_value
            or (value == self.best_value and bits < self.best)
        ):
            self.best, self.best_value = bits, value
        self.trace.append(
            dict(
                bits=bits,
                feasible=result.feasible,
                candidate_objective=str(value),
                objective=None if self.best_value is None else str(self.best_value),
                calls=self.calls,
                elapsed_s=perf_counter() - self.start,
            )
        )
        return result

    def sampled_search(self):
        n = len(self.instance.variable_labels)
        if self.method in QUANTUM_METHODS and n > self.budget.max_qubits:
            raise ValueError("Native variable count exceeds the declared quantum cap")
        if self.method == "conflict_qaoa":
            conflict_masks(self.instance)
        try:
            self.initial = self.descend((0,) * n)
        except _EnergyBudget:
            self.initial = min(self.pool, key=lambda b: (self.pool[b], b))
        # Every method receives the same seed, but not extra proposals searched
        # while preparing it. Its preparation cost remains charged to every run.
        initial_energy = self.pool[self.initial]
        self.pool = {self.initial: initial_energy}
        if self.method in QUANTUM_METHODS:
            self.quantum_search()
        else:
            self.classical_search()
        self.check()
        for bits in sorted(self.pool, key=lambda b: (self.pool[b], b))[
            : self.budget.validator_calls
        ]:
            self.validate(bits)

    def classical_search(self):
        n = len(self.initial)
        try:
            if self.method == "multistart":
                while True:
                    self.descend(tuple(int(b) for b in self.rng.integers(0, 2, n)))
            elif self.method == "annealing":
                bits, value = self.initial, self.pool[self.initial]
                scale = max(1.0, sum(abs(v) for v in self.instance.qubo.linear) / n)
                while True:
                    i = int(self.rng.integers(n))
                    trial = bits[:i] + (1 - bits[i],) + bits[i + 1 :]
                    energy = self.propose(trial)
                    fraction = self.energy_calls / self.budget.energy_evaluations
                    temperature = scale * 0.01**fraction
                    if energy <= value or self.rng.random() < math.exp(
                        min(0.0, (value - energy) / temperature)
                    ):
                        bits, value = trial, energy
            else:
                for _ in range(self.budget.shots):
                    bits = tuple(int(b) for b in self.rng.integers(0, 2, n))
                    self.propose(bits)
                    self.counts[bits] = self.counts.get(bits, 0) + 1
        except _EnergyBudget:
            pass

    def quantum_search(self):
        simulator = _LocalStatevector(
            self.instance, self.method, self.initial, self.budget.max_qubits
        )
        best_angles = np.zeros(2 * self.budget.depth)
        best_energy, best_probabilities = math.inf, None
        self.basis_work += len(simulator.energies)
        for iteration in range(self.budget.tuning_evaluations):
            self.check()
            if iteration == 0:
                angles = best_angles.copy()
            elif iteration < self.budget.tuning_evaluations // 2:
                angles = self.rng.uniform(-np.pi, np.pi, len(best_angles))
            else:
                angles = best_angles + self.rng.normal(0, 0.5, len(best_angles))
            probabilities = simulator.probabilities(
                angles[: self.budget.depth], angles[self.budget.depth :]
            )
            self.quantum_executed = True
            self.basis_work += len(probabilities)
            energy = float(probabilities @ simulator.energies)
            if energy < best_energy:
                best_energy, best_angles, best_probabilities = (
                    energy,
                    angles,
                    probabilities,
                )
        self.check()
        self.angles = best_angles.tolist()
        counts = self.rng.multinomial(self.budget.shots, best_probabilities)
        for index in np.flatnonzero(counts):
            bits = tuple((int(index) >> i) & 1 for i in range(simulator.n))
            self.counts[bits] = int(counts[index])
        try:
            for bits in sorted(self.counts, key=lambda b: (-self.counts[b], b)):
                self.propose(bits)
        except _EnergyBudget:
            pass


def run_contribution_method(instance, method, *, budget=ContributionBudget(), seed=17):
    """Bound search, independently validate candidates and return cost/quality evidence."""
    if method not in METHODS or type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Unknown method or invalid seed")
    budget = ContributionBudget.model_validate(budget.model_dump())
    search = _Search(instance, method, budget, seed)
    status, reason = "infeasible", "no_validated_candidate"
    try:
        if method == "milp":
            # The native solver's mandatory independent validation is counted once.
            solve_milp(
                replace(instance, evaluate=search.validate), time_limit_s=budget.wall_s
            )
        else:
            search.sampled_search()
        search.check()
        if search.best is not None:
            status, reason = "feasible", "independent_validation"
    except _Deadline:
        status, reason = "deadline", "late_result_not_accepted"
        search.best = search.best_value = None
    except ValueError as error:
        if method not in QUANTUM_METHODS:
            raise
        status, reason = "unsupported", str(error)
        search.best = search.best_value = None
    return dict(
        method=method,
        seed=seed,
        status=status,
        reason=reason,
        bits=search.best,
        objective=None if search.best_value is None else str(search.best_value),
        elapsed_s=perf_counter() - search.start,
        validation_s=search.validation_s,
        validator_calls=search.calls,
        energy_evaluations=search.energy_calls,
        simulator_basis_evaluations=search.basis_work,
        unique_proposals=len(search.pool),
        proposal_counts={
            "".join(map(str, b)): c for b, c in sorted(search.counts.items())
        },
        trace=search.trace,
        initial_bits=search.initial,
        angles=search.angles,
        best_origin="common_warm_start"
        if search.best is not None and search.best == search.initial
        else "method_proposal",
        quantum_executed=search.quantum_executed,
        hardware_jobs=0,
        execution_mode="noiseless_simulator"
        if search.quantum_executed
        else "classical",
    )


def contribution_verdict(records):
    """Compare held-out paired runs; simulator evidence cannot certify QPU advantage."""
    applications = {}
    for app in sorted({r["application"] for r in records if r["split"] == "held_out"}):
        paired = [
            r for r in records if r["application"] == app and r["split"] == "held_out"
        ]
        applications[app] = {}
        for variant in QUANTUM_METHODS:
            rows, missing, inferior = [], False, False
            for record in paired:
                methods = {m["method"]: m for m in record["methods"]}
                q = methods.get(variant)
                classical = [methods.get(m) for m in METHODS[:4]]
                if (
                    q is None
                    or q["status"] == "unsupported"
                    or any(m is None for m in classical)
                ):
                    missing = True
                    continue
                feasible = [
                    m
                    for m in classical
                    if m["status"] == "feasible" and m["objective"] is not None
                ]
                if not feasible:
                    missing = True
                    continue
                target = max(Fraction(m["objective"]) for m in feasible)
                references = [m for m in feasible if Fraction(m["objective"]) == target]
                if (
                    q["status"] != "feasible"
                    or q["objective"] is None
                    or Fraction(q["objective"]) < target
                ):
                    inferior = True
                    continue

                def calls_to_target(run):
                    hits = [
                        t["calls"]
                        for t in run["trace"]
                        if t["objective"] is not None
                        and Fraction(t["objective"]) >= target
                    ]
                    return min(hits) if hits else run["validator_calls"]

                rows.append(
                    dict(
                        call_ratio=calls_to_target(q)
                        / max(1, min(calls_to_target(m) for m in references)),
                        time_ratio=q["elapsed_s"]
                        / max(1e-12, min(m["elapsed_s"] for m in references)),
                    )
                )
            call_ratio = (
                float(np.mean([r["call_ratio"] for r in rows])) if rows else None
            )
            time_ratio = (
                float(np.mean([r["time_ratio"] for r in rows])) if rows else None
            )
            if missing or not paired:
                verdict, reason = (
                    "inconclusive",
                    "missing_or_unsupported_paired_evidence",
                )
            elif inferior:
                verdict, reason = "no_go", "final_quality_or_deadline_failed"
            elif call_ratio is not None and call_ratio <= 0.8 and time_ratio <= 1.0:
                verdict, reason = "local_go", "local_assignment_check_gate_only"
            else:
                verdict, reason = (
                    "no_go",
                    "no_20_percent_check_saving_or_total_time_gain",
                )
            applications[app][variant] = dict(
                verdict=verdict,
                reason=reason,
                matched_pairs=len(rows),
                total_pairs=len(paired),
                mean_call_ratio=call_ratio,
                mean_time_ratio=time_ratio,
            )
    return dict(
        applications=applications,
        hardware_recommended=any(
            v["verdict"] == "local_go"
            for a in applications.values()
            for v in a.values()
        ),
        quantum_advantage=False,
        physics_savings="not_tested_precomputed_physics",
    )
