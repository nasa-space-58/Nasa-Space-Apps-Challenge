"""R7/S11 reachable backup landing sites from a mid-descent divert.

References for expected values:
- Minimum-energy lateral divert of a double integrator, zero initial and final
  lateral velocity, time T: a(t) = 6c/T^2 (1 - 2t/T), so delta-v = 3|c|/T and
  peak acceleration 6|c|/T^2 (e.g. Bryson & Ho, Applied Optimal Control, 1975).
- Planar rotation by +theta: (0, r) -> (-r sin theta, r cos theta).
- Apollo 11 LM at a zero-height site (R4 model): about 778 kg of usable
  propellant remains at touchdown; the 145 fps R5 reserve needs about 112 kg.
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

from app.alternatives import (
    AlternativesError,
    AlternativesRequest,
    LocalTerrain,
    divert_state,
    evaluate_divert,
    find_candidates,
    lateral_divert,
    load_local_terrain,
    plan_alternatives,
    rotate_state,
)
from app.analysis_api import create_app
from app.descent import (
    FINAL_STEP_S,
    hohmann_deorbit,
    load_profiles,
    PDI_ALTITUDE_M,
    plan_descent,
    DescentRequest,
    simulate_powered_descent,
)
from shared.constants import MOON_REFERENCE_RADIUS_M as R

STEREO = f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m +no_defs"
POLE = dict(site_id="pole", latitude_deg=-90.0, longitude_deg_east=0.0)
SMALL = dict(
    search_radius_m=1000.0,
    candidate_spacing_m=160.0,
    min_separation_m=400.0,
    max_candidates=6,
    approach_azimuth_deg=0.0,
    safety={"landing_radius_m": 40.0, "hazard_search_m": 200.0},
)
APOLLO = load_profiles()["apollo-lm-11"]


def request(**changes):
    return AlternativesRequest(**(POLE | SMALL | changes))


def terrain(heights):
    size = heights.shape[0]
    half = size * 80 / 2
    return LocalTerrain(
        heights_m=heights,
        transform=from_origin(-half, half, 80, 80),
        site_xy_m=(0.0, 0.0),
        north_unit_xy=(0.0, 1.0),
        east_unit_xy=(1.0, 0.0),
        pixel_m=80.0,
        crs=STEREO,
    )


def flat(size=41, value=0.0):
    return terrain(np.full((size, size), value))


def short_profile(minus_kg):
    return APOLLO.model_copy(
        update={"usable_propellant_kg": APOLLO.usable_propellant_kg - minus_kg}
    )


@pytest.fixture(scope="module")
def primary_run():
    site_radius = R
    deorbit = hohmann_deorbit(R + 111120.0, site_radius + PDI_ALTITUDE_M)
    plan = plan_descent(DescentRequest(**POLE), 0.0)
    run = simulate_powered_descent(
        APOLLO,
        site_radius,
        plan["pdi_range_m"],
        deorbit["periapsis_speed_mps"],
        FINAL_STEP_S,
    )
    return run


@pytest.fixture(scope="module")
def high_state(primary_run):
    return divert_state(primary_run["samples"], R, "high_gate")


# ------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "changes",
    [
        {"divert_from": "pdi"},
        {"search_radius_m": 100.0},
        {"search_radius_m": 20000.0},
        {"candidate_spacing_m": 40.0},
        {"max_candidates": 0},
        {"max_candidates": 13},
        {"max_candidates": 3.0},
        {"latitude_deg": -70.0},
        {"safety": {"slope_limit_deg": 60.0}},
        {"extra": 1},
    ],
)
def test_invalid_requests(changes):
    with pytest.raises(ValidationError):
        AlternativesRequest(**(POLE | changes))


def test_defaults():
    body = AlternativesRequest(**POLE)
    assert body.divert_from == "high_gate"
    assert body.search_radius_m == 3000.0
    assert body.max_candidates == 6
    assert body.safety.slope_limit_deg == 10.0


# ------------------------------------------------------------ geometry


@pytest.mark.parametrize("cross,T", [(0.0, 100.0), (1000.0, 200.0), (-500.0, 50.0)])
def test_lateral_divert_formula(cross, T):
    result = lateral_divert(cross, T)
    assert result["delta_v_mps"] == pytest.approx(3 * abs(cross) / T)
    assert result["peak_acceleration_mps2"] == pytest.approx(6 * abs(cross) / T**2)


def test_lateral_divert_rejects_bad_time():
    for T in (0.0, -1.0, math.inf, math.nan):
        with pytest.raises(ValueError):
            lateral_divert(100.0, T)


def test_lateral_profile_integrates_to_the_offset():
    # The closed form must move the vehicle c metres and stop.
    c, T, n = 800.0, 160.0, 20000
    t = (np.arange(n) + 0.5) * T / n
    a = 6 * c / T**2 * (1 - 2 * t / T)
    v = np.cumsum(a) * T / n
    x = np.cumsum(v) * T / n
    assert v[-1] == pytest.approx(0.0, abs=1e-3)
    assert x[-1] == pytest.approx(c, rel=1e-3)
    assert np.sum(np.abs(a)) * T / n == pytest.approx(
        lateral_divert(c, T)["delta_v_mps"], rel=1e-3
    )


def test_rotate_state():
    theta = 0.01
    position, velocity = rotate_state([0.0, R], [100.0, -5.0], theta)
    assert position == pytest.approx([-R * math.sin(theta), R * math.cos(theta)])
    assert np.linalg.norm(velocity) == pytest.approx(math.hypot(100.0, 5.0))
    back_p, back_v = rotate_state(position, velocity, -theta)
    assert back_p == pytest.approx([0.0, R], abs=1e-6)
    assert back_v == pytest.approx([100.0, -5.0], abs=1e-9)


# ------------------------------------------------------------ divert state


def test_divert_state_reconstructs_the_gate(primary_run, high_state):
    first = next(s for s in primary_run["samples"] if s["phase"] == "approach")
    assert high_state["t_s"] == pytest.approx(first["t_s"])
    assert high_state["mass_kg"] == pytest.approx(first["mass_kg"])
    assert np.linalg.norm(high_state["position_m"]) == pytest.approx(
        R + first["altitude_m"], abs=1e-6
    )
    assert np.linalg.norm(high_state["velocity_mps"]) == pytest.approx(
        first["speed_mps"], abs=1e-6
    )
    low = divert_state(primary_run["samples"], R, "low_gate")
    assert low["t_s"] > high_state["t_s"]
    assert low["altitude_m"] < high_state["altitude_m"]
    with pytest.raises(AlternativesError):
        divert_state(
            [s for s in primary_run["samples"] if s["phase"] != "terminal"],
            R,
            "low_gate",
        )


# ------------------------------------------------------------ evaluation


def test_no_offset_reproduces_the_primary_touchdown(primary_run, high_state):
    result = evaluate_divert(APOLLO, high_state, 0.0, 0.0, 0.0, "high_gate", request())
    assert result["reachable"] is True
    assert result["rejection_reasons"] == []
    assert result["lateral_delta_v_mps"] == 0.0
    final = primary_run["final"]
    assert result["touchdown"]["position_error_m"] == pytest.approx(
        final["position_error_m"], abs=1e-6
    )
    assert result["touchdown"]["vertical_speed_mps"] == pytest.approx(
        final["vertical_speed_mps"], abs=1e-6
    )
    dry = APOLLO.mass_at_pdi_kg - APOLLO.usable_propellant_kg
    assert result["propellant_remaining_kg"] == pytest.approx(
        final["mass_kg"] - dry, abs=1e-6
    )
    assert 700 < result["propellant_remaining_kg"] < 850
    assert result["reserve_margin_kg"] > 0
    assert result["hover_margin_s"] > 0
    assert result["time_to_touchdown_s"] == pytest.approx(
        primary_run["phases"][-1]["end_s"] - high_state["t_s"], abs=0.2
    )


def test_downrange_retarget_lands_on_the_new_site(high_state):
    for down in (-1500.0, 1500.0):
        result = evaluate_divert(
            APOLLO, high_state, 0.0, down, 0.0, "high_gate", request()
        )
        assert result["reachable"] is True
        assert result["touchdown"]["position_error_m"] < 5.0


def test_crossrange_costs_grow_with_offset(high_state):
    costs = [
        evaluate_divert(APOLLO, high_state, 0.0, 0.0, c, "high_gate", request())
        for c in (0.0, 500.0, 1000.0, 2000.0)
    ]
    lateral = [r["lateral_delta_v_mps"] for r in costs]
    remaining = [r["propellant_remaining_kg"] for r in costs]
    assert lateral == sorted(lateral) and lateral[0] == 0.0 and lateral[-1] > 0
    assert remaining == sorted(remaining, reverse=True)
    # Lateral move starts after the fixed-throttle block, so T is below the phase time.
    assert costs[-1]["lateral_start_s"] >= 0.0


def test_reserve_rejects_expensive_diverts(high_state):
    tight = short_profile(640.0)
    near = evaluate_divert(tight, high_state, 0.0, 0.0, 0.0, "high_gate", request())
    far = evaluate_divert(tight, high_state, 0.0, 0.0, 3000.0, "high_gate", request())
    assert near["reachable"] is True
    assert far["reachable"] is False
    assert "reserve" in [r["reason"] for r in far["rejection_reasons"]]
    assert far["reserve_margin_kg"] < 0


def test_thrust_limit_rejects_violent_lateral_diverts(primary_run):
    low = divert_state(primary_run["samples"], R, "low_gate")
    result = evaluate_divert(APOLLO, low, 0.0, 0.0, 15000.0, "low_gate", request())
    assert result["reachable"] is False
    reasons = [r["reason"] for r in result["rejection_reasons"]]
    assert "thrust" in reasons
    for reason in result["rejection_reasons"]:
        assert reason["detail"]


def test_later_divert_costs_more(primary_run, high_state):
    low = divert_state(primary_run["samples"], R, "low_gate")
    high = evaluate_divert(APOLLO, high_state, 0.0, 0.0, 800.0, "high_gate", request())
    late = evaluate_divert(APOLLO, low, 0.0, 0.0, 800.0, "low_gate", request())
    assert late["lateral_delta_v_mps"] > high["lateral_delta_v_mps"]


def test_higher_target_is_flown_to_its_own_height(high_state):
    result = evaluate_divert(APOLLO, high_state, 30.0, 0.0, 0.0, "high_gate", request())
    assert result["reachable"] is True
    assert result["touchdown"]["position_error_m"] < 5.0


# ------------------------------------------------------------ candidates


def test_flat_terrain_candidates_respect_spacing_and_radius():
    found = find_candidates(flat(), request())
    assert 0 < len(found) <= 6
    points = np.array([[c["down_m"], c["cross_m"]] for c in found])
    for c in found:
        assert 400.0 - 1e-6 <= c["distance_m"] <= 1000.0 + 1e-6
        assert c["safety"]["max_landing_slope_deg"] <= 10.0
        assert c["bearing_deg"] == pytest.approx(
            math.degrees(math.atan2(c["cross_m"], c["down_m"])) % 360
        )
        assert c["terrain_height_m"] == 0.0
        assert -90.0 <= c["latitude_deg"] < -89.9
        assert 0.0 <= c["longitude_deg_east"] < 360.0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            assert np.linalg.norm(points[i] - points[j]) >= 400.0 - 1e-6
    scores = [c["score"] for c in found]
    assert scores == sorted(scores, reverse=True)
    assert found == find_candidates(flat(), request())


def test_steep_terrain_is_never_a_candidate():
    size = 41
    x = (np.arange(size) - size // 2 + 0.5) * 80.0  # column centres east of site
    heights = np.tile(np.where(x > 0, x * math.tan(math.radians(20)), 0.0), (size, 1))
    found = find_candidates(terrain(heights), request())
    assert found
    # Travel north: crossrange is east; the east half is a 20 degree ramp.
    assert all(c["cross_m"] < 100.0 for c in found)


def test_unknown_terrain_is_never_a_candidate():
    heights = np.zeros((41, 41))
    heights[:, :20] = np.nan
    found = find_candidates(terrain(heights), request())
    assert all(c["cross_m"] > 0 for c in found)


# ------------------------------------------------------------ full plan


@pytest.fixture(scope="module")
def plan():
    return plan_alternatives(request(), flat(), 0.0)


def test_plan_structure_and_primary(plan):
    assert len(plan["result_id"]) == 64
    assert plan["divert_from"] == "high_gate"
    assert plan["divert_state"]["altitude_m"] > 1000
    assert plan["divert_state"]["time_to_go_s"] > 0
    primary = plan["primary"]
    assert primary["id"] == "PRIMARY" and primary["role"] == "primary"
    assert primary["reachable"] is True
    assert primary["divert_delta_v_mps"] == pytest.approx(0.0, abs=1e-6)
    assert primary["distance_m"] == 0.0
    keys = set(primary)
    for row in plan["candidates"]:
        assert set(row) == keys
        assert row["role"] == "backup"
    assert [c["id"] for c in plan["candidates"]] == [
        f"B{i + 1}" for i in range(len(plan["candidates"]))
    ]
    assert set(plan["reachable_ids"]) | set(plan["rejected_ids"]) == {
        c["id"] for c in plan["candidates"]
    }
    assert plan["assumptions"]


def test_every_offered_backup_is_within_limits(plan):
    tol = AlternativesRequest(**POLE).tolerances
    reachable = [c for c in plan["candidates"] if c["reachable"]]
    assert reachable
    for c in reachable:
        assert c["reserve_margin_kg"] >= 0
        assert c["touchdown"]["position_error_m"] <= tol.position_m
        assert abs(c["touchdown"]["vertical_speed_mps"]) <= tol.vertical_speed_mps
        assert c["touchdown"]["horizontal_speed_mps"] <= tol.horizontal_speed_mps
        assert c["safety"]["max_landing_slope_deg"] <= 10.0
    for c in plan["candidates"]:
        if not c["reachable"]:
            assert c["rejection_reasons"]


def test_reachable_backups_come_first(plan):
    flags = [c["reachable"] for c in plan["candidates"]]
    assert flags == sorted(flags, reverse=True)


def test_plan_is_deterministic(plan):
    again = plan_alternatives(request(), flat(), 0.0)
    assert again == plan


def test_tight_propellant_rejects_some_backups():
    result = plan_alternatives(
        request(custom_profile=short_profile(660.0).model_dump(), max_candidates=12),
        flat(),
        0.0,
    )
    assert result["primary"]["reachable"] is True
    reasons = {
        r["reason"] for c in result["candidates"] for r in c["rejection_reasons"]
    }
    assert result["rejected_ids"]
    assert "reserve" in reasons


def test_primary_without_safety_margin_raises():
    with pytest.raises(AlternativesError):
        plan_alternatives(request(), flat(size=5), 0.0)
    with pytest.raises(AlternativesError):
        plan_alternatives(request(profile_id="missing"), flat(), 0.0)


# ------------------------------------------------------------ DEM + API


def write_dem(root, value=0.0, size=61):
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


def test_load_local_terrain(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_local_terrain(tmp_path, -90.0, 0.0, 1000.0)
    write_dem(tmp_path, value=12.5)
    local = load_local_terrain(tmp_path, -90.0, 0.0, 1000.0)
    assert np.allclose(local.heights_m, 12.5)
    assert local.pixel_m == 80.0
    north, east = np.array(local.north_unit_xy), np.array(local.east_unit_xy)
    assert np.linalg.norm(north) == pytest.approx(1.0, abs=1e-6)
    assert float(north @ east) == pytest.approx(0.0, abs=1e-6)
    wide = load_local_terrain(tmp_path, -90.0, 0.0, 50000.0)
    assert wide.heights_m.shape[0] <= 61
    with pytest.raises(AlternativesError):
        load_local_terrain(tmp_path, -85.0, 0.0, 1000.0)


def test_route(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as client:
        body = POLE | SMALL
        assert client.post("/descents/alternatives", json=body).status_code == 409
        write_dem(tmp_path / "data")
        response = client.post("/descents/alternatives", json=body)
        assert response.status_code == 200
        result = response.json()
        assert result["primary"]["reachable"] is True
        assert result["candidates"]
        again = client.post("/descents/alternatives", json=body).json()
        assert again["result_id"] == result["result_id"]
        for change in (
            {"profile_id": "missing"},
            {"latitude_deg": -85.0},
            {"max_candidates": 0},
        ):
            assert (
                client.post("/descents/alternatives", json=body | change).status_code
                == 422
            )
