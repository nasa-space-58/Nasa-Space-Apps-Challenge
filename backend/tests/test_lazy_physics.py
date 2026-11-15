"""C2 exact reuse, unchanged R6 safety and independent finite-table bounds."""

from fractions import Fraction
from itertools import product

import numpy as np
import pytest

from app import descent
from app.application_robust import RobustPlan, synthetic_slope_map
from app.lazy_physics import select_lazy
from app.physics_cache import PhysicsCache
from app.risk import RiskRequest, run_monte_carlo


def profile():
    return descent.load_profiles()["apollo-lm-11"]


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_cache_capacity_validation(limit):
    with pytest.raises(ValueError):
        PhysicsCache(max_entries=limit)


def test_cache_reuses_exact_inputs_and_isolates_mutation(monkeypatch):
    calls = []

    def engine(*args, **kwargs):
        calls.append((args, kwargs))
        return {"samples": [{"height_m": 1.0}]}

    monkeypatch.setattr(descent, "simulate_powered_descent", engine)
    cache = PhysicsCache()
    first = cache.simulate(profile(), 1737400.0, 1000.0, 1600.0)
    first["samples"][0]["height_m"] = 99
    second = cache.simulate(profile(), 1737400.0, 1000.0, 1600.0)
    assert second["samples"][0]["height_m"] == pytest.approx(1.0)
    assert len(calls) == 1
    assert cache.snapshot()["dynamics"]["hits"] == 1
    assert cache.snapshot()["dynamics"]["misses"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"site_radius_m": 1737401.0},
        {"pdi_range_m": 1001.0},
        {"periapsis_speed_mps": 1601.0},
        {"dt_s": 0.5},
        {"record": False},
        {"approach_s": 35.0},
        {"terminal_s": 10.0},
    ],
)
def test_every_dynamics_argument_invalidates_cache(monkeypatch, change):
    monkeypatch.setattr(descent, "simulate_powered_descent", lambda *a, **kw: {})
    cache = PhysicsCache()
    args = dict(
        profile=profile(),
        site_radius_m=1737400.0,
        pdi_range_m=1000.0,
        periapsis_speed_mps=1600.0,
    )
    cache.simulate(**args)
    cache.simulate(**(args | change))
    assert cache.snapshot()["dynamics"]["misses"] == 2


def test_profile_change_and_lru_eviction(monkeypatch):
    monkeypatch.setattr(descent, "simulate_powered_descent", lambda *a, **kw: {})
    cache = PhysicsCache(max_entries=1)
    p = profile()
    cache.simulate(p, 1, 2, 3)
    cache.simulate(p.model_copy(update={"isp_s": p.isp_s + 1}), 1, 2, 3)
    cache.simulate(p, 1, 2, 3)
    assert cache.snapshot()["entries"] == 1
    assert cache.snapshot()["dynamics"]["misses"] == 3


