"""H3 resource bridges: real R2 shape, exact intervals, and independent storage oracle."""

from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path
import random

import pytest

from app.communication import communication_windows
from app.hybrid_planning import solve_exact, validate_plan
from app.hybrid_quantum import compile_qubo, run_qaoa
from app.hybrid_resources import (
    ActivityLoad,
    BatteryProfile,
    GenerationSegment,
    GeometryInterval,
    StationCalendar,
    battery_trace,
    contact_from_r2,
    integrate_resources,
)
from shared.hybrid import Plan, Scenario, Source, TimeWindow, fingerprint, load_scenario

EXAMPLES = Path("/workspace/shared/examples/hybrid")
if not EXAMPLES.exists():
    EXAMPLES = Path(__file__).resolve().parents[2] / "shared/examples/hybrid"
T0 = datetime(2026, 11, 4, tzinfo=timezone.utc)


def window(a, b):
    return TimeWindow(
        start_utc=T0 + timedelta(hours=a), end_utc=T0 + timedelta(hours=b)
    )


def source(name="h3-assumption"):
    return Source(
        source_id=name,
        kind="synthetic",
        description="H3 teaching input; not an operational mission.",
    )


def scenario():
    data = load_scenario(EXAMPLES / "independent.json").model_dump(mode="json")
    data["resources"] = []
    for candidate in data["candidates"]:
        candidate["demands"] = []
    return Scenario.model_validate(data)


def r2_result(state="above"):
    def row(value):
        return dict(window(0, 12).model_dump(mode="json"), state=value)

    return communication_windows(
        [row("visible")], {"DSS-14": [row(state)]}, T0, T0 + timedelta(hours=12)
    )


def contacts_for(problem, state="above"):
    return tuple(
        contact_from_r2(
            r2_result(state),
            requirement_id="contact-" + c.option_id,
            option_id=c.option_id,
            station_id="DSS-14",
            window=c.window,
            source=source("h3-r2"),
        )
        for c in problem.candidates
    )


def station(capacity=1, availability=None):
    return StationCalendar(
        station_id="DSS-14",
        capacity_channels=capacity,
        availability=(window(0, 12),) if availability is None else availability,
        source=source("h3-calendar"),
    )


def battery(initial=7200, capacity=7200, reserve=0, generation=True):
    return BatteryProfile(
        battery_id="base-battery",
        initial_energy_j=initial,
        capacity_j=capacity,
        reserve_j=reserve,
        generation=(
            GenerationSegment(
                window=window(0, 1), power_w=1, source=source("h3-generation")
            ),
        )
        if generation
        else (),
        source=source("h3-battery"),
    )


def activities_for(problem, power=1):
    return tuple(
        ActivityLoad(
            activity_id="load-" + c.option_id,
            option_id=c.option_id,
            battery_id="base-battery",
            window=c.window,
            power_w=power,
            source=source("h3-load"),
        )
        for c in problem.candidates
    )


def plan(problem, slots=(1, 2, 3)):
    return Plan(
        plan_id="h3-test",
        scenario_id=problem.scenario_id,
        scenario_revision=problem.revision,
        input_fingerprint=fingerprint(problem),
        state_revisions={},
        selected_option_ids=tuple(
            f"mission-{i}-option-{slot}" for i, slot in enumerate(slots, 1)
        ),
        execution_mode="classical",
        solver_name="h3-test",
    )


def test_real_r2_result_adapter_keeps_geometry_and_source():
    req = contacts_for(scenario())[0]
    assert req.station_id == "DSS-14"
    assert req.geometry[0].state == "available"
    assert req.source == source("h3-r2")
    assert req.window == window(0, 1)


@pytest.mark.parametrize("bad", ["station", "gap", "overlap", "state", "coverage"])
def test_invalid_r2_timelines_rejected(bad):
    data = r2_result()
    rows = data["stations"]["DSS-14"]["intervals"]
    if bad == "station":
        data["stations"] = {}
    elif bad == "gap":
        rows[:] = [
            dict(window(0, 0.4).model_dump(mode="json"), state="available"),
            dict(window(0.5, 12).model_dump(mode="json"), state="available"),
        ]
    elif bad == "overlap":
        rows.append(dict(window(0, 12).model_dump(mode="json"), state="available"))
    elif bad == "state":
        rows[0]["state"] = "maybe"
    else:
        rows[0]["end_utc"] = window(0, 0.5).end_utc.isoformat()
    with pytest.raises(ValueError):
        contact_from_r2(
            data,
            requirement_id="contact",
            option_id="mission-1-option-1",
            station_id="DSS-14",
            window=window(0, 1),
            source=source(),
        )


