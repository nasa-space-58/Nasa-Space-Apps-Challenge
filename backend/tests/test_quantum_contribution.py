"""Independent small-graph identities and native experiment acceptance rules."""

import itertools
import math
from dataclasses import replace
from fractions import Fraction

import numpy as np
import pytest
from pydantic import ValidationError

from app.application_comm_slots import build_comm_slots, generate_comm_slots
from app.quantum_applications import (
    ApplicationInstance,
    Evaluation,
    Qubo,
    milp_model,
)
from app.quantum_contribution import (
    METHODS,
    QUANTUM_METHODS,
    ContributionBudget,
    conflict_masks,
    contribution_verdict,
    ising_coefficients,
    quantum_distribution,
    run_contribution_method,
)
from app.run_quantum_contribution import published_protocol, run_protocol


def toy():
    # Max weight independent set of path 0-1-2: {0,2}, value 2+4=6.
    def evaluate(bits):
        violation = bits[0] * bits[1] + bits[1] * bits[2]
        return Evaluation(
            violation == 0,
            Fraction(violation),
            Fraction(sum(b * v for b, v in zip(bits, (2, 3, 4)))),
            {},
        )

    return ApplicationInstance(
        application="comm_slots",
        instance_id="path-three",
        variable_labels=("a", "b", "c"),
        evaluate=evaluate,
        qubo=Qubo(0.0, (-2.0, -3.0, -4.0), {(0, 1): 20.0, (1, 2): 20.0}, True, "exact"),
        milp=milp_model(
            (2, 3, 4),
            (({0: 1, 1: 1}, -math.inf, 1), ({1: 1, 2: 1}, -math.inf, 1)),
            decision_count=3,
        ),
        input_fingerprint="a" * 64,
        source_note="hand-computed test",
    )


def test_ising_coefficients_match_every_binary_assignment():
    q = toy().qubo
    constant, z, zz = ising_coefficients(q)
    for bits in itertools.product((0, 1), repeat=3):
        spin = [1 - 2 * b for b in bits]
        original = q.constant + sum(v * b for v, b in zip(q.linear, bits))
        original += sum(v * bits[i] * bits[j] for (i, j), v in q.quadratic.items())
        actual = constant + sum(a * b for a, b in zip(z, spin))
        actual += sum(v * spin[i] * spin[j] for (i, j), v in zz.items())
        assert actual == pytest.approx(original, abs=1e-12)


def test_conflicts_certified_from_native_constraints():
    assert conflict_masks(toy()) == (2, 5, 2)
    instance = build_comm_slots(
        generate_comm_slots(
            701, missions=2, requests_per_mission=1, stations=1, slots=3
        )
    )
    masks = conflict_masks(instance)
    for index in range(2 ** len(masks)):
        bits = tuple((index >> i) & 1 for i in range(len(masks)))
        permitted = not any(bits[i] and index & mask for i, mask in enumerate(masks))
        assert permitted == instance.evaluate(bits).feasible


@pytest.mark.parametrize(
    "row",
    [({0: 1, 1: 1}, -math.inf, 2), ({0: 1, 1: 2}, -math.inf, 1), ({0: 1, 1: 1}, 1, 1)],
)
def test_unsupported_constraints_are_not_silently_removed(row):
    instance = toy()
    changed = replace(instance, milp=replace(instance.milp, rows=(row,)))
    with pytest.raises(ValueError):
        conflict_masks(changed)
    result = run_contribution_method(changed, "conflict_qaoa")
    assert result["status"] == "unsupported"
    assert result["quantum_executed"] is False


@pytest.mark.parametrize("variant", QUANTUM_METHODS)
def test_distribution_normalized_nonnegative_and_repeatable(variant):
    kwargs = dict(variant=variant, initial=(1, 0, 1))
    p = quantum_distribution(toy(), (0.4, -0.2), (0.7, 0.1), **kwargs)
    assert len(p) == 8
    assert np.all(p >= 0)
    assert sum(p) == pytest.approx(1.0, abs=1e-12)
    assert np.allclose(
        p, quantum_distribution(toy(), (0.4, -0.2), (0.7, 0.1), **kwargs), atol=1e-12
    )


