"""R5/S7 fuel optimization across R4 descent routes.

References:
- NASA TN D-6846, Table II: contingency allowances for landing-site
  redesignation (60 fps) and manual hover (85 fps) -> default reserve 145 fps.
- Apollo 11 Mission Report, Table A-I: LM 33 669.6 lb at DOI ignition and
  33 401.6 lb at DOI cutoff (used to check the DOI mass back-out, 0.5 %).
Physics checks use the rocket equation exactly (1e-9 relative tolerance).
"""

import hashlib
import json
import math

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from pydantic import ValidationError
from rasterio.transform import from_origin

from app.analysis_api import create_app
from app.descent import (
    DescentRequest,
    VehicleProfile,
    gate_states,
    hohmann_deorbit,
    load_profiles,
    plan_descent,
    simulate_powered_descent,
)
from app.fuel_optimization import (
    DEFAULT_APPROACH_DURATIONS_S,
    DEFAULT_ORBIT_ALTITUDES_KM,
    DEFAULT_RESERVE_DELTA_V_MPS,
    DEFAULT_TERMINAL_DURATIONS_S,
    REFERENCE_ORBIT_ALTITUDE_KM,
    RouteRequest,
    hover_time_s,
    mass_at_doi_kg,
    optimize_routes,
    pareto_front,
    reserve_propellant_kg,
)
from shared.constants import (
    FOOT_M,
    MOON_REFERENCE_RADIUS_M as R,
    NAUTICAL_MILE_M,
    POUND_MASS_KG,
    STANDARD_GRAVITY_M_S2,
)

SITE = dict(
    site_id="malapert-massif-r23", latitude_deg=-85.9948, longitude_deg_east=359.76422
)
NOMINAL_KM = 60 * NAUTICAL_MILE_M / 1000


@pytest.fixture(scope="module")
def apollo():
    return load_profiles()["apollo-lm-11"]


@pytest.fixture(scope="module")
def grid():
    return optimize_routes(RouteRequest(**SITE), terrain_height_m=0.0)


def by_id(result):
    return {r["id"]: r for r in result["routes"]}


# ------------------------------------------------------------- inputs & models


def test_defaults_are_sourced():
    assert DEFAULT_RESERVE_DELTA_V_MPS == pytest.approx(145 * FOOT_M, rel=1e-15)
    assert DEFAULT_ORBIT_ALTITUDES_KM == pytest.approx(
        tuple(n * NAUTICAL_MILE_M / 1000 for n in (40, 60, 80))
    )
    assert DEFAULT_APPROACH_DURATIONS_S == (60.0, 100.0, 140.0)
    assert DEFAULT_TERMINAL_DURATIONS_S == (60.0, 108.0, 150.0)
    assert REFERENCE_ORBIT_ALTITUDE_KM == pytest.approx(NOMINAL_KM)
    request = RouteRequest(**SITE)
    assert request.reserve_delta_v_mps == pytest.approx(DEFAULT_RESERVE_DELTA_V_MPS)


@pytest.mark.parametrize(
    "changes",
    [
        {"approach_durations_s": []},
        {"approach_durations_s": [60, 80, 100, 120, 140]},
        {"approach_durations_s": [5]},
        {"terminal_durations_s": [401]},
        {"orbit_altitudes_km": [20]},
        {"orbit_altitudes_km": [600]},
        {"reserve_delta_v_mps": -1},
        {"latitude_deg": -70},
        {"unexpected": 1},
    ],
)
def test_invalid_route_requests(changes):
    with pytest.raises(ValidationError):
        RouteRequest(**(SITE | changes))


# ---------------------------------------------------------------- R4 overrides


def test_gate_duration_overrides_keep_gate_states():
    base = gate_states(R)
    custom = gate_states(R, approach_s=140.0, terminal_s=60.0)
    assert [g["duration_s"] for g in custom] == [506.0, 140.0, 60.0]
    for a, b in zip(base, custom):
        assert np.allclose(a["position_m"], b["position_m"])
        assert np.allclose(a["velocity_mps"], b["velocity_mps"])
    with pytest.raises(ValueError):
        gate_states(R, approach_s=0.0)


