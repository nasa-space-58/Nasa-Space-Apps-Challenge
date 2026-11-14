"""R3 terrain safety from the cached LOLA 80 m DEM (read-only).

Slope uses Horn's 3x3 finite-difference method (Horn 1981, Proc. IEEE 69(1))
on the polar stereographic grid in projected metres; the projection scale
factor (< 0.8 % at 80-90 deg S) is not corrected. An 80 m grid cannot resolve
lander-scale craters or boulders, so these metrics are regional screening
values, not landing-site certification. The slope limit is a documented,
configurable assumption (default 10 degrees).
"""

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import rasterio
from pydantic import BaseModel, ConfigDict, Field
from rasterio.warp import transform
from rasterio.windows import Window

from app.horizon import _validate_dataset
from shared.constants import MOON_REFERENCE_RADIUS_M

DEFAULT_SLOPE_LIMIT_DEG = 10.0
DEFAULT_LANDING_RADIUS_M = 100.0
DEFAULT_HAZARD_SEARCH_M = 2000.0
DEM_PRODUCT = "LDEM_80S_80MPP_ADJ"
DEM_PIXEL_M = 80.0


class TerrainSafetyError(ValueError):
    """Site outside verified terrain coverage or unusable terrain data."""


class SafetySettings(BaseModel):
    """Slope limit (deg), landing-area radius and hazard search radius (m)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    slope_limit_deg: float = Field(default=DEFAULT_SLOPE_LIMIT_DEG, ge=1, le=45)
    landing_radius_m: float = Field(default=DEFAULT_LANDING_RADIUS_M, ge=40, le=500)
    hazard_search_m: float = Field(default=DEFAULT_HAZARD_SEARCH_M, ge=200, le=5000)


def slope_grid_deg(heights_m: np.ndarray, pixel_m: float) -> np.ndarray:
    """Horn slope in degrees; NaN on the border and next to unknown heights."""
    z = np.asarray(heights_m, dtype=float)
    if z.ndim != 2 or z.shape[0] < 3 or z.shape[1] < 3:
        raise ValueError("Heights must be a 2-D grid of at least 3 x 3 cells")
    if not math.isfinite(pixel_m) or pixel_m <= 0:
        raise ValueError("pixel_m must be a positive finite spacing")
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dz_dx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * pixel_m)
    dz_dy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * pixel_m)
    result = np.full(z.shape, np.nan)
    result[1:-1, 1:-1] = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    # Horn ignores the centre cell; a cell with unknown height stays unknown.
    result[~np.isfinite(z)] = np.nan
    return result


def _value(x) -> float | None:
    return None if x is None or not np.isfinite(x) else float(x)


def site_safety(
    heights_m: np.ndarray,
    pixel_m: float,
    row: int,
    col: int,
    settings: SafetySettings,
) -> dict:
    """Slope and steep-terrain distance around one pixel of a height window.

    The landing area holds pixel centres within landing_radius_m; steep terrain
    is any pixel steeper than the limit within hazard_search_m (centre to centre).
    Unknown slopes are counted, never treated as flat. The window must contain
    the whole search radius plus the one-pixel Horn stencil, else ValueError.
    """
    z = np.asarray(heights_m, dtype=float)
    if z.ndim != 2:
        raise ValueError("Heights must be a 2-D grid")
    radius = math.ceil(settings.hazard_search_m / pixel_m)
    margin = radius + 1
    rows, cols = z.shape
    if not (margin <= row < rows - margin and margin <= col < cols - margin):
        raise ValueError("Height window does not cover the hazard search radius")
    sub = z[row - margin : row + margin + 1, col - margin : col + margin + 1]
    slopes = slope_grid_deg(sub, pixel_m)[1:-1, 1:-1]
    offsets = np.arange(-radius, radius + 1)
    distance = pixel_m * np.hypot(*np.meshgrid(offsets, offsets, indexing="ij"))
    landing = distance <= settings.landing_radius_m
    search = distance <= settings.hazard_search_m
    known = np.isfinite(slopes)
    landing_known = slopes[landing & known]
    steep = search & known & (slopes > settings.slope_limit_deg)
    return {
        "site_slope_deg": _value(slopes[radius, radius]),
        "max_landing_slope_deg": (
            float(landing_known.max()) if landing_known.size else None
        ),
        "hazard_distance_m": float(distance[steep].min()) if steep.any() else None,
        "landing_pixel_count": int(landing.sum()),
        "unknown_landing_fraction": float((landing & ~known).sum() / landing.sum()),
        "unknown_search_fraction": float((search & ~known).sum() / search.sum()),
        "pixel_m": float(pixel_m),
    }


def safety_score(safety: dict, settings: SafetySettings) -> float:
    """Combine slope margin and hazard clearance into a 0..1 score.

    0.5 * (1 - min(slope / limit, 1)) + 0.5 * min(distance / search, 1); no steep
    terrain within the search radius counts as full clearance.
    """
    slope = safety.get("max_landing_slope_deg")
    if slope is None:
        raise ValueError("Landing-area slope is unknown")
    slope_part = 1 - min(slope / settings.slope_limit_deg, 1.0)
    distance = safety.get("hazard_distance_m")
    clearance = (
        1.0 if distance is None else min(distance / settings.hazard_search_m, 1.0)
    )
    return 0.5 * max(slope_part, 0.0) + 0.5 * clearance


def _cached_dem(data_dir: Path) -> Path:
    candidates = sorted(Path(data_dir).glob("terrain/raw/*/source.tif"))
    if not candidates:
        raise FileNotFoundError("Terrain not cached yet; run an analysis first")
    path = candidates[0]
    try:
        manifest = json.loads((path.parent / "manifest.json").read_text())
        expected = manifest["sha256"]["source.tif"]
        product = manifest["product_id"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise TerrainSafetyError(f"Unreadable terrain manifest: {exc}") from exc
    if product != DEM_PRODUCT:
        raise TerrainSafetyError(f"Unsupported terrain product {product}")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise TerrainSafetyError("Terrain checksum mismatch")
    return path


def terrain_safety(
    data_dir: Path,
    latitude_deg: float,
    longitude_deg_east: float,
    settings: SafetySettings,
) -> dict:
    """Read a verified cached DEM window around the site and evaluate safety.

    FileNotFoundError means terrain must be acquired first. TerrainSafetyError
    reports corrupt data or a search window outside the raster.
    """
    path = _cached_dem(data_dir)
    margin = math.ceil(settings.hazard_search_m / DEM_PIXEL_M) + 1
    with rasterio.open(path) as dataset:
        try:
            _validate_dataset(dataset)
        except ValueError as exc:
            raise TerrainSafetyError(str(exc)) from exc
        x, y = transform(
            f"+proj=longlat +R={MOON_REFERENCE_RADIUS_M}",
            dataset.crs,
            [longitude_deg_east],
            [latitude_deg],
        )
        row, col = dataset.index(x[0], y[0])
        if not (
            margin <= row < dataset.height - margin
            and margin <= col < dataset.width - margin
        ):
            raise TerrainSafetyError("Site search area is outside terrain coverage")
        size = 2 * margin + 1
        window = Window(col - margin, row - margin, size, size)
        heights = dataset.read(1, window=window, masked=True).astype(float)
    result = site_safety(
        np.ma.filled(heights, np.nan), DEM_PIXEL_M, margin, margin, settings
    )
    score = (
        safety_score(result, settings)
        if result["max_landing_slope_deg"] is not None
        else None
    )
    return {
        **result,
        "covered": True,
        "score": score,
        "dem_product": DEM_PRODUCT,
        "settings": settings.model_dump(),
        "method": "Horn 3x3 slope on the 80 m polar stereographic grid",
    }