def test_conflict_mixer_never_populates_forbidden_states():
    p = quantum_distribution(
        toy(), (0.5, 0.8), (0.7, -0.3), variant="conflict_qaoa", initial=(1, 0, 1)
    )
    assert sum(p[i] for i in (3, 6, 7)) == pytest.approx(0.0, abs=1e-14)
    with pytest.raises(ValueError):
        quantum_distribution(
            toy(), (0.5,), (0.7,), variant="conflict_qaoa", initial=(1, 1, 1)
        )


def test_warm_product_state_and_matching_mixer():
    initial = (1, 0, 1)
    kwargs = dict(variant="warm_qaoa", initial=initial)
    p = quantum_distribution(toy(), (0.0,), (0.0,), **kwargs)
    expected = [
        math.prod(0.8 if ((i >> j) & 1) == initial[j] else 0.2 for j in range(3))
        for i in range(8)
    ]
    assert np.allclose(p, expected, atol=1e-12)
    # Product state is an eigenstate of the matching mixer when cost angle is zero.
    assert np.allclose(
        p, quantum_distribution(toy(), (0.0,), (1.1,), **kwargs), atol=1e-12
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(variant="unknown", initial=(0, 0, 0)),
        dict(variant="qaoa", initial=(0, 0)),
        dict(variant="qaoa", initial=(0, 2, 0)),
        dict(variant="qaoa", initial=(0, 0, 0), max_qubits=2),
    ],
)
def test_bad_distribution_inputs(kwargs):
    with pytest.raises(ValueError):
        quantum_distribution(toy(), (0.2,), (0.4,), **kwargs)


def test_nonfinite_and_mismatched_angles():
    for gamma, beta in [((float("nan"),), (0.0,)), ((0.0,), ())]:
        with pytest.raises(ValueError):
            quantum_distribution(toy(), gamma, beta, variant="qaoa", initial=(0, 0, 0))


@pytest.mark.parametrize("method", METHODS)
def test_all_methods_return_independently_validated_bounded_result(method):
    budget = ContributionBudget(
        wall_s=10.0,
        energy_evaluations=128,
        tuning_evaluations=4,
        validator_calls=8,
        shots=32,
    )
    result = run_contribution_method(toy(), method, budget=budget, seed=17)
    assert result["status"] == "feasible"
    evaluation = toy().evaluate(result["bits"])
    assert evaluation.feasible
    assert Fraction(result["objective"]) == evaluation.objective <= 6
    assert result["validator_calls"] <= 8
    assert result["hardware_jobs"] == 0
    assert result["elapsed_s"] >= result["validation_s"] >= 0
    if method == "milp":
        assert result["bits"] == (1, 0, 1)
        assert result["objective"] == "6"
    if method in QUANTUM_METHODS:
        assert result["execution_mode"] == "noiseless_simulator"
        assert result["simulator_basis_evaluations"] > 0


def test_validator_budget_is_real_and_caches_are_per_run():
    calls = []
    instance = toy()
    original = instance.evaluate

    def observe(bits):
        calls.append(bits)
        return original(bits)

    instance = replace(instance, evaluate=observe)
    budget = ContributionBudget(validator_calls=1, shots=64, tuning_evaluations=2)
    first = run_contribution_method(instance, "warm_qaoa", budget=budget)
    assert len(calls) == first["validator_calls"] == 1
    run_contribution_method(instance, "warm_qaoa", budget=budget)
    assert len(calls) == 2


def test_seed_determinism_excluding_timing():
    budget = ContributionBudget(wall_s=10.0, shots=32, tuning_evaluations=3)
    for method in ("annealing", "multistart", "qaoa", "warm_qaoa", "conflict_qaoa"):
        a = run_contribution_method(toy(), method, budget=budget, seed=99)
        b = run_contribution_method(toy(), method, budget=budget, seed=99)
        for key in ("bits", "objective", "validator_calls", "proposal_counts"):
            assert a[key] == b[key]


