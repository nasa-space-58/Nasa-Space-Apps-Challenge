"""R13 rover target/route selection on R8 route candidates.

Energy reference: out-and-back 2 * (P * t + m * g * climb / efficiency) with
g = GM / R^2 = 1.6242 m/s^2 (DE440 GM, IAU/IAG radius; shared.constants).
Hand case: the 40000 s budget admits a single excursion, and T1 on the
direct (20 deg) route has the best science-minus-energy score.
"""

from fractions import Fraction

import numpy as np
import pytest
from pydantic import ValidationError
from rasterio.transform import from_origin

from app.alternatives import LocalTerrain
from app.application_rover import (
    APPLICATION,
    VARIANT_SLOPE_LIMITS_DEG,
    RoverEnergyModel,
    RoverProblem,
    build_rover,
    generate_rover,
    route_comm_gap_s,
    route_energy_j,
    rover_problem_from_routes,
)
from app.quantum_applications import audit_qubo, solve_exact, solve_milp
from app.surface_routes import RouteRequest, plan_routes
from shared.constants import MOON_GM_M3_S2, MOON_REFERENCE_RADIUS_M as R

G = MOON_GM_M3_S2 / R**2
STEREO = f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m +no_defs"


def waypoints(blocked, total=5, dark=0):
    return [
        {
            "earth": {"state": "blocked" if i < blocked else "visible"},
            "sun": {"state": "dark" if i < dark else "full"},
        }
        for i in range(total)
    ]


def route(target, duration=None, gain=0.0, blocked=0, dark=0, reason=None):
    if reason:
        return {
            "target_id": target,
            "reachable": False,
            "reason": reason,
            "detail": reason,
        }
    return {
        "target_id": target,
        "reachable": True,
        "reason": None,
        "detail": None,
        "length_m": duration * 0.1,
        "duration_s": duration,
        "elevation_gain_m": gain,
        "max_slope_deg": 8.0,
        "waypoints": waypoints(blocked, dark=dark),
        "no_earth_waypoints": blocked,
        "shadowed_waypoints": dark,
    }


RESULTS = {
    "conservative": {
        "settings": {"slope_limit_deg": 12.0},
        "result_id": "a" * 64,
        "routes": [
            route("T1", 10000.0, 5.0, dark=1),
            route("T2", reason="no_path"),
            route("T3", 20000.0, 0.0, blocked=4),
        ],
    },
    "direct": {
        "settings": {"slope_limit_deg": 20.0},
        "result_id": "b" * 64,
        "routes": [
            route("T1", 8000.0, 20.0),
            route("T2", 15000.0, 0.0, blocked=1),
            route("T3", reason="target_impassable"),
        ],
    },
}
VALUES = {"T1": 5.0, "T2": 3.0, "T3": 4.0}


def problem(**changes):
    body = dict(time_budget_s=40000.0, energy_budget_j=1e9, max_comm_gap_s=10000.0)
    body.update(changes)
    return rover_problem_from_routes("rover-test", RESULTS, VALUES, **body)


def energy(duration, gain):
    return 2 * (100.0 * duration + 300.0 * G * gain / 0.5)


def test_energy_and_gap_models():
    model = RoverEnergyModel()
    assert route_energy_j(1000.0, 10.0, model) == pytest.approx(energy(1000.0, 10.0))
    assert route_energy_j(0.0, 0.0, model) == 0
    assert route_comm_gap_s(route("T", 1000.0, blocked=2)) == pytest.approx(400.0)
    assert route_comm_gap_s(route("T", 1000.0)) == 0
    with pytest.raises(ValueError):
        route_comm_gap_s(dict(route("T", 1000.0), no_earth_waypoints=None))
    with pytest.raises(ValueError):
        route_energy_j(-1.0, 0.0, model)


def test_candidates_and_coded_rejections_from_r8_routes():
    p = problem()
    assert [(c.target_id, c.variant_id) for c in p.candidates] == [
        ("T1", "conservative"),
        ("T1", "direct"),
        ("T2", "direct"),
    ]
    codes = {(r.target_id, r.variant_id): r.code for r in p.rejections}
    assert codes == {
        ("T2", "conservative"): "no_path",
        ("T3", "conservative"): "comm_gap_exceeds_limit",
        ("T3", "direct"): "target_impassable",
    }
    first = p.candidates[0]
    assert first.slope_limit_deg == 12.0 and first.route_result_id == "a" * 64
    assert first.energy_j == pytest.approx(energy(10000.0, 5.0))
    assert first.shadowed_fraction == pytest.approx(0.2)
    assert p.candidates[2].comm_gap_s == pytest.approx(3000.0)


def test_hand_optimum_and_metrics():
    instance = build_rover(problem())
    assert instance.application == APPLICATION == "rover_routes"
    assert instance.variable_labels == ("T1@conservative", "T1@direct", "T2@direct")
    exact = solve_exact(instance)
    assert exact.bits == (0, 1, 0)
    assert float(Fraction(exact.objective)) == pytest.approx(
        5 - energy(8000.0, 20.0) / 1e7
    )
    metrics = instance.evaluate(exact.bits).metrics
    assert metrics["science_return"] == pytest.approx(5.0)
    assert metrics["targets_visited"] == 1
    assert metrics["traverse_time_s"] == pytest.approx(16000.0)
    assert metrics["energy_used_j"] == pytest.approx(energy(8000.0, 20.0))
    assert metrics["comm_gap_total_s"] == 0 and metrics["comm_gap_max_s"] == 0
    milp = solve_milp(instance)
    assert float(Fraction(milp.objective)) == pytest.approx(
        float(Fraction(exact.objective)), abs=1e-9
    )


