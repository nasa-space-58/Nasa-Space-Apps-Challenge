"""R10 instance families, enumeration-free pairwise QUBO model and MILP baseline.

Oracles: the independent H1 validator/enumerator (app.hybrid_planning) and the
audited H2 QUBO/compact construction (app.hybrid_quantum). Exact rational
equality is used for energies and scores; float reconstruction uses 1e-12.
"""

from datetime import datetime, timedelta, timezone
from fractions import Fraction
from itertools import product
from pathlib import Path

import pytest

from app.hybrid_planning import solve_exact, validate_plan
from app.hybrid_quantum import compact_model, compile_qubo, qubo_energy
from app.hybrid_scaling import (
    MILP_SOLVER_NAME,
    InstanceSpec,
    MilpResult,
    PairwiseModel,
    ResourceProfile,
    build_pairwise_model,
    choice_energy,
    compact_energies,
    generate_instance,
    one_hot_energy,
    resource_profile,
    scaling_sweep,
    solve_milp,
)
from shared.hybrid import Plan, Scenario, fingerprint, load_scenario

EXAMPLES = Path("/workspace/shared/examples/hybrid")
if not EXAMPLES.exists():
    EXAMPLES = Path(__file__).resolve().parents[2] / "shared/examples/hybrid"

START = datetime(2030, 1, 1, tzinfo=timezone.utc)


def example(name="shared-conflict"):
    return load_scenario(EXAMPLES / f"{name}.json")


def changed(mutator, name="shared-conflict"):
    data = example(name).model_dump(mode="json")
    mutator(data)
    return Scenario.model_validate(data)


def spec(**overrides):
    values = dict(
        family="test",
        missions=3,
        options_per_mission=4,
        slots=4,
        antennas=1,
        incompatibility_probability=0.0,
        deadline_tightness=0.0,
        seed=7,
    )
    values.update(overrides)
    return InstanceSpec(**values)


def plan_for(scenario, ids):
    return Plan(
        plan_id="test-plan",
        scenario_id=scenario.scenario_id,
        scenario_revision=scenario.revision,
        input_fingerprint=fingerprint(scenario),
        state_revisions={s.state_id: s.revision for s in scenario.states},
        selected_option_ids=tuple(sorted(ids)),
        execution_mode="classical",
        solver_name="test",
    )


def choices(model):
    return product(*(range(len(group)) for group in model.mission_groups))


def ids_of(model, choice):
    return tuple(
        model.option_ids[group[index]]
        for group, index in zip(model.mission_groups, choice)
    )


SMALL_SPECS = [
    spec(seed=seed, incompatibility_probability=p, deadline_tightness=t, antennas=a)
    for seed, p, t, a in [
        (1, 0.0, 0.0, 1),
        (2, 0.2, 0.0, 1),
        (3, 0.0, 0.6, 2),
        (4, 0.3, 0.4, 1),
        (5, 0.5, 1.0, 2),
        (6, 0.1, 0.3, 3),
    ]
]


# ---------------------------------------------------------------- generation


def test_generation_is_deterministic_and_seed_sensitive():
    first, again = generate_instance(spec()), generate_instance(spec())
    assert fingerprint(first) == fingerprint(again)
    assert first == again
    assert fingerprint(generate_instance(spec(seed=8))) != fingerprint(first)


