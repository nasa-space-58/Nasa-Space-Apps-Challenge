"""R8 post-landing exploration: terrain-constrained surface routes (scenario S12).

From a landing site to up to five science targets on the cached 80 m DEM:

- Traversability: a cell is passable when its Horn slope (R3 method) is known
  and at most the mobility slope limit (planning assumption, default 15 deg).
- Routing: A* over 8-connected passable cells; step cost is the 3-D distance
  between cell centres (horizontal 80 m or 113 m, plus the height change),
  with the admissible straight-line horizontal heuristic.
- Duration uses a constant traverse speed (planning assumption, default
  0.1 m/s including stops); no rover power or thermal model.
- Sunlight and Earth visibility along a route are evaluated only when a ready
  site analysis (Task 4 job) is supplied: at up to `waypoints_per_route`
  evenly spaced route cells, the local terrain horizon is computed for that
  cell and compared with the analysis' Sun/Earth direction at the arrival
  time (linear interpolation between analysis frames). Reusing the landing
  site's ephemeris shifts directions by at most distance / R (0.033 deg/km).
  The Sun uses the mean solar angular radius (959.63 arcsec at 1 au). Route
  stretches between sampled cells are not evaluated.
"""

from pathlib import Path
from typing import Callable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.alternatives import LocalTerrain

DEFAULT_SLOPE_LIMIT_DEG = 15.0
DEFAULT_SPEED_MPS = 0.1
DEFAULT_OBSERVER_HEIGHT_M = 2.0
MAX_TARGET_DISTANCE_M = 8000.0
MAX_TARGETS = 5
MAX_PATH_POINTS = 200
SOLAR_RADIUS_DEG = 959.63 / 3600


class RouteError(ValueError):
    """Invalid route inputs, unusable terrain or an unusable site analysis."""


class Target(BaseModel):
    """A science target by bearing (deg from north, clockwise) and distance (m)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    target_id: str = Field(min_length=1, max_length=40)
    bearing_deg: float = Field(ge=0, lt=360)
    distance_m: float = Field(gt=0, le=MAX_TARGET_DISTANCE_M)


class RouteRequest(BaseModel):
    """Landing site, targets, mobility assumptions and an optional analysis."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    targets: list[Target] = Field(min_length=1, max_length=MAX_TARGETS)
    slope_limit_deg: float = Field(default=DEFAULT_SLOPE_LIMIT_DEG, ge=1, le=35)
    speed_mps: float = Field(default=DEFAULT_SPEED_MPS, gt=0, le=2)
    observer_height_m: float = Field(default=DEFAULT_OBSERVER_HEIGHT_M, gt=0, le=10)
    analysis_job_id: str | None = Field(default=None, max_length=64)
    departure_utc: str | None = None
    waypoints_per_route: int = Field(default=5, ge=2, le=8, strict=True)

    @model_validator(mode="after")
    def unique_targets(self):
        ids = [t.target_id for t in self.targets]
        if len(set(ids)) != len(ids):
            raise ValueError("Target IDs must be unique")
        return self


def passable_grid(slope_deg: np.ndarray, slope_limit_deg: float) -> np.ndarray:
    """True where the slope is known and at most the limit."""
    raise NotImplementedError


def astar(
    heights_m: np.ndarray,
    passable: np.ndarray,
    start: tuple[int, int],
    goal: tuple[int, int],
    pixel_m: float,
) -> list[tuple[int, int]] | None:
    """Shortest 3-D path over 8-connected passable cells; None when unreachable.

    Includes start and goal. Deterministic for equal costs. Start or goal
    outside the grid or impassable returns None.
    """
    raise NotImplementedError


def path_metrics(
    path: list[tuple[int, int]],
    heights_m: np.ndarray,
    slope_deg: np.ndarray,
    pixel_m: float,
    speed_mps: float,
) -> dict:
    """length_m (3-D), horizontal_length_m, duration_s, max_slope_deg,
    elevation_gain_m (sum of climbs) and cumulative distance per cell."""
    raise NotImplementedError


def sample_waypoints(cell_count: int, count: int) -> list[int]:
    """Evenly spaced path indices including first and last, without repeats."""
    raise NotImplementedError


def interpolate_sky(frames: list[dict], utc: str, target: str) -> dict | None:
    """Linear azimuth (shortest way round) and elevation at a UTC instant.

    `frames` are Task 4 playback frames ({'utc', 'sun': {...}, 'earth': {...}}).
    None outside the frame span or when a neighbouring value is unknown.
    """
    raise NotImplementedError


def classify_sun(clearance_deg: float | None) -> str:
    """full / partial / dark from clearance against the mean solar radius."""
    raise NotImplementedError


def classify_earth(clearance_deg: float | None) -> str:
    """visible / blocked / boundary for the Earth centre; unknown for None."""
    raise NotImplementedError


HorizonFunction = Callable[[float, float, float], dict]


def plan_routes(
    request: RouteRequest,
    terrain: LocalTerrain,
    frames: list[dict] | None = None,
    horizon_at: HorizonFunction | None = None,
) -> dict:
    """Routes to every target with metrics, waypoints and a site efficiency score.

    `horizon_at(latitude_deg, longitude_deg_east, altitude_m_above_sphere)`
    returns a horizon profile (Task 2 format). Without frames, sunlight and
    communication are reported as not evaluated. Raises RouteError when the
    landing cell is impassable or outside the terrain.
    """
    raise NotImplementedError


def register_surface_routes(app, data_dir: Path, store) -> None:
    """POST /surface/routes: 409 without cached terrain, 404 unknown analysis."""

    @app.post("/surface/routes")
    def surface_routes(body: RouteRequest):
        raise NotImplementedError
