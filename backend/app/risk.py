"""R6 risk and uncertainty: seeded Monte Carlo over the R4 descent (scenario S10).

Each run perturbs the R4 inputs with independent Gaussian errors and flies the
same closed-loop descent model, then classifies the touchdown against the R4
tolerances, the R5 propellant reserve and, when terrain is cached, the R3 slope
limit at the dispersed landing point.

Uncertainty sources (all standard deviations are planning assumptions unless
a source is given in the settings; none is a certified vehicle value):

- thrust: multiplicative error on maximum and design thrust (engine performance)
- isp: multiplicative error on specific impulse (engine performance)
- pdi_downrange: navigation state error in downrange position at PDI (m)
- pdi_speed: navigation state error in horizontal speed at PDI (m/s)
- terrain_height: DEM height error at the site; true ground minus believed (m)
- navigation_down / navigation_cross: landing-position knowledge error along
  and across the approach direction (m). Closed-loop guidance flies to where it
  believes the site is, so this error moves the landing point one-to-one.

Terrain height error: if the true ground is higher, contact occurs at the first
simulated sample at or below that height; if lower, the engine cuts off at the
believed touchdown (as at the Apollo contact-light cutoff) and the vehicle falls
the remaining height under lunar gravity.

The planar R4 model has no crossrange dynamics: crossrange dispersion comes only
from the navigation error. The landing frame is local: downrange along the
approach azimuth (clockwise from local north), crossrange to its right.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from affine import Affine
from pydantic import BaseModel, ConfigDict, Field

from app.descent import OrbitSettings, Tolerances, VehicleProfile
from app.fuel_optimization import DEFAULT_RESERVE_DELTA_V_MPS
from app.terrain_safety import DEFAULT_SLOPE_LIMIT_DEG

MC_STEP_S = (
    0.5  # descent integration step; 0.5 s vs 0.1 s differs by < 1 cm at touchdown
)
MIN_RUNS = 10
MAX_RUNS = 1000
DEFAULT_RUNS = 200
MAX_REPORTED_POINTS = 300
HISTOGRAM_BINS = 20
MAX_SLOPE_MAP_HALF_WIDTH_M = 20000.0
SOURCES = (
    "thrust",
    "isp",
    "pdi_downrange",
    "pdi_speed",
    "terrain_height",
    "navigation_down",
    "navigation_cross",
)
FAILURE_CAUSES = (
    "propellant_exhausted",
    "propellant_reserve",
    "touchdown_position",
    "vertical_speed",
    "horizontal_speed",
    "hazard_slope",
    "hazard_unknown",
)


class RiskError(ValueError):
    """Invalid risk inputs or unusable terrain for the dispersion map."""


class UncertaintySettings(BaseModel):
    """One-sigma Gaussian errors; zero disables a source."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    thrust_sigma_fraction: float = Field(default=0.01, ge=0, le=0.2)
    isp_sigma_fraction: float = Field(default=0.005, ge=0, le=0.1)
    pdi_downrange_sigma_m: float = Field(default=500.0, ge=0, le=20000)
    pdi_speed_sigma_mps: float = Field(default=1.0, ge=0, le=50)
    terrain_height_sigma_m: float = Field(default=2.0, ge=0, le=50)
    navigation_sigma_m: float = Field(default=100.0, ge=0, le=5000)


class RiskRequest(BaseModel):
    """Landing plan (R4 inputs), uncertainty, run count and required seed."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    profile_id: str = "apollo-lm-11"
    custom_profile: VehicleProfile | None = None
    orbit: OrbitSettings = Field(default_factory=OrbitSettings)
    tolerances: Tolerances = Field(default_factory=Tolerances)
    allow_plane_change: bool = False
    reserve_delta_v_mps: float = Field(
        default=DEFAULT_RESERVE_DELTA_V_MPS, ge=0, le=500
    )
    uncertainty: UncertaintySettings = Field(default_factory=UncertaintySettings)
    runs: int = Field(default=DEFAULT_RUNS, ge=MIN_RUNS, le=MAX_RUNS, strict=True)
    seed: int = Field(ge=0, le=2**32 - 1, strict=True)
    approach_azimuth_deg: float = Field(default=180.0, ge=0, lt=360)
    slope_limit_deg: float = Field(default=DEFAULT_SLOPE_LIMIT_DEG, ge=1, le=45)


@dataclass(frozen=True)
class LocalSlopeMap:
    """Slope grid (deg, NaN unknown) in projected metres around a site.

    `transform` maps (col, row) to projected (x, y); `north_unit_xy` and
    `east_unit_xy` are the local north/east directions at the site in that
    projection. Offsets beyond the grid are unknown, never flat.
    """

    slope_deg: np.ndarray
    transform: Affine
    site_xy_m: tuple[float, float]
    north_unit_xy: tuple[float, float]
    east_unit_xy: tuple[float, float]
    pixel_m: float

    def slopes_at(self, down_m, cross_m, approach_azimuth_deg: float) -> np.ndarray:
        """Slope of the containing cell for each local offset; NaN outside."""
        raise NotImplementedError

    def cell_offsets(self, approach_azimuth_deg: float):
        """(down_m, cross_m, slope_deg) flattened arrays for every grid cell."""
        raise NotImplementedError


def sample_standard_normals(runs: int, seed: int) -> np.ndarray:
    """(runs, len(SOURCES)) standard normals from numpy default_rng(seed)."""
    raise NotImplementedError


def wilson_interval(successes: int, runs: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95 % by default)."""
    raise NotImplementedError


def dispersion_ellipse(points_m: np.ndarray, k_sigma: float) -> dict:
    """k-sigma ellipse of 2-D points from the sample covariance (ddof = 1).

    Returns centre, semi-major/minor axes (m), orientation of the major axis
    in degrees from the first axis toward the second, and the covariance.
    Fewer than two points or identical points give zero axes.
    """
    raise NotImplementedError


def rank_correlation(x, y) -> float | None:
    """Spearman rank correlation; None when either input is constant."""
    raise NotImplementedError


def touchdown_with_terrain_error(
    samples: list[dict], final: dict, terrain_error_m: float, gravity_mps2: float
) -> dict:
    """Contact state when the true ground differs from the believed ground.

    terrain_error_m > 0 (ground higher): first sample with altitude at or below
    it. < 0: free fall from the believed touchdown, v = -sqrt(vz^2 + 2 g |dh|).
    Returns downrange_m, vertical_speed_mps and horizontal_speed_mps.
    """
    raise NotImplementedError


def run_monte_carlo(
    request: RiskRequest,
    terrain_height_m: float,
    slope_map: LocalSlopeMap | None = None,
) -> dict:
    """Fly `request.runs` perturbed descents and aggregate success and causes.

    Raises RiskError for an unknown vehicle or a site/orbit the R4 model rejects.
    Without a slope map, hazard checks are reported as not evaluated.
    """
    raise NotImplementedError


def load_slope_map(
    data_dir: Path, latitude_deg: float, longitude_deg_east: float, half_width_m: float
) -> LocalSlopeMap:
    """Verified cached DEM window around the site with Horn slopes.

    FileNotFoundError means terrain must be acquired first; RiskError marks a
    site outside coverage. The window is clipped to the raster.
    """
    raise NotImplementedError


def register_risk_routes(app, data_dir: Path) -> None:
    """POST /descents/risk: 409 without cached terrain, 422 for invalid plans."""

    @app.post("/descents/risk")
    def descent_risk(body: RiskRequest):
        raise NotImplementedError