def test_generated_structure_ids_units_and_objectives():
    s = generate_instance(spec(missions=3, options_per_mission=4, antennas=2))
    assert isinstance(s, Scenario)
    assert s.seed == 7
    assert s.window.start_utc == START
    assert s.window.end_utc == START + timedelta(hours=4)
    assert [m.mission_id for m in s.missions] == [
        "mission-01",
        "mission-02",
        "mission-03",
    ]
    assert len(s.candidates) == 12
    assert {c.option_id for c in s.candidates} == {
        f"mission-{m:02d}-option-{k:02d}" for m in (1, 2, 3) for k in (1, 2, 3, 4)
    }
    assert {r.resource_id for r in s.resources} == {"antenna-01", "antenna-02"}
    for resource in s.resources:
        assert (resource.mode, resource.unit, resource.capacity) == (
            "concurrent",
            "channel",
            1,
        )
        assert [(w.start_utc, w.end_utc) for w in resource.availability] == [
            (s.window.start_utc, s.window.end_utc)
        ]
    for candidate in s.candidates:
        assert candidate.window.end_utc - candidate.window.start_utc == timedelta(
            hours=1
        )
        assert (candidate.window.start_utc - START) % timedelta(hours=1) == timedelta(0)
        assert len(candidate.demands) == 1
        demand = candidate.demands[0]
        assert (demand.amount, demand.unit) == (1, "channel")
        assert demand.window == candidate.window
        assert set(candidate.metrics) == {"science_value", "waiting_s"}
        assert 1 <= candidate.metrics["science_value"] <= 20
        assert (
            candidate.metrics["waiting_s"]
            == (candidate.window.start_utc - START).total_seconds()
        )
    objectives = {o.metric: o for o in s.objectives}
    assert (
        objectives["science_value"].unit,
        objectives["science_value"].direction,
    ) == (
        "score",
        "maximize",
    )
    assert (objectives["science_value"].weight, objectives["science_value"].scale) == (
        1,
        10,
    )
    assert (objectives["waiting_s"].unit, objectives["waiting_s"].direction) == (
        "s",
        "minimize",
    )
    assert (objectives["waiting_s"].weight, objectives["waiting_s"].scale) == (1, 36000)
    assert all(src.kind == "synthetic" for src in s.sources)
    assert "r10-generator-v1" in " ".join(src.description for src in s.sources)
    assert s.events == () and s.states == ()


def test_incompatibility_density_extremes():
    none = generate_instance(spec(incompatibility_probability=0.0))
    assert none.incompatibilities == ()
    full = generate_instance(
        spec(missions=2, options_per_mission=2, incompatibility_probability=1.0)
    )
    pairs = {tuple(sorted(c.option_ids)) for c in full.incompatibilities}
    by_mission = {c.option_id: c.mission_id for c in full.candidates}
    assert all(len(c.option_ids) == 2 for c in full.incompatibilities)
    assert len(pairs) == 4
    assert all(by_mission[a] != by_mission[b] for a, b in pairs)


def test_deadlines_and_tightness_keep_one_timely_option_per_mission():
    loose = generate_instance(spec(deadline_tightness=0.0))
    assert all(m.deadline_utc == loose.window.end_utc for m in loose.missions)
    for seed in range(12):
        tight = generate_instance(spec(seed=seed, slots=8, deadline_tightness=1.0))
        for mission in tight.missions:
            assert tight.window.start_utc < mission.deadline_utc <= tight.window.end_utc
            timely = [
                c
                for c in tight.candidates
                if c.mission_id == mission.mission_id
                and c.window.end_utc <= mission.deadline_utc
            ]
            assert timely


def test_tightness_produces_some_late_options_across_seeds():
    late = 0
    for seed in range(20):
        s = generate_instance(spec(seed=seed, slots=8, deadline_tightness=1.0))
        deadlines = {m.mission_id: m.deadline_utc for m in s.missions}
        late += sum(c.window.end_utc > deadlines[c.mission_id] for c in s.candidates)
    assert late > 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"missions": 0},
        {"missions": 33},
        {"options_per_mission": 0},
        {"options_per_mission": 17},
        {"slots": 0},
        {"antennas": 0},
        {"incompatibility_probability": 1.5},
        {"incompatibility_probability": -0.1},
        {"deadline_tightness": 2.0},
        {"seed": -1},
        {"seed": 2**32},
        {"missions": "3"},
        {"seed": 1.0},
        {"family": "bad family"},
    ],
)
def test_invalid_specs_are_rejected(overrides):
    with pytest.raises(ValueError):
        spec(**overrides)


