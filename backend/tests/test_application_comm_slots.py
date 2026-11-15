"""R13 communication-slot allocation: contract, validator, MILP and QUBO.

Hand-computed reference: delivered data is min(volume, rate * slot_s); the
objective counts delivered data in units of bit_scale minus
latency_weight_per_slot per slot waited after the ready slot.
"""

from datetime import datetime, timezone
from fractions import Fraction

import pytest
from pydantic import ValidationError

from app.application_comm_slots import (
    APPLICATION,
    CommRate,
    CommRequest,
    CommSlotProblem,
    CommStation,
    CommVisibility,
    build_comm_slots,
    generate_comm_slots,
)
from app.quantum_applications import audit_qubo, solve_exact, solve_milp

START = datetime(2026, 11, 20, tzinfo=timezone.utc)


def problem(extra_requests=(), capacity=1, **changes):
    body = dict(
        problem_id="comm-test",
        start_utc=START,
        slot_s=3600,
        slot_count=4,
        stations=(
            CommStation(
                station_id="ST1", capacity_links=capacity, available_slots=(0, 1, 2, 3)
            ),
            CommStation(station_id="ST2", capacity_links=1, available_slots=(1, 2, 3)),
        ),
        visibility=(
            CommVisibility(mission_id="M1", station_id="ST1", visible_slots=(0, 1, 2)),
            CommVisibility(mission_id="M1", station_id="ST2", visible_slots=(2, 3)),
            CommVisibility(mission_id="M2", station_id="ST1", visible_slots=(1, 2, 3)),
        ),
        rates=(
            CommRate(mission_id="M1", station_id="ST1", rate_bps=1e5),
            CommRate(mission_id="M1", station_id="ST2", rate_bps=5e4),
            CommRate(mission_id="M2", station_id="ST1", rate_bps=2e5),
        ),
        requests=(
            CommRequest(
                request_id="R1",
                mission_id="M1",
                volume_bits=3e8,
                ready_slot=0,
                deadline_slot=2,
            ),
            CommRequest(
                request_id="R2",
                mission_id="M2",
                volume_bits=5e8,
                ready_slot=1,
                deadline_slot=3,
            ),
            *extra_requests,
        ),
        bit_scale=1e8,
        latency_weight_per_slot=0.05,
    )
    body.update(changes)
    return CommSlotProblem(**body)


LABELS = (
    "R1@ST1#0",
    "R1@ST1#1",
    "R1@ST1#2",
    "R1@ST2#2",
    "R2@ST1#1",
    "R2@ST1#2",
    "R2@ST1#3",
)


def bits_for(instance, *labels):
    return tuple(int(label in labels) for label in instance.variable_labels)


def has(evaluation, code):
    return any(issue.startswith(code) for issue in evaluation.issues)


def test_variables_respect_visibility_availability_and_windows():
    instance = build_comm_slots(problem())
    assert instance.application == APPLICATION == "comm_slots"
    assert instance.variable_labels == LABELS
    assert len(instance.input_fingerprint) == 64
    assert "DSN" in instance.source_note or "synthetic" in instance.source_note


def test_hand_computed_optimum_and_metrics():
    instance = build_comm_slots(problem())
    exact = solve_exact(instance)
    assert exact.bits == bits_for(instance, "R1@ST1#0", "R2@ST1#1")
    assert Fraction(exact.objective) == 8
    metrics = instance.evaluate(exact.bits).metrics
    assert metrics["delivered_bits"] == pytest.approx(8e8)
    assert metrics["unmet_demand_bits"] == pytest.approx(0.0)
    assert metrics["served_requests"] == 2
    assert metrics["conflicts"] == 0
    assert metrics["mean_latency_s"] == pytest.approx(3600.0)
    assert metrics["max_latency_s"] == pytest.approx(3600.0)


def test_objective_counts_latency_and_rate_limited_delivery():
    instance = build_comm_slots(problem())
    late = instance.evaluate(bits_for(instance, "R1@ST2#2", "R2@ST1#3"))
    assert late.feasible
    # R1 via ST2: 5e4 bit/s * 3600 s = 1.8e8 bit, two slots late; R2 two slots late.
    assert late.objective == Fraction("1.8") - Fraction("0.1") + 5 - Fraction("0.1")
    assert late.metrics["delivered_bits"] == pytest.approx(6.8e8)
    assert late.metrics["unmet_demand_bits"] == pytest.approx(1.2e8)
    assert late.metrics["max_latency_s"] == pytest.approx(3 * 3600.0)


def test_empty_assignment_is_feasible_with_full_unmet_demand():
    instance = build_comm_slots(problem())
    empty = instance.evaluate((0,) * len(LABELS))
    assert empty.feasible and empty.violation == 0 and empty.objective == 0
    assert empty.metrics["unmet_demand_bits"] == pytest.approx(8e8)
    assert empty.metrics["delivered_bits"] == 0
    assert empty.metrics["served_requests"] == 0
    assert empty.metrics["mean_latency_s"] is None


def test_shared_station_conflict_is_detected_and_counted():
    instance = build_comm_slots(problem())
    clash = instance.evaluate(bits_for(instance, "R1@ST1#1", "R2@ST1#1"))
    assert not clash.feasible and has(clash, "station_capacity")
    assert clash.metrics["conflicts"] == 1
    assert clash.violation > 0
    assert set(clash.violating_variables) == {
        LABELS.index("R1@ST1#1"),
        LABELS.index("R2@ST1#1"),
    }


