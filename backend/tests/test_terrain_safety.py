"""R3 terrain safety: Horn slope, landing-area slope and steep-terrain distance.

Reference: Horn (1981), Proc. IEEE 69(1):14-47. Horn's weighted 3x3 finite
difference is exact for planar surfaces, so a plane z = x tan(theta) must give
theta to floating-point precision (1e-9 deg tolerance).
"""

import hashlib
import json
import math

import numpy as np
import pytest
import rasterio
from pydantic import ValidationError
from rasterio.transform import from_origin

from app.terrain_safety import (
    DEFAULT_HAZARD_SEARCH_M,
    DEFAULT_LANDING_RADIUS_M,
    DEFAULT_SLOPE_LIMIT_DEG,
    SafetySettings,
    TerrainSafetyError,
    safety_score,
    site_safety,
    slope_grid_deg,
    terrain_safety,
)
from shared.constants import MOON_REFERENCE_RADIUS_M as R

PIXEL_M = 80.0
TOL_DEG = 1e-9


def plane(size, slope_deg, direction="x"):
    rows, cols = np.mgrid[0:size, 0:size].astype(float)
    gradient = math.tan(math.radians(slope_deg))
    if direction == "x":
        return cols * PIXEL_M * gradient
    if direction == "y":
        return rows * PIXEL_M * gradient
    # Diagonal: equal x/y components with the requested total gradient.
    return (rows + cols) * PIXEL_M * gradient / math.sqrt(2)


def ramp(size=61, start_col=40, slope_deg=30.0):
    """Flat ground with a steep ramp beginning at start_col."""
    cols = np.arange(size, dtype=float)
    height = np.where(
        cols >= start_col,
        (cols - start_col + 1) * PIXEL_M * math.tan(math.radians(slope_deg)),
        0.0,
    )
    return np.tile(height, (size, 1))


def test_defaults_are_documented_assumptions():
    settings = SafetySettings()
    assert settings.slope_limit_deg == DEFAULT_SLOPE_LIMIT_DEG == 10.0
    assert settings.landing_radius_m == DEFAULT_LANDING_RADIUS_M == 100.0
    assert settings.hazard_search_m == DEFAULT_HAZARD_SEARCH_M == 2000.0


@pytest.mark.parametrize(
    "changes",
    [
        {"slope_limit_deg": 0.5},
        {"slope_limit_deg": 46},
        {"landing_radius_m": 39},
        {"landing_radius_m": 501},
        {"hazard_search_m": 100},
        {"hazard_search_m": 6000},
        {"slope_limit_deg": float("nan")},
        {"unexpected": 1},
    ],
)
def test_settings_bounds(changes):
    with pytest.raises(ValidationError):
        SafetySettings(**changes)


@pytest.mark.parametrize("direction", ["x", "y", "diagonal"])
@pytest.mark.parametrize("slope", [0.0, 5.0, 10.0, 30.0, 60.0])
def test_horn_slope_is_exact_for_planes(slope, direction):
    result = slope_grid_deg(plane(9, slope, direction), PIXEL_M)
    assert result.shape == (9, 9)
    interior = result[1:-1, 1:-1]
    assert np.allclose(interior, slope, rtol=0, atol=TOL_DEG)
    # The 3x3 stencil is undefined on the border.
    assert np.isnan(result[0]).all() and np.isnan(result[-1]).all()
    assert np.isnan(result[:, 0]).all() and np.isnan(result[:, -1]).all()


def test_descending_plane_has_positive_slope():
    result = slope_grid_deg(-plane(5, 20.0), PIXEL_M)
    assert np.allclose(result[1:-1, 1:-1], 20.0, atol=TOL_DEG)


def test_unknown_height_propagates_to_neighbours_only():
    heights = plane(9, 5.0)
    heights[4, 4] = np.nan
    result = slope_grid_deg(heights, PIXEL_M)
    assert np.isnan(result[3:6, 3:6]).all()
    assert np.isclose(result[1, 1], 5.0, atol=TOL_DEG)
    assert np.isclose(result[7, 7], 5.0, atol=TOL_DEG)