def test_simulation_honours_overrides(apollo):
    vp = hohmann_deorbit(R + 60 * NAUTICAL_MILE_M, R + 50000 * FOOT_M)[
        "periapsis_speed_mps"
    ]
    run = simulate_powered_descent(
        apollo, R, 540e3, vp, 0.2, approach_s=60.0, terminal_s=150.0
    )
    assert [p["end_s"] for p in run["phases"]] == pytest.approx([506.0, 566.0, 716.0])
    assert run["final"]["position_error_m"] < 5


# ------------------------------------------------------------------ physics


def test_doi_mass_back_out_matches_mission_report(apollo):
    r1, rp = R + 60 * NAUTICAL_MILE_M, R + 50000 * FOOT_M
    dv = hohmann_deorbit(r1, rp)["delta_v_mps"]
    mass = mass_at_doi_kg(apollo, dv)
    assert mass == pytest.approx(
        apollo.mass_at_pdi_kg * math.exp(dv / (apollo.isp_s * STANDARD_GRAVITY_M_S2)),
        rel=1e-12,
    )
    assert mass == pytest.approx(33669.6 * POUND_MASS_KG, rel=0.005)


def test_reserve_and_hover_follow_the_rocket_equation():
    ve = 310 * STANDARD_GRAVITY_M_S2
    assert reserve_propellant_kg(7000.0, 0.0, 310) == 0.0
    assert reserve_propellant_kg(7000.0, 44.196, 310) == pytest.approx(
        7000 * (1 - math.exp(-44.196 / ve)), rel=1e-12
    )
    with pytest.raises(ValueError):
        reserve_propellant_kg(7000.0, -1.0, 310)
    # Hover at constant weight-equals-thrust: m(t) = m0 exp(-g t / ve).
    g = 1.62
    assert hover_time_s(100.0, 7000.0, 310, g) == pytest.approx(
        ve / g * math.log(7000 / 6900), rel=1e-12
    )
    assert hover_time_s(0.0, 7000.0, 310, g) == 0.0
    assert hover_time_s(-5.0, 7000.0, 310, g) == 0.0


def test_pareto_front():
    routes = [
        {
            "id": "a",
            "feasible": True,
            "propellant_used_kg": 100,
            "assessment_time_s": 120,
        },
        {
            "id": "b",
            "feasible": True,
            "propellant_used_kg": 120,
            "assessment_time_s": 200,
        },
        {
            "id": "c",
            "feasible": True,
            "propellant_used_kg": 130,
            "assessment_time_s": 150,
        },  # dominated by b
        {
            "id": "d",
            "feasible": True,
            "propellant_used_kg": 100,
            "assessment_time_s": 120,
        },  # tie with a
        {
            "id": "e",
            "feasible": False,
            "propellant_used_kg": 50,
            "assessment_time_s": 300,
        },
        {
            "id": "f",
            "feasible": True,
            "propellant_used_kg": 150,
            "assessment_time_s": 260,
        },
    ]
    assert pareto_front(routes) == ["a", "d", "b", "f"]
    assert pareto_front([]) == []


# --------------------------------------------------------------- the route grid


def test_grid_covers_every_variant(grid):
    routes = grid["routes"]
    assert len(routes) == 27
    assert len({r["id"] for r in routes}) == 27
    combos = {
        (round(r["orbit_altitude_km"], 6), r["approach_s"], r["terminal_s"])
        for r in routes
    }
    assert len(combos) == 27
    assert grid["reserve"]["delta_v_mps"] == pytest.approx(DEFAULT_RESERVE_DELTA_V_MPS)
    assert "TN D-6846" in grid["reserve"]["source"]
    assert grid["assumptions"]


def test_apollo_routes_all_respect_the_default_reserve(grid):
    assert sorted(grid["feasible_ids"]) == sorted(r["id"] for r in grid["routes"])
    assert grid["rejected_ids"] == []
    for r in grid["routes"]:
        assert r["feasible"] is True and r["rejection_reasons"] == []
        assert r["reserve_margin_kg"] >= 0
        assert r["propellant_remaining_kg"] == pytest.approx(
            r["reserve_required_kg"] + r["reserve_margin_kg"], rel=1e-9
        )
        assert r["hover_margin_s"] >= 0
        assert r["assessment_time_s"] == r["approach_s"] + r["terminal_s"]
        assert r["delta_v_mps"] == pytest.approx(
            r["doi_delta_v_mps"] + r["powered_delta_v_mps"], rel=1e-12
        )
        assert r["touchdown"]["position_error_m"] <= 5


