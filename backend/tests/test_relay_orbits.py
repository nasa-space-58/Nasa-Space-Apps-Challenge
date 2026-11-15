"""H11 independent geometry, coverage and QUBO selection acceptance."""

from itertools import combinations
import math
import numpy as np
import pytest
from pydantic import ValidationError
from app.relay_orbits import (
    RelayRequest,
    cr3bp_rhs,
    jacobi_constant,
    segment_clear,
    contact_geometry,
    validate_selection,
    build_relay_scenario,
    compare_relays,
)
from app.hybrid_planning import solve_exact
from app.hybrid_quantum import compile_qubo, audit_mapping
from shared.horizon_geometry import interpolate_horizon


@pytest.fixture
def evidence():
    return dict(
        version="h11-geometry-v1",
        source_digest="a" * 64,
        epoch_label_utc="2026-11-04T00:00:00Z",
        times_s=[0, 3600, 7200, 10800, 14400],
        orbits=[dict(orbit_id=x, cost_score=i + 1) for i, x in enumerate("abcd")],
        sites=[dict(site_id="south", latitude_deg=-85.0, longitude_deg_east=0.0)],
        contacts={
            "south": {
                "a": [True, True, False, False],
                "b": [False, False, True, True],
                "c": [True, False, True, False],
                "d": [False, True, False, True],
            }
        },
    )


def test_l4_equilibrium_and_jacobi_reference():
    mu = 0.01215058560962404
    s = np.array([0.5 - mu, math.sqrt(3) / 2, 0, 0, 0, 0])
    assert np.max(np.abs(cr3bp_rhs(0, s, mu))) < 1e-14
    assert jacobi_constant(s, mu) == pytest.approx(3 - mu + mu * mu, abs=1e-14)


@pytest.mark.parametrize(
    "state,mu",
    [
        ([0] * 5, 0.01),
        ([float("nan")] * 6, 0.01),
        ([0] * 6, 0),
        ([0] * 6, 0.6),
        ([-0.01, 0, 0, 0, 0, 0], 0.01),
    ],
)
def test_bad_dynamics_inputs(state, mu):
    with pytest.raises(ValueError):
        cr3bp_rhs(0, state, mu)


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ([-2, 0, 0], [2, 0, 0], False),
        ([-2, 1, 0], [2, 1, 0], False),
        ([-2, 1.01, 0], [2, 1.01, 0], True),
        ([2, 0, 0], [3, 0, 0], True),
    ],
)
def test_finite_segment_occultation(a, b, expected):
    assert segment_clear(a, b, [0, 0, 0], 1) is expected


def test_zero_segment_rejected():
    with pytest.raises(ValueError):
        segment_clear([1, 0, 0], [1, 0, 0], [0, 0, 0], 1)


def test_horizon_wrap_and_unknown():
    p = {
        "directions": [
            {"azimuth_deg": 0, "elevation_deg": 2},
            {"azimuth_deg": 180, "elevation_deg": 4},
        ]
    }
    assert interpolate_horizon(p, 270) == pytest.approx(3)
    assert interpolate_horizon(p, -90) == pytest.approx(3)
    p["directions"][1]["elevation_deg"] = None
    assert interpolate_horizon(p, 90) is None
    assert interpolate_horizon(p, 0) == 2


def test_explicit_synodic_site_orientation():
    sys = dict(mass_ratio=0.01, lunit=1000.0, radius_secondary=1.0)
    site = dict(latitude_deg=0.0, longitude_deg_east=0.0, altitude_km=0.0)
    h = {
        "directions": [
            {"azimuth_deg": 0.0, "elevation_deg": 0.0},
            {"azimuth_deg": 180.0, "elevation_deg": 0.0},
        ]
    }
    g = contact_geometry([0.5, 0, 0], site, sys, h)
    assert g["elevation_deg"] == pytest.approx(90)
    assert g["state"] == "available"
    blocked = contact_geometry([1.5, 0, 0], site, sys, h)
    assert blocked["state"] == "unavailable"
    h["directions"][0]["elevation_deg"] = None
    assert contact_geometry([0.5, 0.1, 0], site, sys, h)["state"] == "unknown"


def test_union_not_sum_and_gap(evidence):
    r = RelayRequest(
        minimum_coverage=0.75, maximum_gap_s=3600.0, minimum_contact_s=10800.0
    )
    a = validate_selection(evidence, ("a", "c"), r)
    assert a["status"] == "feasible"
    assert a["sites"][0]["covered_s"] == 10800
    assert a["sites"][0]["maximum_gap_s"] == 3600
    assert validate_selection(evidence, ("a", "a"), r)["status"] == "infeasible"


def test_hard_thresholds_and_unknown(evidence):
    r = RelayRequest(minimum_coverage=1.0, maximum_gap_s=0.0, minimum_contact_s=14400.0)
    assert validate_selection(evidence, ("a", "b"), r)["status"] == "feasible"
    assert validate_selection(evidence, ("a", "c"), r)["status"] == "infeasible"
    evidence["contacts"]["south"]["a"][0] = None
    assert validate_selection(evidence, ("a", "b"), r)["status"] == "incomplete"


def test_monotone_times_required(evidence):
    evidence["times_s"][1] = 0
    with pytest.raises(ValueError):
        validate_selection(evidence, ("a", "b"), RelayRequest())


def test_unknown_orbit_rejected(evidence):
    with pytest.raises(ValueError):
        validate_selection(evidence, ("a", "missing"), RelayRequest())


@pytest.mark.parametrize("k", [1, 2])
def test_mapping_agrees_with_independent_set_oracle(evidence, k):
    r = RelayRequest(
        relay_count=k,
        minimum_coverage=1.0,
        maximum_gap_s=0.0,
        minimum_contact_s=14400.0,
    )
    expected = [
        x
        for x in combinations("abcd", k)
        if all(any(evidence["contacts"]["south"][o][i] for o in x) for i in range(4))
    ]
    s = build_relay_scenario(evidence, r)
    exact = solve_exact(s)
    assert exact.feasible_count == len(expected) * math.factorial(k)
    audit = audit_mapping(compile_qubo(s), s)
    assert audit.feasible_assignments == exact.feasible_count
    assert audit.ground_matches_classical is bool(expected)


def test_comparison_revalidates_original_constraints(evidence):
    r = compare_relays(evidence, RelayRequest(minimum_coverage=1.0, maximum_gap_s=0.0))
    assert r["comparison"]["hardware_jobs"] == 0
    assert r["classical_validation"]["status"] == "feasible"
    assert set(r["classical_validation"]["orbit_ids"]) == {"a", "b"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"relay_count": 3},
        {"relay_count": True},
        {"minimum_coverage": 1.1},
        {"maximum_gap_s": -1},
        {"minimum_contact_s": float("nan")},
    ],
)
def test_bad_requests(kwargs):
    with pytest.raises(ValidationError):
        RelayRequest(**kwargs)
