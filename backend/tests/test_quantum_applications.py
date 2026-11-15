"""R13 shared engine: references, QUBO audit, repair, search, decisions, gate.

Reference values are hand-computed on small toy instances built here, so the
engine is tested independently of the five application modules. Unbalanced
penalization follows Montanez-Barrera et al. (2023), lambda1 = 0.96 and
lambda2 = 0.0371.
"""

import itertools
import json
import math
from fractions import Fraction

import numpy as np
import pytest
from pydantic import ValidationError

from app.hybrid_benchmark import CriterionResult, Decision, Timing
from app.quantum_applications import (
    APPLICATIONS,
    HYBRID_SOLVER,
    MILP_SOLVER,
    NO_ADVANTAGE_STATEMENT,
    SCHEMA,
    SUBPROBLEM_MAX_QUBITS,
    UNBALANCED_LAMBDA_1,
    UNBALANCED_LAMBDA_2,
    ApplicationCriteria,
    ApplicationInstance,
    ApplicationSplit,
    BenchmarkConfig,
    Evaluation,
    ExecutionPolicy,
    InstanceRecord,
    MethodRun,
    MilpModel,
    Qubo,
    SearchConfig,
    audit_qubo,
    config_fingerprint,
    decide_application,
    execution_policy,
    qubo_energy,
    repair,
    run_benchmark,
    run_instance,
    solve_application,
    solve_exact,
    solve_milp,
    subproblem_energies,
    subproblem_search,
    unbalanced_penalty,
)

INF = math.inf
FAST = SearchConfig(evaluations=12, shots=64, steps=3)


# ------------------------------------------------------------ toy instances


def pair_toy(values=(3, 2, 2, 1), pairs=((0, 1), (2, 3)), application="comm_slots"):
    """max sum v_i x_i with at most one variable of every pair."""
    n = len(values)
    total = sum(abs(v) for v in values)
    strength = 1 + 2 * total

    def evaluate(bits):
        bits = tuple(bits)
        bad = [p for p in pairs if bits[p[0]] and bits[p[1]]]
        objective = Fraction(sum(v * b for v, b in zip(values, bits)))
        return Evaluation(
            feasible=not bad,
            violation=Fraction(len(bad)),
            objective=objective,
            metrics={"selected": sum(bits), "value": float(objective)},
            issues=tuple(f"pair:{a}-{b}" for a, b in bad),
            violating_variables=tuple(sorted({i for p in bad for i in p})),
        )

    qubo = Qubo(
        constant=0.0,
        linear=tuple(-float(v) for v in values),
        quadratic={p: float(strength) for p in pairs},
        exact=True,
        encoding="pairwise exact",
    )
    milp = MilpModel(
        objective=tuple(float(v) for v in values),
        integrality=(1,) * n,
        lower=(0.0,) * n,
        upper=(1.0,) * n,
        rows=tuple(({a: 1.0, b: 1.0}, -INF, 1.0) for a, b in pairs),
        decision_count=n,
    )
    return ApplicationInstance(
        application=application,
        instance_id=f"toy-{n}",
        variable_labels=tuple(f"x{i}" for i in range(n)),
        evaluate=evaluate,
        qubo=qubo,
        milp=milp,
        input_fingerprint="f" * 64,
        source_note="toy",
    )


def one_hot_toy(values=(1, 3, 2)):
    """Exactly one variable selected; the best is index 1."""
    n = len(values)
    strength = 1 + 2 * sum(values)

    def evaluate(bits):
        count = sum(bits)
        objective = Fraction(sum(v * b for v, b in zip(values, bits)))
        return Evaluation(
            feasible=count == 1,
            violation=Fraction(abs(count - 1)),
            objective=objective,
            metrics={"count": count},
            issues=() if count == 1 else (f"count:{count}",),
            violating_variables=() if count == 1 else tuple(range(n)),
        )

    linear = [-float(v) - strength for v in values]  # A(sum x - 1)^2 expanded
    quadratic = {(i, j): 2.0 * strength for i in range(n) for j in range(i + 1, n)}
    return ApplicationInstance(
        application="robust_plans",
        instance_id="one-hot",
        variable_labels=tuple(f"p{i}" for i in range(n)),
        evaluate=evaluate,
        qubo=Qubo(
            constant=float(strength),
            linear=tuple(linear),
            quadratic=quadratic,
            exact=True,
            encoding="one-hot exact",
        ),
        milp=MilpModel(
            objective=tuple(float(v) for v in values),
            integrality=(1,) * n,
            lower=(0.0,) * n,
            upper=(1.0,) * n,
            rows=(({i: 1.0 for i in range(n)}, 1.0, 1.0),),
            decision_count=n,
        ),
        input_fingerprint="e" * 64,
        source_note="toy",
    )