def test_spec_rejects_extra_fields():
    with pytest.raises(ValueError):
        InstanceSpec(**spec().model_dump(), extra=1)


# ---------------------------------------------------------- pairwise model


def test_pairwise_model_matches_audited_h2_qubo_on_teaching_example():
    s = example()
    model = build_pairwise_model(s)
    reference = compile_qubo(s)
    assert isinstance(model, PairwiseModel)
    assert model.input_fingerprint == fingerprint(s)
    assert model.option_ids == reference.option_ids
    assert model.mission_groups == reference.mission_groups
    assert Fraction(model.penalty) == Fraction(reference.penalty) == Fraction(53, 5)
    assert model.forbidden_options == reference.forbidden_options
    assert set(model.conflict_pairs) == set(reference.conflict_pairs)
    assert [Fraction(v) for v in model.scores] == [
        Fraction(c.metrics["science_value"]) / 10
        for c in sorted(s.candidates, key=lambda c: c.option_id)
    ]


def test_one_hot_energy_equals_h2_energy_for_all_4096_assignments():
    s = example()
    model, reference = build_pairwise_model(s), compile_qubo(s)
    for mask in range(4096):
        bits = tuple((mask >> i) & 1 for i in range(12))
        assert one_hot_energy(model, bits) == qubo_energy(reference, bits)


@pytest.mark.parametrize("name", ["independent", "infeasible"])
def test_pairwise_model_on_non_binding_and_zero_capacity_examples(name):
    s = example(name)
    model, reference = build_pairwise_model(s), compile_qubo(s)
    assert model.forbidden_options == reference.forbidden_options
    assert set(model.conflict_pairs) == set(reference.conflict_pairs)
    if name == "independent":
        assert model.forbidden_options == () and model.conflict_pairs == ()
    else:
        assert model.forbidden_options == tuple(range(12))


@pytest.mark.parametrize("instance_spec", SMALL_SPECS)
def test_pairwise_rules_equal_original_validator_on_every_assignment(instance_spec):
    s = generate_instance(instance_spec)
    model = build_pairwise_model(s)
    forbidden, conflicts = set(model.forbidden_options), set(model.conflict_pairs)
    assert all(i < j for i, j in conflicts)
    feasible = 0
    for choice in choices(model):
        indices = [g[c] for g, c in zip(model.mission_groups, choice)]
        predicted = not (set(indices) & forbidden) and not any(
            (min(a, b), max(a, b)) in conflicts
            for n, a in enumerate(indices)
            for b in indices[n + 1 :]
        )
        report = validate_plan(plan_for(s, ids_of(model, choice)), s)
        assert predicted == (report.status == "feasible")
        energy = choice_energy(model, choice)
        if predicted:
            feasible += 1
            assert energy == -Fraction(report.exact_score)
        else:
            assert energy >= Fraction(model.penalty) - sum(
                abs(Fraction(v)) for v in model.scores
            )
    reference = solve_exact(s)
    assert feasible == reference.feasible_count


def test_choice_energy_formula_and_penalty_dominance():
    s = example()
    model = build_pairwise_model(s)
    penalty = Fraction(model.penalty)
    assert penalty == 1 + sum(abs(Fraction(v)) for v in model.scores)
    # All three first-hour options share the capacity-1 antenna: three conflicts.
    assert choice_energy(model, (0, 0, 0)) == -Fraction(3) + 3 * penalty
    assert choice_energy(model, (0, 1, 2)) == -Fraction(26, 10)
    feasible = [
        choice_energy(model, c) for c in choices(model) if choice_energy(model, c) < 0
    ]
    infeasible = [e for c in choices(model) if (e := choice_energy(model, c)) > 0]
    assert max(feasible) < min(infeasible)


@pytest.mark.parametrize(
    "choice", [(0, 0), (0, 0, 0, 0), (4, 0, 0), (-1, 0, 0), (0, 0, 1.0)]
)
def test_choice_energy_rejects_invalid_choices(choice):
    with pytest.raises(ValueError):
        choice_energy(build_pairwise_model(example()), choice)


