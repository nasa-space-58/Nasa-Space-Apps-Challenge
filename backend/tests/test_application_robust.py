"""R13 robust plan selection across R6 seeded uncertainty scenarios.

Hand case (10 scenarios): (a1, b1) succeed together 5 times at worst-case
cost 130 m; (a1, b2) would succeed 9 times but costs 300 m > 250 m; the
objective subtracts cost / 1000 m. Wilson interval from app.risk (Wilson 1927).
"""

import math
from fractions import Fraction

import pytest
from pydantic import ValidationError

from app.application_robust import (
    APPLICATION,
    LanderPlans,
    LanderSpec,
    PlanOption,
    RobustPlan,
    RobustProblem,
    build_robust,
    generate_robust,
    robust_problem_from_r6,
    synthetic_slope_map,
)
from app.quantum_applications import audit_qubo, solve_exact, solve_milp
from app.risk import RiskRequest, run_monte_carlo, wilson_interval
from app.descent import OrbitSettings


def plan(name, success, miss):
    return RobustPlan(
        plan_id=name,
        orbit_altitude_km=100.0,
        approach_azimuth_deg=180.0,
        success=tuple(bool(s) for s in success),
        miss_m=(float(miss),) * len(success),
        r6_result_id="0" * 64,
    )


def problem(**changes):
    body = dict(
        problem_id="robust-test",
        runs=10,
        seed=1,
        landers=(
            LanderPlans(
                lander_id="LA",
                site_id="A",
                plans=(plan("a1", [1] * 10, 100), plan("a2", [1] * 5 + [0] * 5, 50)),
            ),
            LanderPlans(
                lander_id="LB",
                site_id="B",
                plans=(
                    plan("b1", [0] * 5 + [1] * 5, 30),
                    plan("b2", [0] + [1] * 9, 200),
                ),
            ),
        ),
        cost_limit_m=250.0,
        cost_weight=1.0,
        cost_scale_m=1000.0,
    )
    body.update(changes)
    return RobustProblem(**body)


def has(evaluation, code):
    return any(issue.startswith(code) for issue in evaluation.issues)


def test_hand_optimum_objective_and_metrics():
    instance = build_robust(problem())
    assert instance.application == APPLICATION == "robust_plans"
    assert instance.variable_labels == ("LA:a1", "LA:a2", "LB:b1", "LB:b2")
    exact = solve_exact(instance)
    assert exact.bits == (1, 0, 1, 0)
    assert Fraction(exact.objective) == 5 - Fraction(130, 1000)
    metrics = instance.evaluate(exact.bits).metrics
    assert metrics["campaign_success_rate"] == pytest.approx(0.5)
    low, high = wilson_interval(5, 10)
    assert metrics["campaign_wilson95_low"] == pytest.approx(low)
    assert metrics["campaign_wilson95_high"] == pytest.approx(high)
    assert metrics["LA_success_rate"] == pytest.approx(1.0)
    assert metrics["LB_success_rate"] == pytest.approx(0.5)
    assert metrics["worst_case_cost_m"] == pytest.approx(130.0)
    assert metrics["mean_cost_m"] == pytest.approx(130.0)
    assert Fraction(solve_milp(instance).objective) == Fraction(exact.objective)


def test_other_pairs():
    instance = build_robust(problem())
    assert instance.evaluate((0, 1, 0, 1)).objective == 4 - Fraction(250, 1000)
    assert instance.evaluate((0, 1, 0, 1)).feasible  # 250 m equals the limit
    assert instance.evaluate((0, 1, 1, 0)).objective == -Fraction(80, 1000)


def test_worst_case_cost_limit_and_plan_count():
    instance = build_robust(problem())
    expensive = instance.evaluate((1, 0, 0, 1))
    assert not expensive.feasible and has(expensive, "worst_case_cost")
    assert expensive.violation > 0
    two = instance.evaluate((1, 1, 1, 0))
    assert not two.feasible and has(two, "plan_count")
    none = instance.evaluate((0, 0, 1, 0))
    assert not none.feasible and has(none, "plan_count")
    assert none.metrics["campaign_success_rate"] is None


def test_qubo_is_exact_and_audited():
    instance = build_robust(problem())
    assert instance.qubo.exact is True
    audit = audit_qubo(instance)
    assert audit.feasible_assignments == 3
    assert audit.feasible_energy_matches_objective
    assert audit.ground_state_optimal and audit.infeasible_below_optimum == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"runs": 9},
        {"runs": 11},
        {"cost_limit_m": 0.0},
    ],
)
def test_invalid_problems_are_rejected(changes):
    with pytest.raises(ValidationError):
        problem(**changes)