def replace(instance, **changes):
    fields = dict(instance.__dict__)
    fields.update(changes)
    return ApplicationInstance(**fields)


def brute_energy(qubo, bits):
    value = qubo.constant + sum(w * b for w, b in zip(qubo.linear, bits))
    return value + sum(q * bits[i] * bits[j] for (i, j), q in qubo.quadratic.items())


def all_bits(n):
    return [tuple(b) for b in itertools.product((0, 1), repeat=n)]


def stable(model):
    """Dump without wall-clock fields for determinism checks."""
    data = model.model_dump(mode="json")

    def strip(value):
        if isinstance(value, dict):
            return {
                k: strip(v)
                for k, v in value.items()
                if k not in {"timing", "elapsed_s"} and not k.endswith("_s")
            }
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    return strip(data)


# ------------------------------------------------------------ constants


def test_constants_match_the_pre_registration():
    assert APPLICATIONS == (
        "comm_slots",
        "science_activities",
        "relay_selection",
        "rover_routes",
        "robust_plans",
    )
    assert SCHEMA == "r13-application-benchmark-v1"
    assert SUBPROBLEM_MAX_QUBITS == 8
    assert UNBALANCED_LAMBDA_1 == 0.96 and UNBALANCED_LAMBDA_2 == 0.0371
    assert MILP_SOLVER != HYBRID_SOLVER
    assert "no quantum advantage" in NO_ADVANTAGE_STATEMENT


def test_search_config_defaults_are_the_r10_budgets():
    config = SearchConfig()
    assert (config.depth, config.evaluations, config.shots) == (2, 160, 512)
    assert config.max_free_variables == 8 and config.steps == 8
    assert config.candidates_per_step == 8 and config.repair_moves == 64
    assert config.noise.readout_flip == 0.02


@pytest.mark.parametrize(
    "changes",
    [
        {"depth": 0},
        {"depth": 4},
        {"max_free_variables": 9},
        {"max_free_variables": 0},
        {"shots": 0},
        {"steps": -1},
    ],
)
def test_search_config_rejects_out_of_range_budgets(changes):
    with pytest.raises(ValidationError):
        SearchConfig(**changes)


def test_benchmark_config_defaults():
    config = BenchmarkConfig(applications=(ApplicationSplit(application="comm_slots"),))
    split = config.applications[0]
    assert split.dev_seeds == (101, 102)
    assert split.held_out_seeds == (201, 202, 203, 204)
    assert config.qaoa_seeds == (17, 23, 42)
    assert config.exact_max_variables == 16 and config.milp_time_limit_s == 30.0
    with pytest.raises(ValidationError):
        BenchmarkConfig(applications=(ApplicationSplit(application="unknown"),))
    with pytest.raises(ValidationError):
        BenchmarkConfig(
            applications=(ApplicationSplit(application="comm_slots"),),
            exact_max_variables=17,
        )


# ------------------------------------------------------------ energies


def test_qubo_energy_matches_hand_expansion():
    toy = pair_toy()
    for bits in all_bits(4):
        assert qubo_energy(toy.qubo, bits) == pytest.approx(
            brute_energy(toy.qubo, bits), abs=1e-12
        )
    assert qubo_energy(toy.qubo, (1, 0, 1, 0)) == pytest.approx(-5.0)
    assert qubo_energy(toy.qubo, (1, 1, 0, 0)) == pytest.approx(-5.0 + 17.0)


def test_qubo_energy_rejects_wrong_length_and_non_binary():
    toy = pair_toy()
    with pytest.raises(ValueError):
        qubo_energy(toy.qubo, (1, 0, 1))
    with pytest.raises(ValueError):
        qubo_energy(toy.qubo, (1, 0, 2, 0))


def test_subproblem_energies_are_little_endian_over_free_variables():
    toy = pair_toy()
    fixed = (1, 0, 0, 1)
    free = (3, 1)
    energies = subproblem_energies(toy.qubo, fixed, free)
    assert energies.shape == (4,)
    for index in range(4):
        bits = list(fixed)
        for position, variable in enumerate(free):
            bits[variable] = (index >> position) & 1
        assert energies[index] == pytest.approx(brute_energy(toy.qubo, bits), abs=1e-12)


def test_subproblem_energies_validate_the_neighbourhood():
    toy = pair_toy()
    with pytest.raises(ValueError):
        subproblem_energies(toy.qubo, (0, 0, 0, 0), ())
    with pytest.raises(ValueError):
        subproblem_energies(toy.qubo, (0, 0, 0, 0), (0, 0))
    with pytest.raises(ValueError):
        subproblem_energies(toy.qubo, (0, 0, 0, 0), (4,))
    big = pair_toy(values=tuple(range(1, 11)), pairs=((0, 1),))
    with pytest.raises(ValueError):
        subproblem_energies(big.qubo, (0,) * 10, tuple(range(9)))