def test_station_booking_is_separate_from_visible_geometry():
    original = scenario()
    before = fingerprint(original)
    integrated = integrate_resources(
        original, stations=(station(),), contacts=contacts_for(original)
    )
    assert original.revision + 1 == integrated.revision
    assert fingerprint(original) == before != fingerprint(integrated)
    result = solve_exact(integrated)
    assert result.feasible_count == 24
    assert result.validation.exact_score == "13/5"
    assert (
        len(integrated.resources) == 13
    )  # 1 calendar, 12 per-requirement geometry gates.
    unavailable = integrate_resources(
        original, stations=(station(availability=()),), contacts=contacts_for(original)
    )
    assert solve_exact(unavailable).status == "infeasible"


@pytest.mark.parametrize(
    "state,status",
    [("above", "optimal"), ("below", "infeasible"), ("unknown", "incomplete")],
)
def test_unknown_geometry_is_not_unavailable_or_free_capacity(state, status):
    original = scenario()
    integrated = integrate_resources(
        original, stations=(station(),), contacts=contacts_for(original, state)
    )
    assert solve_exact(integrated).status == status
    if state == "unknown":
        assert validate_plan(plan(integrated), integrated).status == "incomplete"


def test_unknown_station_capacity_remains_unknown():
    original = scenario()
    integrated = integrate_resources(
        original, stations=(station(None),), contacts=contacts_for(original)
    )
    assert solve_exact(integrated).status == "incomplete"


def test_geometry_is_candidate_specific_not_station_union():
    original = scenario()
    contacts = list(contacts_for(original))
    first = contacts[0]
    contacts[0] = first.model_copy(
        update={
            "geometry": (GeometryInterval(window=window(0, 12), state="unavailable"),)
        }
    )
    integrated = integrate_resources(
        original, stations=(station(3),), contacts=tuple(contacts)
    )
    report = validate_plan(plan(integrated, (1, 1, 1)), integrated)
    assert report.status == "infeasible"
    assert any("mission-1-option-1" in issue.option_ids for issue in report.issues)
    assert validate_plan(plan(integrated, (2, 1, 1)), integrated).status == "feasible"


def test_unknown_outside_requested_contact_does_not_block():
    original = scenario()
    req = contacts_for(original)[0].model_copy(
        update={
            "geometry": (
                GeometryInterval(window=window(0, 1), state="available"),
                GeometryInterval(window=window(1, 12), state="unknown"),
            )
        }
    )
    integrated = integrate_resources(original, stations=(station(),), contacts=(req,))
    assert solve_exact(integrated).status == "optimal"


def test_battery_spill_requires_subinterval_constraints():
    original = scenario()
    profile, loads = battery(), activities_for(original)
    integrated = integrate_resources(original, batteries=(profile,), activities=loads)
    result = solve_exact(integrated)
    # At least one mission must consume during the initial generation interval.
    # Otherwise its 3600 J spills from the full battery and only 7200 J remain.
    assert result.feasible_count == 64 - 27
    assert result.validation.exact_score == "3"
    late = plan(integrated, (2, 2, 2))
    assert validate_plan(late, integrated).status == "infeasible"
    trace = battery_trace(late, integrated, profile, loads)
    assert sum(row.spilled_energy_j for row in trace) == pytest.approx(3600, abs=1e-12)
    assert min(row.end_energy_j for row in trace) == pytest.approx(-3600, abs=1e-12)
    assert any(row.reserve_violated for row in trace)
    assert not any(
        row.reserve_violated
        for row in battery_trace(
            plan(integrated, (1, 1, 1)), integrated, profile, loads
        )
    )


def test_late_generation_cannot_pay_for_early_consumption():
    original = scenario()
    profile = battery(initial=0, capacity=10800, generation=False).model_copy(
        update={
            "generation": (
                GenerationSegment(
                    window=window(1, 2), power_w=3, source=source("h3-generation")
                ),
            )
        }
    )
    loads = activities_for(original)
    integrated = integrate_resources(original, batteries=(profile,), activities=loads)
    assert validate_plan(plan(integrated, (1, 1, 1)), integrated).status == "infeasible"
    assert validate_plan(plan(integrated, (3, 3, 3)), integrated).status == "feasible"


@pytest.mark.parametrize("reserve,status", [(0, "optimal"), (1, "infeasible")])
def test_battery_reserve_boundary(reserve, status):
    original = scenario()
    integrated = integrate_resources(
        original,
        batteries=(
            battery(initial=10800, capacity=10800, reserve=reserve, generation=False),
        ),
        activities=activities_for(original),
    )
    assert solve_exact(integrated).status == status