def test_slope_is_physical():
    rng = np.random.default_rng(20261114)
    result = slope_grid_deg(rng.normal(0, 500, (30, 30)), PIXEL_M)
    finite = result[np.isfinite(result)]
    assert finite.size == 28 * 28
    assert (finite >= 0).all() and (finite < 90).all()


@pytest.mark.parametrize(
    "heights, pixel",
    [
        (np.zeros((9, 9)), 0.0),
        (np.zeros((9, 9)), -80.0),
        (np.zeros((2, 9)), 80.0),
        (np.zeros(9), 80.0),
        (np.zeros((3, 3, 3)), 80.0),
    ],
)
def test_slope_rejects_invalid_input(heights, pixel):
    with pytest.raises(ValueError):
        slope_grid_deg(heights, pixel)


def test_flat_site_with_distant_ramp():
    # Horn at column 39 sees the first ramp column: atan(tan(30 deg) / 2) = 16.1 deg.
    result = site_safety(ramp(), PIXEL_M, 30, 30, SafetySettings())
    assert result["site_slope_deg"] == pytest.approx(0.0, abs=TOL_DEG)
    assert result["max_landing_slope_deg"] == pytest.approx(0.0, abs=TOL_DEG)
    assert result["hazard_distance_m"] == pytest.approx(9 * PIXEL_M)
    assert result["landing_pixel_count"] == 5  # centers within 100 m on an 80 m grid
    assert result["unknown_landing_fraction"] == 0.0
    assert result["unknown_search_fraction"] == 0.0
    assert result["pixel_m"] == PIXEL_M


def test_no_steep_terrain_within_search_radius():
    result = site_safety(ramp(start_col=60), PIXEL_M, 30, 30, SafetySettings())
    # Column 59 lies 2320 m away, beyond the 2000 m search radius.
    assert result["hazard_distance_m"] is None


def test_steep_terrain_inside_landing_area():
    result = site_safety(ramp(start_col=32), PIXEL_M, 30, 30, SafetySettings())
    expected = math.degrees(math.atan(math.tan(math.radians(30)) / 2))
    assert result["site_slope_deg"] == pytest.approx(0.0, abs=TOL_DEG)
    assert result["max_landing_slope_deg"] == pytest.approx(expected, abs=TOL_DEG)
    assert result["hazard_distance_m"] == pytest.approx(PIXEL_M)


def test_site_on_uniform_steep_plane():
    result = site_safety(plane(61, 15.0), PIXEL_M, 30, 30, SafetySettings())
    assert result["site_slope_deg"] == pytest.approx(15.0, abs=TOL_DEG)
    assert result["max_landing_slope_deg"] == pytest.approx(15.0, abs=TOL_DEG)
    assert result["hazard_distance_m"] == 0.0


def test_larger_landing_radius_includes_more_pixels():
    small = site_safety(ramp(), PIXEL_M, 30, 30, SafetySettings(landing_radius_m=100))
    large = site_safety(ramp(), PIXEL_M, 30, 30, SafetySettings(landing_radius_m=200))
    assert small["landing_pixel_count"] < large["landing_pixel_count"]


def test_slope_limit_controls_hazard_distance():
    loose = site_safety(ramp(), PIXEL_M, 30, 30, SafetySettings(slope_limit_deg=20))
    # 16.1 deg at column 39 no longer counts; column 40 (30 deg) is 800 m away.
    assert loose["hazard_distance_m"] == pytest.approx(10 * PIXEL_M)


def test_unknown_heights_are_reported_not_hidden():
    heights = ramp()
    heights[30, 31] = np.nan
    heights[30, 10] = np.nan
    result = site_safety(heights, PIXEL_M, 30, 30, SafetySettings())
    assert 0 < result["unknown_landing_fraction"] <= 1
    assert 0 < result["unknown_search_fraction"] <= 1
    assert result["hazard_distance_m"] == pytest.approx(9 * PIXEL_M)


def test_fully_unknown_landing_area():
    heights = ramp()
    heights[27:34, 27:34] = np.nan
    result = site_safety(heights, PIXEL_M, 30, 30, SafetySettings())
    assert result["unknown_landing_fraction"] == 1.0
    assert result["site_slope_deg"] is None
    assert result["max_landing_slope_deg"] is None