@pytest.mark.parametrize("capacity", [1.0, 2.5, 4.0])
def test_unbalanced_penalty_reproduces_its_definition(capacity):
    weights = {0: 1.0, 2: 2.0, 3: 1.5}
    strength = 7.0
    constant, linear, quadratic = unbalanced_penalty(weights, capacity, strength)
    assert set(linear) <= {0, 2, 3}
    assert all(i < j for i, j in quadratic)
    for bits in all_bits(4):
        h = 1 - sum(w / capacity * bits[i] for i, w in weights.items())
        expected = strength * (-UNBALANCED_LAMBDA_1 * h + UNBALANCED_LAMBDA_2 * h * h)
        value = constant + sum(linear.get(i, 0.0) * bits[i] for i in range(4))
        value += sum(q * bits[i] * bits[j] for (i, j), q in quadratic.items())
        assert value == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_unbalanced_penalty_rejects_invalid_capacity():
    with pytest.raises(ValueError):
        unbalanced_penalty({0: 1.0}, 0.0, 1.0)
    with pytest.raises(ValueError):
        unbalanced_penalty({0: 1.0}, -1.0, 1.0)
    with pytest.raises(ValueError):
        unbalanced_penalty({0: 1.0}, 1.0, -1.0)


# ------------------------------------------------------------ references


def test_exact_reference_finds_the_hand_optimum():
    result = solve_exact(pair_toy())
    assert result.method == "exact" and result.status == "optimal"
    assert result.bits == (1, 0, 1, 0)
    assert Fraction(result.objective) == 5
    assert result.objective_value == pytest.approx(5.0)
    assert result.assignments_checked == 16
    assert result.validator_calls == 16


def test_exact_reference_reports_infeasible_problems():
    toy = pair_toy()

    def never(bits):
        return Evaluation(
            feasible=False,
            violation=Fraction(1),
            objective=Fraction(0),
            metrics={},
            issues=("never",),
        )

    result = solve_exact(replace(toy, evaluate=never))
    assert result.status == "infeasible" and result.bits is None
    assert result.objective is None and result.objective_value is None


def test_exact_reference_rejects_too_many_variables():
    big = pair_toy(values=tuple(range(1, 18)), pairs=((0, 1),))
    with pytest.raises(ValueError):
        solve_exact(big)
    with pytest.raises(ValueError):
        solve_exact(pair_toy(), max_variables=3)


def test_milp_matches_exact_on_toys():
    for toy in (
        pair_toy(),
        one_hot_toy(),
        pair_toy((5, 4, 3, 2, 1, 1), ((0, 1), (1, 2), (3, 4), (4, 5))),
    ):
        exact = solve_exact(toy)
        milp = solve_milp(toy)
        assert milp.method == "milp" and milp.status == "optimal"
        assert Fraction(milp.objective) == Fraction(exact.objective)
        assert toy.evaluate(milp.bits).feasible
        assert milp.validator_calls >= 1


def test_milp_reports_infeasible_models():
    toy = one_hot_toy()
    impossible = MilpModel(
        objective=toy.milp.objective,
        integrality=toy.milp.integrality,
        lower=toy.milp.lower,
        upper=toy.milp.upper,
        rows=(({0: 1.0, 1: 1.0, 2: 1.0}, 4.0, INF),),
        decision_count=3,
    )
    result = solve_milp(replace(toy, milp=impossible))
    assert result.status == "infeasible" and result.bits is None


def test_milp_disagreeing_with_the_validator_fails_loudly():
    toy = pair_toy()
    loose = MilpModel(
        objective=toy.milp.objective,
        integrality=toy.milp.integrality,
        lower=toy.milp.lower,
        upper=toy.milp.upper,
        rows=(),
        decision_count=4,
    )
    with pytest.raises(RuntimeError):
        solve_milp(replace(toy, milp=loose))


def test_milp_objective_disagreeing_with_the_validator_fails_loudly():
    toy = pair_toy()
    skewed = MilpModel(
        objective=(3.0, 2.0, 2.0, 5.0),
        integrality=toy.milp.integrality,
        lower=toy.milp.lower,
        upper=toy.milp.upper,
        rows=toy.milp.rows,
        decision_count=4,
    )
    with pytest.raises(RuntimeError):
        solve_milp(replace(toy, milp=skewed))


# ------------------------------------------------------------ audit


