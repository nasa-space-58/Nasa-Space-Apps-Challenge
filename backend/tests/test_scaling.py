"""H10 exact-search contracts and independent combinatorial oracle."""

from itertools import product
import pytest
from pydantic import ValidationError
from app.scaling import ScalingCase, make_problem, check_assignment, enumerate_problem


@pytest.mark.parametrize("missions,options", [(1, 2), (2, 3), (3, 4), (5, 2)])
def test_enumeration_matches_independent_oracle(missions, options):
    p = make_problem(ScalingCase(missions=missions, options=options))
    valid = [
        x
        for x in product(range(options), repeat=missions)
        if all(x.count(i) <= p["capacities"][i] for i in range(options))
    ]
    scores = [sum(p["utilities"][m][o] for m, o in enumerate(x)) for x in valid]
    r = enumerate_problem(p, max_evaluations=10000, time_limit_s=10)
    assert r["status"] == "optimal"
    assert r["evaluated"] == options**missions
    assert r["feasible_count"] == len(valid)
    assert r["score"] == max(scores)
    assert check_assignment(p, tuple(r["selection"]))["feasible"]


def test_identity_and_qubits():
    c = ScalingCase(missions=3, options=3)
    a, b = make_problem(c), make_problem(c)
    assert a == b
    assert a["combinations"] == 27
    assert a["logical_qubits"] == 6
    assert a["one_hot_variables"] == 9
    assert (
        a["fingerprint"]
        != make_problem(ScalingCase(missions=3, options=3, seed=18))["fingerprint"]
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"missions": 0, "options": 4},
        {"missions": 33, "options": 4},
        {"missions": True, "options": 4},
        {"missions": 3, "options": 1},
        {"missions": 3, "options": 9},
        {"missions": 3, "options": 4, "seed": -1},
    ],
)
def test_invalid_case(kwargs):
    with pytest.raises(ValidationError):
        ScalingCase(**kwargs)


@pytest.mark.parametrize(
    "selection", [(), (0, 0), (0, 0, 9), (0, 0, True), (0, 0, 1.0)]
)
def test_invalid_assignment(selection):
    with pytest.raises(ValueError):
        check_assignment(make_problem(ScalingCase(missions=3, options=4)), selection)


def test_conflicts_are_infeasible():
    p = make_problem(ScalingCase(missions=3, options=4))
    assert not check_assignment(p, (0, 0, 0))["feasible"]


def test_partial_search_is_not_optimal():
    p = make_problem(ScalingCase(missions=5, options=4))
    r = enumerate_problem(p, max_evaluations=3, time_limit_s=10)
    assert r["status"] == "limit_reached"
    assert r["evaluated"] == 3
    assert not r["complete"]


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_evaluation_budget(limit):
    with pytest.raises(ValueError):
        enumerate_problem(
            make_problem(ScalingCase(missions=2, options=2)), max_evaluations=limit
        )


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf"), True])
def test_invalid_time_budget(limit):
    with pytest.raises(ValueError):
        enumerate_problem(
            make_problem(ScalingCase(missions=2, options=2)), time_limit_s=limit
        )