def test_multiple_passes_for_one_request_are_rejected():
    instance = build_comm_slots(problem())
    double = instance.evaluate(bits_for(instance, "R1@ST1#0", "R1@ST1#1"))
    assert not double.feasible and has(double, "request_multiple_passes")


def test_single_transmitter_per_mission_and_slot():
    extra = (
        CommRequest(
            request_id="R3",
            mission_id="M1",
            volume_bits=1e8,
            ready_slot=2,
            deadline_slot=3,
        ),
    )
    instance = build_comm_slots(problem(extra))
    clash = instance.evaluate(bits_for(instance, "R1@ST1#2", "R3@ST2#2"))
    assert not clash.feasible and has(clash, "mission_transmitter")
    assert clash.metrics["conflicts"] == 1


def test_capacity_two_allows_two_links_and_is_not_an_exact_qubo():
    extra = (
        CommRequest(
            request_id="R3",
            mission_id="M1",
            volume_bits=1e8,
            ready_slot=2,
            deadline_slot=3,
        ),
    )
    # Three candidate links on ST1 in slot 2 can exceed capacity 2: binding row.
    instance = build_comm_slots(problem(extra, capacity=2))
    shared = instance.evaluate(bits_for(instance, "R1@ST1#1", "R2@ST1#1"))
    assert shared.feasible
    assert instance.qubo.exact is False
    assert "unbalanced" in instance.qubo.encoding


def test_capacity_one_qubo_is_exact_and_audited():
    instance = build_comm_slots(problem())
    assert instance.qubo.exact is True
    audit = audit_qubo(instance)
    assert audit.feasible_energy_matches_objective
    assert audit.ground_state_optimal
    assert audit.infeasible_below_optimum == 0


def test_milp_matches_exact_on_the_hand_case_and_generated_instances():
    instances = [build_comm_slots(problem()), build_comm_slots(problem(capacity=2))]
    instances += [
        build_comm_slots(generate_comm_slots(seed, missions=2, slots=4))
        for seed in (1, 2, 3, 4)
    ]
    instances.append(
        build_comm_slots(generate_comm_slots(9, missions=2, slots=4, capacity_links=2))
    )
    for instance in instances:
        if len(instance.variable_labels) > 16:
            continue
        exact = solve_exact(instance)
        milp = solve_milp(instance)
        assert milp.status == exact.status == "optimal"
        assert Fraction(milp.objective) == Fraction(exact.objective)


def test_generator_is_seeded_and_bounded():
    first = generate_comm_slots(7)
    assert first == generate_comm_slots(7)
    assert first != generate_comm_slots(8)
    assert len({r.mission_id for r in first.requests}) == 3
    assert len(first.requests) == 6 and len(first.stations) == 2
    assert first.slot_count == 6
    assert all(s.capacity_links == 1 for s in first.stations)
    assert generate_comm_slots(7, capacity_links=2).stations[0].capacity_links == 2
    instance = build_comm_slots(first)
    assert len(instance.variable_labels) >= 1
    assert instance.evaluate((0,) * len(instance.variable_labels)).feasible
    assert first.start_utc.tzinfo is not None


@pytest.mark.parametrize(
    "changes",
    [
        {"start_utc": datetime(2026, 11, 20)},
        {"slot_count": 2},
        {
            "requests": (
                CommRequest(
                    request_id="R1",
                    mission_id="M1",
                    volume_bits=1e8,
                    ready_slot=2,
                    deadline_slot=1,
                ),
            )
        },
        {
            "stations": (
                CommStation(station_id="ST1", capacity_links=1, available_slots=(0,)),
                CommStation(station_id="ST1", capacity_links=1, available_slots=(1,)),
            )
        },
        {"rates": (CommRate(mission_id="M1", station_id="ST9", rate_bps=1e5),)},
        {
            "visibility": (
                CommVisibility(mission_id="M7", station_id="ST1", visible_slots=(0,)),
            )
        },
        {
            "requests": (
                CommRequest(
                    request_id="R1",
                    mission_id="M1",
                    volume_bits=1e8,
                    ready_slot=0,
                    deadline_slot=1,
                ),
                CommRequest(
                    request_id="R1",
                    mission_id="M2",
                    volume_bits=1e8,
                    ready_slot=0,
                    deadline_slot=1,
                ),
            )
        },
    ],
)
def test_invalid_problems_are_rejected(changes):
    with pytest.raises(ValidationError):
        problem(**changes)


def test_invalid_field_values_are_rejected():
    with pytest.raises(ValidationError):
        CommStation(station_id="ST1", capacity_links=0, available_slots=())
    with pytest.raises(ValidationError):
        CommRequest(
            request_id="R",
            mission_id="M",
            volume_bits=0.0,
            ready_slot=0,
            deadline_slot=0,
        )
    with pytest.raises(ValidationError):
        CommRate(mission_id="M", station_id="S", rate_bps=-1.0)


def test_problem_without_any_variable_is_rejected():
    hidden = problem(
        visibility=(
            CommVisibility(mission_id="M1", station_id="ST1", visible_slots=(3,)),
        ),
        rates=(CommRate(mission_id="M1", station_id="ST1", rate_bps=1e5),),
        requests=(
            CommRequest(
                request_id="R1",
                mission_id="M1",
                volume_bits=1e8,
                ready_slot=0,
                deadline_slot=1,
            ),
        ),
    )
    with pytest.raises(ValueError):
        build_comm_slots(hidden)
