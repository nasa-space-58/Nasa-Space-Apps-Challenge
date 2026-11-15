"""R13 science-activity selection and ordering under energy/time limits.

Hand-computed reference (slot 3600 s, reserve 50 J, initial 150 J, solar
0/50/50/50 J): A (5, 2 slots, 200 J), B (3, 1 slot, 100 J, deadline 2),
C (2.5, 1 slot, 50 J, earliest 1) with B before C. A with B exceeds the 250 J
available by slot 3, C needs B, so the optimum is B@0 + C@1 = 3 + 2.5 - 0.05.
"""

from datetime import datetime, timezone
from fractions import Fraction

import pytest
from pydantic import ValidationError

from app.application_science import (
    APPLICATION,
    Precedence,
    ScienceActivity,
    ScienceProblem,
    build_science,
    generate_science,
)
from app.quantum_applications import audit_qubo, solve_exact, solve_milp

START = datetime(2026, 11, 20, tzinfo=timezone.utc)


def activity(name, value, duration, energy, earliest, deadline):
    return ScienceActivity(
        activity_id=name,
        science_value=value,
        duration_slots=duration,
        energy_j=energy,
        earliest_slot=earliest,
        deadline_slot=deadline,
    )


def problem(**changes):
    body = dict(
        problem_id="science-test",
        start_utc=START,
        slot_s=3600,
        slot_count=4,
        activities=(
            activity("A", 5.0, 2, 200.0, 0, 4),
            activity("B", 3.0, 1, 100.0, 0, 2),
            activity("C", 2.5, 1, 50.0, 1, 4),
        ),
        precedences=(Precedence(before_id="B", after_id="C"),),
        initial_energy_j=150.0,
        reserve_energy_j=50.0,
        solar_input_j_per_slot=(0.0, 50.0, 50.0, 50.0),
        lateness_weight_per_slot=0.05,
    )
    body.update(changes)
    return ScienceProblem(**body)


LABELS = ("A@0", "A@1", "A@2", "B@0", "B@1", "C@1", "C@2", "C@3")


def bits_for(instance, *labels):
    return tuple(int(label in labels) for label in instance.variable_labels)


def has(evaluation, code):
    return any(issue.startswith(code) for issue in evaluation.issues)


def test_variables_only_allow_on_time_starts():
    instance = build_science(problem())
    assert instance.application == APPLICATION == "science_activities"
    assert instance.variable_labels == LABELS


def test_hand_computed_optimum_and_metrics():
    instance = build_science(problem())
    exact = solve_exact(instance)
    assert exact.bits == bits_for(instance, "B@0", "C@1")
    assert Fraction(exact.objective) == Fraction("5.45")
    metrics = instance.evaluate(exact.bits).metrics
    assert metrics["completed_science_value"] == pytest.approx(5.5)
    assert metrics["completed_activities"] == 2
    assert metrics["missed_deadlines"] == 1
    assert metrics["min_battery_margin_j"] == pytest.approx(0.0)
    assert metrics["makespan_s"] == pytest.approx(7200.0)


def test_empty_plan_misses_every_deadline():
    instance = build_science(problem())
    empty = instance.evaluate((0,) * len(LABELS))
    assert empty.feasible and empty.objective == 0
    assert empty.metrics["missed_deadlines"] == 3
    assert empty.metrics["completed_activities"] == 0
    assert empty.metrics["makespan_s"] == 0
    assert empty.metrics["min_battery_margin_j"] == pytest.approx(100.0)


def test_energy_reserve_violation():
    instance = build_science(problem())
    early = instance.evaluate(bits_for(instance, "A@0"))
    # End of slot 1: 150 + 50 - 200 = 0 J, 50 J below the reserve.
    assert not early.feasible and has(early, "energy_reserve")
    assert early.violation > 0
    assert early.metrics["min_battery_margin_j"] == pytest.approx(-50.0)
    assert instance.evaluate(bits_for(instance, "A@1")).feasible


