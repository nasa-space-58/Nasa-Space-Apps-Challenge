"""Route terrain grid: the DEM resampled into the mission route frame.

References for expected values:
- Bilinear interpolation reproduces a plane exactly between known samples.
- Route frame (app.risk._approach_basis): azimuth 0 puts down_m along north
  and cross_m along east; azimuth 90 puts down_m along east and cross_m
  along south.
"""

import json
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from app.alternatives import LocalTerrain
from app.analysis_api import create_app
from app.mission_routes import (
    MAX_TERRAIN_CELLS,
    TERRAIN_MARGIN_M,
    route_extent,
    route_terrain,
)
from shared.constants import MOON_REFERENCE_RADIUS_M as R
from tests.test_mission_routes import stored_analysis, write_dem

STEREO = f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m +no_defs"
SIZE = 101
HALF = SIZE * 80 / 2


def plane(x, y):
    return 0.01 * x + 0.02 * y + 5.0


def terrain(heights=None):
    if heights is None:
        cols = -HALF + 40 + 80 * np.arange(SIZE)
        rows = HALF - 40 - 80 * np.arange(SIZE)
        x, y = np.meshgrid(cols, rows)
        heights = plane(x, y)
    return LocalTerrain(
        heights_m=heights,
        transform=from_origin(-HALF, HALF, 80, 80),
        site_xy_m=(0.0, 0.0),
        north_unit_xy=(0.0, 1.0),
        east_unit_xy=(1.0, 0.0),
        pixel_m=80.0,
        crs=STEREO,
    )


def route(points, down=0.0, cross=0.0):
    return {
        "down_m": down,
        "cross_m": cross,
        "trajectory": [
            {"t_s": float(i), "down_m": d, "cross_m": c, "altitude_m": 10.0}
            for i, (d, c) in enumerate(points)
        ],
    }


EXTENT = {
    "down_min_m": -1500.0,
    "down_max_m": 900.0,
    "cross_min_m": -700.0,
    "cross_max_m": 1100.0,
}


# ------------------------------------------------------------ extent


def test_extent_covers_paths_and_end_points():
    routes = [
        route([(-7900.0, 0.0), (-100.0, 50.0), (0.0, 0.0)]),
        route([(-7900.0, 0.0), (300.0, -400.0)], down=300.0, cross=-400.0),
        route([], down=900.0, cross=1200.0),  # terrain-only rejection
    ]
    exact = route_extent(routes, 0.0)
    assert exact == {
        "down_min_m": -7900.0,
        "down_max_m": 900.0,
        "cross_min_m": -400.0,
        "cross_max_m": 1200.0,
    }
    wide = route_extent(routes, 100.0)
    assert wide == {
        "down_min_m": -8000.0,
        "down_max_m": 1000.0,
        "cross_min_m": -500.0,
        "cross_max_m": 1300.0,
    }


def test_extent_rejects_empty_routes_and_negative_margins():
    with pytest.raises(ValueError):
        route_extent([], 0.0)
    with pytest.raises(ValueError):
        route_extent([route([(0.0, 0.0)])], -1.0)


def test_margin_default_is_documented():
    assert TERRAIN_MARGIN_M == 600.0
    assert MAX_TERRAIN_CELLS == 160


# ------------------------------------------------------------ grid


def cell_xy(grid, i, j, azimuth_deg):
    down = grid["down0_m"] + i * grid["spacing_m"]
    cross = grid["cross0_m"] + j * grid["spacing_m"]
    a = math.radians(azimuth_deg)
    travel = np.array([math.sin(a), math.cos(a)])  # (east, north) components
    right = np.array([math.cos(a), -math.sin(a)])
    return down * travel + cross * right


@pytest.mark.parametrize("azimuth", [0.0, 90.0, 217.0])
def test_grid_reproduces_a_plane_in_the_route_frame(azimuth):
    grid = route_terrain(terrain(), azimuth, EXTENT, 5.0)
    assert len(grid["heights_m"]) == grid["rows"]
    assert all(len(row) == grid["cols"] for row in grid["heights_m"])
    checked = 0
    for i in range(0, grid["rows"], 3):
        for j in range(0, grid["cols"], 3):
            x, y = cell_xy(grid, i, j, azimuth)
            value = grid["heights_m"][i][j]
            assert value is not None
            assert value == pytest.approx(plane(x, y) - 5.0, abs=1e-6)
            checked += 1
    assert checked > 20


