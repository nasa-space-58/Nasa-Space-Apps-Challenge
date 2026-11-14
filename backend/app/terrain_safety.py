"""R3 terrain safety from the cached LOLA 80 m DEM (read-only).

Slope uses Horn's 3x3 finite-difference method on the polar stereographic grid.
An 80 m grid cannot resolve lander-scale craters or boulders, so these metrics
are regional screening values, not landing-site certification. The slope limit
is a documented, configurable assumption (default 10 degrees).
"""

from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SLOPE_LIMIT_DEG = 10.0
DEFAULT_LANDING_RADIUS_M = 100.0
DEFAULT_HAZARD_SEARCH_M = 2000.0


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
    raise NotImplementedError


def site_safety(
    heights_m: np.ndarray,
    pixel_m: float,
    row: int,
    col: int,
    settings: SafetySettings,
) -> dict:
    """Slope and steep-terrain distance around one pixel of a height window."""
    raise NotImplementedError


def safety_score(safety: dict, settings: SafetySettings) -> float:
    """Combine slope margin and hazard clearance into a 0..1 score."""
    raise NotImplementedError


def terrain_safety(
    data_dir: Path,
    latitude_deg: float,
    longitude_deg_east: float,
    settings: SafetySettings,
) -> dict:
    """Read a verified cached DEM window around the site and evaluate safety."""
    raise NotImplementedError
