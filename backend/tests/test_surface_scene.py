"""Opt-in 3-D scene payload for R8 surface routes: terrain window and A* order.

The scene lets the interface draw the real DEM window, its traversable cells
and the planner's expansion order. A* expansion order follows Hart, Nilsson
and Raphael (1968): each popped cell is closed once, the goal returns before
it is closed, and every path cell except the goal is closed first.
"""

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis_api import create_app
from app.surface_routes import (
    DEFAULT_MAX_EXPLORED_CELLS,
    DEFAULT_SCENE_MARGIN_M,
    astar,
    passable_grid,
    plan_routes,
    scene_window,
)
from app.terrain_safety import slope_grid_deg
from tests.test_surface_routes import POLE, body, flat, target, terrain, write_dem

TOL_M = 1e-6


def local(scene, row, col):
    """Window-relative cell centre to local east/north metres via the affine."""
    east = scene["local_from_cell"]["east"]
    north = scene["local_from_cell"]["north"]
    x, y = col + 0.5, row + 0.5
    return (
        east[0] * x + east[1] * y + east[2],
        north[0] * x + north[1] * y + north[2],
    )


def cell_of(scene, east_m, north_m):
    """Inverse of `local` (window-relative row, col of the containing cell)."""
    e, n = scene["local_from_cell"]["east"], scene["local_from_cell"]["north"]
    a = np.array([[e[0], e[1]], [n[0], n[1]]])
    x, y = np.linalg.solve(a, np.array([east_m - e[2], north_m - n[2]]))
    return int(math.floor(y)), int(math.floor(x))


# ------------------------------------------------------------------ defaults


def test_defaults_and_bounds():
    assert DEFAULT_SCENE_MARGIN_M == 400.0
    assert DEFAULT_MAX_EXPLORED_CELLS == 4000
    request = body(target("EAST", 90.0, 1000.0))
    assert request.include_scene is False
    for changes in (
        {"scene_margin_m": -1.0},
        {"scene_margin_m": 2001.0},
        {"max_explored_cells": 99},
        {"max_explored_cells": 20001},
        {"max_explored_cells": 500.0},
    ):
        with pytest.raises(ValidationError):
            body(target("EAST", 90.0, 1000.0), **changes)


# ---------------------------------------------------------- A* search order


def test_astar_records_expansion_order_without_changing_the_path():
    heights = np.zeros((11, 11))
    ok = np.ones((11, 11), dtype=bool)
    explored = []
    path = astar(heights, ok, (5, 0), (5, 10), 80.0, explored=explored)
    assert path == astar(heights, ok, (5, 0), (5, 10), 80.0)
    assert explored[0] == (5, 0)
    assert (5, 10) not in explored  # the goal returns before it is closed
    assert len(explored) == len(set(explored))
    assert set(path[:-1]) <= set(explored)


def test_astar_never_expands_impassable_cells():
    heights = np.zeros((17, 17))
    ok = np.ones((17, 17), dtype=bool)
    ok[2:15, 8] = False
    explored = []
    path = astar(heights, ok, (8, 0), (8, 16), 80.0, explored=explored)
    assert path is not None
    assert all(ok[cell] for cell in explored)


def test_unreachable_goal_explores_the_whole_start_component():
    heights = np.zeros((9, 9))
    ok = np.ones((9, 9), dtype=bool)
    ok[:, 4] = False
    explored = []
    assert astar(heights, ok, (4, 0), (4, 8), 80.0, explored=explored) is None
    assert sorted(explored) == sorted((r, c) for r in range(9) for c in range(4))


def test_invalid_endpoints_record_nothing():
    heights = np.zeros((5, 5))
    ok = np.ones((5, 5), dtype=bool)
    explored = []
    assert astar(heights, ok, (-1, 0), (4, 4), 80.0, explored=explored) is None
    assert explored == []


# ------------------------------------------------------------------- window


def test_scene_window_adds_margin_and_clips():
    assert scene_window(101, 101, [(50, 50), (50, 75)], 5) == (45, 45, 56, 81)
    assert scene_window(20, 20, [(1, 1), (18, 3)], 5) == (0, 0, 20, 9)
    assert scene_window(10, 10, [(4, 4)], 0) == (4, 4, 5, 5)
    with pytest.raises(ValueError):
        scene_window(10, 10, [], 2)
    with pytest.raises(ValueError):
        scene_window(10, 10, [(1, 1)], -1)


# ------------------------------------------------------------------- scene


@pytest.fixture(scope="module")
def flat_scene():
    return plan_routes(body(target("EAST", 90.0, 2000.0), include_scene=True), flat())


def test_scene_is_absent_by_default():
    result = plan_routes(body(target("EAST", 90.0, 2000.0)), flat())
    assert result["scene"] is None