@pytest.mark.parametrize(
    "bits", [(0,) * 11, (0,) * 13, (2,) + (0,) * 11, (True,) + (0,) * 11]
)
def test_one_hot_energy_rejects_invalid_bits(bits):
    with pytest.raises(ValueError):
        one_hot_energy(build_pairwise_model(example()), bits)


def test_events_and_unknown_capacity_are_rejected():
    with pytest.raises(ValueError):
        build_pairwise_model(example("resource-outage"))
    with pytest.raises(ValueError):
        build_pairwise_model(example("missing-capacity"))


def test_unknown_metric_is_rejected():
    def mutate(data):
        data["candidates"][0]["metrics"]["science_value"] = None

    with pytest.raises(ValueError):
        build_pairwise_model(changed(mutate))


def test_capacity_two_with_unit_demands_requires_slack_and_is_rejected():
    def mutate(data):
        data["resources"][0]["capacity"] = 2

    with pytest.raises(ValueError, match="slack|higher-order"):
        build_pairwise_model(changed(mutate))


def add_budget(capacity, per_option_j):
    def mutate(data):
        window = data["window"]
        data["resources"].append(
            {
                "resource_id": "battery",
                "mode": "budget",
                "unit": "J",
                "capacity": capacity,
                "availability": [window],
                "source_ids": data["resources"][0]["source_ids"],
            }
        )
        for candidate in data["candidates"]:
            candidate["demands"].append(
                {
                    "resource_id": "battery",
                    "amount": per_option_j,
                    "unit": "J",
                    "window": candidate["window"],
                }
            )

    return mutate


def test_non_binding_budget_is_supported_without_new_conflicts():
    base = build_pairwise_model(example())
    model = build_pairwise_model(changed(add_budget(30, 10)))
    assert model.forbidden_options == base.forbidden_options
    assert set(model.conflict_pairs) == set(base.conflict_pairs)


def test_pairwise_exact_budget_adds_every_cross_mission_pair():
    s = changed(add_budget(15, 10))
    model = build_pairwise_model(s)
    # Any two 10 J demands exceed 15 J: every cross-mission pair conflicts.
    cross = {
        (i, j)
        for a, ga in enumerate(model.mission_groups)
        for gb in model.mission_groups[a + 1 :]
        for i in ga
        for j in gb
    }
    assert set(model.conflict_pairs) == cross
    assert solve_exact(s).status == "infeasible"


def test_binding_budget_with_small_demands_is_rejected():
    with pytest.raises(ValueError, match="slack|higher-order"):
        build_pairwise_model(changed(add_budget(25, 10)))


def test_deadline_and_availability_become_unary_rules():
    def mutate(data):
        data["missions"][0]["deadline_utc"] = "2026-11-04T02:00:00Z"
        data["resources"][0]["availability"] = [
            {"start_utc": "2026-11-04T01:00:00Z", "end_utc": "2026-11-04T12:00:00Z"}
        ]

    s = changed(mutate)
    model = build_pairwise_model(s)
    forbidden = {model.option_ids[i] for i in model.forbidden_options}
    assert forbidden == {
        "mission-1-option-1",
        "mission-1-option-3",
        "mission-1-option-4",
        "mission-2-option-1",
        "mission-3-option-1",
    }


def test_incompatibility_sets_become_pairs_and_input_is_not_mutated():
    def mutate(data):
        data["incompatibilities"] = [
            {
                "constraint_id": "trio",
                "option_ids": [
                    "mission-1-option-4",
                    "mission-2-option-3",
                    "mission-3-option-2",
                ],
            }
        ]

    s = changed(mutate)
    before = fingerprint(s)
    model = build_pairwise_model(s)
    index = {oid: n for n, oid in enumerate(model.option_ids)}
    for a, b in [
        ("mission-1-option-4", "mission-2-option-3"),
        ("mission-1-option-4", "mission-3-option-2"),
        ("mission-2-option-3", "mission-3-option-2"),
    ]:
        assert (index[a], index[b]) in model.conflict_pairs
    assert fingerprint(s) == before


