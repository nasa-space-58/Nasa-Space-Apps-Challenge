"""R10 repair, subproblem search, noise labelling, benchmark and go/no-go rules.

Exact expectations on the teaching example are derived by hand from its data:
four 1-hour slots on one capacity-1 antenna, science values 10/9/7/6 per slot,
score = science_value / 10. Probability tolerance is 1e-12 unless stated.
"""

import json
import math
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from app.hybrid_benchmark import (
    NO_ADVANTAGE_STATEMENT,
    BenchmarkConfig,
    BenchmarkReport,
    GoNoGoCriteria,
    InstanceRecord,
    MethodRecord,
    NoiseModel,
    RepairOutcome,
    SearchResult,
    Timing,
    apply_noise,
    compare_direct,
    decide_go_no_go,
    pauli_z_resources,
    repair_and_improve,
    run_benchmark,
    solve_subproblem_search,
    tune_qaoa,
)
from app.hybrid_planning import solve_exact, validate_plan
from app.hybrid_quantum import run_qaoa
from app.hybrid_scaling import (
    MILP_SOLVER_NAME,
    InstanceSpec,
    build_pairwise_model,
    compact_energies,
    generate_instance,
    resource_profile,
    solve_milp,
)
from app.run_r10_benchmark import published_config, write_report
from shared.hybrid import Scenario, fingerprint, load_scenario

EXAMPLES = Path("/workspace/shared/examples/hybrid")
if not EXAMPLES.exists():
    EXAMPLES = Path(__file__).resolve().parents[2] / "shared/examples/hybrid"

UTC_NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def example(name="shared-conflict"):
    return load_scenario(EXAMPLES / f"{name}.json")


def option(mission, slot):
    return f"mission-{mission}-option-{slot}"


def spec(**overrides):
    values = dict(
        family="bench",
        missions=6,
        options_per_mission=4,
        slots=6,
        antennas=2,
        incompatibility_probability=0.1,
        deadline_tightness=0.2,
        seed=21,
    )
    values.update(overrides)
    return InstanceSpec(**values)


def strip_timing(value):
    """Remove wall-clock fields, which are measured and not deterministic."""
    if isinstance(value, dict):
        return {
            key: strip_timing(item)
            for key, item in value.items()
            if not (key.endswith("_s") or key in ("timing", "generated_utc"))
        }
    if isinstance(value, list):
        return [strip_timing(item) for item in value]
    return value


# ---------------------------------------------------------- Pauli resources


def test_pauli_resources_of_constant_single_and_pair_terms():
    constant = pauli_z_resources((Fraction(3),) * 4)
    assert (constant.qubits, constant.z_terms, constant.cx_per_layer) == (2, 0, 0)
    assert (constant.rz_per_layer, constant.rx_per_layer, constant.max_z_locality) == (
        0,
        2,
        0,
    )
    z0 = pauli_z_resources((1, -1, 1, -1))
    assert (z0.z_terms, z0.max_z_locality, z0.cx_per_layer, z0.rz_per_layer) == (
        1,
        1,
        0,
        1,
    )
    z0z1 = pauli_z_resources((1, -1, -1, 1))
    assert (z0z1.z_terms, z0z1.max_z_locality, z0z1.cx_per_layer) == (1, 2, 2)
    three = pauli_z_resources((1, -1, -1, 1, -1, 1, 1, -1))
    assert (three.max_z_locality, three.cx_per_layer, three.rx_per_layer) == (3, 4, 3)


def test_pauli_resources_match_documented_teaching_counts():
    energies = compact_energies(build_pairwise_model(example()))
    resources = pauli_z_resources(energies)
    assert (resources.qubits, resources.cx_per_layer, resources.rz_per_layer) == (
        6,
        30,
        15,
    )
    assert (resources.rx_per_layer, resources.max_z_locality) == (6, 4)


@pytest.mark.parametrize(
    "energies",
    [(1, 2, 3), (1,), tuple(range(512)), (1, float("nan")), ("1", 2), (True, 0)],
)
def test_pauli_resources_reject_invalid_diagonals(energies):
    with pytest.raises(ValueError):
        pauli_z_resources(energies)


# ---------------------------------------------------------------- tuning


@pytest.mark.parametrize("depth,seed", [(1, 17), (2, 23)])
def test_tuning_reproduces_h2_run_qaoa_probabilities(depth, seed):
    s = example()
    energies = compact_energies(build_pairwise_model(s))
    tuning = tune_qaoa(energies, depth=depth, evaluations=40, seed=seed)
    reference = run_qaoa(s, depth=depth, evaluations=40, shots=64, seed=seed)
    assert tuning.gammas == pytest.approx(reference.gammas, abs=1e-12)
    assert tuning.betas == pytest.approx(reference.betas, abs=1e-12)
    assert tuning.expected_normalized_energy == pytest.approx(
        reference.expected_normalized_energy, abs=1e-12
    )
    by_basis = {sample.basis_index: sample.probability for sample in reference.samples}
    for basis, probability in enumerate(tuning.probabilities):
        assert probability == pytest.approx(by_basis[basis], abs=1e-12)