def test_flat_scene_geometry(flat_scene):
    scene = flat_scene["scene"]
    assert scene["pixel_m"] == 80.0
    assert scene["slope_limit_deg"] == 15.0
    # Site cell (50, 50), target cell (50, 75) and a 400 m (5-cell) margin.
    assert scene["window"] == {"row0": 45, "col0": 45, "row1": 56, "col1": 81}
    assert (scene["rows"], scene["cols"]) == (11, 36)
    assert scene["site_cell"] == [5, 5]
    assert len(scene["heights_m"]) == 11 and all(
        len(r) == 36 for r in scene["heights_m"]
    )
    assert all(v == 0.0 for row in scene["heights_m"] for v in row)
    assert all(all(row) for row in scene["passable"])
    east, north = local(scene, *scene["site_cell"])
    assert abs(east) < TOL_M and abs(north) < TOL_M
    end = scene["path_cells"]["EAST"][-1]
    assert end == [5, 30]
    east, north = local(scene, *end)
    assert east == pytest.approx(2000.0, abs=TOL_M) and north == pytest.approx(
        0.0, abs=TOL_M
    )


def test_explored_cells_start_at_the_site_and_stay_in_the_window(flat_scene):
    scene = flat_scene["scene"]
    explored = scene["explored"]["EAST"]
    assert explored[0] == scene["site_cell"]
    assert 0 < len(explored) <= DEFAULT_MAX_EXPLORED_CELLS
    assert scene["explored_truncated"]["EAST"] is False
    for row, col in explored:
        assert 0 <= row < scene["rows"] and 0 <= col < scene["cols"]
    path = [tuple(c) for c in scene["path_cells"]["EAST"]]
    assert set(path[:-1]) <= {tuple(c) for c in explored}


def test_relative_heights_follow_the_dem():
    heights = np.tile(np.arange(101, dtype=float) * 2.0, (101, 1))  # 2 m per column
    result = plan_routes(
        body(target("EAST", 90.0, 1000.0), include_scene=True), terrain(heights)
    )
    scene = result["scene"]
    r0, c0 = scene["window"]["row0"], scene["window"]["col0"]
    site = scene["site_cell"]
    assert scene["heights_m"][site[0]][site[1]] == 0.0
    for r in range(scene["rows"]):
        for c in range(scene["cols"]):
            expected = heights[r0 + r, c0 + c] - heights[r0 + site[0], c0 + site[1]]
            assert scene["heights_m"][r][c] == pytest.approx(expected, abs=TOL_M)


def test_scene_marks_steep_cells_and_routes_avoid_them():
    heights = np.zeros((101, 101))
    heights[5:90, 62] = 400.0  # the ridge from test_route_detours_around_steep_terrain
    result = plan_routes(
        body(target("EAST", 90.0, 2000.0), include_scene=True), terrain(heights)
    )
    scene = result["scene"]
    r0, c0 = scene["window"]["row0"], scene["window"]["col0"]
    expected = passable_grid(slope_grid_deg(heights, 80.0), 15.0)
    for r in range(scene["rows"]):
        for c in range(scene["cols"]):
            assert scene["passable"][r][c] == bool(expected[r0 + r, c0 + c])
    assert not all(all(row) for row in scene["passable"])
    for row, col in scene["path_cells"]["EAST"] + scene["explored"]["EAST"]:
        assert scene["passable"][row][col]
    # The downsampled route points map back onto traversable window cells.
    for point in result["routes"][0]["path"]:
        row, col = cell_of(scene, point["east_m"], point["north_m"])
        assert scene["passable"][row][col]


def test_explored_list_is_capped_and_flagged():
    heights = np.zeros((101, 101))
    heights[5:90, 62] = 400.0
    result = plan_routes(
        body(target("EAST", 90.0, 2000.0), include_scene=True, max_explored_cells=100),
        terrain(heights),
    )
    scene = result["scene"]
    assert len(scene["explored"]["EAST"]) == 100
    assert scene["explored_truncated"]["EAST"] is True


def test_unknown_heights_are_null_and_impassable():
    heights = np.zeros((101, 101))
    heights[50, 52] = np.nan
    result = plan_routes(
        body(target("EAST", 90.0, 1000.0), include_scene=True), terrain(heights)
    )
    scene = result["scene"]
    r0, c0 = scene["window"]["row0"], scene["window"]["col0"]
    assert scene["heights_m"][50 - r0][52 - c0] is None
    assert scene["passable"][50 - r0][52 - c0] is False


def test_unreachable_and_outside_targets_have_no_path_cells():
    heights = np.zeros((101, 101))
    heights[:, 62] = 400.0
    result = plan_routes(
        body(
            target("EAST", 90.0, 2000.0), target("FAR", 0.0, 8000.0), include_scene=True
        ),
        terrain(heights),
    )
    scene = result["scene"]
    assert scene["path_cells"]["EAST"] == [] and scene["path_cells"]["FAR"] == []
    assert len(scene["explored"]["EAST"]) > 0  # the search still ran
    assert scene["explored"]["FAR"] == []  # outside the terrain: no search


def test_scene_is_deterministic(flat_scene):
    again = plan_routes(body(target("EAST", 90.0, 2000.0), include_scene=True), flat())
    assert again == flat_scene


def test_route_api_returns_the_scene(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as client:
        write_dem(tmp_path / "data")
        payload = POLE | {
            "targets": [target("EAST", 90.0, 1500.0)],
            "include_scene": True,
        }
        response = client.post("/surface/routes", json=payload)
        assert response.status_code == 200
        scene = response.json()["scene"]
        assert scene["rows"] > 0 and scene["path_cells"]["EAST"]
        bad = client.post("/surface/routes", json=payload | {"scene_margin_m": 3000})
        assert bad.status_code == 422