# ------------------------------------------------------ compact and profile


def test_compact_energies_match_h2_compact_hamiltonian():
    s = example()
    energies = compact_energies(build_pairwise_model(s))
    reference = compact_model(compile_qubo(s))
    assert len(energies) == 64
    assert all(isinstance(e, Fraction) for e in energies)
    for exact, normalized in zip(energies, reference.normalized_energies):
        rebuilt = reference.energy_shift + reference.energy_scale * normalized
        assert float(exact) == pytest.approx(rebuilt, abs=1e-12)


def test_compact_energy_order_is_little_endian_by_mission():
    model = build_pairwise_model(example())
    energies = compact_energies(model)
    for basis in range(64):
        choice = tuple((basis >> (2 * m)) & 3 for m in range(3))
        assert energies[basis] == choice_energy(model, choice)


def test_compact_energies_reject_non_power_of_two_and_budget_overflow():
    with pytest.raises(ValueError):
        compact_energies(
            build_pairwise_model(generate_instance(spec(options_per_mission=3)))
        )
    with pytest.raises(ValueError):
        compact_energies(build_pairwise_model(example()), max_qubits=5)
    with pytest.raises(ValueError):
        compact_energies(build_pairwise_model(example()), max_qubits=9)
    with pytest.raises(ValueError):
        compact_energies(build_pairwise_model(generate_instance(spec(missions=5))))


def test_resource_profile_reproduces_documented_h2_gate_counts():
    profile = resource_profile(build_pairwise_model(example()))
    assert isinstance(profile, ResourceProfile)
    assert profile.missions == 3 and profile.options == 12
    assert profile.assignments == 64
    assert profile.one_hot_variables == 12
    assert profile.compact_qubits == 6
    assert profile.statevector_amplitudes == 64
    assert profile.cx_per_layer == 30
    assert profile.rz_per_layer == 15
    assert profile.rx_per_layer == 6
    assert profile.compact_max_z_locality == 4
    assert profile.compact_z_terms == 15
    assert sum(profile.compact_locality_histogram.values()) == 15
    model = build_pairwise_model(example())
    assert profile.one_hot_quadratic_terms == 18 + len(model.conflict_pairs)
    assert profile.conflict_pairs == len(model.conflict_pairs)
    assert profile.forbidden_options == 0
    assert profile.conflict_density == pytest.approx(len(model.conflict_pairs) / 48)


@pytest.mark.parametrize("instance_spec", SMALL_SPECS)
def test_profile_equals_h2_compact_expansion_on_generated_instances(instance_spec):
    s = generate_instance(instance_spec)
    profile = resource_profile(build_pairwise_model(s))
    reference = compact_model(compile_qubo(s))
    assert profile.compact_qubits == reference.logical_qubits
    assert profile.cx_per_layer == reference.cx_per_cost_layer
    assert profile.compact_max_z_locality == reference.max_z_locality
    assert profile.rz_per_layer == sum(mask != 0 for mask, _ in reference.z_terms)
    assert profile.compact_z_terms == profile.rz_per_layer


def test_profile_growth_with_problem_size():
    profiles = [
        resource_profile(
            build_pairwise_model(
                generate_instance(spec(missions=m, slots=8, antennas=4))
            )
        )
        for m in (2, 4, 8)
    ]
    assert [p.one_hot_variables for p in profiles] == [8, 16, 32]
    assert [p.compact_qubits for p in profiles] == [4, 8, 16]
    assert [p.assignments for p in profiles] == [4**2, 4**4, 4**8]
    assert [p.statevector_amplitudes for p in profiles] == [2**4, 2**8, 2**16]
    assert all(p.compact_max_z_locality <= 4 for p in profiles)
    assert profiles[0].one_hot_quadratic_terms < profiles[2].one_hot_quadratic_terms