def test_tuning_single_evaluation_is_uniform_and_probabilities_are_physical():
    energies = compact_energies(build_pairwise_model(example()))
    tuning = tune_qaoa(energies, depth=1, evaluations=1, seed=5)
    assert tuning.gammas == (0.0,) and tuning.betas == (0.0,)
    assert tuning.probabilities == pytest.approx((1 / 64,) * 64, abs=1e-12)
    full = tune_qaoa(energies, depth=2, evaluations=30, seed=5)
    assert all(p >= 0 for p in full.probabilities)
    assert math.fsum(full.probabilities) == pytest.approx(1, abs=1e-12)
    assert full.expected_normalized_energy <= tuning.expected_normalized_energy
    assert -1 - 1e-12 <= full.expected_normalized_energy <= 1 + 1e-12


def test_tuning_is_deterministic_and_constant_diagonal_stays_uniform():
    energies = compact_energies(build_pairwise_model(example()))
    assert tune_qaoa(energies, evaluations=20, seed=9) == tune_qaoa(
        energies, evaluations=20, seed=9
    )
    flat = tune_qaoa((Fraction(2),) * 8, depth=2, evaluations=20, seed=1)
    assert flat.probabilities == pytest.approx((1 / 8,) * 8, abs=1e-12)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"depth": 0},
        {"depth": 4},
        {"evaluations": 0},
        {"evaluations": 513},
        {"seed": -1},
        {"seed": 2**32},
        {"depth": 1.0},
    ],
)
def test_tuning_rejects_invalid_budgets(kwargs):
    with pytest.raises(ValueError):
        tune_qaoa((0, 1, 2, 3), **kwargs)


def test_tuning_rejects_invalid_diagonals():
    with pytest.raises(ValueError):
        tune_qaoa((0, 1, 2))
    with pytest.raises(ValueError):
        tune_qaoa(tuple(range(512)))


# ----------------------------------------------------------------- noise


def test_zero_noise_is_identity():
    ideal = np.array([0.5, 0.25, 0.125, 0.125])
    noisy = apply_noise(
        ideal,
        noise=NoiseModel(
            single_qubit_depolarizing=0, two_qubit_depolarizing=0, readout_flip=0
        ),
        cx_gates=10,
        single_qubit_gates=10,
    )
    assert noisy == pytest.approx(ideal, abs=1e-15)


def test_readout_flip_channel_single_and_two_qubits():
    zero = dict(single_qubit_depolarizing=0, two_qubit_depolarizing=0)
    one = apply_noise(
        np.array([1.0, 0.0]),
        noise=NoiseModel(readout_flip=0.25, **zero),
        cx_gates=0,
        single_qubit_gates=0,
    )
    assert one == pytest.approx([0.75, 0.25], abs=1e-15)
    two = apply_noise(
        np.array([1.0, 0.0, 0.0, 0.0]),
        noise=NoiseModel(readout_flip=0.1, **zero),
        cx_gates=0,
        single_qubit_gates=0,
    )
    assert two == pytest.approx([0.81, 0.09, 0.09, 0.01], abs=1e-15)


def test_global_depolarizing_survival_formula():
    noisy = apply_noise(
        np.array([1.0, 0.0, 0.0, 0.0]),
        noise=NoiseModel(
            single_qubit_depolarizing=0.1, two_qubit_depolarizing=0.0, readout_flip=0
        ),
        cx_gates=5,
        single_qubit_gates=1,
    )
    assert noisy == pytest.approx([0.925, 0.025, 0.025, 0.025], abs=1e-15)
    survival = (1 - 0.001) ** 7 * (1 - 0.01) ** 3
    default = apply_noise(
        np.array([0.0, 1.0]), noise=NoiseModel(), cx_gates=3, single_qubit_gates=7
    )
    mixed = np.array([0.5 * (1 - survival), survival + 0.5 * (1 - survival)])
    flipped = np.array(
        [0.98 * mixed[0] + 0.02 * mixed[1], 0.02 * mixed[0] + 0.98 * mixed[1]]
    )
    assert default == pytest.approx(flipped, abs=1e-15)


def test_complete_depolarization_gives_uniform():
    noisy = apply_noise(
        np.array([0.0, 0.0, 1.0, 0.0]),
        noise=NoiseModel(two_qubit_depolarizing=1.0),
        cx_gates=1,
        single_qubit_gates=0,
    )
    assert noisy == pytest.approx([0.25] * 4, abs=1e-15)
    assert math.fsum(noisy) == pytest.approx(1, abs=1e-15)