def test_audit_of_an_exact_qubo():
    audit = audit_qubo(pair_toy())
    assert audit.variables == 4 and audit.assignments == 16
    assert audit.feasible_assignments == 9
    assert audit.feasible_energy_matches_objective is True
    assert audit.max_feasible_energy_error == pytest.approx(0.0, abs=1e-9)
    assert audit.ground_state_feasible and audit.ground_state_optimal
    assert audit.infeasible_below_optimum == 0


def test_audit_of_one_hot_qubo():
    audit = audit_qubo(one_hot_toy())
    assert audit.feasible_assignments == 3
    assert audit.feasible_energy_matches_objective
    assert audit.ground_state_optimal and audit.infeasible_below_optimum == 0


def test_audit_exposes_a_missing_penalty():
    toy = pair_toy()
    broken = Qubo(
        constant=0.0,
        linear=toy.qubo.linear,
        quadratic={},
        exact=False,
        encoding="penalty removed",
    )
    audit = audit_qubo(replace(toy, qubo=broken))
    assert audit.ground_state_feasible is False
    assert audit.ground_state_optimal is False
    # (1,1,1,1) -8, (1,1,1,0) -7, (1,1,0,1)/(1,0,1,1) -6 lie below the optimum -5.
    assert audit.infeasible_below_optimum == 4
    assert audit.feasible_energy_matches_objective is True


def test_audit_exposes_energy_mismatch():
    toy = pair_toy()
    shifted = Qubo(
        constant=0.5,
        linear=toy.qubo.linear,
        quadratic=toy.qubo.quadratic,
        exact=False,
        encoding="shifted",
    )
    audit = audit_qubo(replace(toy, qubo=shifted))
    assert audit.feasible_energy_matches_objective is False
    assert audit.max_feasible_energy_error == pytest.approx(0.5)
    assert audit.ground_state_optimal is True


def test_audit_rejects_too_many_variables():
    big = pair_toy(values=tuple(range(1, 18)), pairs=((0, 1),))
    with pytest.raises(ValueError):
        audit_qubo(big)


# ------------------------------------------------------------ repair


def test_repair_keeps_raw_evidence_and_reaches_feasibility():
    result = repair(pair_toy(), (1, 1, 1, 1))
    assert result.raw_bits == (1, 1, 1, 1) and result.raw_feasible is False
    assert Fraction(result.raw_violation) == 2
    assert result.feasible and Fraction(result.violation) == 0
    assert Fraction(result.objective) == 5 and result.bits == (1, 0, 1, 0)
    assert result.repair_moves == 2
    assert result.validator_calls > 0 and not result.budget_exhausted


def test_repair_improves_a_feasible_start_by_steepest_ascent():
    result = repair(pair_toy(), (0, 0, 0, 0))
    assert result.raw_feasible and result.repair_moves == 0
    assert result.improvement_moves == 2 and result.bits == (1, 0, 1, 0)


def test_repair_uses_swaps_when_no_single_flip_improves():
    result = repair(one_hot_toy(), (1, 0, 0))
    assert result.bits == (0, 1, 0) and Fraction(result.objective) == 3
    assert result.improvement_moves == 1


def test_repair_budget():
    exhausted = repair(pair_toy(), (1, 1, 1, 1), max_moves=0)
    assert exhausted.bits == (1, 1, 1, 1) and exhausted.budget_exhausted
    assert exhausted.feasible is False and exhausted.objective is None
    optimal = repair(pair_toy(), (1, 0, 1, 0), max_moves=0)
    assert optimal.feasible and not optimal.budget_exhausted
    with pytest.raises(ValueError):
        repair(pair_toy(), (1, 0, 1, 0), max_moves=-1)
    with pytest.raises(ValueError):
        repair(pair_toy(), (1, 0, 1))


# ------------------------------------------------------------ search


@pytest.mark.parametrize(
    "sampler,mode",
    [
        ("qaoa", "noiseless_simulator"),
        ("qaoa_noise", "noise_model"),
        ("random", "classical"),
        ("exact", "classical"),
    ],
)
def test_search_on_a_small_instance(sampler, mode):
    toy = pair_toy()
    result = subproblem_search(toy, sampler=sampler, seed=17, config=FAST)
    assert result.sampler == sampler and result.execution_mode == mode
    assert result.status == "feasible" and Fraction(result.objective) == 5
    assert toy.evaluate(result.bits).feasible
    assert result.hardware_jobs == 0
    assert len(result.steps) == FAST.steps
    # n <= 8: every neighbourhood frees all variables.
    assert all(step.free_variables == (0, 1, 2, 3) for step in result.steps)
    assert all(step.qubits == 4 for step in result.steps)
    assert result.max_qubits == 4
    assert 0 <= result.raw_feasible_rate <= 1
    assert result.validator_calls > 0
    assert result.timing.total_s >= result.timing.tuning_s >= 0
    if sampler in ("qaoa", "qaoa_noise"):
        assert result.cx_gates > 0
    else:
        assert result.cx_gates == 0
    if sampler == "exact":
        # 9 of the 16 sub-assignments are feasible.
        assert result.steps[0].raw_feasible_fraction == pytest.approx(9 / 16)