def test_deadline_does_not_accept_late_validator(monkeypatch):
    import app.quantum_contribution as module

    now = [0.0]
    monkeypatch.setattr(module, "perf_counter", lambda: now[0])
    instance = toy()
    original = instance.evaluate

    def late(bits):
        now[0] = 2.0
        return original(bits)

    result = run_contribution_method(
        replace(instance, evaluate=late),
        "random",
        budget=ContributionBudget(wall_s=1.0),
    )
    assert result["status"] == "deadline"
    assert result["bits"] is None and result["objective"] is None


def test_quantum_cap_does_not_disable_classical_solver():
    budget = ContributionBudget(max_qubits=2)
    assert (
        run_contribution_method(toy(), "qaoa", budget=budget)["status"] == "unsupported"
    )
    assert run_contribution_method(toy(), "milp", budget=budget)["status"] == "feasible"


@pytest.mark.parametrize(
    "changes",
    [
        dict(wall_s=0.0),
        dict(shots=0),
        dict(max_qubits=17),
        dict(validator_calls=True),
        dict(energy_evaluations=-1),
    ],
)
def test_invalid_budget(changes):
    with pytest.raises(ValidationError):
        ContributionBudget(**changes)


def test_invalid_method_and_seed():
    for method, seed in [("made_up", 1), ("random", -1), ("random", True)]:
        with pytest.raises(ValueError):
            run_contribution_method(toy(), method, seed=seed)


def record(*, split="held_out", q_score="6", q_calls=1, classical_calls=1):
    methods = []
    for method in METHODS:
        is_q = method in QUANTUM_METHODS
        score = q_score if is_q else "6"
        calls = q_calls if is_q else classical_calls
        methods.append(
            dict(
                method=method,
                status="feasible",
                objective=score,
                elapsed_s=0.5 if is_q else 1.0,
                validator_calls=calls,
                trace=[dict(objective=score, calls=calls, elapsed_s=0.4)],
            )
        )
    return dict(
        application="comm_slots", split=split, instance_id="x", seed=17, methods=methods
    )


def test_equal_final_answers_are_no_contribution():
    result = contribution_verdict([record()])
    assert not result["hardware_recommended"]
    assert not result["quantum_advantage"]
    assert result["physics_savings"] == "not_tested_precomputed_physics"
    assert all(
        r["verdict"] == "no_go" for r in result["applications"]["comm_slots"].values()
    )


def test_better_raw_sampling_cannot_hide_worse_final_quality():
    result = contribution_verdict([record(q_score="5", q_calls=1, classical_calls=10)])
    assert not result["hardware_recommended"]


def test_measured_local_contribution_is_not_hardware_advantage():
    result = contribution_verdict([record(q_calls=5, classical_calls=10)])
    assert result["hardware_recommended"]
    assert not result["quantum_advantage"]


def test_dev_and_missing_runs_cannot_manufacture_go():
    assert not contribution_verdict(
        [record(split="dev", q_calls=1, classical_calls=10)]
    )["hardware_recommended"]
    missing = record(q_calls=1, classical_calls=10)
    missing["methods"] = [
        m for m in missing["methods"] if m["method"] not in QUANTUM_METHODS
    ]
    report = contribution_verdict([record(), missing])
    assert all(
        r["verdict"] == "inconclusive"
        for r in report["applications"]["comm_slots"].values()
    )


def test_protocol_uses_fresh_disjoint_seeds_and_fixed_limits():
    protocol = published_protocol()
    assert protocol["version"] == "contribution-c1"
    assert set(protocol["instance_seeds"]["dev"]).isdisjoint(
        protocol["instance_seeds"]["held_out"]
    )
    assert protocol["instance_seeds"]["held_out"] == [711, 712, 713, 714]
    assert protocol["budget"] == ContributionBudget().model_dump(mode="json")
    assert protocol["methods"] == list(METHODS)
    with pytest.raises(ValueError):
        run_protocol(split="not-a-split")