@pytest.mark.parametrize(
    "probabilities,cx,single",
    [
        ([0.5, 0.4], 0, 0),
        ([0.5, 0.25, 0.25], 0, 0),
        ([1.5, -0.5], 0, 0),
        ([1.0, 0.0], -1, 0),
        ([1.0, 0.0], 0, -1),
        ([1.0, float("nan")], 0, 0),
    ],
)
def test_noise_rejects_invalid_input(probabilities, cx, single):
    with pytest.raises(ValueError):
        apply_noise(
            np.array(probabilities),
            noise=NoiseModel(),
            cx_gates=cx,
            single_qubit_gates=single,
        )


def test_noise_model_rejects_invalid_probabilities_and_labels():
    with pytest.raises(ValueError):
        NoiseModel(readout_flip=1.5)
    with pytest.raises(ValueError):
        NoiseModel(label="calibrated-device")


# ---------------------------------------------------------------- repair


def test_repair_of_independent_conflict_follows_documented_key():
    s = example()
    raw = (option(1, 1), option(2, 1), option(3, 1))
    outcome = repair_and_improve(raw, s)
    assert isinstance(outcome, RepairOutcome)
    assert outcome.raw_option_ids == raw
    assert outcome.raw_status == "infeasible"
    assert outcome.raw_violation_count == 1
    assert Fraction(outcome.raw_violation_measure) == 2
    assert Fraction(outcome.raw_exact_score) == 3
    assert [
        (m.phase, m.mission_id, m.from_option_id, m.to_option_id) for m in outcome.moves
    ] == [
        ("repair", "mission-3", option(3, 1), option(3, 2)),
        ("repair", "mission-2", option(2, 1), option(2, 3)),
    ]
    assert [Fraction(m.violation_after) for m in outcome.moves] == [1, 0]
    assert [Fraction(m.exact_score_after) for m in outcome.moves] == [
        Fraction(29, 10),
        Fraction(26, 10),
    ]
    assert outcome.repair_moves == 2 and outcome.improvement_moves == 0
    assert outcome.changed_mission_ids == ("mission-2", "mission-3")
    assert Fraction(outcome.exact_score_change) == Fraction(-2, 5)
    assert outcome.final_validation.status == "feasible"
    assert Fraction(outcome.final_validation.exact_score) == Fraction(13, 5)
    assert outcome.final_plan.selected_option_ids == (
        option(1, 1),
        option(2, 3),
        option(3, 2),
    )
    assert outcome.validator_calls == 1 + 9 + 9 + 9
    assert not outcome.budget_exhausted and not outcome.repair_failed
    assert validate_plan(outcome.final_plan, s) == outcome.final_validation


def test_improvement_of_feasible_suboptimal_plan():
    s = example()
    outcome = repair_and_improve((option(1, 4), option(2, 3), option(3, 2)), s)
    assert outcome.raw_status == "feasible"
    assert Fraction(outcome.raw_violation_measure) == 0
    assert Fraction(outcome.raw_exact_score) == Fraction(22, 10)
    assert [(m.phase, m.mission_id, m.to_option_id) for m in outcome.moves] == [
        ("improve", "mission-1", option(1, 1))
    ]
    assert outcome.repair_moves == 0 and outcome.improvement_moves == 1
    assert Fraction(outcome.final_validation.exact_score) == Fraction(13, 5)
    assert Fraction(outcome.exact_score_change) == Fraction(2, 5)
    assert outcome.validator_calls == 1 + 9 + 9


def test_optimal_raw_plan_is_unchanged():
    s = example()
    raw = (option(1, 1), option(2, 2), option(3, 3))
    outcome = repair_and_improve(raw, s)
    assert outcome.moves == () and outcome.changed_mission_ids == ()
    assert Fraction(outcome.exact_score_change) == 0
    assert outcome.final_plan.selected_option_ids == raw
    assert outcome.validator_calls == 10


def test_move_budget_exhaustion_is_reported_without_hiding_infeasibility():
    s = example()
    outcome = repair_and_improve(
        (option(1, 1), option(2, 1), option(3, 1)), s, max_moves=1
    )
    assert outcome.repair_moves == 1
    assert outcome.budget_exhausted and outcome.repair_failed
    assert outcome.final_validation.status == "infeasible"
    assert outcome.validator_calls == 10


def test_unrepairable_zero_capacity_case_reports_failure():
    s = example("infeasible")
    outcome = repair_and_improve((option(1, 1), option(2, 1), option(3, 1)), s)
    assert outcome.repair_failed and not outcome.budget_exhausted
    assert outcome.final_validation.status == "infeasible"
    assert outcome.raw_status == "infeasible"
    assert outcome.validator_calls >= 10


