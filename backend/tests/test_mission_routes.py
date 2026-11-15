"""Mission run route set: primary, backup and rejected routes with paths.

References for expected values:
- Minimum-energy lateral divert of a double integrator with zero initial and
  final lateral velocity over a window T: position c (3 s^2 - 2 s^3) with
  s = t / T (the integral of a(t) = 6c/T^2 (1 - 2t/T) twice; e.g. Bryson & Ho,
  Applied Optimal Control, 1975). s = 1/4 gives 0.15625 c, s = 1/2 gives c/2.
- Horn slope of a plane rising at 30 deg is 30 deg; cells on it fail the
  default 10 deg R3 slope limit.
- R7 with 660 kg less usable propellant on flat terrain keeps the primary
  reachable and rejects some backups for the reserve (test_alternatives).
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
    AlternativesRequest,
    LocalTerrain,
    divert_state,
    evaluate_divert,
    lateral_offset_m,
    plan_alternatives,
)
from app.analysis_api import Submission, create_app
from app.descent import (
    FINAL_STEP_S,
    DescentRequest,
    load_profiles,
    plan_descent,
    simulate_powered_descent,
)
from app.mission_routes import (
    ASSUMPTIONS,
    DISCLAIMER,
    FRAME,
    MAX_ROUTE_SAMPLES,
    MissionRoutesError,
    MissionRoutesRequest,
    alternatives_request,
    build_routes,
    downsample,
    plan_mission_routes,
    terrain_rejections,
)
from shared.constants import MOON_REFERENCE_RADIUS_M as R

STEREO = f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m +no_defs"
SITE = dict(site_id="pole", latitude_deg=-90.0, longitude_deg_east=0.0)
SMALL = dict(
    search_radius_m=1000.0,
    max_candidates=6,
    approach_azimuth_deg=0.0,
    terrain={"landing_radius_m": 40.0, "hazard_search_m": 200.0},
)
APOLLO = load_profiles()["apollo-lm-11"]
METRICS = {
    "safety_score",
    "max_landing_slope_deg",
    "hazard_distance_m",
    "divert_delta_v_mps",
    "propellant_remaining_kg",
    "reserve_margin_kg",
    "hover_margin_s",
    "time_to_touchdown_s",
    "touchdown_position_error_m",
    "touchdown_vertical_speed_mps",
    "touchdown_horizontal_speed_mps",
}
FLIGHT_METRICS = METRICS - {
    "safety_score",
    "max_landing_slope_deg",
    "hazard_distance_m",
}
ROUTE_KEYS = {
    "id",
    "role",
    "rank",
    "reachable",
    "evaluated",
    "latitude_deg",
    "longitude_deg_east",
    "down_m",
    "cross_m",
    "distance_m",
    "bearing_deg",
    "terrain_height_m",
    "metrics",
    "reasons",
    "trajectory",
}
REASON_KEYS = {"code", "detail", "value", "limit", "unit"}
FLIGHT_REJECTIONS = {"propellant", "reserve", "thrust", "touchdown_tolerance"}


def routes_request(**changes):
    return MissionRoutesRequest(**(SMALL | changes))


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


def flat(size=61, value=0.0):
    return terrain(np.full((size, size), value))


def east_ramp(size=61, start_m=500.0, slope_deg=30.0):
    """Flat ground that rises east of start_m (column x) at slope_deg."""
    half = size * 80 / 2
    x = -half + 40 + 80 * np.arange(size)
    row = np.where(x > start_m, (x - start_m) * math.tan(math.radians(slope_deg)), 0)
    return terrain(np.tile(row, (size, 1)))


def unknown_patch(size=61):
    heights = np.zeros((size, size))
    centre = size // 2
    heights[centre - 3 : centre + 4, centre + 7 : centre + 12] = np.nan
    return terrain(heights)


def short_profile(minus_kg):
    return APOLLO.model_copy(
        update={"usable_propellant_kg": APOLLO.usable_propellant_kg - minus_kg}
    )


def by_role(result, role):
    return [r for r in result["routes"] if r["role"] == role]


@pytest.fixture(scope="module")
def high_state():
    plan = plan_descent(DescentRequest(**SITE), 0.0)
    run = simulate_powered_descent(
        APOLLO,
        R,
        plan["pdi_range_m"],
        plan["deorbit"]["periapsis_speed_mps"],
        FINAL_STEP_S,
    )
    return divert_state(run["samples"], R, "high_gate")


@pytest.fixture(scope="module")
def nominal():
    return plan_mission_routes(routes_request(), SITE, flat(), 0.0)


@pytest.fixture(scope="module")
def mixed():
    return plan_mission_routes(
        routes_request(
            custom_profile=short_profile(660.0).model_dump(), max_candidates=12
        ),
        SITE,
        flat(),
        0.0,
    )


@pytest.fixture(scope="module")
def ramp():
    return plan_mission_routes(routes_request(), SITE, east_ramp(), 0.0)


# ------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "changes",
    [
        {"divert_from": "pdi"},
        {"search_radius_m": 100.0},
        {"search_radius_m": 20000.0},
        {"max_candidates": 0},
        {"max_candidates": 13},
        {"max_candidates": 3.0},
        {"max_terrain_rejections": -1},
        {"max_terrain_rejections": 13},
        {"approach_azimuth_deg": 360.0},
        {"approach_azimuth_deg": -1.0},
        {"reserve_delta_v_mps": -1.0},
        {"search_radius_m": float("nan")},
        {"unknown": 1},
        {"terrain": {"slope_limit_deg": 0.5}},
    ],
)
def test_request_rejects_invalid_values(changes):
    with pytest.raises(ValidationError):
        MissionRoutesRequest(**changes)


def test_request_defaults_match_the_mission_run():
    request = MissionRoutesRequest()
    assert request.profile_id == "apollo-lm-11"
    assert request.approach_azimuth_deg == 0.0  # mission run default
    assert request.divert_from == "high_gate"
    assert request.search_radius_m == 3000.0
    assert request.max_candidates == 8
    assert request.max_terrain_rejections == 6
    assert request.terrain.slope_limit_deg == 10.0


def test_alternatives_request_maps_every_setting():
    request = routes_request(
        divert_from="low_gate",
        approach_azimuth_deg=45.0,
        reserve_delta_v_mps=20.0,
        custom_profile=short_profile(10.0).model_dump(),
    )
    mapped = alternatives_request(request, SITE)
    assert isinstance(mapped, AlternativesRequest)
    assert mapped.site_id == "pole"
    assert mapped.latitude_deg == -90.0 and mapped.longitude_deg_east == 0.0
    assert mapped.profile_id == request.profile_id
    assert mapped.custom_profile == request.custom_profile
    assert mapped.divert_from == "low_gate"
    assert mapped.approach_azimuth_deg == 45.0
    assert mapped.search_radius_m == 1000.0
    assert mapped.max_candidates == 6
    assert mapped.reserve_delta_v_mps == 20.0
    assert mapped.safety == request.terrain


def test_alternatives_request_needs_a_complete_site():
    with pytest.raises((KeyError, MissionRoutesError, ValidationError)):
        alternatives_request(routes_request(), {"site_id": "x"})


# ------------------------------------------------------------ lateral profile


@pytest.mark.parametrize(
    "t_s, expected",
    [
        (0.0, 0.0),
        (10.0, 0.0),
        (15.0, 15.625),
        (20.0, 50.0),
        (30.0, 100.0),
        (99.0, 100.0),
    ],
)
def test_lateral_offset_follows_the_minimum_energy_profile(t_s, expected):
    assert lateral_offset_m(100.0, t_s, 10.0, 20.0) == pytest.approx(expected)


def test_lateral_offset_is_odd_in_the_offset_and_zero_without_a_window():
    assert lateral_offset_m(-100.0, 20.0, 10.0, 20.0) == pytest.approx(-50.0)
    assert lateral_offset_m(0.0, 20.0, 10.0, 20.0) == 0.0
    assert lateral_offset_m(100.0, 20.0, 10.0, 0.0) == 0.0
    assert lateral_offset_m(100.0, 20.0, 10.0, -5.0) == 0.0


def test_lateral_offset_is_monotonic_inside_the_window():
    values = [lateral_offset_m(80.0, t, 0.0, 50.0) for t in np.linspace(0, 50, 101)]
    assert all(b >= a for a, b in zip(values, values[1:]))


# ------------------------------------------------------------ R7 path recording


def r7_request(**changes):
    return AlternativesRequest(
        **(
            SITE
            | dict(
                search_radius_m=1000.0,
                max_candidates=6,
                approach_azimuth_deg=0.0,
                safety={"landing_radius_m": 40.0, "hazard_search_m": 200.0},
            )
            | changes
        )
    )


def test_recording_a_path_changes_no_metric(high_state):
    plain = evaluate_divert(
        APOLLO, high_state, 0.0, 300.0, 200.0, "high_gate", r7_request()
    )
    recorded = evaluate_divert(
        APOLLO,
        high_state,
        0.0,
        300.0,
        200.0,
        "high_gate",
        r7_request(),
        record_path=True,
    )
    assert "path" not in plain
    path = recorded.pop("path")
    assert recorded == plain
    assert len(path) > 100
    assert set(path[0]) == {"t_s", "downrange_m", "cross_m", "altitude_m"}
    times = [p["t_s"] for p in path]
    assert times[0] == pytest.approx(0.0)
    assert all(b > a for a, b in zip(times, times[1:]))
    assert path[0]["cross_m"] == pytest.approx(0.0)
    assert path[-1]["cross_m"] == pytest.approx(200.0)
    assert path[-1]["t_s"] == pytest.approx(plain["time_to_touchdown_s"])
    assert abs(path[-1]["downrange_m"]) <= 5.0
    assert path[-1]["altitude_m"] == pytest.approx(0.0, abs=2.0)
    assert path[0]["altitude_m"] == pytest.approx(high_state["altitude_m"], rel=1e-6)


def test_recorded_crossrange_is_zero_before_the_lateral_divert(high_state):
    recorded = evaluate_divert(
        APOLLO, high_state, 0.0, 0.0, 300.0, "high_gate", r7_request(), record_path=True
    )
    start = recorded["lateral_start_s"]
    before = [p for p in recorded["path"] if p["t_s"] <= start]
    assert before and all(p["cross_m"] == 0.0 for p in before)


def test_trajectories_do_not_change_the_r7_plan():
    plain = plan_alternatives(r7_request(), flat(), 0.0)
    traced = plan_alternatives(r7_request(), flat(), 0.0, trajectories=True)
    paths = traced.pop("trajectories")
    assert traced == plain
    assert set(paths) == {"PRIMARY", *(c["id"] for c in plain["candidates"])}
    t0 = plain["divert_state"]["t_s"]
    for row in [plain["primary"], *plain["candidates"]]:
        path = paths[row["id"]]
        assert set(path[0]) == {"t_s", "down_m", "cross_m", "altitude_m"}
        assert path[0]["t_s"] == pytest.approx(t0)
        assert path[0]["down_m"] == pytest.approx(plain["divert_state"]["downrange_m"])
        assert path[-1]["t_s"] == pytest.approx(t0 + row["time_to_touchdown_s"])
        assert path[-1]["cross_m"] == pytest.approx(row["cross_m"])
        if row["reachable"]:
            assert path[-1]["down_m"] == pytest.approx(row["down_m"], abs=5.0)


def test_trajectory_altitude_uses_the_primary_sphere():
    heights = np.zeros((61, 61))
    heights[:, 33:] = 40.0  # a flat terrace east of the site
    traced = plan_alternatives(r7_request(), terrain(heights), 0.0, trajectories=True)
    terrace = [c for c in traced["candidates"] if c["terrain_height_m"] == 40.0]
    assert terrace
    for row in terrace:
        last = traced["trajectories"][row["id"]][-1]
        assert last["altitude_m"] == pytest.approx(40.0, abs=2.0)


# ------------------------------------------------------------ downsample


def test_downsample_keeps_the_ends_and_bounds_the_length():
    points = list(range(1000))
    kept = downsample(points, 120)
    assert 2 <= len(kept) <= 120
    assert kept[0] == 0 and kept[-1] == 999
    assert kept == sorted(set(kept))
    assert set(kept) <= set(points)


def test_downsample_returns_short_lists_unchanged():
    assert downsample([1, 2, 3], 120) == [1, 2, 3]
    assert downsample([], 10) == []
    assert downsample([5, 6], 2) == [5, 6]


def test_downsample_rejects_too_few_samples():
    with pytest.raises(ValueError):
        downsample([1, 2, 3], 1)


# ------------------------------------------------------------ terrain rejections


def test_flat_terrain_has_no_terrain_rejections():
    request = alternatives_request(routes_request(), SITE)
    assert terrain_rejections(flat(), request, 6) == []


def test_steep_cells_are_rejected_for_slope_nearest_first():
    request = alternatives_request(routes_request(), SITE)
    cells = terrain_rejections(east_ramp(), request, 6)
    assert 1 <= len(cells) <= 6
    distances = [c["distance_m"] for c in cells]
    assert distances == sorted(distances)
    for c in cells:
        assert c["cross_m"] > 400.0  # on the eastern ramp
        assert c["distance_m"] <= request.search_radius_m + 1e-9
        assert c["distance_m"] >= request.min_separation_m - 1e-9
        codes = [r["code"] for r in c["reasons"]]
        assert "slope" in codes
        slope = next(r for r in c["reasons"] if r["code"] == "slope")
        assert set(slope) == REASON_KEYS
        assert slope["value"] > slope["limit"] == 10.0
        assert slope["unit"] == "deg"
        assert -90.0 <= c["latitude_deg"] <= -80.0
        assert 0.0 <= c["longitude_deg_east"] < 360.0
        assert 0.0 <= c["bearing_deg"] < 360.0
    for a, b in zip(cells, cells[1:]):
        assert math.hypot(a["down_m"] - b["down_m"], a["cross_m"] - b["cross_m"]) > 0


def test_terrain_rejections_keep_their_separation_and_limit():
    request = alternatives_request(routes_request(), SITE)
    cells = terrain_rejections(east_ramp(start_m=200.0), request, 3)
    assert len(cells) == 3
    for i, a in enumerate(cells):
        for b in cells[i + 1 :]:
            gap = math.hypot(a["down_m"] - b["down_m"], a["cross_m"] - b["cross_m"])
            assert gap >= request.min_separation_m - 1e-9
    assert terrain_rejections(east_ramp(), request, 0) == []


def test_unknown_terrain_is_its_own_reason():
    request = alternatives_request(routes_request(), SITE)
    cells = terrain_rejections(unknown_patch(), request, 6)
    assert cells
    assert all("unknown_terrain" in {r["code"] for r in c["reasons"]} for c in cells)


# ------------------------------------------------------------ route set


def test_route_set_structure(nominal):
    assert nominal["frame"] == FRAME
    assert nominal["assumptions"] == list(ASSUMPTIONS)
    assert nominal["disclaimer"] == DISCLAIMER
    assert nominal["divert_from"] == "high_gate"
    assert nominal["divert_state"]["altitude_m"] > 1000
    assert nominal["settings"] == routes_request().model_dump(mode="json")
    assert len(nominal["result_id"]) == 64
    roles = [r["role"] for r in nominal["routes"]]
    assert roles[0] == "primary" and roles.count("primary") == 1
    assert set(roles) <= {"primary", "backup", "rejected"}
    assert nominal["counts"] == {
        role: roles.count(role) for role in ("primary", "backup", "rejected")
    }
    ids = [r["id"] for r in nominal["routes"]]
    assert len(ids) == len(set(ids))
    for route in nominal["routes"]:
        assert set(route) == ROUTE_KEYS
        assert set(route["metrics"]) == METRICS
        for reason in route["reasons"]:
            assert set(reason) == REASON_KEYS
            assert reason["detail"]
    json.dumps(nominal, allow_nan=False)


def test_result_id_hashes_the_output(nominal):
    body = {k: v for k, v in nominal.items() if k != "result_id"}
    identity = json.dumps(body, sort_keys=True, allow_nan=False).encode()
    assert nominal["result_id"] == hashlib.sha256(identity).hexdigest()


def test_route_set_is_deterministic(nominal):
    assert plan_mission_routes(routes_request(), SITE, flat(), 0.0) == nominal


def test_primary_is_the_planned_target(nominal):
    primary = nominal["routes"][0]
    assert primary["id"] == "PRIMARY"
    assert primary["rank"] is None
    assert primary["reachable"] is True
    assert primary["evaluated"] == "flown"
    assert primary["latitude_deg"] == SITE["latitude_deg"]
    assert primary["longitude_deg_east"] == SITE["longitude_deg_east"]
    assert primary["distance_m"] == 0.0
    assert [r["code"] for r in primary["reasons"]] == ["planned_target"]
    margin = primary["reasons"][0]
    assert margin["value"] == pytest.approx(primary["metrics"]["reserve_margin_kg"])
    assert margin["unit"] == "kg"
    assert primary["metrics"]["divert_delta_v_mps"] == pytest.approx(0.0, abs=1e-6)


def test_backups_are_reachable_ranked_and_explained(nominal):
    backups = by_role(nominal, "backup")
    assert backups
    assert [b["rank"] for b in backups] == list(range(1, len(backups) + 1))
    for b in backups:
        assert b["reachable"] is True
        assert b["evaluated"] == "flown"
        assert b["metrics"]["reserve_margin_kg"] >= 0
        assert b["distance_m"] > 0
        codes = [r["code"] for r in b["reasons"]]
        assert codes == ["reachable", "backup_rank", "not_planned_target"]
        reasons = {r["code"]: r for r in b["reasons"]}
        assert reasons["reachable"]["value"] == pytest.approx(
            b["metrics"]["reserve_margin_kg"]
        )
        assert reasons["backup_rank"]["value"] == b["rank"]
        assert reasons["backup_rank"]["limit"] == len(backups)
        assert reasons["not_planned_target"]["value"] == pytest.approx(b["distance_m"])
        assert reasons["not_planned_target"]["unit"] == "m"


def test_backup_order_follows_r7(nominal):
    plan = plan_alternatives(alternatives_request(routes_request(), SITE), flat(), 0.0)
    reachable = [c for c in plan["candidates"] if c["reachable"]]
    backups = by_role(nominal, "backup")
    assert [b["id"] for b in backups] == [c["id"] for c in reachable]
    for b, c in zip(backups, reachable):
        assert b["down_m"] == c["down_m"] and b["cross_m"] == c["cross_m"]
        assert b["metrics"]["safety_score"] == c["safety_score"]
        assert b["metrics"]["divert_delta_v_mps"] == c["divert_delta_v_mps"]
        assert b["metrics"]["propellant_remaining_kg"] == c["propellant_remaining_kg"]
        assert (
            b["metrics"]["max_landing_slope_deg"]
            == c["safety"]["max_landing_slope_deg"]
        )
        assert b["metrics"]["hazard_distance_m"] == c["safety"]["hazard_distance_m"]
        assert (
            b["metrics"]["touchdown_position_error_m"]
            == c["touchdown"]["position_error_m"]
        )


def test_flown_routes_carry_bounded_paths(nominal):
    t0 = nominal["divert_state"]["t_s"]
    for route in nominal["routes"]:
        if route["evaluated"] != "flown":
            continue
        path = route["trajectory"]
        assert 2 <= len(path) <= MAX_ROUTE_SAMPLES
        assert set(path[0]) == {"t_s", "down_m", "cross_m", "altitude_m"}
        times = [p["t_s"] for p in path]
        assert times[0] == pytest.approx(t0)
        assert all(b > a for a, b in zip(times, times[1:]))
        assert path[-1]["t_s"] == pytest.approx(
            t0 + route["metrics"]["time_to_touchdown_s"]
        )
        assert path[-1]["cross_m"] == pytest.approx(route["cross_m"])
        assert path[-1]["down_m"] == pytest.approx(route["down_m"], abs=5.0)


def test_flown_rejections_keep_r7_reasons_and_paths(mixed):
    assert mixed["routes"][0]["reachable"] is True
    rejected = by_role(mixed, "rejected")
    flown = [r for r in rejected if r["evaluated"] == "flown"]
    assert flown
    codes = set()
    for r in flown:
        assert r["reachable"] is False
        assert r["rank"] is None
        assert r["reasons"]
        assert {x["code"] for x in r["reasons"]} <= FLIGHT_REJECTIONS
        codes |= {x["code"] for x in r["reasons"]}
        assert len(r["trajectory"]) >= 2
        assert all(r["metrics"][k] is not None for k in FLIGHT_METRICS)
    assert "reserve" in codes
    for b in by_role(mixed, "backup"):
        assert b["reachable"] is True


def test_primary_stays_primary_when_unreachable():
    result = plan_mission_routes(
        routes_request(custom_profile=short_profile(760.0).model_dump()),
        SITE,
        flat(),
        0.0,
    )
    primary = result["routes"][0]
    assert primary["role"] == "primary" and primary["reachable"] is False
    codes = [r["code"] for r in primary["reasons"]]
    assert codes[0] == "planned_target"
    assert set(codes[1:]) and set(codes[1:]) <= FLIGHT_REJECTIONS
    assert by_role(result, "backup") == []


def test_terrain_rejections_are_points_without_paths(ramp):
    rejected = by_role(ramp, "rejected")
    terrain_only = [r for r in rejected if r["evaluated"] == "terrain_only"]
    assert terrain_only
    assert [r["id"] for r in terrain_only] == [
        f"T{i + 1}" for i in range(len(terrain_only))
    ]
    for r in terrain_only:
        assert r["reachable"] is None
        assert r["rank"] is None
        assert r["trajectory"] == []
        assert "slope" in {x["code"] for x in r["reasons"]}
        assert all(r["metrics"][k] is None for k in FLIGHT_METRICS)
        assert r["metrics"]["max_landing_slope_deg"] > 10.0
    for b in by_role(ramp, "backup"):
        assert b["metrics"]["max_landing_slope_deg"] <= 10.0


def test_route_order_is_primary_backups_flown_then_terrain(ramp, mixed):
    for result in (ramp, mixed):
        order = {"primary": 0, "backup": 1}
        keys = [
            order.get(r["role"], 2 if r["evaluated"] == "flown" else 3)
            for r in result["routes"]
        ]
        assert keys == sorted(keys)


def test_terrain_rejections_can_be_turned_off():
    result = plan_mission_routes(
        routes_request(max_terrain_rejections=0), SITE, east_ramp(), 0.0
    )
    assert all(r["evaluated"] == "flown" for r in result["routes"])


def test_build_routes_respects_the_sample_limit():
    plan = plan_alternatives(
        alternatives_request(routes_request(), SITE), flat(), 0.0, trajectories=True
    )
    routes = build_routes(plan, [], max_samples=10)
    assert len(routes) == 1 + len(plan["candidates"])
    assert all(2 <= len(r["trajectory"]) <= 10 for r in routes)


def test_unknown_vehicle_raises():
    with pytest.raises(MissionRoutesError):
        plan_mission_routes(routes_request(profile_id="missing"), SITE, flat(), 0.0)
    with pytest.raises(MissionRoutesError):
        plan_mission_routes(routes_request(), SITE, flat(size=5), 0.0)


# ------------------------------------------------------------ API


def write_dem(root, value=1234.0, size=201):
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


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    with TestClient(app) as c:
        c.store = app.state.store
        yield c


def stored_analysis(client, status="ready"):
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 11, 20, tzinfo=timezone.utc)
    site = dict(latitude_deg=-89.99, longitude_deg_east=0.0)
    job = client.store.create(
        Submission(**site, start_utc=start, end_utc=start + timedelta(hours=240))
    )
    if status != "pending":
        analysis = {
            "request": {
                "site": {"site_id": "test", "altitude_km": 0.0, **site},
                "start_utc": "2026-11-20T00:00:00Z",
                "end_utc": "2026-11-30T00:00:00Z",
                "step_s": 3600,
            },
            "sun": {"intervals": []},
            "earth": {"intervals": []},
        }
        client.store.update(job["id"], status, result={"analysis": analysis})
    return job["id"]


def test_mission_routes_endpoint(client, tmp_path):
    body = {"search_radius_m": 1000.0, "max_candidates": 4}
    assert client.post("/analyses/missing/mission-routes", json=body).status_code == 404
    pending = stored_analysis(client, "pending")
    assert (
        client.post(f"/analyses/{pending}/mission-routes", json=body).status_code == 409
    )
    ready = stored_analysis(client)
    url = f"/analyses/{ready}/mission-routes"
    assert client.post(url, json=body).status_code == 409  # terrain not cached
    write_dem(tmp_path / "data")
    response = client.post(url, json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["analysis_id"] == ready
    assert result["routes"][0]["role"] == "primary"
    assert result["routes"][0]["terrain_height_m"] == pytest.approx(1234.0)
    assert by_role(result, "backup")
    assert client.post(url, json={"profile_id": "missing"}).status_code == 422
    assert client.post(url, json={"max_candidates": 0}).status_code == 422
    insufficient = stored_analysis(client, "insufficient_data")
    assert (
        client.post(f"/analyses/{insufficient}/mission-routes", json=body).status_code
        == 200
    )
