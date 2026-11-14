"""R8/S12 terrain-constrained surface routes with sunlight and Earth checks.

References for expected values:
- A* with an admissible heuristic returns a shortest path (Hart, Nilsson &
  Raphael 1968, IEEE Trans. SSC 4(2)); on a flat grid a straight row of n
  cells is n * 80 m and a diagonal is n * 80 * sqrt(2) m.
- Mean solar angular radius 959.63 arcsec at 1 au (IAU 2015 nominal radius).
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

from app.alternatives import LocalTerrain
from app.analysis_api import create_app
from app.surface_routes import (
    MAX_PATH_POINTS,
    SOLAR_RADIUS_DEG,
    RouteError,
    RouteRequest,
    astar,
    classify_earth,
    classify_sun,
    interpolate_sky,
    passable_grid,
    path_metrics,
    plan_routes,
    sample_waypoints,
)
from app.terrain_safety import slope_grid_deg
from shared.constants import MOON_REFERENCE_RADIUS_M as R

STEREO = f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m +no_defs"
POLE = dict(site_id="pole", latitude_deg=-90.0, longitude_deg_east=0.0)


def body(*targets, **changes):
    return RouteRequest(**(POLE | {"targets": [dict(t) for t in targets]} | changes))


def target(name, bearing, distance):
    return {"target_id": name, "bearing_deg": bearing, "distance_m": distance}


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


def flat(size=101):
    return terrain(np.zeros((size, size)))


def frames(sun_el, earth_el, hours=10, sun_az=90.0, earth_az=0.0):
    return [
        {
            "utc": f"2026-11-04T{h:02d}:00:00Z",
            "sun": {"azimuth_deg": sun_az, "elevation_deg": sun_el},
            "earth": {"azimuth_deg": earth_az, "elevation_deg": earth_el},
        }
        for h in range(hours + 1)
    ]


class FlatHorizon:
    def __init__(self, elevation=0.0):
        self.calls = []
        self.elevation = elevation

    def __call__(self, latitude_deg, longitude_deg_east, altitude_m):
        self.calls.append((latitude_deg, longitude_deg_east, altitude_m))
        return {
            "directions": [
                {"azimuth_deg": float(a), "elevation_deg": self.elevation}
                for a in range(360)
            ]
        }


# ------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "changes",
    [
        {"targets": []},
        {"targets": [target(f"T{i}", 10.0 * i, 500.0) for i in range(6)]},
        {"targets": [target("A", 0, 500), target("A", 90, 500)]},
        {"targets": [target("A", 0, 0.0)]},
        {"targets": [target("A", 0, 9000.0)]},
        {"targets": [target("A", 360.0, 500)]},
        {"targets": [target("A", 0, 500)], "slope_limit_deg": 40.0},
        {"targets": [target("A", 0, 500)], "speed_mps": 0.0},
        {"targets": [target("A", 0, 500)], "waypoints_per_route": 9},
        {"targets": [target("A", 0, 500)], "waypoints_per_route": 3.0},
        {"targets": [target("A", 0, 500)], "extra": 1},
    ],
)
def test_invalid_requests(changes):
    with pytest.raises(ValidationError):
        RouteRequest(**(POLE | changes))


def test_defaults_are_planning_assumptions():
    request = body(target("A", 0, 500))
    assert request.slope_limit_deg == 15.0
    assert request.speed_mps == 0.1
    assert request.observer_height_m == 2.0
    assert request.waypoints_per_route == 5
    assert SOLAR_RADIUS_DEG == pytest.approx(959.63 / 3600)


# ------------------------------------------------------------ grid search


def test_passable_grid():
    slopes = np.array([[0.0, 15.0, 15.1], [np.nan, 3.0, 40.0]])
    assert passable_grid(slopes, 15.0).tolist() == [
        [True, True, False],
        [False, True, False],
    ]


def test_astar_straight_and_diagonal():
    heights = np.zeros((11, 11))
    ok = np.ones((11, 11), bool)
    row = astar(heights, ok, (5, 0), (5, 10), 80.0)
    assert row[0] == (5, 0) and row[-1] == (5, 10)
    assert len(row) == 11
    assert all(r == 5 for r, _ in row)
    diagonal = astar(heights, ok, (0, 0), (10, 10), 80.0)
    assert len(diagonal) == 11
    assert diagonal == [(i, i) for i in range(11)]
    assert astar(heights, ok, (5, 0), (5, 10), 80.0) == row


def test_astar_detours_around_a_wall_and_never_crosses_it():
    heights = np.zeros((11, 11))
    ok = np.ones((11, 11), bool)
    ok[1:, 5] = False  # wall with a gap at row 0
    path = astar(heights, ok, (8, 0), (8, 10), 80.0)
    assert path is not None
    assert (0, 5) in path
    assert all(ok[r, c] for r, c in path)
    steps = [math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(path, path[1:])]
    assert all(s in (1.0, math.sqrt(2)) for s in steps)


def test_astar_unreachable_cases():
    heights = np.zeros((11, 11))
    ok = np.ones((11, 11), bool)
    ok[:, 5] = False
    assert astar(heights, ok, (5, 0), (5, 10), 80.0) is None
    ok = np.ones((11, 11), bool)
    ok[5, 10] = False
    assert astar(heights, ok, (5, 0), (5, 10), 80.0) is None
    ok = np.ones((11, 11), bool)
    assert astar(heights, ok, (5, 0), (5, 11), 80.0) is None
    assert astar(heights, ok, (-1, 0), (5, 5), 80.0) is None


def test_astar_prefers_flat_over_climbing():
    # Same horizontal length either way round a hill: the climb costs more.
    heights = np.zeros((7, 7))
    heights[2:5, 3] = 200.0
    ok = np.ones((7, 7), bool)
    path = astar(heights, ok, (3, 0), (3, 6), 80.0)
    assert all(heights[r, c] == 0.0 for r, c in path)


def test_path_metrics():
    heights = np.array([[0.0, 10.0, 20.0, 15.0]])
    slopes = np.array([[1.0, 4.0, 9.0, 2.0]])
    path = [(0, 0), (0, 1), (0, 2), (0, 3)]
    metrics = path_metrics(path, heights, slopes, 80.0, 0.5)
    step = [math.hypot(80, 10), math.hypot(80, 10), math.hypot(80, 5)]
    assert metrics["length_m"] == pytest.approx(sum(step))
    assert metrics["horizontal_length_m"] == pytest.approx(240.0)
    assert metrics["duration_s"] == pytest.approx(sum(step) / 0.5)
    assert metrics["max_slope_deg"] == pytest.approx(9.0)
    assert metrics["elevation_gain_m"] == pytest.approx(20.0)
    assert metrics["cumulative_m"] == pytest.approx(
        [0.0, step[0], step[0] + step[1], sum(step)]
    )
    single = path_metrics([(0, 0)], heights, slopes, 80.0, 0.5)
    assert single["length_m"] == 0.0 and single["cumulative_m"] == [0.0]


def test_sample_waypoints():
    assert sample_waypoints(11, 5) == [0, 2, 5, 8, 10]
    assert sample_waypoints(2, 5) == [0, 1]
    assert sample_waypoints(1, 5) == [0]
    picks = sample_waypoints(100, 8)
    assert picks[0] == 0 and picks[-1] == 99 and len(picks) == 8
    assert picks == sorted(set(picks))


# ------------------------------------------------------------ sky


def test_interpolate_sky_wraps_azimuth():
    rows = [
        {"utc": "2026-11-04T00:00:00Z", "sun": {"azimuth_deg": 350.0, "elevation_deg": 1.0}},
        {"utc": "2026-11-04T01:00:00Z", "sun": {"azimuth_deg": 10.0, "elevation_deg": 3.0}},
    ]  # fmt: skip
    mid = interpolate_sky(rows, "2026-11-04T00:30:00Z", "sun")
    assert mid["azimuth_deg"] % 360 == pytest.approx(0.0, abs=1e-9)
    assert mid["elevation_deg"] == pytest.approx(2.0)
    start = interpolate_sky(rows, "2026-11-04T00:00:00Z", "sun")
    assert start == pytest.approx({"azimuth_deg": 350.0, "elevation_deg": 1.0})
    assert interpolate_sky(rows, "2026-11-04T01:00:01Z", "sun") is None
    assert interpolate_sky(rows, "2026-11-03T23:59:59Z", "sun") is None
    rows[1]["sun"]["elevation_deg"] = None
    assert interpolate_sky(rows, "2026-11-04T00:30:00Z", "sun") is None


def test_classify_sun_and_earth():
    r = SOLAR_RADIUS_DEG
    assert classify_sun(r + 0.01) == "full"
    assert classify_sun(0.0) == "partial"
    assert classify_sun(-r - 0.01) == "dark"
    assert classify_sun(None) == "unknown"
    assert classify_earth(0.5) == "visible"
    assert classify_earth(-0.5) == "blocked"
    assert classify_earth(0.0) == "boundary"
    assert classify_earth(None) == "unknown"


# ------------------------------------------------------------ planning


@pytest.fixture(scope="module")
def straight():
    return plan_routes(body(target("EAST", 90.0, 2000.0)), flat())


def test_straight_route_on_flat_terrain(straight):
    route = straight["routes"][0]
    assert route["target_id"] == "EAST"
    assert route["reachable"] is True and route["reason"] is None
    assert route["length_m"] == pytest.approx(2000.0)
    assert route["horizontal_length_m"] == pytest.approx(2000.0)
    assert route["detour_ratio"] == pytest.approx(1.0)
    assert route["duration_s"] == pytest.approx(20000.0)
    assert route["max_slope_deg"] == pytest.approx(0.0)
    assert route["path"][0] == pytest.approx({"north_m": 0.0, "east_m": 0.0})
    assert route["path"][-1] == pytest.approx({"north_m": 0.0, "east_m": 2000.0})
    assert len(route["path"]) <= MAX_PATH_POINTS
    assert -90.0 <= route["latitude_deg"] < -89.9


def test_waypoints_without_sky(straight):
    route = straight["routes"][0]
    points = route["waypoints"]
    assert len(points) == 5
    assert points[0]["distance_along_m"] == 0.0
    assert points[-1]["distance_along_m"] == pytest.approx(2000.0)
    assert points[-1]["elapsed_s"] == pytest.approx(20000.0)
    assert all(p["sun"] is None and p["earth"] is None for p in points)
    assert all(p["arrival_utc"] is None for p in points)
    assert route["shadowed_waypoints"] is None
    assert straight["sky_evaluated"] is False


def test_efficiency_without_sky(straight):
    efficiency = straight["efficiency"]
    assert efficiency["sunlit_fraction"] is None
    assert efficiency["reachable_fraction"] == 1.0
    assert efficiency["detour_efficiency"] == pytest.approx(1.0)
    assert efficiency["score"] == pytest.approx(1.0)
    assert efficiency["weights"] == pytest.approx(
        {"reachable": 2 / 3, "detour": 1 / 3, "sunlit": 0.0}
    )


def test_outside_and_impassable_targets_are_explained():
    heights = np.zeros((101, 101))
    # 30 degree slope band over the eastern target (Horn ignores the centre
    # cell, so a single spike would not make the cell itself steep).
    for col in range(70, 81):
        heights[45:56, col] = (col - 70) * 80.0 * math.tan(math.radians(30))
    result = plan_routes(
        body(
            target("EAST", 90.0, 2000.0),
            target("FAR", 0.0, 6000.0),
            target("WEST", 270.0, 1000.0),
        ),
        terrain(heights),
    )
    by_id = {r["target_id"]: r for r in result["routes"]}
    assert by_id["EAST"]["reachable"] is False
    assert by_id["EAST"]["reason"] == "target_impassable"
    assert by_id["FAR"]["reason"] == "outside_terrain"
    assert by_id["WEST"]["reachable"] is True
    for route in (by_id["EAST"], by_id["FAR"]):
        assert route["detail"]
        assert route["length_m"] is None and route["waypoints"] == []
    efficiency = result["efficiency"]
    assert efficiency["reachable_fraction"] == pytest.approx(1 / 3)
    assert efficiency["score"] == pytest.approx(2 / 3 * (1 / 3) + 1 / 3 * 1.0)


def test_route_detours_around_steep_terrain():
    heights = np.zeros((101, 101))
    heights[5:90, 62] = 400.0  # ridge east of the site, open to the south
    slopes = slope_grid_deg(heights, 80.0)
    result = plan_routes(body(target("EAST", 90.0, 2000.0)), terrain(heights))
    route = result["routes"][0]
    assert route["reachable"] is True
    assert route["detour_ratio"] > 1.2
    assert route["max_slope_deg"] <= 15.0
    for point in route["path"]:
        col = int(math.floor((point["east_m"] + 4040.0) / 80.0))
        row = int(math.floor((4040.0 - point["north_m"]) / 80.0))
        assert slopes[row, col] <= 15.0


def test_blocked_route_has_no_path():
    heights = np.zeros((101, 101))
    heights[:, 62] = 400.0
    result = plan_routes(body(target("EAST", 90.0, 2000.0)), terrain(heights))
    assert result["routes"][0]["reason"] == "no_path"


def test_impassable_landing_cell_raises():
    heights = np.zeros((101, 101))
    for col in range(45, 56):  # 30 degree slope band under the landing cell
        heights[45:56, col] = (col - 45) * 80.0 * math.tan(math.radians(30))
    with pytest.raises(RouteError):
        plan_routes(body(target("EAST", 90.0, 2000.0)), terrain(heights))


def test_sunlit_route_with_flat_horizon():
    horizon = FlatHorizon(0.0)
    result = plan_routes(
        body(target("EAST", 90.0, 2000.0), observer_height_m=2.0),
        flat(),
        frames(sun_el=5.0, earth_el=-3.0),
        horizon,
    )
    route = result["routes"][0]
    assert result["sky_evaluated"] is True
    assert len(horizon.calls) == 5
    assert all(call[2] == pytest.approx(2.0) for call in horizon.calls)
    points = route["waypoints"]
    assert points[0]["arrival_utc"] == "2026-11-04T00:00:00Z"
    assert all(p["sun"]["state"] == "full" for p in points)
    assert all(p["earth"]["state"] == "blocked" for p in points)
    assert points[0]["sun"]["clearance_deg"] == pytest.approx(5.0)
    assert route["shadowed_waypoints"] == 0
    assert route["no_earth_waypoints"] == 5
    assert result["efficiency"]["sunlit_fraction"] == 1.0
    assert result["efficiency"]["score"] == pytest.approx(1.0)


def test_dark_route_and_horizon_obstruction():
    dark = plan_routes(
        body(target("EAST", 90.0, 2000.0)),
        flat(),
        frames(sun_el=-5.0, earth_el=4.0),
        FlatHorizon(0.0),
    )
    route = dark["routes"][0]
    assert route["shadowed_waypoints"] == 5
    assert all(p["earth"]["state"] == "visible" for p in route["waypoints"])
    assert dark["efficiency"]["sunlit_fraction"] == 0.0
    assert dark["efficiency"]["score"] == pytest.approx(0.5 * 1 + 0.25 * 1 + 0.0)
    blocked = plan_routes(
        body(target("EAST", 90.0, 2000.0)),
        flat(),
        frames(sun_el=5.0, earth_el=4.0),
        FlatHorizon(8.0),
    )
    assert blocked["routes"][0]["shadowed_waypoints"] == 5


def test_arrivals_after_the_analysis_are_unknown():
    result = plan_routes(
        body(target("EAST", 90.0, 2000.0), speed_mps=0.01),
        flat(),
        frames(sun_el=5.0, earth_el=4.0, hours=10),
        FlatHorizon(0.0),
    )
    states = [p["sun"]["state"] for p in result["routes"][0]["waypoints"]]
    assert states[0] == "full"
    assert states[-1] == "unknown"
    assert result["routes"][0]["waypoints"][-1]["arrival_utc"] is not None


def test_explicit_departure_time_is_used():
    result = plan_routes(
        body(target("EAST", 90.0, 2000.0), departure_utc="2026-11-04T05:00:00Z"),
        flat(),
        frames(sun_el=5.0, earth_el=4.0),
        FlatHorizon(0.0),
    )
    assert result["routes"][0]["waypoints"][0]["arrival_utc"] == "2026-11-04T05:00:00Z"


def test_plan_is_deterministic(straight):
    again = plan_routes(body(target("EAST", 90.0, 2000.0)), flat())
    assert again == straight
    assert len(straight["result_id"]) == 64
    assert straight["assumptions"]


# ------------------------------------------------------------ API


def write_dem(root, value=0.0, size=81):
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
        payload = POLE | {"targets": [target("EAST", 90.0, 1500.0)]}
        assert client.post("/surface/routes", json=payload).status_code == 409
        write_dem(tmp_path / "data")
        response = client.post("/surface/routes", json=payload)
        assert response.status_code == 200
        result = response.json()
        assert result["routes"][0]["reachable"] is True
        assert result["sky_evaluated"] is False
        assert (
            client.post(
                "/surface/routes", json=payload | {"analysis_job_id": "missing"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/surface/routes", json=payload | {"latitude_deg": -85.0}
            ).status_code
            == 422
        )
        assert (
            client.post("/surface/routes", json=POLE | {"targets": []}).status_code
            == 422
        )