def test_repair_handles_higher_order_capacity_without_pairwise_model():
    data = example().model_dump(mode="json")
    data["resources"][0]["capacity"] = 2
    s = Scenario.model_validate(data)
    outcome = repair_and_improve((option(1, 1), option(2, 1), option(3, 1)), s)
    assert Fraction(outcome.raw_violation_measure) == 1
    assert outcome.final_validation.status == "feasible"
    assert Fraction(outcome.final_validation.exact_score) == Fraction(
        solve_exact(s).validation.exact_score
    )


@pytest.mark.parametrize(
    "ids",
    [
        (option(1, 1), option(1, 2), option(3, 1)),
        (option(1, 1), option(2, 1)),
        (option(1, 1), option(2, 1), "unknown-option"),
        (),
    ],
)
def test_repair_rejects_invalid_raw_assignments(ids):
    with pytest.raises(ValueError):
        repair_and_improve(ids, example())


@pytest.mark.parametrize("moves", [0, 257, 1.0, True])
def test_repair_rejects_invalid_budgets(moves):
    with pytest.raises(ValueError):
        repair_and_improve(
            (option(1, 1), option(2, 1), option(3, 1)), example(), max_moves=moves
        )


@pytest.mark.parametrize("name", ["resource-outage", "missing-capacity"])
def test_repair_rejects_static_blockers(name):
    with pytest.raises(ValueError):
        repair_and_improve((option(1, 1), option(2, 2), option(3, 3)), example(name))


# --------------------------------------------------------- direct compare


def direct(seed=17, noise=NoiseModel(), **kwargs):
    values = dict(
        seed=seed,
        depth=1,
        evaluations=24,
        shots=128,
        candidate_budget=4,
        max_moves=16,
        annealing_steps=16,
        noise=noise,
        optimum_exact_score="13/5",
    )
    values.update(kwargs)
    return compare_direct(example(), **values)


def test_direct_comparison_labels_and_order():
    records = direct()
    assert [(r.method, r.execution_mode) for r in records] == [
        ("qaoa", "noiseless_simulator"),
        ("qaoa", "noise_model"),
        ("random", "classical"),
        ("annealing", "classical"),
    ]
    assert all(isinstance(r, MethodRecord) and r.hardware_jobs == 0 for r in records)
    assert [(r.method, r.execution_mode) for r in direct(noise=None)] == [
        ("qaoa", "noiseless_simulator"),
        ("random", "classical"),
        ("annealing", "classical"),
    ]


def test_direct_noiseless_sampling_reproduces_h2_counts():
    qaoa = direct()[0]
    reference = run_qaoa(example(), depth=1, evaluations=24, shots=128, seed=17)
    assert qaoa.raw_sample_feasible_rate == pytest.approx(
        reference.sampled_feasible_rate, abs=1e-12
    )
    assert qaoa.raw_sample_optimal_rate == pytest.approx(
        reference.sampled_optimal_rate, abs=1e-12
    )
    assert qaoa.samples == 128
    assert (qaoa.logical_qubits, qaoa.depth, qaoa.evaluations) == (6, 1, 24)
    assert qaoa.cx_gates == 30
    top = sorted(
        (s for s in reference.samples if s.count),
        key=lambda s: (-s.count, s.basis_index),
    )[:4]
    assert [o.raw_option_ids for o in qaoa.outcomes] == [
        s.selected_option_ids for s in top
    ]
    assert [o.raw_status == "feasible" for o in qaoa.outcomes] == [
        s.feasible for s in top
    ]


def test_direct_records_keep_raw_and_repaired_evidence_consistent():
    s = example()
    for record in direct():
        summary = record.candidates
        assert summary.candidates == len(record.outcomes) <= 4
        assert len({o.raw_option_ids for o in record.outcomes}) == summary.candidates
        assert summary.raw_feasible == sum(
            o.raw_status == "feasible" for o in record.outcomes
        )
        assert summary.repaired_feasible == sum(
            o.final_validation.status == "feasible" for o in record.outcomes
        )
        assert summary.repaired_feasible >= summary.raw_feasible
        assert summary.repair_failures == sum(o.repair_failed for o in record.outcomes)
        assert summary.validator_calls == sum(
            o.validator_calls for o in record.outcomes
        )
        assert summary.mean_repair_moves == pytest.approx(
            np.mean([o.repair_moves for o in record.outcomes])
        )
        assert 0 <= record.raw_sample_feasible_rate <= 1
        assert 0 <= record.raw_sample_optimal_rate <= record.raw_sample_feasible_rate
        if record.best_plan is not None:
            report = validate_plan(record.best_plan, s)
            assert report == record.best_validation
            assert report.status == "feasible"
            assert Fraction(record.best_exact_score) == Fraction(report.exact_score)
            gap = Fraction(13, 5) - Fraction(record.best_exact_score)
            assert gap >= 0
            assert Fraction(record.optimum_gap) == gap
            assert record.optimum_gap_value == pytest.approx(float(gap), abs=1e-12)
        timing = record.timing
        assert timing.queue_s is None
        parts = (
            timing.preparation_s
            + timing.tuning_s
            + timing.sampling_s
            + timing.validation_s
            + timing.repair_s
        )
        assert parts <= timing.total_s + 1e-6