def test_fuel_grows_with_orbit_and_durations(grid):
    table = {
        (round(r["orbit_altitude_km"], 6), r["approach_s"], r["terminal_s"]): r[
            "propellant_used_kg"
        ]
        for r in grid["routes"]
    }
    alts = sorted({k[0] for k in table})
    for alt in alts:
        for t in DEFAULT_TERMINAL_DURATIONS_S:
            fuel = [table[(alt, a, t)] for a in DEFAULT_APPROACH_DURATIONS_S]
            assert fuel == sorted(fuel)
        for a in DEFAULT_APPROACH_DURATIONS_S:
            fuel = [table[(alt, a, t)] for t in DEFAULT_TERMINAL_DURATIONS_S]
            assert fuel == sorted(fuel)
    for a in DEFAULT_APPROACH_DURATIONS_S:
        for t in DEFAULT_TERMINAL_DURATIONS_S:
            fuel = [table[(alt, a, t)] for alt in alts]
            assert fuel == sorted(fuel)


def test_extremes_and_front(grid):
    routes = by_id(grid)
    lowest = routes[grid["lowest_fuel_id"]]
    assert lowest["orbit_altitude_km"] == pytest.approx(min(DEFAULT_ORBIT_ALTITUDES_KM))
    assert (lowest["approach_s"], lowest["terminal_s"]) == (60.0, 60.0)
    longest = routes[grid["longest_assessment_id"]]
    assert (longest["approach_s"], longest["terminal_s"]) == (140.0, 150.0)
    assert longest["orbit_altitude_km"] == pytest.approx(
        min(DEFAULT_ORBIT_ALTITUDES_KM)
    )
    front = grid["pareto_ids"]
    assert grid["lowest_fuel_id"] in front and grid["longest_assessment_id"] in front
    assert front == pareto_front(grid["routes"])
    fuels = [routes[i]["propellant_used_kg"] for i in front]
    assert fuels == sorted(fuels)


def test_reference_route_matches_r4_plan(grid):
    reference = by_id(grid)[grid["reference_route_id"]]
    assert reference["orbit_altitude_km"] == pytest.approx(NOMINAL_KM)
    assert (reference["approach_s"], reference["terminal_s"]) == (100.0, 108.0)
    plan = plan_descent(DescentRequest(**SITE), terrain_height_m=0.0)
    assert reference["powered_delta_v_mps"] == pytest.approx(
        plan["totals"]["powered_delta_v_mps"], rel=1e-6
    )
    doi_propellant = (
        grid["mass_at_doi_kg"] - load_profiles()["apollo-lm-11"].mass_at_pdi_kg
    )
    assert reference["propellant_used_kg"] == pytest.approx(
        plan["totals"]["propellant_used_kg"] + doi_propellant, rel=1e-6
    )


def test_optimization_is_deterministic(grid):
    small = dict(
        orbit_altitudes_km=[NOMINAL_KM],
        approach_durations_s=[100],
        terminal_durations_s=[108],
    )
    a = optimize_routes(RouteRequest(**SITE, **small), terrain_height_m=0.0)
    b = optimize_routes(RouteRequest(**SITE, **small), terrain_height_m=0.0)
    assert a == b


# ------------------------------------------------------------------ rejections


SMALL = dict(
    orbit_altitudes_km=[NOMINAL_KM],
    approach_durations_s=[100.0, 140.0],
    terminal_durations_s=[108.0, 150.0],
)