@pytest.mark.parametrize("row, col", [(-1, 30), (30, 61), (0, 0)])
def test_site_outside_or_too_close_to_window_edge(row, col):
    with pytest.raises(ValueError):
        site_safety(ramp(), PIXEL_M, row, col, SafetySettings())


def test_window_must_cover_search_radius():
    with pytest.raises(ValueError):
        site_safety(ramp(size=41, start_col=30), PIXEL_M, 20, 20, SafetySettings())


@pytest.mark.parametrize(
    "safety, expected",
    [
        ({"max_landing_slope_deg": 0.0, "hazard_distance_m": None}, 1.0),
        ({"max_landing_slope_deg": 5.0, "hazard_distance_m": None}, 0.75),
        ({"max_landing_slope_deg": 0.0, "hazard_distance_m": 1000.0}, 0.75),
        ({"max_landing_slope_deg": 10.0, "hazard_distance_m": 0.0}, 0.0),
        ({"max_landing_slope_deg": 20.0, "hazard_distance_m": 4000.0}, 0.5),
    ],
)
def test_safety_score_rule(safety, expected):
    # 0.5 * (1 - min(slope / limit, 1)) + 0.5 * min(distance / search, 1)
    assert safety_score(safety, SafetySettings()) == pytest.approx(expected, abs=1e-12)


def test_safety_score_requires_known_landing_slope():
    with pytest.raises(ValueError):
        safety_score(
            {"max_landing_slope_deg": None, "hazard_distance_m": None}, SafetySettings()
        )


def write_dem(root, heights, product="LDEM_80S_80MPP_ADJ"):
    folder = root / "terrain/raw" / ("a" * 64)
    folder.mkdir(parents=True)
    path = folder / "source.tif"
    size = heights.shape[0]
    half = size * PIXEL_M / 2
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=size,
        height=size,
        count=1,
        dtype="float32",
        crs=f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m",
        transform=from_origin(-half, half, PIXEL_M, PIXEL_M),
        nodata=np.nan,
    ) as ds:
        ds.write(heights.astype("float32"), 1)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "product_id": product,
                "sha256": {"source.tif": hashlib.sha256(path.read_bytes()).hexdigest()},
            }
        )
    )
    return path


def test_cached_dem_window_at_the_pole(tmp_path):
    write_dem(tmp_path, plane(81, 5.0))
    result = terrain_safety(tmp_path, -90.0, 0.0, SafetySettings())
    # float32 storage limits precision; 1e-3 deg is far below any decision margin.
    assert result["site_slope_deg"] == pytest.approx(5.0, abs=1e-3)
    assert result["max_landing_slope_deg"] == pytest.approx(5.0, abs=1e-3)
    assert result["hazard_distance_m"] is None
    assert result["covered"] is True
    assert result["dem_product"] == "LDEM_80S_80MPP_ADJ"
    assert result["pixel_m"] == PIXEL_M
    assert result["settings"] == SafetySettings().model_dump()
    assert 0 <= result["score"] <= 1


def test_missing_cache_requires_acquisition(tmp_path):
    with pytest.raises(FileNotFoundError):
        terrain_safety(tmp_path, -90.0, 0.0, SafetySettings())


def test_site_near_dem_edge_is_outside_coverage(tmp_path):
    write_dem(tmp_path, plane(81, 5.0))
    # About 3 km from the pole: the 2 km search window leaves the 6.5 km raster.
    with pytest.raises(TerrainSafetyError):
        terrain_safety(tmp_path, -89.9, 0.0, SafetySettings())


def test_corrupt_or_unexpected_cache_is_rejected(tmp_path):
    path = write_dem(tmp_path, plane(81, 5.0))
    with path.open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(TerrainSafetyError, match="checksum"):
        terrain_safety(tmp_path, -90.0, 0.0, SafetySettings())


def test_unexpected_product_is_rejected(tmp_path):
    write_dem(tmp_path, plane(81, 5.0), product="OTHER")
    with pytest.raises(TerrainSafetyError):
        terrain_safety(tmp_path, -90.0, 0.0, SafetySettings())