def test_direct_sample_counts_and_noise_record():
    records = direct()
    noisy, random_record, annealing = records[1], records[2], records[3]
    assert noisy.samples == 128 and noisy.cx_gates == 30
    assert random_record.samples == 128
    assert random_record.logical_qubits is None and random_record.depth is None
    assert annealing.samples == 4


def test_direct_comparison_is_deterministic_and_gap_optional():
    first = [strip_timing(r.model_dump(mode="json")) for r in direct(seed=3)]
    second = [strip_timing(r.model_dump(mode="json")) for r in direct(seed=3)]
    assert first == second
    without = direct(optimum_exact_score=None)
    assert all(r.optimum_gap is None and r.optimum_gap_value is None for r in without)


def test_direct_comparison_refuses_infeasible_unsupported_and_large_problems():
    with pytest.raises(ValueError):
        compare_direct(example("infeasible"), seed=1)
    with pytest.raises(ValueError):
        compare_direct(example("resource-outage"), seed=1)
    with pytest.raises(ValueError):
        compare_direct(example(), seed=1, max_qubits=5)
    with pytest.raises(ValueError):
        compare_direct(generate_instance(spec(missions=5)), seed=1)


# -------------------------------------------------------- subproblem search


def search(method="qaoa", seed=4, instance=None, **kwargs):
    values = dict(
        method=method,
        seed=seed,
        iterations=3,
        subproblem_missions=3,
        depth=1,
        evaluations=12,
        shots=64,
        max_moves=32,
    )
    values.update(kwargs)
    return solve_subproblem_search(instance or generate_instance(spec()), **values)


@pytest.mark.parametrize(
    "method,noise,mode",
    [
        ("qaoa", None, "noiseless_simulator"),
        ("qaoa", NoiseModel(), "noise_model"),
        ("exact", None, "classical"),
        ("random", None, "classical"),
    ],
)
def test_search_records_labels_steps_and_revalidates(method, noise, mode):
    s = generate_instance(spec())
    result = search(method, instance=s, noise=noise)
    assert isinstance(result, SearchResult)
    assert result.execution_mode == mode and result.hardware_jobs == 0
    assert result.iterations == 3 == len(result.steps)
    assert validate_plan(result.plan, s) == result.validation
    assert result.status == (
        "feasible" if result.validation.status == "feasible" else "infeasible"
    )
    previous_violation = Fraction(result.initial.raw_violation_measure)
    previous_score = None
    for n, step in enumerate(result.steps, start=1):
        assert step.iteration == n
        assert len(step.mission_ids) == 3 == len(set(step.mission_ids))
        assert step.qubits == 6
        assert 0 <= step.raw_feasible_fraction <= 1
        violation = Fraction(step.incumbent_violation)
        assert violation <= previous_violation
        if violation == 0 and previous_score is not None:
            assert Fraction(step.incumbent_exact_score) >= previous_score
        if step.accepted:
            assert Fraction(step.proposal_violation) == violation
        if violation == 0:
            previous_score = Fraction(step.incumbent_exact_score)
        previous_violation = violation
    if (
        result.status == "feasible"
        and result.initial.final_validation.status == "feasible"
    ):
        assert Fraction(result.validation.exact_score) >= Fraction(
            result.initial.final_validation.exact_score
        )


def test_exact_search_over_all_missions_reaches_global_optimum():
    s = example()
    result = solve_subproblem_search(
        s, method="exact", seed=1, iterations=1, subproblem_missions=3
    )
    assert result.status == "feasible"
    assert Fraction(result.validation.exact_score) == Fraction(13, 5)
    assert result.steps[0].qubits == 6


def test_subproblem_size_is_capped_by_mission_count():
    s = generate_instance(spec(missions=2, slots=4))
    result = solve_subproblem_search(
        s, method="random", seed=2, iterations=2, subproblem_missions=4, shots=16
    )
    assert all(len(step.mission_ids) == 2 for step in result.steps)


def test_search_is_deterministic_excluding_timing():
    first = strip_timing(search(seed=8).model_dump(mode="json"))
    second = strip_timing(search(seed=8).model_dump(mode="json"))
    assert first == second


def test_search_on_infeasible_problem_reports_infeasible():
    result = solve_subproblem_search(
        example("infeasible"), method="exact", seed=1, iterations=2
    )
    assert result.status == "infeasible"
    assert result.validation.status == "infeasible"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"method": "annealing"},
        {"iterations": 0},
        {"iterations": 65},
        {"subproblem_missions": 0},
        {"subproblem_missions": 5},
        {"seed": -1},
        {"shots": 0},
    ],
)
def test_search_rejects_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        search(**kwargs)


