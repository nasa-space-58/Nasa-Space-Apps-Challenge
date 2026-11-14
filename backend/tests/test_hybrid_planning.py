"""H1 static planning acceptance tests; hand-derived and discrete-slot oracles.

Float display assertions use 1e-12 absolute tolerance. Decisions use exact
rational arithmetic; H0 fixtures are synthetic, not NASA reference missions.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from itertools import product
from pathlib import Path
import random

import pytest

from app.hybrid_planning import (
    CandidateInput,
    SolverResult,
    generate_candidates,
    select_independent,
    solve_exact,
    validate_plan,
)
from shared.hybrid import Plan, Scenario, fingerprint, load_scenario

EXAMPLES = Path("/workspace/shared/examples/hybrid")
if not EXAMPLES.exists():
    EXAMPLES = Path(__file__).resolve().parents[2] / "shared/examples/hybrid"


def document(name="shared-conflict"):
    return load_scenario(EXAMPLES / f"{name}.json").model_dump(mode="json")


def problem(name="shared-conflict"):
    return Scenario.model_validate(document(name))


def proposal(scenario, ids=None):
    return Plan(
        plan_id="test-plan",
        scenario_id=scenario.scenario_id,
        scenario_revision=scenario.revision,
        input_fingerprint=fingerprint(scenario),
        state_revisions={s.state_id: s.revision for s in scenario.states},
        selected_option_ids=ids or tuple(f"mission-{i}-option-{i}" for i in (1, 2, 3)),
        execution_mode="classical",
        solver_name="manual-test",
    )


def codes(report):
    return {issue.code for issue in report.issues}


def window(start, end):
    epoch = datetime(2026, 11, 4, tzinfo=timezone.utc)
    return {
        "start_utc": (epoch + timedelta(hours=start)).isoformat(),
        "end_utc": (epoch + timedelta(hours=end)).isoformat(),
    }


def one_option_each():
    data = document("independent")
    data["candidates"] = [
        c for c in data["candidates"] if c["option_id"].endswith("-1")
    ]
    return data


@pytest.mark.parametrize(
    "name,count,score,ties",
    [
        ("independent", 64, 3.0, 1),
        ("shared-conflict", 24, 2.6, 6),
        ("infeasible", 0, None, 0),
    ],
)
def test_exact_known_benchmarks(name, count, score, ties):
    scenario = problem(name)
    before = fingerprint(scenario)
    result = solve_exact(scenario)
    assert result.total_combinations == result.evaluated_combinations == 64
    assert result.feasible_count == count
    assert result.infeasible_count == 64 - count
    assert result.search_complete is True
    assert result.input_fingerprint == before == fingerprint(scenario)
    assert result.elapsed_s >= 0
    assert SolverResult.model_validate_json(result.model_dump_json()) == result
    if score is None:
        assert result.status == "infeasible"
        assert result.plan is None and result.validation is None
        assert result.optimal_plan_count is None
    else:
        assert result.status == "optimal"
        assert result.optimal_plan_count == ties
        assert result.validation.status == "feasible"
        assert result.validation.score == pytest.approx(score, abs=1e-12)
        assert result.plan.validation_status == "unvalidated"
        assert validate_plan(result.plan, scenario) == result.validation


def test_joint_selection_ties_and_independent_conflict():
    scenario = problem()
    joint = solve_exact(scenario)
    independent = select_independent(scenario)
    assert joint.plan.selected_option_ids == (
        "mission-1-option-1",
        "mission-2-option-2",
        "mission-3-option-3",
    )
    assert joint.validation.exact_score == "13/5"
    assert joint.validation.metric_totals["science_value"] == pytest.approx(
        26, abs=1e-12
    )
    assert independent.status == "proposal"
    assert not independent.search_complete and independent.optimal_plan_count is None
    assert independent.validation.status == "infeasible"
    assert independent.validation.score == pytest.approx(3, abs=1e-12)
    conflict = next(i for i in independent.validation.issues if i.code == "capacity")
    assert conflict.required == "3" and conflict.capacity == "1"
    assert conflict.resource_id == "shared-antenna" and conflict.unit == "channel"
    assert len(conflict.option_ids) == 3 and conflict.window.start_utc.hour == 0
    assert joint.failure_counts["capacity"] == 40


@pytest.mark.parametrize(
    "limit,evaluated,has_plan", [(1, 1, False), (7, 7, True), (63, 63, True)]
)
def test_search_limit_never_claims_optimal_or_infeasible(limit, evaluated, has_plan):
    result = solve_exact(problem(), max_evaluations=limit)
    assert result.status == "limit_reached"
    assert result.evaluated_combinations == evaluated
    assert result.feasible_count + result.infeasible_count == evaluated
    assert result.search_complete is False and result.optimal_plan_count is None
    assert (result.plan is not None) is has_plan
    assert result.max_evaluations == limit
    assert "search_limit" in codes(result)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "64", None])
def test_invalid_search_limits(limit):
    with pytest.raises(ValueError, match="max_evaluations"):
        solve_exact(problem(), max_evaluations=limit)


def test_limit_exactly_number_of_assignments_finishes():
    assert solve_exact(problem(), max_evaluations=64).status == "optimal"


@pytest.mark.parametrize("solver", [solve_exact, select_independent])
@pytest.mark.parametrize("missing", ["capacity", "metric"])
def test_incomplete_inputs_block_selection(solver, missing):
    data = document()
    if missing == "capacity":
        data["resources"][0]["capacity"] = None
    else:
        data["candidates"][-1]["metrics"]["science_value"] = None
    result = solver(Scenario.model_validate(data))
    assert result.status == "incomplete"
    assert result.plan is None and result.evaluated_combinations == 0
    assert not result.search_complete
    assert f"unknown_{missing}" in codes(result)


def test_validator_only_requires_selected_metrics():
    data = document()
    data["candidates"][-1]["metrics"]["science_value"] = None
    scenario = Scenario.model_validate(data)
    assert validate_plan(proposal(scenario), scenario).status == "feasible"
    selected = proposal(
        scenario, ("mission-1-option-1", "mission-2-option-2", "mission-3-option-4")
    )
    report = validate_plan(selected, scenario)
    assert report.status == "incomplete" and report.score is None
    assert "unknown_metric" in codes(report)


def test_unused_or_zero_demands_do_not_require_capacity_or_availability():
    data = one_option_each()
    data["resources"][0]["capacity"] = None
    data["resources"][0]["availability"] = []
    for candidate in data["candidates"]:
        candidate["demands"][0]["amount"] = 0
    assert solve_exact(Scenario.model_validate(data)).status == "optimal"
    for candidate in data["candidates"]:
        candidate["demands"] = []
    assert solve_exact(Scenario.model_validate(data)).status == "optimal"


@pytest.mark.parametrize(
    "kind", ["resource_outage", "thrust_loss", "sensor_degradation"]
)
def test_events_never_silently_ignored(kind):
    data = document("resource-outage")
    if kind == "thrust_loss":
        data["events"][0]["payload"] = {
            "kind": kind,
            "vehicle_id": "vehicle-1",
            "available_thrust_n": 1,
        }
    elif kind == "sensor_degradation":
        data["events"][0]["payload"] = {
            "kind": kind,
            "vehicle_id": "vehicle-1",
            "sensor_id": "lidar",
            "position_std_m": [1, 1, 1],
        }
    scenario = Scenario.model_validate(data)
    for result in (
        solve_exact(scenario),
        select_independent(scenario),
        validate_plan(proposal(scenario), scenario),
    ):
        assert result.status == "unsupported"
        assert "unapplied_events" in codes(result)


@pytest.mark.parametrize(
    "ids",
    [
        ("mission-1-option-1",),
        (
            "mission-1-option-1",
            "mission-1-option-2",
            "mission-2-option-3",
            "mission-3-option-4",
        ),
    ],
)
def test_exactly_one_option_per_mission(ids):
    scenario = problem()
    report = validate_plan(proposal(scenario, ids), scenario)
    assert report.status == "infeasible" and "selection_count" in codes(report)


@pytest.mark.parametrize(
    "mutation", ["revision", "capacity", "state", "unknown_option", "duplicate_option"]
)
def test_rejects_stale_or_malformed_proposal(mutation):
    scenario = problem("resource-outage" if mutation == "state" else "shared-conflict")
    plan = proposal(scenario)
    if mutation == "revision":
        plan = plan.model_copy(update={"scenario_revision": 99})
    elif mutation == "capacity":
        data = scenario.model_dump(mode="json")
        data["resources"][0]["capacity"] = 2
        scenario = Scenario.model_validate(data)
    elif mutation == "state":
        plan = plan.model_copy(update={"state_revisions": {}})
    elif mutation == "unknown_option":
        plan = plan.model_copy(update={"selected_option_ids": ("missing-option",)})
    else:
        plan = plan.model_copy(
            update={"selected_option_ids": ("mission-1-option-1",) * 2}
        )
    with pytest.raises(ValueError):
        validate_plan(plan, scenario)


def test_nested_mutation_revalidated():
    scenario = problem()
    scenario.candidates[0].metrics["science_value"] = float("nan")
    with pytest.raises(ValueError):
        solve_exact(scenario)


def test_deadline_equal_allowed_later_rejected():
    data = document()
    data["missions"][0]["deadline_utc"] = window(0, 1)["end_utc"]
    scenario = Scenario.model_validate(data)
    assert validate_plan(proposal(scenario), scenario).status == "feasible"
    late = proposal(
        scenario, ("mission-1-option-4", "mission-2-option-2", "mission-3-option-3")
    )
    report = validate_plan(late, scenario)
    assert report.status == "infeasible" and "deadline" in codes(report)


def test_explicit_incompatibilities():
    data = document()
    data["incompatibilities"] = [
        {
            "constraint_id": "pair",
            "option_ids": ["mission-1-option-1", "mission-2-option-2"],
        }
    ]
    scenario = Scenario.model_validate(data)
    assert "incompatibility" in codes(validate_plan(proposal(scenario), scenario))
    result = solve_exact(scenario)
    assert result.status == "optimal"
    assert not {"mission-1-option-1", "mission-2-option-2"} <= set(
        result.plan.selected_option_ids
    )


@pytest.mark.parametrize(
    "availability,feasible",
    [
        ([window(0, 1), window(1, 12)], True),
        ([window(0, 0.5), window(0.6, 12)], False),
        ([], False),
    ],
)
def test_availability_union_and_gaps(availability, feasible):
    data = one_option_each()
    data["resources"][0]["availability"] = availability
    scenario = Scenario.model_validate(data)
    ids = tuple(c.option_id for c in scenario.candidates)
    report = validate_plan(proposal(scenario, ids), scenario)
    assert report.status == ("feasible" if feasible else "infeasible")
    if not feasible:
        assert "availability" in codes(report)


def test_half_open_intervals_and_small_overlap():
    scenario = problem()
    assert validate_plan(proposal(scenario), scenario).status == "feasible"
    data = document()
    candidate = next(
        c for c in data["candidates"] if c["option_id"] == "mission-2-option-2"
    )
    candidate["window"] = window(0.999, 2)
    candidate["demands"][0]["window"] = window(0.999, 2)
    changed = Scenario.model_validate(data)
    assert "capacity" in codes(validate_plan(proposal(changed), changed))


def test_decimal_capacity_boundary_and_multiple_demands():
    data = one_option_each()
    data["resources"][0]["capacity"] = 0.3
    for candidate, amount in zip(data["candidates"], (0.1, 0.2, 0)):
        candidate["demands"][0]["amount"] = amount
    assert solve_exact(Scenario.model_validate(data)).status == "optimal"
    data["candidates"][0]["demands"].append(
        deepcopy(data["candidates"][0]["demands"][0])
    )
    scenario = Scenario.model_validate(data)
    report = validate_plan(
        proposal(scenario, tuple(c.option_id for c in scenario.candidates)), scenario
    )
    assert report.status == "infeasible"
    assert next(i for i in report.issues if i.code == "capacity").required == "2/5"


@pytest.mark.parametrize(
    "capacity,status", [(30, "optimal"), (29.9, "infeasible"), (None, "incomplete")]
)
def test_cumulative_energy_counts_nonoverlapping_demands(capacity, status):
    data = one_option_each()
    data["resources"][0].update(mode="budget", unit="J", capacity=capacity)
    for i, candidate in enumerate(data["candidates"]):
        candidate["window"] = window(i, i + 1)
        candidate["demands"][0].update(unit="J", amount=10, window=window(i, i + 1))
    result = solve_exact(Scenario.model_validate(data))
    assert result.status == status
    if status == "infeasible":
        assert result.failure_counts["budget"] == 1


def test_weighted_minimize_objective_and_negative_scores():
    data = document("independent")
    data["objectives"] = [
        {
            "metric": "science_value",
            "unit": "score",
            "direction": "maximize",
            "weight": 1,
            "scale": 10,
        },
        {
            "metric": "cost",
            "unit": "J",
            "direction": "minimize",
            "weight": 2,
            "scale": 10,
        },
    ]
    for candidate in data["candidates"]:
        slot = int(candidate["option_id"][-1])
        candidate["metrics"]["cost"] = {1: 100, 2: 20, 3: 10, 4: 5}[slot]
    result = solve_exact(Scenario.model_validate(data))
    assert result.validation.score == pytest.approx(-1.2, abs=1e-12)
    assert result.validation.exact_score == "-6/5"
    assert all(option.endswith("-4") for option in result.plan.selected_option_ids)


def test_extreme_objective_fails_instead_of_exporting_infinity():
    data = document()
    data["objectives"][0]["scale"] = 1e-308
    data["candidates"][0]["metrics"]["science_value"] = 1e308
    with pytest.raises(ValueError, match="finite|range"):
        solve_exact(Scenario.model_validate(data))


def test_determinism_under_collection_permutation():
    original = solve_exact(problem()).model_dump(exclude={"elapsed_s"})
    data = document()
    for key in ("missions", "candidates", "resources", "objectives", "sources"):
        data[key].reverse()
    repeated = solve_exact(Scenario.model_validate(data)).model_dump(
        exclude={"elapsed_s"}
    )
    assert repeated == original


def test_candidate_generation_stable_ids_preserves_unknowns_and_sources():
    scenario = problem()
    rows = tuple(
        CandidateInput.model_validate(c.model_dump(exclude={"option_id"}))
        for c in scenario.candidates
    )
    first = generate_candidates(rows)
    assert first == generate_candidates(tuple(reversed(rows)))
    assert len(first) == 12 and len({c.option_id for c in first}) == 12
    expected = {c.model_dump_json(exclude={"option_id"}) for c in scenario.candidates}
    assert {c.model_dump_json(exclude={"option_id"}) for c in first} == expected
    raw = rows[0].model_dump(mode="json")
    raw["metrics"]["science_value"] = None
    changed = generate_candidates((CandidateInput.model_validate(raw),))[0]
    assert changed.metrics["science_value"] is None
    assert changed.source_ids == rows[0].source_ids
    assert changed.option_id not in {c.option_id for c in first}
    # Replacing candidate IDs requires rebuilding any ID-based constraints explicitly.
    data = scenario.model_dump(mode="json")
    data["candidates"] = [c.model_dump(mode="json") for c in first]
    assert solve_exact(Scenario.model_validate(data)).validation.score == pytest.approx(
        2.6, abs=1e-12
    )


@pytest.mark.parametrize(
    "bad", ["empty", "duplicate", "outside_window", "missing_source", "nonfinite"]
)
def test_candidate_generation_rejects_bad_inputs(bad):
    candidate = problem().candidates[0]
    row = CandidateInput.model_validate(candidate.model_dump(exclude={"option_id"}))
    rows = (row,)
    if bad == "empty":
        rows = ()
    elif bad == "duplicate":
        rows = (row, row)
    elif bad == "outside_window":
        row = row.model_copy(update={"window": problem().candidates[1].window})
        rows = (row,)
    elif bad == "missing_source":
        rows = (row.model_copy(update={"source_ids": ()}),)
    else:
        row.metrics["science_value"] = float("nan")
    with pytest.raises(ValueError):
        generate_candidates(rows)


@pytest.mark.parametrize("seed", range(12))
def test_exact_against_independent_discrete_slot_oracle(seed):
    rng = random.Random(seed)
    data = document()
    data["candidates"] = [
        c for c in data["candidates"] if c["option_id"].endswith(("-1", "-2"))
    ]
    capacity = rng.randint(1, 3)
    data["resources"][0]["capacity"] = capacity
    deadlines = {}
    for mission in data["missions"]:
        deadlines[mission["mission_id"]] = rng.randint(2, 5)
        mission["deadline_utc"] = window(0, deadlines[mission["mission_id"]])["end_utc"]
    cells = {}
    for candidate in data["candidates"]:
        start, length = rng.randint(0, 3), rng.randint(1, 2)
        amount, value = rng.randint(1, 2), rng.randint(-5, 10)
        candidate["window"] = window(start, start + length)
        candidate["demands"][0].update(
            amount=amount, window=window(start, start + length)
        )
        candidate["metrics"]["science_value"] = value
        cells[candidate["option_id"]] = (start, start + length, amount, value)
    feasible = []
    for choice in product((1, 2), repeat=3):
        ids = tuple(f"mission-{i}-option-{slot}" for i, slot in enumerate(choice, 1))
        if any(
            cells[oid][1] > deadlines[f"mission-{i}"] for i, oid in enumerate(ids, 1)
        ):
            continue
        # Independent oracle: integer occupancy array, not a timestamp sweep.
        occupancy = [
            sum(cells[oid][2] for oid in ids if cells[oid][0] <= hour < cells[oid][1])
            for hour in range(6)
        ]
        if max(occupancy) <= capacity:
            feasible.append((sum(Fraction(cells[oid][3], 10) for oid in ids), ids))
    result = solve_exact(Scenario.model_validate(data))
    assert result.evaluated_combinations == 8
    assert result.feasible_count == len(feasible)
    if feasible:
        best = max(score for score, _ in feasible)
        expected = min(ids for score, ids in feasible if score == best)
        assert result.status == "optimal"
        assert result.plan.selected_option_ids == expected
        assert result.validation.exact_score == str(best)
        assert result.optimal_plan_count == sum(score == best for score, _ in feasible)
    else:
        assert result.status == "infeasible" and result.plan is None