def test_overlap_multiple_starts_and_precedence():
    instance = build_science(problem())
    overlap = instance.evaluate(bits_for(instance, "A@1", "C@2", "B@0"))
    assert has(overlap, "instrument_overlap")
    twice = instance.evaluate(bits_for(instance, "A@1", "A@2"))
    assert has(twice, "activity_multiple_starts")
    orphan = instance.evaluate(bits_for(instance, "C@1"))
    assert not orphan.feasible and has(orphan, "precedence")
    ordered = instance.evaluate(bits_for(instance, "B@1", "C@2"))
    assert ordered.feasible
    wrong = instance.evaluate(bits_for(instance, "B@1", "C@1"))
    assert has(wrong, "precedence")


def test_qubo_exactness_depends_on_binding_energy_rows():
    binding = build_science(problem())
    assert binding.qubo.exact is False and "unbalanced" in binding.qubo.encoding
    relaxed = build_science(problem(initial_energy_j=1e6))
    assert relaxed.qubo.exact is True
    audit = audit_qubo(relaxed)
    assert audit.feasible_energy_matches_objective
    assert audit.ground_state_optimal and audit.infeasible_below_optimum == 0


def test_unschedulable_activity_counts_as_missed():
    extra = activity("D", 4.0, 3, 10.0, 2, 4)
    instance = build_science(
        problem(
            activities=(
                activity("A", 5.0, 2, 200.0, 0, 4),
                activity("B", 3.0, 1, 100.0, 0, 2),
                activity("C", 2.5, 1, 50.0, 1, 4),
                extra,
            )
        )
    )
    assert not any(label.startswith("D@") for label in instance.variable_labels)
    empty = instance.evaluate((0,) * len(instance.variable_labels))
    assert empty.metrics["missed_deadlines"] == 4


def test_milp_matches_exact():
    instances = [build_science(problem()), build_science(problem(initial_energy_j=1e6))]
    instances += [
        build_science(generate_science(seed, activities=3, slots=5))
        for seed in (1, 2, 3, 4, 5)
    ]
    for instance in instances:
        if len(instance.variable_labels) > 16:
            continue
        exact = solve_exact(instance)
        milp = solve_milp(instance)
        assert milp.status == exact.status == "optimal"
        assert Fraction(milp.objective) == Fraction(exact.objective)


def test_generator_is_seeded():
    first = generate_science(11)
    assert first == generate_science(11)
    assert first != generate_science(12)
    assert len(first.activities) == 4 and first.slot_count == 6
    assert len(first.solar_input_j_per_slot) == 6
    assert len(first.precedences) == 1
    assert first.reserve_energy_j <= first.initial_energy_j
    instance = build_science(first)
    assert len(instance.variable_labels) >= 1
    assert instance.evaluate((0,) * len(instance.variable_labels)).feasible


@pytest.mark.parametrize(
    "changes",
    [
        {"solar_input_j_per_slot": (0.0, 50.0)},
        {"precedences": (Precedence(before_id="B", after_id="Z"),)},
        {"precedences": (Precedence(before_id="B", after_id="B"),)},
        {
            "precedences": (
                Precedence(before_id="B", after_id="C"),
                Precedence(before_id="C", after_id="B"),
            )
        },
        {"reserve_energy_j": 200.0},
        {"activities": (activity("A", 1.0, 1, 1.0, 3, 3),)},
        {"activities": (activity("A", 1.0, 1, 1.0, 0, 5),)},
        {
            "activities": (
                activity("A", 1.0, 1, 1.0, 0, 2),
                activity("A", 1.0, 1, 1.0, 0, 2),
            )
        },
        {"start_utc": datetime(2026, 11, 20)},
    ],
)
def test_invalid_problems_are_rejected(changes):
    with pytest.raises(ValidationError):
        problem(**changes)


def test_invalid_activity_fields():
    with pytest.raises(ValidationError):
        activity("A", 0.0, 1, 1.0, 0, 1)
    with pytest.raises(ValidationError):
        activity("A", 1.0, 0, 1.0, 0, 1)
    with pytest.raises(ValidationError):
        activity("A", 1.0, 1, -1.0, 0, 1)