def test_search_rejects_oversized_subproblems_and_unsupported_mappings():
    with pytest.raises(ValueError):
        search(
            instance=generate_instance(spec(options_per_mission=8)),
            subproblem_missions=3,
        )
    data = example().model_dump(mode="json")
    data["resources"][0]["capacity"] = 2
    with pytest.raises(ValueError):
        search(instance=Scenario.model_validate(data))
    with pytest.raises(ValueError):
        search(instance=example("resource-outage"))


# --------------------------------------------------------------- go/no-go


def fake_record(base, method, mode, *, gap, total_s, raw=None, feasible=True, seed=1):
    return MethodRecord(
        method=method,
        execution_mode=mode,
        seed=seed,
        raw_sample_feasible_rate=raw,
        best_plan=base.milp.plan if feasible else None,
        best_validation=base.milp.validation if feasible else None,
        best_exact_score=base.milp.validation.exact_score if feasible else None,
        optimum_gap=str(Fraction(gap)) if feasible else None,
        optimum_gap_value=float(gap) if feasible else None,
        timing=Timing(total_s=total_s),
    )


@pytest.fixture(scope="module")
def base_record():
    s = example()
    milp = solve_milp(s)
    return InstanceRecord(
        split="held_out",
        spec=spec(missions=3),
        scenario_id=s.scenario_id,
        input_fingerprint=fingerprint(s),
        profile=resource_profile(build_pairwise_model(s)),
        exact_status="optimal",
        exact_elapsed_s=0.01,
        milp=milp,
        optimum_exact_score="13/5",
        optimum_source="exact_enumeration",
        methods=(),
    )


def instance_with(base, methods, split="held_out"):
    return base.model_copy(update={"methods": tuple(methods), "split": split})


CRITERIA = GoNoGoCriteria(
    application="direct_qaoa_selection",
    hybrid_method="qaoa",
    random_method="random",
)


def test_go_requires_every_criterion(base_record):
    good = instance_with(
        base_record,
        [
            fake_record(
                base_record, "milp", "classical", gap=0, total_s=1.0, seed=None
            ),
            fake_record(
                base_record, "qaoa", "noiseless_simulator", gap=0, total_s=0.5, raw=0.9
            ),
            fake_record(
                base_record, "random", "classical", gap=0.1, total_s=0.1, raw=0.3
            ),
        ],
    )
    decision = decide_go_no_go((good,), CRITERIA)
    assert decision.verdict == "go"
    assert decision.evaluated_split == "held_out"
    assert all(c.passed for c in decision.criteria)
    assert {c.name for c in decision.criteria} == {
        "validated_feasibility",
        "objective_quality",
        "raw_feasibility_vs_random",
        "repaired_quality_vs_random",
        "end_to_end_runtime",
    }
    assert decision.retained_solver == "qaoa:noiseless_simulator"
    assert NO_ADVANTAGE_STATEMENT in decision.statement


@pytest.mark.parametrize(
    "hybrid_gap,hybrid_s,hybrid_raw,random_gap,failing",
    [
        (0.0, 2.0, 0.9, 0.1, "end_to_end_runtime"),
        (0.2, 0.5, 0.9, 0.3, "objective_quality"),
        (0.0, 0.5, 0.2, 0.1, "raw_feasibility_vs_random"),
        (0.1, 0.5, 0.9, 0.0, "repaired_quality_vs_random"),
    ],
)
def test_single_failed_criterion_retains_classical_solver(
    base_record, hybrid_gap, hybrid_s, hybrid_raw, random_gap, failing
):
    criteria = CRITERIA.model_copy(update={"max_mean_gap_excess": 0.15})
    record = instance_with(
        base_record,
        [
            fake_record(
                base_record, "milp", "classical", gap=0, total_s=1.0, seed=None
            ),
            fake_record(
                base_record,
                "qaoa",
                "noiseless_simulator",
                gap=hybrid_gap,
                total_s=hybrid_s,
                raw=hybrid_raw,
            ),
            fake_record(
                base_record, "random", "classical", gap=random_gap, total_s=0.1, raw=0.3
            ),
        ],
    )
    decision = decide_go_no_go((record,), criteria)
    assert decision.verdict == "no_go"
    assert decision.retained_solver == MILP_SOLVER_NAME
    assert [c.name for c in decision.criteria if not c.passed] == [failing]


def test_infeasible_hybrid_run_fails_validated_feasibility(base_record):
    record = instance_with(
        base_record,
        [
            fake_record(
                base_record, "milp", "classical", gap=0, total_s=1.0, seed=None
            ),
            fake_record(
                base_record, "qaoa", "noiseless_simulator", gap=0, total_s=0.5, raw=0.9
            ),
            fake_record(
                base_record,
                "qaoa",
                "noiseless_simulator",
                gap=0,
                total_s=0.5,
                raw=0.9,
                feasible=False,
                seed=2,
            ),
            fake_record(
                base_record, "random", "classical", gap=0.1, total_s=0.1, raw=0.3
            ),
        ],
    )
    decision = decide_go_no_go((record,), CRITERIA)
    feasibility = {c.name: c for c in decision.criteria}["validated_feasibility"]
    assert not feasibility.passed
    assert feasibility.measured == pytest.approx(0.5)
    assert decision.verdict == "no_go"