def test_profile_is_enumeration_free_for_large_instances():
    s = generate_instance(
        spec(missions=32, options_per_mission=8, slots=48, antennas=8, seed=3)
    )
    profile = resource_profile(build_pairwise_model(s))
    assert profile.options == 256
    assert profile.assignments == 8**32
    assert profile.compact_qubits == 96
    assert profile.statevector_amplitudes == 2**96
    assert profile.compact_max_z_locality <= 6


def test_profile_reports_unsupported_compact_encoding_explicitly():
    profile = resource_profile(
        build_pairwise_model(generate_instance(spec(options_per_mission=3)))
    )
    assert profile.one_hot_variables == 9
    assert profile.compact_qubits is None
    assert profile.cx_per_layer is None
    assert profile.compact_unsupported_reason


def test_conflict_density_rises_with_contention_and_incompatibility():
    loose = resource_profile(
        build_pairwise_model(
            generate_instance(spec(missions=4, slots=16, antennas=4, seed=11))
        )
    )
    contended = resource_profile(
        build_pairwise_model(
            generate_instance(spec(missions=4, slots=2, antennas=1, seed=11))
        )
    )
    dense = resource_profile(
        build_pairwise_model(
            generate_instance(
                spec(
                    missions=4,
                    slots=16,
                    antennas=4,
                    seed=11,
                    incompatibility_probability=0.8,
                )
            )
        )
    )
    assert 0 <= loose.conflict_density < contended.conflict_density <= 1
    assert loose.conflict_density < dense.conflict_density <= 1


def test_single_mission_has_zero_conflict_density():
    profile = resource_profile(
        build_pairwise_model(generate_instance(spec(missions=1)))
    )
    assert profile.conflict_density == 0
    assert profile.conflict_pairs == 0


# ------------------------------------------------------------------- MILP


def test_milp_matches_exact_teaching_optimum():
    s = example()
    result = solve_milp(s)
    assert isinstance(result, MilpResult)
    assert result.status == "optimal"
    assert result.solver_name == MILP_SOLVER_NAME
    assert result.input_fingerprint == fingerprint(s)
    assert result.variables == 12
    assert result.constraints >= 3
    assert Fraction(result.validation.exact_score) == Fraction(13, 5)
    assert result.validation.status == "feasible"
    assert validate_plan(result.plan, s) == result.validation
    assert result.plan.execution_mode == "classical"
    assert result.plan.solver_name == MILP_SOLVER_NAME
    assert result.objective_bound == pytest.approx(13 / 5, abs=1e-6)


def test_milp_independent_and_infeasible_examples():
    independent = solve_milp(example("independent"))
    assert independent.status == "optimal"
    assert Fraction(independent.validation.exact_score) == 3
    infeasible = solve_milp(example("infeasible"))
    assert infeasible.status == "infeasible"
    assert infeasible.plan is None and infeasible.validation is None


def test_milp_blocks_missing_capacity_and_events():
    assert solve_milp(example("missing-capacity")).status == "incomplete"
    unsupported = solve_milp(example("resource-outage"))
    assert unsupported.status == "unsupported"
    assert unsupported.plan is None
    assert unsupported.issues


@pytest.mark.parametrize("seed", range(10))
def test_milp_equals_exact_enumeration_on_generated_instances(seed):
    s = generate_instance(
        spec(
            seed=seed,
            slots=3 + seed % 3,
            antennas=1 + seed % 2,
            incompatibility_probability=0.05 * seed,
            deadline_tightness=0.1 * seed,
        )
    )
    exact, milp = solve_exact(s), solve_milp(s)
    assert milp.status == exact.status
    if exact.status == "optimal":
        assert Fraction(milp.validation.exact_score) == Fraction(
            exact.validation.exact_score
        )
        assert milp.validation.status == "feasible"


