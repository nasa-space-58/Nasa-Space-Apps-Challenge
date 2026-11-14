"""Optional SciPy baseline must agree with independently enumerated assignments."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.scaling import (
    ScalingCase,
    make_problem,
    enumerate_problem,
    solve_milp,
    benchmark,
)
import pytest


@pytest.mark.parametrize("m,o", [(1, 2), (3, 4), (4, 3), (5, 2)])
def test_milp_matches_exact(m, o):
    p = make_problem(ScalingCase(missions=m, options=o))
    a = enumerate_problem(p, time_limit_s=10)
    b = solve_milp(p, time_limit_s=10)
    assert b["status"] == "optimal"
    assert b["score"] == a["score"]
    assert b["validation"]["feasible"]


def test_benchmark_retains_measurements_and_limits():
    r = benchmark(
        (ScalingCase(missions=2, options=2), ScalingCase(missions=8, options=4)),
        repeats=2,
        max_evaluations=20,
        time_limit_s=1,
    )
    assert r["hardware_jobs"] == 0 and r["credits_spent"] == 0
    assert r["environment"]["python"] and r["environment"]["scipy"]
    assert len(r["points"]) == 2
    for p in r["points"]:
        assert len(p["exact_runs"]) == len(p["milp_runs"]) == 2
        assert p["measurement_kind"] == "measured"
        assert p["exact_median_s"] >= 0 and p["milp_median_s"] >= 0
    assert r["points"][1]["exact_runs"][0]["status"] == "limit_reached"
    assert all(p["measurement_kind"] == "extrapolated" for p in r["projections"])
    assert all(
        p["source_fingerprint"] == r["points"][0]["fingerprint"]
        for p in r["projections"]
    )


@pytest.mark.parametrize("repeats", [0, 6, True])
def test_bad_repeat_budget(repeats):
    with pytest.raises(ValueError):
        benchmark((ScalingCase(missions=2, options=2),), repeats=repeats)