def test_raw_rate_criterion_can_be_disabled(base_record):
    criteria = CRITERIA.model_copy(update={"require_raw_rate_above_random": False})
    record = instance_with(
        base_record,
        [
            fake_record(
                base_record, "milp", "classical", gap=0, total_s=1.0, seed=None
            ),
            fake_record(
                base_record, "qaoa", "noiseless_simulator", gap=0, total_s=0.5, raw=0.1
            ),
            fake_record(
                base_record, "random", "classical", gap=0.1, total_s=0.1, raw=0.3
            ),
        ],
    )
    assert decide_go_no_go((record,), criteria).verdict == "go"


def test_dev_records_are_ignored_and_missing_evidence_fails(base_record):
    dev = instance_with(
        base_record,
        [
            fake_record(
                base_record, "milp", "classical", gap=0, total_s=1.0, seed=None
            ),
            fake_record(
                base_record, "qaoa", "noiseless_simulator", gap=0, total_s=0.1, raw=0.9
            ),
            fake_record(
                base_record, "random", "classical", gap=0.1, total_s=0.1, raw=0.3
            ),
        ],
        split="dev",
    )
    held_out = instance_with(
        base_record,
        [fake_record(base_record, "milp", "classical", gap=0, total_s=1.0, seed=None)],
    )
    decision = decide_go_no_go((dev, held_out), CRITERIA)
    assert decision.verdict == "no_go"
    assert decision.retained_solver == MILP_SOLVER_NAME
    assert any(c.measured is None and not c.passed for c in decision.criteria)


# -------------------------------------------------------------- benchmark


def tiny_config(**overrides):
    values = dict(
        dev_specs=(
            spec(family="dev", missions=2, options_per_mission=2, slots=2, seed=1),
        ),
        held_out_specs=(
            spec(family="held", missions=3, options_per_mission=2, slots=3, seed=2),
            spec(family="held", missions=4, options_per_mission=2, slots=3, seed=3),
        ),
        scaling_specs=(
            spec(family="scale", missions=2, seed=4),
            spec(family="scale", missions=8, slots=8, seed=5),
        ),
        qaoa_seeds=(17,),
        depth=1,
        evaluations=8,
        shots=32,
        candidate_budget=2,
        max_moves=16,
        annealing_steps=8,
        lns_iterations=2,
        subproblem_missions=2,
        exact_max_assignments=4096,
        milp_time_limit_s=10.0,
        criteria=(
            CRITERIA,
            GoNoGoCriteria(
                application="lns_qaoa_subproblem",
                hybrid_method="lns_qaoa",
                random_method="lns_random",
            ),
        ),
    )
    values.update(overrides)
    return BenchmarkConfig(**values)


@pytest.fixture(scope="module")
def tiny_report():
    return run_benchmark(tiny_config(), generated_utc=UTC_NOW)


def test_benchmark_structure_splits_and_labels(tiny_report):
    report = tiny_report
    assert isinstance(report, BenchmarkReport)
    assert report.schema_id == "r10-benchmark-v1"
    assert report.hardware_jobs == 0
    assert report.statement == NO_ADVANTAGE_STATEMENT
    assert report.generated_utc == UTC_NOW
    assert [i.split for i in report.instances] == ["dev", "held_out", "held_out"]
    assert {"python", "numpy", "scipy"} <= set(report.environment)
    assert len(report.scaling) == 2
    assert [d.application for d in report.decisions] == [
        "direct_qaoa_selection",
        "lns_qaoa_subproblem",
    ]
    for decision in report.decisions:
        assert decision.verdict in ("go", "no_go")
        if decision.verdict == "no_go":
            assert decision.retained_solver == MILP_SOLVER_NAME