def test_milp_is_exact_for_higher_order_capacity_unlike_pairwise_mapping():
    def mutate(data):
        data["resources"][0]["capacity"] = 2

    s = changed(mutate)
    exact, milp = solve_exact(s), solve_milp(s)
    assert exact.status == milp.status == "optimal"
    assert Fraction(milp.validation.exact_score) == Fraction(
        exact.validation.exact_score
    )


@pytest.mark.parametrize("capacity,status", [(25, "infeasible"), (30, "optimal")])
def test_milp_cumulative_budget(capacity, status):
    s = changed(add_budget(capacity, 10))
    exact, milp = solve_exact(s), solve_milp(s)
    assert exact.status == milp.status == status
    if status == "optimal":
        assert Fraction(milp.validation.exact_score) == Fraction(
            exact.validation.exact_score
        )


def test_milp_respects_deadline_availability_and_incompatibility():
    def mutate(data):
        data["missions"][0]["deadline_utc"] = "2026-11-04T02:00:00Z"
        data["resources"][0]["availability"] = [
            {"start_utc": "2026-11-04T01:00:00Z", "end_utc": "2026-11-04T12:00:00Z"}
        ]
        data["incompatibilities"] = [
            {
                "constraint_id": "pair",
                "option_ids": ["mission-1-option-2", "mission-2-option-2"],
            }
        ]

    s = changed(mutate)
    exact, milp = solve_exact(s), solve_milp(s)
    assert milp.status == exact.status
    if exact.status == "optimal":
        assert Fraction(milp.validation.exact_score) == Fraction(
            exact.validation.exact_score
        )
        assert "mission-1-option-2" in milp.plan.selected_option_ids


def test_milp_handles_fractional_capacity_and_multiple_demands():
    def mutate(data):
        data["resources"][0]["capacity"] = 1.5
        first = data["candidates"][0]
        first["demands"] = [
            {
                "resource_id": "shared-antenna",
                "amount": 0.75,
                "unit": "channel",
                "window": first["window"],
            },
            {
                "resource_id": "shared-antenna",
                "amount": 0.75,
                "unit": "channel",
                "window": first["window"],
            },
        ]

    s = changed(mutate)
    exact, milp = solve_exact(s), solve_milp(s)
    assert milp.status == exact.status
    assert Fraction(milp.validation.exact_score) == Fraction(
        exact.validation.exact_score
    )


def test_milp_scales_beyond_enumeration_and_is_deterministic():
    s = generate_instance(
        spec(
            missions=24, slots=24, antennas=4, seed=5, incompatibility_probability=0.02
        )
    )
    first, second = solve_milp(s, time_limit_s=60.0), solve_milp(s, time_limit_s=60.0)
    assert first.status == "optimal"
    assert first.validation.status == "feasible"
    assert first.variables == 96
    assert first.plan == second.plan


@pytest.mark.parametrize(
    "limit", [0, -1.0, float("nan"), float("inf"), 3601.0, "1", True]
)
def test_milp_rejects_invalid_time_limits(limit):
    with pytest.raises(ValueError):
        solve_milp(example(), time_limit_s=limit)


# ----------------------------------------------------------------- sweep


def test_scaling_sweep_reports_every_point_in_order():
    specs = (spec(missions=2), spec(missions=4, slots=8), spec(options_per_mission=3))
    points = scaling_sweep(specs, milp_time_limit_s=10.0)
    assert [p.spec for p in points] == list(specs)
    assert [p.milp_variables for p in points] == [8, 16, 9]
    assert all(p.pairwise_supported for p in points)
    assert points[2].profile.compact_qubits is None
    for point, item in zip(points, specs):
        assert point.input_fingerprint == fingerprint(generate_instance(item))
        assert point.milp_status in ("optimal", "infeasible")
        assert point.milp_elapsed_s >= 0


@pytest.mark.parametrize("specs", [(), tuple(spec(seed=i) for i in range(65))])
def test_scaling_sweep_rejects_empty_or_oversized_input(specs):
    with pytest.raises(ValueError):
        scaling_sweep(specs)