def test_search_shots_and_raw_rates_for_sampled_methods():
    toy = pair_toy()
    for sampler in ("qaoa", "qaoa_noise", "random"):
        result = subproblem_search(toy, sampler=sampler, seed=5, config=FAST)
        assert result.samples_drawn == FAST.shots * FAST.steps
        assert all(step.shots == FAST.shots for step in result.steps)
        weighted = sum(s.raw_feasible_fraction * s.shots for s in result.steps)
        assert result.raw_feasible_rate == pytest.approx(
            weighted / result.samples_drawn
        )


def test_noiseless_qaoa_concentrates_on_feasible_samples_more_than_random():
    toy = pair_toy()
    config = SearchConfig(steps=1)
    qaoa = subproblem_search(toy, sampler="qaoa", seed=17, config=config)
    random = subproblem_search(toy, sampler="random", seed=17, config=config)
    assert qaoa.raw_feasible_rate > random.raw_feasible_rate


def test_search_is_deterministic_for_a_seed():
    toy = pair_toy(
        (5, 4, 3, 3, 2, 2, 1, 1, 1, 1), ((0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (1, 2))
    )
    for sampler in ("qaoa", "qaoa_noise", "random", "exact"):
        first = subproblem_search(toy, sampler=sampler, seed=23, config=FAST)
        second = subproblem_search(toy, sampler=sampler, seed=23, config=FAST)
        assert stable(first) == stable(second)


def test_large_instances_use_bounded_neighbourhoods():
    values = tuple(range(12, 0, -1))
    pairs = tuple((i, i + 1) for i in range(0, 12, 2)) + ((1, 2), (5, 6))
    toy = pair_toy(values, pairs)
    config = SearchConfig(evaluations=12, shots=64, steps=4, max_free_variables=5)
    result = subproblem_search(toy, sampler="qaoa", seed=42, config=config)
    assert result.status == "feasible"
    assert all(1 <= len(step.free_variables) <= 5 for step in result.steps)
    assert all(
        len(set(step.free_variables)) == len(step.free_variables)
        for step in result.steps
    )
    assert all(step.qubits == len(step.free_variables) for step in result.steps)
    assert result.max_qubits <= 5
    initial = Fraction(result.initial.objective)
    assert Fraction(result.objective) >= initial
    exact = solve_exact(toy)
    assert Fraction(result.objective) <= Fraction(exact.objective)


def test_accepted_steps_strictly_improve_the_incumbent():
    values = tuple(range(12, 0, -1))
    pairs = tuple((i, i + 1) for i in range(11))
    toy = pair_toy(values, pairs)
    result = subproblem_search(
        toy, sampler="exact", seed=3, config=SearchConfig(steps=6, max_free_variables=4)
    )
    key = (Fraction(result.initial.violation), -Fraction(result.initial.objective or 0))
    for step in result.steps:
        proposal = (
            Fraction(step.proposal_violation),
            -Fraction(step.proposal_objective or 0),
        )
        if step.accepted:
            assert proposal < key
            key = proposal


def test_infeasible_start_neighbourhoods_target_violations():
    values = tuple(range(12, 0, -1))
    pairs = tuple((i, i + 1) for i in range(11))
    toy = pair_toy(values, pairs)
    # No repair moves: the all-zero start stays, which is feasible; with a
    # broken validator that flags variables 0 and 1 the search must free them.
    flagged = toy.evaluate

    def evaluate(bits):
        base = flagged(bits)
        if bits[0] == 0 and bits[1] == 0:
            return Evaluation(
                feasible=False,
                violation=Fraction(1),
                objective=base.objective,
                metrics=base.metrics,
                issues=("needs:0-or-1",),
                violating_variables=(0, 1),
            )
        return base

    result = subproblem_search(
        replace(toy, evaluate=evaluate),
        sampler="exact",
        seed=1,
        config=SearchConfig(steps=1, repair_moves=0, max_free_variables=4),
    )
    step = result.steps[0]
    assert step.selection_reason == "violation"
    assert {0, 1} <= set(step.free_variables)


def test_search_rejects_invalid_inputs():
    toy = pair_toy()
    with pytest.raises(ValueError):
        subproblem_search(toy, sampler="annealing", seed=1)
    with pytest.raises(ValueError):
        subproblem_search(toy, sampler="qaoa", seed=-1)


# ------------------------------------------------------------ records and decisions


def timing(total):
    return Timing(total_s=total)


def run(method, mode, gap, total, raw=None, feasible=True):
    return MethodRun(
        method=method,
        execution_mode=mode,
        seed=17,
        status="feasible" if feasible else "infeasible",
        bits=(1, 0) if feasible else None,
        objective="1" if feasible else None,
        objective_value=1.0 if feasible else None,
        optimum_gap_value=gap if feasible else None,
        metrics={},
        raw_feasible_rate=raw,
        validator_calls=1,
        timing=timing(total),
    )


def record(methods, split="held_out", application="comm_slots", optimum=1.0, seed=201):
    return InstanceRecord(
        application=application,
        split=split,
        seed=seed,
        instance_id=f"i{seed}",
        input_fingerprint="a" * 64,
        variables=2,
        qubo_exact=True,
        qubo_encoding="toy",
        audit=None,
        optimum_source="exact" if optimum is not None else "none",
        optimum=None if optimum is None else "1",
        optimum_value=optimum,
        rejections=(),
        source_note="toy",
        methods=tuple(methods),
    )


def good_methods(hybrid_total=0.1, hybrid_gap=0.0, raw=0.6, feasible=True):
    return [
        run("milp", "classical", 0.0, 1.0),
        run("lns_qaoa", "noiseless_simulator", hybrid_gap, hybrid_total, raw, feasible),
        run("lns_qaoa", "noise_model", 0.0, 0.1, 0.2),
        run("lns_random", "classical", 0.0, 0.1, 0.3),
    ]


CRITERIA = ApplicationCriteria(application="comm_slots")


def names(decision):
    return {c.name: c for c in decision.criteria}


def test_decision_go_when_every_criterion_passes():
    decision = decide_application((record(good_methods()),), CRITERIA)
    assert decision.verdict == "go" and decision.application == "comm_slots"
    assert decision.retained_solver == HYBRID_SOLVER
    assert set(names(decision)) == {
        "validated_feasibility",
        "objective_quality",
        "raw_feasibility_vs_random",
        "repaired_quality_vs_random",
        "end_to_end_runtime",
    }
    assert all(c.passed for c in decision.criteria)
    assert NO_ADVANTAGE_STATEMENT in decision.statement


@pytest.mark.parametrize(
    "methods,failed",
    [
        (good_methods(hybrid_total=1.5), "end_to_end_runtime"),
        (good_methods(hybrid_gap=0.5), "objective_quality"),
        (good_methods(raw=0.3), "raw_feasibility_vs_random"),
        (good_methods(feasible=False), "validated_feasibility"),
    ],
)
def test_decision_no_go_when_a_criterion_fails(methods, failed):
    decision = decide_application((record(methods),), CRITERIA)
    assert decision.verdict == "no_go"
    assert decision.retained_solver == MILP_SOLVER
    assert names(decision)[failed].passed is False


def test_decision_compares_repaired_quality_with_random():
    methods = good_methods(hybrid_gap=0.0)
    methods[3] = run("lns_random", "classical", 0.0, 0.1, 0.3)
    methods[0] = run("milp", "classical", 0.5, 1.0)  # MILP gap 0.5 (time limit)
    methods[1] = run("lns_qaoa", "noiseless_simulator", 0.25, 0.1, 0.6)
    decision = decide_application((record(methods),), CRITERIA)
    criteria = names(decision)
    assert criteria["objective_quality"].passed
    assert criteria["objective_quality"].measured == pytest.approx(-0.25)
    assert criteria["repaired_quality_vs_random"].passed is False
    assert criteria["repaired_quality_vs_random"].measured == pytest.approx(0.25)


def test_decision_ignores_dev_other_applications_and_unknown_optima():
    bad = good_methods(hybrid_total=50.0)
    records = (
        record(good_methods()),
        record(bad, split="dev", seed=101),
        record(bad, application="science_activities", seed=202),
        record(bad, optimum=None, seed=203),
    )
    assert decide_application(records, CRITERIA).verdict == "go"


def test_decision_without_held_out_evidence_is_no_go():
    decision = decide_application((record(good_methods(), split="dev"),), CRITERIA)
    assert decision.verdict == "no_go"
    assert all(c.measured is None for c in decision.criteria)


def test_runtime_ratio_measurement():
    decision = decide_application((record(good_methods(hybrid_total=0.5)),), CRITERIA)
    runtime = names(decision)["end_to_end_runtime"]
    assert runtime.measured == pytest.approx(0.5) and runtime.threshold == 1.0


def decision(application, verdict):
    return Decision(
        application=application,
        verdict=verdict,
        criteria=(
            CriterionResult(
                name="x",
                passed=verdict == "go",
                measured=None,
                threshold=None,
                explanation="x",
            ),
        ),
        retained_solver=HYBRID_SOLVER if verdict == "go" else MILP_SOLVER,
        statement=NO_ADVANTAGE_STATEMENT,
    )


def test_execution_policy_enables_quantum_only_for_go():
    policy = execution_policy(
        (decision("comm_slots", "go"), decision("science_activities", "no_go"))
    )
    assert set(policy.quantum_enabled) == set(APPLICATIONS)
    assert policy.quantum_enabled["comm_slots"] is True
    assert policy.quantum_enabled["science_activities"] is False
    assert policy.quantum_enabled["robust_plans"] is False
    assert policy.retained_solver["comm_slots"] == HYBRID_SOLVER
    assert policy.retained_solver["robust_plans"] == MILP_SOLVER
    assert all(policy.reasons[a] for a in APPLICATIONS)


def test_execution_policy_rejects_duplicate_or_unknown_decisions():
    with pytest.raises(ValueError):
        execution_policy(
            (decision("comm_slots", "go"), decision("comm_slots", "no_go"))
        )
    with pytest.raises(ValueError):
        execution_policy((decision("unknown", "go"),))


def test_solver_gate_keeps_milp_for_no_go():
    toy = pair_toy()
    policy = execution_policy((decision("comm_slots", "no_go"),))
    result = solve_application(toy, policy, config=FAST)
    assert result.solver == MILP_SOLVER and result.quantum_executed is False
    assert result.execution_mode == "classical" and result.status == "feasible"
    assert Fraction(result.objective) == 5


def test_solver_gate_without_decision_keeps_milp():
    result = solve_application(pair_toy(), execution_policy(()), config=FAST)
    assert result.quantum_executed is False and result.solver == MILP_SOLVER


def test_solver_gate_runs_validated_qaoa_only_for_go():
    toy = pair_toy()
    policy = execution_policy((decision("comm_slots", "go"),))
    result = solve_application(toy, policy, config=FAST)
    assert result.quantum_executed is True
    assert result.solver == HYBRID_SOLVER
    assert result.execution_mode == "noiseless_simulator"
    assert toy.evaluate(result.bits).feasible


def test_solver_gate_falls_back_to_milp_when_qaoa_finds_nothing_valid():
    toy = one_hot_toy()

    def evaluate(bits):
        base = one_hot_toy().evaluate(bits)
        if bits == (0, 1, 0) or bits == (1, 0, 0):
            return base
        return Evaluation(
            feasible=False,
            violation=Fraction(1) + base.violation,
            objective=base.objective,
            metrics={},
            issues=("restricted",),
            violating_variables=(0, 1, 2),
        )

    restricted = replace(toy, evaluate=evaluate)
    policy = ExecutionPolicy(
        quantum_enabled={"robust_plans": True},
        retained_solver={"robust_plans": HYBRID_SOLVER},
        reasons={"robust_plans": "test"},
    )
    config = SearchConfig(evaluations=4, shots=8, steps=0, repair_moves=0)
    result = solve_application(restricted, policy, config=config)
    assert result.quantum_executed is True
    assert result.solver == MILP_SOLVER and result.status == "feasible"
    assert result.execution_mode == "classical"
    assert result.reason


# ------------------------------------------------------------ benchmark runner


TINY = BenchmarkConfig(
    applications=(
        ApplicationSplit(
            application="comm_slots", dev_seeds=(1,), held_out_seeds=(2, 3)
        ),
        ApplicationSplit(application="robust_plans", dev_seeds=(), held_out_seeds=(4,)),
    ),
    qaoa_seeds=(17, 23),
    search=FAST,
)


def toy_builders():
    def comm(seed):
        values = (3 + seed, 2, 2, 1, seed % 3 + 1)
        built = pair_toy(values, ((0, 1), (2, 3), (3, 4)))
        return replace(
            built, instance_id=f"comm-{seed}", input_fingerprint=f"{seed:064d}"
        )

    def robust(seed):
        built = one_hot_toy((1, seed, 2))
        return replace(
            built, instance_id=f"robust-{seed}", input_fingerprint=f"{seed + 50:064d}"
        )

    return {"comm_slots": comm, "robust_plans": robust}


@pytest.fixture(scope="module")
def tiny_report():
    return run_benchmark(TINY, toy_builders())


def test_run_instance_methods_and_gaps():
    config = TINY
    record = run_instance(pair_toy(), split="held_out", seed=9, config=config)
    labels = [(m.method, m.execution_mode, m.seed) for m in record.methods]
    assert labels[:2] == [("milp", "classical", None), ("exact", "classical", None)]
    for seed in (17, 23):
        for label in (
            ("lns_qaoa", "noiseless_simulator", seed),
            ("lns_qaoa", "noise_model", seed),
            ("lns_random", "classical", seed),
            ("lns_exact", "classical", seed),
        ):
            assert label in labels
    assert len(record.methods) == 2 + 4 * 2
    assert record.optimum_source == "exact" and record.optimum_value == 5.0
    assert record.audit is not None and record.qubo_exact
    for method in record.methods:
        assert method.hardware_jobs == 0
        assert method.status == "feasible"
        assert method.optimum_gap_value == pytest.approx(0.0)
        assert method.metrics["value"] == pytest.approx(5.0)
    lns = [m for m in record.methods if m.method.startswith("lns")]
    assert all(m.raw_feasible_rate is not None and m.steps == FAST.steps for m in lns)


def test_run_instance_uses_milp_optimum_beyond_the_exact_limit():
    values = tuple(range(18, 0, -1))
    pairs = tuple((i, i + 1) for i in range(0, 18, 2))
    record = run_instance(pair_toy(values, pairs), split="dev", seed=1, config=TINY)
    assert record.optimum_source == "milp" and record.audit is None
    assert "exact" not in {m.method for m in record.methods}
    assert record.optimum_value == pytest.approx(sum(values[0::2]))


def test_report_structure(tiny_report):
    report = tiny_report
    assert report.schema_version == SCHEMA
    assert len(report.instances) == 4
    assert [(i.application, i.split, i.seed) for i in report.instances] == [
        ("comm_slots", "dev", 1),
        ("comm_slots", "held_out", 2),
        ("comm_slots", "held_out", 3),
        ("robust_plans", "held_out", 4),
    ]
    assert [d.application for d in report.decisions] == ["comm_slots", "robust_plans"]
    assert all(d.evaluated_split == "held_out" for d in report.decisions)
    for d in report.decisions:
        assert report.policy.quantum_enabled[d.application] is (d.verdict == "go")
    assert report.policy.quantum_enabled["rover_routes"] is False
    assert len(report.config_fingerprint) == 64
    assert report.config_fingerprint == config_fingerprint(TINY)
    assert NO_ADVANTAGE_STATEMENT in report.statement
    assert {"python", "numpy", "scipy"} <= set(report.environment)
    json.loads(report.model_dump_json())


def test_report_is_deterministic_apart_from_timing(tiny_report):
    again = run_benchmark(TINY, toy_builders())
    left = stable(tiny_report)
    right = stable(again)
    for side in (left, right):
        for item in side["decisions"]:
            item["criteria"] = [
                c for c in item["criteria"] if c["name"] != "end_to_end_runtime"
            ]
            item.pop("verdict")
            item.pop("retained_solver")
        side.pop("policy")
        side.pop("environment")
    assert left == right


def test_config_fingerprint_changes_with_the_configuration():
    other = TINY.model_copy(update={"qaoa_seeds": (17,)})
    assert config_fingerprint(other) != config_fingerprint(TINY)
    assert config_fingerprint(TINY) == config_fingerprint(
        BenchmarkConfig.model_validate(TINY.model_dump())
    )


def test_runner_rejects_missing_builders_and_leaks():
    with pytest.raises(ValueError):
        run_benchmark(TINY, {"comm_slots": toy_builders()["comm_slots"]})

    def leaky(seed):
        return replace(pair_toy(), instance_id="same")

    config = BenchmarkConfig(
        applications=(
            ApplicationSplit(
                application="comm_slots", dev_seeds=(1,), held_out_seeds=(2,)
            ),
        ),
        qaoa_seeds=(17,),
        search=FAST,
    )
    with pytest.raises(ValueError):
        run_benchmark(config, {"comm_slots": leaky})

    def wrong_application(seed):
        return replace(
            pair_toy(application="rover_routes"), input_fingerprint=f"{seed:064d}"
        )

    with pytest.raises(ValueError):
        run_benchmark(config, {"comm_slots": wrong_application})


def test_physical_sanity_of_rates_and_gaps(tiny_report):
    for instance in tiny_report.instances:
        for method in instance.methods:
            if method.raw_feasible_rate is not None:
                assert 0 <= method.raw_feasible_rate <= 1
            if method.optimum_gap_value is not None:
                assert method.optimum_gap_value >= 0
            assert method.timing.total_s >= 0
            assert method.timing.queue_s is None
    assert np.isfinite(
        [d.criteria[0].measured or 0 for d in tiny_report.decisions]
    ).all()