def test_benchmark_methods_and_independent_revalidation(tiny_report):
    expected = {
        ("exact", "classical"),
        ("milp", "classical"),
        ("qaoa", "noiseless_simulator"),
        ("qaoa", "noise_model"),
        ("random", "classical"),
        ("annealing", "classical"),
        ("lns_qaoa", "noiseless_simulator"),
        ("lns_qaoa", "noise_model"),
        ("lns_exact", "classical"),
        ("lns_random", "classical"),
    }
    for instance in tiny_report.instances:
        s = generate_instance(instance.spec)
        assert instance.input_fingerprint == fingerprint(s)
        assert instance.profile == resource_profile(build_pairwise_model(s))
        exact = solve_exact(s)
        if exact.status == "infeasible":
            assert instance.optimum_exact_score is None
            assert instance.optimum_source == "none"
            continue
        assert instance.optimum_source == "exact_enumeration"
        assert Fraction(instance.optimum_exact_score) == Fraction(
            exact.validation.exact_score
        )
        assert {(m.method, m.execution_mode) for m in instance.methods} == expected
        for record in instance.methods:
            assert record.hardware_jobs == 0
            if record.best_plan is not None:
                report = validate_plan(record.best_plan, s)
                assert report == record.best_validation
                assert Fraction(record.optimum_gap) == Fraction(
                    instance.optimum_exact_score
                ) - Fraction(report.exact_score)
                assert Fraction(record.optimum_gap) >= 0
            if record.method in ("milp", "exact"):
                assert Fraction(record.optimum_gap) == 0


def test_benchmark_aggregates_match_records(tiny_report):
    for aggregate in tiny_report.aggregates:
        records = [
            r
            for i in tiny_report.instances
            if i.split == aggregate.split and i.optimum_exact_score is not None
            for r in i.methods
            if (r.method, r.execution_mode)
            == (aggregate.method, aggregate.execution_mode)
        ]
        assert aggregate.runs == len(records) > 0
        assert aggregate.feasible_runs == sum(
            r.best_validation is not None and r.best_validation.status == "feasible"
            for r in records
        )
        gaps = [r.optimum_gap_value for r in records if r.optimum_gap_value is not None]
        assert aggregate.optimum_hits == sum(g == 0 for g in gaps)
        if gaps:
            assert aggregate.mean_gap == pytest.approx(float(np.mean(gaps)), abs=1e-12)
            assert aggregate.std_gap == pytest.approx(float(np.std(gaps)), abs=1e-12)
            assert aggregate.max_gap == pytest.approx(max(gaps), abs=1e-12)
        assert aggregate.mean_total_s == pytest.approx(
            float(np.mean([r.timing.total_s for r in records])), abs=1e-12
        )


def test_benchmark_is_deterministic_excluding_timing(tiny_report):
    again = run_benchmark(tiny_config(), generated_utc=UTC_NOW)
    first = strip_timing(tiny_report.model_dump(mode="json"))
    second = strip_timing(again.model_dump(mode="json"))
    for report in (first, second):
        for decision in report["decisions"]:
            decision["criteria"] = [
                c for c in decision["criteria"] if c["name"] != "end_to_end_runtime"
            ]
            del decision["verdict"], decision["retained_solver"]
    assert first == second
    assert tiny_report.config_fingerprint == again.config_fingerprint


def test_benchmark_rejects_leaky_splits_and_naive_time():
    leaked = spec(family="other", missions=3, options_per_mission=2, slots=3, seed=2)
    with pytest.raises(ValueError):
        run_benchmark(
            tiny_config(dev_specs=(leaked,)),
            generated_utc=UTC_NOW,
        )
    with pytest.raises(ValueError):
        run_benchmark(tiny_config(), generated_utc=datetime(2026, 11, 15, 12, 0))


def test_benchmark_config_rejects_invalid_budgets():
    for overrides in (
        {"qaoa_seeds": ()},
        {"depth": 4},
        {"shots": 0},
        {"criteria": ()},
        {"milp_time_limit_s": 0.0},
        {"subproblem_missions": 5},
    ):
        with pytest.raises(ValueError):
            tiny_config(**overrides)


# ------------------------------------------------------------ publication


def test_published_config_is_preregistered_and_disjoint():
    config = published_config()
    assert isinstance(config, BenchmarkConfig)
    assert len(config.dev_specs) >= 2 and len(config.held_out_specs) >= 3
    assert len(config.qaoa_seeds) >= 3
    strip = lambda item: tuple(  # noqa: E731
        sorted((k, v) for k, v in item.model_dump().items() if k != "family")
    )
    assert not {strip(s) for s in config.dev_specs} & {
        strip(s) for s in config.held_out_specs
    }
    assert {c.application for c in config.criteria} == {
        "direct_qaoa_selection",
        "lns_qaoa_subproblem",
    }
    scaling = config.scaling_specs
    assert len({s.missions for s in scaling}) >= 3
    assert len({s.incompatibility_probability for s in scaling}) >= 3
    assert len({s.antennas for s in scaling}) >= 3
    held_sizes = {s.missions for s in config.held_out_specs}
    assert max(held_sizes) > config.subproblem_missions


def test_write_report_round_trips_and_hashes(tiny_report, tmp_path):
    path = tmp_path / "report.json"
    digest = write_report(tiny_report, path)
    raw = path.read_bytes()
    import hashlib

    assert digest == hashlib.sha256(raw).hexdigest()
    loaded = BenchmarkReport.model_validate_json(raw)
    assert loaded == tiny_report
    assert json.loads(raw)["schema_id"] == "r10-benchmark-v1"