def test_duplicate_ids_are_rejected():
    base = problem()
    a = base.landers[0]
    with pytest.raises(ValidationError):
        problem(landers=(a, a))
    with pytest.raises(ValidationError):
        problem(
            landers=(
                LanderPlans(
                    lander_id="LA", site_id="A", plans=(a.plans[0], a.plans[0])
                ),
                base.landers[1],
            )
        )
    with pytest.raises(ValidationError):
        RobustPlan(
            plan_id="x",
            orbit_altitude_km=100.0,
            approach_azimuth_deg=0.0,
            success=(True, False),
            miss_m=(1.0,),
            r6_result_id="0" * 64,
        )


# ------------------------------------------------------------ R6 integration


def test_synthetic_slope_map_is_seeded_and_physical():
    first = synthetic_slope_map(3)
    again = synthetic_slope_map(3)
    other = synthetic_slope_map(4)
    assert first.slope_deg.shape == (101, 101) and first.pixel_m == 80.0
    assert (first.slope_deg == again.slope_deg).all()
    assert not (first.slope_deg == other.slope_deg).all()
    assert (first.slope_deg >= 0).all() and (first.slope_deg < 90).all()
    assert (first.slope_deg > 15).any() and (first.slope_deg < 10).any()


SPEC_A = LanderSpec(
    lander_id="LA",
    site_id="malapert-massif-r23",
    latitude_deg=-85.9948,
    longitude_deg_east=359.76422,
    terrain_height_m=0.0,
    slope_map=None,
    plans=(
        PlanOption(
            plan_id="low-s", orbit_altitude_km=100.0, approach_azimuth_deg=180.0
        ),
        PlanOption(plan_id="low-n", orbit_altitude_km=100.0, approach_azimuth_deg=0.0),
    ),
)


@pytest.fixture(scope="module")
def r6_problem():
    spec_b = LanderSpec(
        lander_id="LB",
        site_id="synthetic-b",
        latitude_deg=-88.5,
        longitude_deg_east=120.0,
        terrain_height_m=0.0,
        slope_map=synthetic_slope_map(3),
        plans=SPEC_A.plans,
    )
    return robust_problem_from_r6(
        "r6", (SPEC_A, spec_b), runs=10, seed=7, cost_limit_m=1e9
    )


def test_r6_outcomes_are_reproduced_per_scenario(r6_problem):
    assert r6_problem.runs == 10 and r6_problem.seed == 7
    for lander in r6_problem.landers:
        assert len(lander.plans) == 2
        for item in lander.plans:
            assert len(item.success) == len(item.miss_m) == 10
            assert len(item.r6_result_id) == 64
            assert all(m >= 0 for m in item.miss_m)
    first = r6_problem.landers[0].plans[0]
    direct = run_monte_carlo(
        RiskRequest(
            site_id=SPEC_A.site_id,
            latitude_deg=SPEC_A.latitude_deg,
            longitude_deg_east=SPEC_A.longitude_deg_east,
            orbit=OrbitSettings(altitude_km=100.0),
            approach_azimuth_deg=180.0,
            runs=10,
            seed=7,
        ),
        0.0,
        None,
    )
    assert first.r6_result_id == direct["result_id"]
    points = direct["landing"]["points"]
    assert first.success == tuple(p["success"] for p in points)
    for value, point in zip(first.miss_m, points):
        assert value == pytest.approx(math.hypot(point["down_m"], point["cross_m"]))


def test_r6_problem_builds_and_solves(r6_problem):
    instance = build_robust(r6_problem)
    assert len(instance.variable_labels) == 4
    exact = solve_exact(instance)
    milp = solve_milp(instance)
    assert exact.status == "optimal"
    assert Fraction(exact.objective) == Fraction(milp.objective)
    rate = instance.evaluate(exact.bits).metrics["campaign_success_rate"]
    assert 0 <= rate <= 1


def test_generated_campaign():
    generated = generate_robust(5, runs=10)
    assert generated.runs == 10 and generated.seed == 5
    assert [item.lander_id for item in generated.landers] == ["LA", "LB"]
    assert all(len(item.plans) == 4 for item in generated.landers)
    assert all(len({p.plan_id for p in item.plans}) == 4 for item in generated.landers)
    instance = build_robust(generated)
    exact = solve_exact(instance)
    assert exact.status == "optimal"
    assert instance.evaluate(exact.bits).feasible
    assert "common-cause" in generated.source_note