def test_exceptions_are_not_cached(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("engine failure")

    monkeypatch.setattr(descent, "simulate_powered_descent", broken)
    cache = PhysicsCache()
    for _ in range(2):
        with pytest.raises(ValueError, match="engine failure"):
            cache.simulate(profile(), 1, 2, 3)
    assert cache.snapshot()["entries"] == 0
    assert cache.snapshot()["dynamics"]["misses"] == 2


def test_disabled_cache_and_snapshot_isolation(monkeypatch):
    monkeypatch.setattr(descent, "simulate_powered_descent", lambda *a, **kw: {})
    cache = PhysicsCache(enabled=False)
    for _ in range(2):
        cache.simulate(profile(), 1, 2, 3)
    snapshot = cache.snapshot()
    assert snapshot["dynamics"]["misses"] == 2
    assert snapshot["entries"] == 0
    snapshot["dynamics"]["misses"] = 99
    assert cache.snapshot()["dynamics"]["misses"] == 2


def test_nominal_inputs_and_resolved_vehicle_invalidate(monkeypatch):
    monkeypatch.setattr(descent, "plan_descent", lambda *a, **kw: {"x": []})
    p = profile()
    monkeypatch.setattr(descent, "load_profiles", lambda: {"apollo-lm-11": p})
    cache = PhysicsCache()
    req = descent.DescentRequest(latitude_deg=-86, longitude_deg_east=0)
    cache.plan(req, 0)
    cache.plan(req, 0)
    cache.plan(req, 1)
    cache.plan(req.model_copy(update={"latitude_deg": -87}), 0)
    p = p.model_copy(update={"isp_s": p.isp_s + 1})
    cache.plan(req, 0)
    assert cache.snapshot()["nominal"]["misses"] == 4
    assert cache.snapshot()["nominal"]["hits"] == 1


@pytest.mark.parametrize("azimuth", [0.0, 180.0])
def test_full_r6_output_identical_and_hazard_rechecked(azimuth):
    req = RiskRequest(
        latitude_deg=-86,
        longitude_deg_east=0,
        runs=10,
        seed=821,
        approach_azimuth_deg=azimuth,
    )
    terrain = synthetic_slope_map(821)
    cache = PhysicsCache()
    plain = run_monte_carlo(req, 0, terrain)
    cold = run_monte_carlo(req, 0, terrain, physics_cache=cache)
    warm = run_monte_carlo(req, 0, terrain, physics_cache=cache)
    assert cold == plain == warm  # exact deterministic computation, no approximation
    assert cache.snapshot()["dynamics"]["misses"] == 10
    assert cache.snapshot()["dynamics"]["hits"] == 10
    terrain.slope_deg[:] = np.nan
    unknown = run_monte_carlo(req, 0, terrain, physics_cache=cache)
    assert unknown["failure_causes"]["hazard_unknown"] == 10
    assert unknown["success"]["count"] == 0
    assert cache.snapshot()["dynamics"]["misses"] == 10


def plan(name, successes, misses):
    return RobustPlan(
        plan_id=name,
        orbit_altitude_km=60,
        approach_azimuth_deg=0,
        success=tuple(successes),
        miss_m=tuple(misses),
        r6_result_id=name,
    )


def exhaustive(plans, limit):
    scores = []
    for a, b in product(*plans):
        worst = max(x + y for x, y in zip(a.miss_m, b.miss_m))
        if worst <= limit:
            both = sum(x and y for x, y in zip(a.success, b.success))
            scores.append(Fraction(both) - Fraction(str(worst)) / 1000)
    return max(scores, default=None)


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("limit", [1.0, 80.0, 500.0])
def test_lazy_matches_independent_enumeration(seed, limit):
    rng = np.random.default_rng(seed)
    plans = [
        [
            plan(f"p{i}", rng.random(10) > 0.3, rng.integers(0, 100, 10))
            for i in range(4)
        ]
        for _ in range(2)
    ]
    calls = []

    def load(lander, index):
        calls.append((lander, index))
        return plans[lander][index]

    result = select_lazy((4, 4), runs=10, cost_limit_m=limit, load_plan=load)
    expected = exhaustive(plans, limit)
    assert result["status"] == ("infeasible" if expected is None else "optimal")
    assert result["objective"] == (None if expected is None else str(expected))
    assert len(calls) == len(set(calls)) <= 8
    assert result["physics_batches"] == len(calls)


def test_perfect_validated_pair_certifies_without_hidden_calls():
    calls = []

    def load(lander, index):
        calls.append((lander, index))
        return plan(f"p{index}", [True] * 10, [0.0] * 10)

    result = select_lazy((4, 4), runs=10, cost_limit_m=1, load_plan=load)
    assert result["status"] == "optimal"
    assert result["objective"] == "10"
    assert len(calls) == 2


def test_budget_exhaustion_never_certifies_unchecked_plan():
    result = select_lazy(
        (4, 4),
        runs=10,
        cost_limit_m=1,
        max_batches=1,
        load_plan=lambda lander, i: plan(f"p{i}", [True] * 10, [0.0] * 10),
    )
    assert result["status"] == "budget_exhausted"
    assert result["selected"] is None
    assert result["objective"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sizes": (0, 4)},
        {"sizes": (4,)},
        {"sizes": (True, 4)},
        {"runs": 0},
        {"cost_limit_m": float("nan")},
        {"cost_limit_m": -1},
        {"max_batches": -1},
    ],
)
def test_invalid_lazy_inputs(kwargs):
    args = dict(sizes=(4, 4), runs=10, cost_limit_m=100, load_plan=lambda lander, i: None)
    with pytest.raises(ValueError):
        select_lazy(**(args | kwargs))


def test_wrong_oracle_length_rejected():
    with pytest.raises(ValueError, match="scenarios"):
        select_lazy(
            (1, 1),
            runs=10,
            cost_limit_m=100,
            load_plan=lambda lander, i: plan("p", [True], [0.0]),
        )