def test_azimuth_zero_puts_down_north_and_cross_east():
    grid = route_terrain(terrain(), 0.0, EXTENT, 0.0)
    rows, cols = grid["rows"], grid["cols"]
    # Height rises 0.02 m per metre north and 0.01 m per metre east.
    north_step = grid["heights_m"][rows - 1][0] - grid["heights_m"][0][0]
    east_step = grid["heights_m"][0][cols - 1] - grid["heights_m"][0][0]
    assert north_step == pytest.approx(0.02 * (rows - 1) * grid["spacing_m"])
    assert east_step == pytest.approx(0.01 * (cols - 1) * grid["spacing_m"])


def test_grid_covers_the_extent_within_the_cell_limit():
    grid = route_terrain(terrain(), 0.0, EXTENT, 0.0)
    spacing = grid["spacing_m"]
    assert spacing >= 80.0
    assert 2 <= grid["rows"] <= MAX_TERRAIN_CELLS
    assert 2 <= grid["cols"] <= MAX_TERRAIN_CELLS
    assert grid["down0_m"] <= EXTENT["down_min_m"] + 1e-9
    assert grid["down0_m"] > EXTENT["down_min_m"] - spacing
    assert grid["cross0_m"] <= EXTENT["cross_min_m"] + 1e-9
    assert grid["cross0_m"] > EXTENT["cross_min_m"] - spacing
    assert grid["down0_m"] + (grid["rows"] - 1) * spacing >= EXTENT["down_max_m"] - 1e-9
    assert (
        grid["cross0_m"] + (grid["cols"] - 1) * spacing >= EXTENT["cross_max_m"] - 1e-9
    )


def test_small_cell_limit_coarsens_the_grid():
    fine = route_terrain(terrain(), 0.0, EXTENT, 0.0)
    coarse = route_terrain(terrain(), 0.0, EXTENT, 0.0, max_cells=12)
    assert coarse["rows"] <= 12 and coarse["cols"] <= 12
    assert coarse["spacing_m"] > fine["spacing_m"]
    with pytest.raises(ValueError):
        route_terrain(terrain(), 0.0, EXTENT, 0.0, max_cells=1)


def test_samples_outside_the_window_are_unknown():
    wide = {
        "down_min_m": -9000.0,
        "down_max_m": 0.0,
        "cross_min_m": -100.0,
        "cross_max_m": 100.0,
    }
    grid = route_terrain(terrain(), 0.0, wide, 0.0)
    first = grid["heights_m"][0]  # about 9 km south, past the 4 km window
    assert all(v is None for v in first)
    assert grid["heights_m"][-1][grid["cols"] // 2] is not None
    json.dumps(grid, allow_nan=False)


def test_unknown_dem_cells_stay_unknown():
    heights = np.zeros((SIZE, SIZE))
    centre = SIZE // 2
    heights[centre - 2 : centre + 3, centre - 2 : centre + 3] = np.nan
    grid = route_terrain(terrain(heights), 0.0, EXTENT, 0.0)
    values = [v for row in grid["heights_m"] for v in row]
    assert any(v is None for v in values)
    assert all(v == 0.0 for v in values if v is not None)
    json.dumps(grid, allow_nan=False)


# ------------------------------------------------------------ API


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    with TestClient(app) as c:
        c.store = app.state.store
        yield c


def test_endpoint_returns_terrain_covering_every_route(client, tmp_path):
    write_dem(tmp_path / "data", size=301)
    ready = stored_analysis(client)
    body = {"search_radius_m": 1000.0, "max_candidates": 4}
    result = client.post(f"/analyses/{ready}/mission-routes", json=body).json()
    grid = result["terrain"]
    assert set(grid) == {
        "down0_m",
        "cross0_m",
        "spacing_m",
        "rows",
        "cols",
        "heights_m",
    }
    end_down = grid["down0_m"] + (grid["rows"] - 1) * grid["spacing_m"]
    end_cross = grid["cross0_m"] + (grid["cols"] - 1) * grid["spacing_m"]
    for r in result["routes"]:
        for p in [*r["trajectory"], {"down_m": r["down_m"], "cross_m": r["cross_m"]}]:
            assert grid["down0_m"] <= p["down_m"] <= end_down
            assert grid["cross0_m"] <= p["cross_m"] <= end_cross
    known = [v for row in grid["heights_m"] for v in row if v is not None]
    assert known and all(v == pytest.approx(0.0, abs=1e-3) for v in known)