def test_rules_are_enforced():
    instance = build_rover(problem())
    twice = instance.evaluate((1, 1, 0))
    assert any(i.startswith("target_multiple_routes") for i in twice.issues)
    slow = instance.evaluate((0, 1, 1))
    assert not slow.feasible and any(i.startswith("time_budget") for i in slow.issues)
    assert slow.metrics["comm_gap_total_s"] == pytest.approx(3000.0)
    poor = build_rover(problem(energy_budget_j=1e6)).evaluate((0, 1, 0))
    assert any(i.startswith("energy_budget") for i in poor.issues)
    assert instance.evaluate((0, 0, 0)).feasible


def test_qubo_exactness_follows_binding_budgets():
    assert build_rover(problem()).qubo.exact is False
    relaxed = build_rover(problem(time_budget_s=1e7))
    assert relaxed.qubo.exact is True
    audit = audit_qubo(relaxed)
    assert audit.feasible_energy_matches_objective and audit.ground_state_optimal
    assert audit.infeasible_below_optimum == 0


def test_sky_must_be_evaluated():
    unevaluated = {
        "conservative": dict(
            RESULTS["conservative"],
            routes=[dict(route("T1", 1000.0), no_earth_waypoints=None)],
        )
    }
    with pytest.raises(ValueError):
        rover_problem_from_routes(
            "x",
            unevaluated,
            VALUES,
            time_budget_s=1.0,
            energy_budget_j=1.0,
            max_comm_gap_s=1.0,
        )


def test_candidates_need_science_values():
    with pytest.raises((ValueError, ValidationError)):
        rover_problem_from_routes(
            "x",
            RESULTS,
            {"T1": 1.0},
            time_budget_s=1.0,
            energy_budget_j=1.0,
            max_comm_gap_s=1e5,
        )


def test_contract_rejects_duplicate_candidates():
    p = problem()
    with pytest.raises(ValidationError):
        RoverProblem(
            **(p.model_dump() | {"candidates": [p.candidates[0].model_dump()] * 2})
        )


# ------------------------------------------------------------ real R8 routes


def flat_terrain(size=101):
    half = size * 80 / 2
    return LocalTerrain(
        heights_m=np.zeros((size, size)),
        transform=from_origin(-half, half, 80, 80),
        site_xy_m=(0.0, 0.0),
        north_unit_xy=(0.0, 1.0),
        east_unit_xy=(1.0, 0.0),
        pixel_m=80.0,
        crs=STEREO,
    )


def frames(hours=12):
    return [
        {
            "utc": f"2026-11-04T{h:02d}:00:00Z",
            "sun": {"azimuth_deg": 90.0, "elevation_deg": 3.0},
            "earth": {"azimuth_deg": 0.0, "elevation_deg": 5.0},
        }
        for h in range(hours + 1)
    ]


def flat_horizon(latitude_deg, longitude_deg_east, altitude_m):
    return {
        "directions": [
            {"azimuth_deg": float(a), "elevation_deg": 0.0} for a in range(360)
        ]
    }


def test_problem_from_real_r8_routes():
    results = {}
    for variant, slope in VARIANT_SLOPE_LIMITS_DEG.items():
        request = RouteRequest(
            site_id="pole",
            latitude_deg=-90.0,
            longitude_deg_east=0.0,
            targets=[
                {"target_id": "N", "bearing_deg": 0.0, "distance_m": 800.0},
                {"target_id": "E", "bearing_deg": 90.0, "distance_m": 1200.0},
            ],
            slope_limit_deg=slope,
            speed_mps=0.1,
        )
        results[variant] = plan_routes(request, flat_terrain(), frames(), flat_horizon)
    p = rover_problem_from_routes(
        "real",
        results,
        {"N": 2.0, "E": 3.0},
        time_budget_s=1e6,
        energy_budget_j=1e9,
        max_comm_gap_s=1.0,
    )
    assert len(p.candidates) == 4 and not p.rejections
    assert {c.route_result_id for c in p.candidates} == {
        r["result_id"] for r in results.values()
    }
    assert all(c.comm_gap_s == 0 for c in p.candidates)
    north = next(c for c in p.candidates if c.target_id == "N")
    assert north.length_m == pytest.approx(800.0, rel=0.1)
    assert north.duration_s == pytest.approx(north.length_m / 0.1)
    exact = solve_exact(build_rover(p))
    assert exact.status == "optimal" and sum(exact.bits) == 2


def test_generator_uses_r8_and_is_seeded():
    first = generate_rover(3, targets=4)
    assert first == generate_rover(3, targets=4)
    assert first != generate_rover(4, targets=4)
    assert set(first.science_values) and len(first.science_values) == 4
    assert {c.variant_id for c in first.candidates} <= set(VARIANT_SLOPE_LIMITS_DEG)
    assert all(len(c.route_result_id) == 64 for c in first.candidates)
    total = len(first.candidates) + len(first.rejections)
    assert total == 4 * len(VARIANT_SLOPE_LIMITS_DEG)
    instance = build_rover(first)
    exact = solve_exact(instance)
    milp = solve_milp(instance)
    assert float(Fraction(exact.objective)) == pytest.approx(
        float(Fraction(milp.objective)), abs=1e-9
    )
    assert exact.status == "optimal"