def test_high_reserve_rejects_long_routes_with_reasons():
    result = optimize_routes(
        RouteRequest(**SITE, **SMALL, reserve_delta_v_mps=700 * FOOT_M),
        terrain_height_m=0.0,
    )
    routes = by_id(result)
    assert result["rejected_ids"]
    for rid in result["rejected_ids"]:
        reasons = routes[rid]["rejection_reasons"]
        assert reasons and reasons[0]["reason"] == "reserve"
        assert routes[rid]["reserve_margin_kg"] < 0
        assert reasons[0]["detail"]
    # Acceptance: no listed feasible route violates the reserve.
    for rid in result["feasible_ids"]:
        assert routes[rid]["reserve_margin_kg"] >= 0
    longest = [
        r
        for r in result["routes"]
        if (r["approach_s"], r["terminal_s"]) == (140.0, 150.0)
    ][0]
    assert longest["id"] in result["rejected_ids"]
    assert result["lowest_fuel_id"] in result["feasible_ids"]
    assert set(result["pareto_ids"]) <= set(result["feasible_ids"])


def test_plane_change_rejects_every_route():
    one = dict(
        orbit_altitudes_km=[NOMINAL_KM],
        approach_durations_s=[100],
        terminal_durations_s=[108],
    )
    result = optimize_routes(
        RouteRequest(**SITE, **one, inclination_deg=80), terrain_height_m=0.0
    )
    assert result["feasible_ids"] == [] and result["pareto_ids"] == []
    assert result["lowest_fuel_id"] is None and result["longest_assessment_id"] is None
    assert result["routes"][0]["rejection_reasons"][0]["reason"] == "plane_change"


def test_underpowered_vehicle_is_rejected():
    weak = VehicleProfile(
        name="weak",
        mass_at_pdi_kg=15000,
        usable_propellant_kg=8000,
        max_thrust_n=12000,
        design_thrust_n=12500,
        min_throttle_fraction=0.1,
        max_throttle_fraction=0.6,
        isp_s=310,
    )
    one = dict(
        orbit_altitudes_km=[NOMINAL_KM],
        approach_durations_s=[100],
        terminal_durations_s=[108],
    )
    result = optimize_routes(
        RouteRequest(**SITE, **one, custom_profile=weak), terrain_height_m=0.0
    )
    reasons = {x["reason"] for x in result["routes"][0]["rejection_reasons"]}
    assert reasons & {"touchdown_tolerance", "propellant"}
    assert result["feasible_ids"] == []


def test_orbit_below_pdi_altitude_is_rejected_not_crashing():
    result = optimize_routes(
        RouteRequest(
            **SITE,
            orbit_altitudes_km=[30.0],
            approach_durations_s=[100],
            terminal_durations_s=[108],
        ),
        terrain_height_m=20000.0,
    )
    route = result["routes"][0]
    assert route["feasible"] is False
    assert route["rejection_reasons"][0]["reason"] == "orbit"


# ------------------------------------------------------------------------- API


def write_dem(root, value=0.0, size=21):
    folder = root / "terrain/raw" / ("a" * 64)
    folder.mkdir(parents=True)
    path = folder / "source.tif"
    half = size * 80 / 2
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=size,
        height=size,
        count=1,
        dtype="float32",
        crs=f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m",
        transform=from_origin(-half, half, 80, 80),
        nodata=np.nan,
    ) as ds:
        ds.write(np.full((size, size), value, dtype="float32"), 1)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "product_id": "LDEM_80S_80MPP_ADJ",
                "sha256": {"source.tif": hashlib.sha256(path.read_bytes()).hexdigest()},
            }
        )
    )


def test_route(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as client:
        body = dict(
            site_id="pole",
            latitude_deg=-90.0,
            longitude_deg_east=0.0,
            orbit_altitudes_km=[NOMINAL_KM],
            approach_durations_s=[100],
            terminal_durations_s=[108],
        )
        assert client.post("/descents/optimize", json=body).status_code == 409
        write_dem(tmp_path / "data")
        response = client.post("/descents/optimize", json=body)
        assert response.status_code == 200
        assert response.json()["feasible_ids"]
        assert (
            client.post(
                "/descents/optimize", json=body | {"approach_durations_s": [5]}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/descents/optimize", json=body | {"latitude_deg": -85.0}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/descents/optimize", json=body | {"profile_id": "missing"}
            ).status_code
            == 422
        )