@pytest.mark.parametrize(
    "bad",
    [
        "initial",
        "reserve",
        "generation_overlap",
        "generation_outside",
        "unknown_battery",
        "unknown_option",
        "load_outside",
        "duplicate_activity",
        "duplicate_station",
        "unknown_station",
        "duplicate_contact",
        "source_collision",
        "time_cuts",
        "events",
    ],
)
def test_invalid_integration_inputs_fail_loudly(bad):
    original = scenario()
    profile, loads = battery(), activities_for(original)
    calendars, contacts = (station(),), contacts_for(original)
    kwargs = {}
    if bad == "initial":
        profile = profile.model_copy(update={"initial_energy_j": 9000})
    elif bad == "reserve":
        profile = profile.model_copy(update={"reserve_j": 8000})
    elif bad == "generation_overlap":
        profile = profile.model_copy(update={"generation": profile.generation * 2})
    elif bad == "generation_outside":
        profile = profile.model_copy(
            update={
                "generation": (
                    profile.generation[0].model_copy(update={"window": window(-1, 0)}),
                )
            }
        )
    elif bad == "unknown_battery":
        loads = (loads[0].model_copy(update={"battery_id": "missing"}),)
    elif bad == "unknown_option":
        loads = (loads[0].model_copy(update={"option_id": "missing"}),)
    elif bad == "load_outside":
        loads = (loads[0].model_copy(update={"window": window(0, 2)}),)
    elif bad == "duplicate_activity":
        loads = loads + (loads[0],)
    elif bad == "duplicate_station":
        calendars = calendars * 2
    elif bad == "unknown_station":
        contacts = (contacts[0].model_copy(update={"station_id": "missing"}),)
    elif bad == "duplicate_contact":
        contacts = contacts + (contacts[0],)
    elif bad == "source_collision":
        calendars = (
            calendars[0].model_copy(update={"source": source("teaching-inputs-v1")}),
        )
    elif bad == "time_cuts":
        kwargs["max_time_cuts"] = 2
    else:
        original = load_scenario(EXAMPLES / "resource-outage.json")
    with pytest.raises(ValueError):
        integrate_resources(
            original,
            stations=calendars,
            contacts=contacts,
            batteries=(profile,),
            activities=loads,
            **kwargs,
        )


def test_provenance_determinism_and_no_silent_reintegration():
    original = scenario()
    loads, contacts = activities_for(original), contacts_for(original)
    kwargs = dict(
        stations=(station(),),
        contacts=contacts,
        batteries=(battery(),),
        activities=loads,
    )
    first = integrate_resources(original, **kwargs)
    kwargs.update(contacts=tuple(reversed(contacts)), activities=tuple(reversed(loads)))
    second = integrate_resources(original, **kwargs)
    assert fingerprint(first) == fingerprint(second)
    assert {"h3-r2", "h3-calendar", "h3-battery", "h3-generation", "h3-load"} <= {
        s.source_id for s in first.sources
    }
    assert any(s.kind == "derived" and s.version for s in first.sources)
    with pytest.raises(ValueError):
        integrate_resources(first, **kwargs)
    with pytest.raises(ValueError):
        battery_trace(plan(original), first, battery(), loads)


def test_same_integrated_problem_reaches_classical_and_quantum():
    original = scenario()
    integrated = integrate_resources(
        original,
        stations=(station(),),
        contacts=contacts_for(original),
        batteries=(battery(initial=10800, capacity=10800, generation=False),),
        activities=activities_for(original),
    )
    classical = solve_exact(integrated)
    quantum = run_qaoa(integrated, evaluations=8, shots=128)
    assert classical.input_fingerprint == quantum.input_fingerprint
    assert classical.validation.exact_score == "13/5"
    assert quantum.uniform_feasible_probability == pytest.approx(24 / 64, abs=1e-12)
    # The spilled-energy case is genuinely higher order; keep the classical result,
    # and report the mapping limit instead of quietly changing the quantum problem.
    higher = integrate_resources(
        original, batteries=(battery(),), activities=activities_for(original)
    )
    with pytest.raises(ValueError, match="higher.order|slack"):
        compile_qubo(higher)


@pytest.mark.parametrize("seed", range(8))
def test_storage_inequalities_match_independent_hourly_oracle(seed):
    rng = random.Random(seed)
    original = scenario()
    capacity = rng.randint(2, 8) * 3600
    initial = rng.randint(0, capacity // 3600) * 3600
    generation_w = rng.randint(0, 3)
    profile = battery(initial=initial, capacity=capacity, generation=False).model_copy(
        update={
            "generation": (
                GenerationSegment(
                    window=window(0, 1),
                    power_w=generation_w,
                    source=source("h3-generation"),
                ),
            )
        }
    )
    loads = tuple(
        load.model_copy(update={"power_w": rng.randint(1, 3)})
        for load in activities_for(original)
    )
    integrated = integrate_resources(original, batteries=(profile,), activities=loads)
    lookup = {load.option_id: load for load in loads}
    feasible = 0
    for choice in product(range(1, 5), repeat=3):
        selected = tuple(
            f"mission-{i}-option-{slot}" for i, slot in enumerate(choice, 1)
        )
        energy, valid = initial, True
        for hour in range(4):
            consumption_w = sum(
                lookup[oid].power_w
                for oid in selected
                if lookup[oid].window.start_utc.hour == hour
            )
            energy = min(
                capacity,
                energy
                + (generation_w if hour == 0 else 0) * 3600
                - consumption_w * 3600,
            )
            valid = valid and energy >= 0
        report = validate_plan(plan(integrated, choice), integrated)
        assert (report.status == "feasible") == valid
        feasible += valid
    assert solve_exact(integrated).feasible_count == feasible
