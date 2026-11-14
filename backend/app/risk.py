"""R6 risk and uncertainty: seeded Monte Carlo over the R4 descent (scenario S10).

Each run perturbs the R4 inputs with independent Gaussian errors and flies the
same closed-loop descent model, then classifies the touchdown against the R4
tolerances, the R5 propellant reserve and, when terrain is cached, the R3 slope
limit at the dispersed landing point.

Uncertainty sources (all standard deviations are planning assumptions unless
a source is given in the settings; none is a certified vehicle value):

- thrust: multiplicative error on maximum and design thrust (engine performance)
- isp: multiplicative error on specific impulse (engine performance)
- pdi_downrange: dispersion of the actual downrange position at PDI (m)
- pdi_speed: dispersion of the actual horizontal speed at PDI (m/s)
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

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from rasterio.warp import transform
from rasterio.windows import Window

from app.descent import (
    DescentError,
    DescentRequest,
    OrbitSettings,
    Tolerances,
    VehicleProfile,
    load_profiles,
    plan_descent,
    simulate_powered_descent,
    terrain_height_m,
)
from app.fuel_optimization import DEFAULT_RESERVE_DELTA_V_MPS, reserve_propellant_kg
from app.horizon import _validate_dataset, ray_coordinates
from app.terrain_safety import (
    DEFAULT_SLOPE_LIMIT_DEG,
    DEM_PIXEL_M,
    TerrainSafetyError,
    _cached_dem,
    slope_grid_deg,
)
from shared.constants import (
    MOON_GM_M3_S2,
    MOON_REFERENCE_RADIUS_M,
    STANDARD_GRAVITY_M_S2,
)
from shared.contracts import ObserverSite

MC_STEP_S = (
    0.5  # descent integration step; 0.5 s vs 0.1 s differs by < 1 cm at touchdown
)
MIN_RUNS = 10
MAX_RUNS = 1000
DEFAULT_RUNS = 200
MAX_REPORTED_POINTS = 300
HISTOGRAM_BINS = 20
MAX_SLOPE_MAP_HALF_WIDTH_M = 20000.0
MIN_SLOPE_MAP_HALF_WIDTH_M = 2000.0
SOURCES = (
    "thrust",
    "isp",
    "pdi_downrange",
    "pdi_speed",
    "terrain_height",
    "navigation_down",
    "navigation_cross",
)
SOURCE_UNITS = {
    "thrust": "fraction",
    "isp": "fraction",
    "pdi_downrange": "m",
    "pdi_speed": "m/s",
    "terrain_height": "m",
    "navigation_down": "m",
    "navigation_cross": "m",
}
FAILURE_CAUSES = (
    "propellant_exhausted",
    "propellant_reserve",
    "touchdown_position",
    "vertical_speed",
    "horizontal_speed",
    "hazard_slope",
    "hazard_unknown",
)
ASSUMPTIONS = (
    "Every run flies the R4 model (planar point-mass Moon, ZEM/ZEV guidance through the Apollo 11 gates) at a 0.5 s step.",
    "Uncertainty sources are independent Gaussians; their standard deviations are planning assumptions, not certified vehicle values.",
    "Thrust error scales maximum and design thrust together; Isp error scales specific impulse.",
    "PDI dispersions change the actual starting downrange position and speed; guidance knows the actual state.",
    "Navigation (knowledge) error shifts the landing point one-to-one, because guidance flies to the believed site.",
    "Crossrange dispersion comes only from navigation error: the planar descent model has no crossrange dynamics.",
    "Lower true ground: engine cut-off at the believed touchdown, then free fall. Higher true ground: contact at the first sample at or below it.",
    "Propellant remaining is taken at the believed touchdown (conservative when contact is earlier).",
    "Slope is the R3 Horn slope of the containing 80 m DEM cell; landings off the map or on unknown cells are failures, never assumed safe.",
    "Sensitivity is a Spearman rank correlation, not a variance decomposition; values below 2/sqrt(runs) are not distinguishable from noise.",
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


def _approach_basis(north_xy, east_xy, approach_azimuth_deg: float):
    """Downrange (travel) and crossrange (right of travel) unit vectors in xy."""
    north, east = np.asarray(north_xy, float), np.asarray(east_xy, float)
    azimuth = math.radians(approach_azimuth_deg)
    travel = north * math.cos(azimuth) + east * math.sin(azimuth)
    right = -north * math.sin(azimuth) + east * math.cos(azimuth)
    return travel, right


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
        travel, right = _approach_basis(
            self.north_unit_xy, self.east_unit_xy, approach_azimuth_deg
        )
        down = np.asarray(down_m, dtype=float)
        cross = np.asarray(cross_m, dtype=float)
        x = self.site_xy_m[0] + down * travel[0] + cross * right[0]
        y = self.site_xy_m[1] + down * travel[1] + cross * right[1]
        inverse = ~self.transform
        cols = np.floor(inverse.a * x + inverse.b * y + inverse.c).astype(int)
        rows = np.floor(inverse.d * x + inverse.e * y + inverse.f).astype(int)
        height, width = self.slope_deg.shape
        inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
        result = np.full(down.shape, np.nan)
        result[inside] = self.slope_deg[rows[inside], cols[inside]]
        return result

    def cell_offsets(self, approach_azimuth_deg: float):
        """(down_m, cross_m, slope_deg) flattened arrays for every grid cell."""
        travel, right = _approach_basis(
            self.north_unit_xy, self.east_unit_xy, approach_azimuth_deg
        )
        height, width = self.slope_deg.shape
        rows, cols = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
        t = self.transform
        x = t.a * (cols + 0.5) + t.b * (rows + 0.5) + t.c - self.site_xy_m[0]
        y = t.d * (cols + 0.5) + t.e * (rows + 0.5) + t.f - self.site_xy_m[1]
        down = x * travel[0] + y * travel[1]
        cross = x * right[0] + y * right[1]
        return down.ravel(), cross.ravel(), self.slope_deg.astype(float).ravel()


def sample_standard_normals(runs: int, seed: int) -> np.ndarray:
    """(runs, len(SOURCES)) standard normals from numpy default_rng(seed)."""
    return np.random.default_rng(seed).standard_normal((runs, len(SOURCES)))


def wilson_interval(successes: int, runs: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95 % by default)."""
    if runs <= 0 or not 0 <= successes <= runs:
        raise ValueError("Need 0 <= successes <= runs and runs > 0")
    p = successes / runs
    denominator = 1 + z * z / runs
    centre = (p + z * z / (2 * runs)) / denominator
    half = z / denominator * math.sqrt(p * (1 - p) / runs + z * z / (4 * runs * runs))
    low = 0.0 if successes == 0 else max(centre - half, 0.0)
    high = 1.0 if successes == runs else min(centre + half, 1.0)
    return low, high


def dispersion_ellipse(points_m: np.ndarray, k_sigma: float) -> dict:
    """k-sigma ellipse of 2-D points from the sample covariance (ddof = 1).

    Returns centre, semi-major/minor axes (m), orientation of the major axis
    in degrees from the first axis toward the second, and the covariance.
    Fewer than two points or identical points give zero axes.
    """
    points = np.asarray(points_m, dtype=float).reshape(-1, 2)
    centre = points.mean(axis=0)
    covariance = (
        np.cov(points.T, ddof=1) if len(points) >= 2 else np.zeros((2, 2))
    ).reshape(2, 2)
    values, vectors = np.linalg.eigh(covariance)
    major, minor = max(values[1], 0.0), max(values[0], 0.0)
    if major == 0.0:
        orientation = 0.0
    else:
        vector = vectors[:, 1]
        orientation = math.degrees(math.atan2(vector[1], vector[0])) % 180.0
    return {
        "center_m": [float(centre[0]), float(centre[1])],
        "semi_major_m": float(k_sigma * math.sqrt(major)),
        "semi_minor_m": float(k_sigma * math.sqrt(minor)),
        "orientation_deg": float(orientation),
        "covariance_m2": covariance.tolist(),
        "k_sigma": float(k_sigma),
    }


def _ranks(values: np.ndarray) -> np.ndarray:
    """Average ranks; ties share the mean of their positions."""
    order = np.argsort(values, kind="mergesort")
    ordered = values[order]
    ranks = np.empty(len(values))
    start = 0
    while start < len(values):
        stop = start
        while stop + 1 < len(values) and ordered[stop + 1] == ordered[start]:
            stop += 1
        ranks[order[start : stop + 1]] = (start + stop) / 2
        start = stop + 1
    return ranks


def rank_correlation(x, y) -> float | None:
    """Spearman rank correlation; None when either input is constant."""
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    if a.shape != b.shape or a.size < 2:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ra, rb = ra - ra.mean(), rb - rb.mean()
    scale = math.sqrt(float(ra @ ra) * float(rb @ rb))
    if scale == 0.0:
        return None
    return float(np.clip(float(ra @ rb) / scale, -1.0, 1.0))


def touchdown_with_terrain_error(
    samples: list[dict], final: dict, terrain_error_m: float, gravity_mps2: float
) -> dict:
    """Contact state when the true ground differs from the believed ground.

    terrain_error_m > 0 (ground higher): first sample with altitude at or below
    it. < 0: free fall from the believed touchdown, v = -sqrt(vz^2 + 2 g |dh|).
    Returns downrange_m, vertical_speed_mps and horizontal_speed_mps.
    Raises RiskError when the higher ground is above the first sample.
    """
    landed = samples[-1]
    believed = {
        "downrange_m": float(landed["downrange_m"]),
        "vertical_speed_mps": float(final["vertical_speed_mps"]),
        "horizontal_speed_mps": float(final["horizontal_speed_mps"]),
    }
    if terrain_error_m < 0:
        fall = math.sqrt(
            final["vertical_speed_mps"] ** 2 + 2 * gravity_mps2 * -terrain_error_m
        )
        return believed | {"vertical_speed_mps": -fall}
    if terrain_error_m == 0:
        return believed
    if samples[0]["altitude_m"] <= terrain_error_m:
        raise RiskError("Terrain error places the ground above the simulated start")
    for sample in samples:
        if sample["altitude_m"] <= terrain_error_m:
            return {
                "downrange_m": float(sample["downrange_m"]),
                "vertical_speed_mps": float(sample["vertical_speed_mps"]),
                "horizontal_speed_mps": float(sample["horizontal_speed_mps"]),
            }
    # Ground is higher by less than the final sample's residual altitude.
    return believed


def _vehicle(request: RiskRequest) -> VehicleProfile:
    if request.custom_profile is not None:
        return request.custom_profile
    profiles = load_profiles()
    if request.profile_id not in profiles:
        raise RiskError(f"Unknown vehicle profile {request.profile_id}")
    return profiles[request.profile_id]


def _stats(values: np.ndarray) -> dict:
    p05, p50, p95 = np.percentile(values, [5, 50, 95])
    return {
        "min": float(values.min()),
        "p05": float(p05),
        "p50": float(p50),
        "p95": float(p95),
        "max": float(values.max()),
    }


def _sigmas(settings: UncertaintySettings) -> np.ndarray:
    return np.array(
        [
            settings.thrust_sigma_fraction,
            settings.isp_sigma_fraction,
            settings.pdi_downrange_sigma_m,
            settings.pdi_speed_sigma_mps,
            settings.terrain_height_sigma_m,
            settings.navigation_sigma_m,
            settings.navigation_sigma_m,
        ]
    )


def _ellipse_cells(slope_map, request, covariance, centre) -> dict:
    """DEM cells inside the 3-sigma ellipse, inflated by half a pixel."""
    down, cross, slope = slope_map.cell_offsets(request.approach_azimuth_deg)
    inflated = np.asarray(covariance) + (slope_map.pixel_m / 2) ** 2 * np.eye(2)
    delta = np.stack([down - centre[0], cross - centre[1]], axis=1)
    distance2 = np.einsum("ij,jk,ik->i", delta, np.linalg.inv(inflated), delta)
    inside = distance2 <= 9.0
    count = int(inside.sum())
    if count == 0:
        return {"count": 0, "steep_fraction": None, "unknown_fraction": None}
    chosen = slope[inside]
    known = np.isfinite(chosen)
    return {
        "count": count,
        "steep_fraction": float(
            (known & (chosen > request.slope_limit_deg)).sum() / count
        ),
        "unknown_fraction": float((~known).sum() / count),
    }


def run_monte_carlo(
    request: RiskRequest,
    terrain_height_m: float,
    slope_map: LocalSlopeMap | None = None,
) -> dict:
    """Fly `request.runs` perturbed descents and aggregate success and causes.

    Raises RiskError for an unknown vehicle or a site/orbit the R4 model rejects.
    Without a slope map, hazard checks are reported as not evaluated.
    """
    profile = _vehicle(request)
    descent = DescentRequest(
        site_id=request.site_id,
        latitude_deg=request.latitude_deg,
        longitude_deg_east=request.longitude_deg_east,
        profile_id=request.profile_id,
        custom_profile=request.custom_profile,
        orbit=request.orbit,
        tolerances=request.tolerances,
        allow_plane_change=request.allow_plane_change,
    )
    try:
        nominal = plan_descent(descent, terrain_height_m)
    except (DescentError, ValueError) as exc:
        raise RiskError(str(exc)) from exc
    vehicle = profile
    if nominal["plane_change"]["applied"]:
        used = nominal["plane_change"]["propellant_kg"]
        vehicle = profile.model_copy(
            update={
                "mass_at_pdi_kg": profile.mass_at_pdi_kg - used,
                "usable_propellant_kg": max(profile.usable_propellant_kg - used, 0.0),
            }
        )
    site_radius = nominal["site"]["radius_m"]
    pdi_range = nominal["pdi_range_m"]
    periapsis_speed = nominal["deorbit"]["periapsis_speed_mps"]
    gravity = MOON_GM_M3_S2 / site_radius**2
    tolerances = request.tolerances
    sigmas = _sigmas(request.uncertainty)
    normals = sample_standard_normals(request.runs, request.seed)
    errors = normals * sigmas

    landing = np.zeros((request.runs, 2))
    remaining = np.zeros(request.runs)
    margins = np.zeros(request.runs)
    causes_per_run: list[list[str]] = []
    for index, error in enumerate(errors):
        thrust_scale = 1 + error[0]
        perturbed = vehicle.model_copy(
            update={
                "max_thrust_n": vehicle.max_thrust_n * thrust_scale,
                "design_thrust_n": vehicle.design_thrust_n * thrust_scale,
                "isp_s": vehicle.isp_s * (1 + error[1]),
            }
        )
        run = simulate_powered_descent(
            perturbed,
            site_radius,
            pdi_range + error[2],
            periapsis_speed + error[3],
            MC_STEP_S,
            record=True,
        )
        contact = touchdown_with_terrain_error(
            run["samples"], run["final"], float(error[4]), gravity
        )
        final_mass = run["final"]["mass_kg"]
        dry_mass = perturbed.mass_at_pdi_kg - perturbed.usable_propellant_kg
        remaining[index] = final_mass - dry_mass
        margins[index] = remaining[index] - reserve_propellant_kg(
            final_mass, request.reserve_delta_v_mps, perturbed.isp_s
        )
        causes = []
        if run["propellant_exhausted"]:
            causes.append("propellant_exhausted")
        elif margins[index] < 0:
            causes.append("propellant_reserve")
        if abs(contact["downrange_m"]) > tolerances.position_m:
            causes.append("touchdown_position")
        if abs(contact["vertical_speed_mps"]) > tolerances.vertical_speed_mps:
            causes.append("vertical_speed")
        if contact["horizontal_speed_mps"] > tolerances.horizontal_speed_mps:
            causes.append("horizontal_speed")
        landing[index] = (contact["downrange_m"] + error[5], error[6])
        causes_per_run.append(causes)

    if slope_map is not None:
        slopes = slope_map.slopes_at(
            landing[:, 0], landing[:, 1], request.approach_azimuth_deg
        )
        for causes, slope in zip(causes_per_run, slopes):
            if not np.isfinite(slope):
                causes.append("hazard_unknown")
            elif slope > request.slope_limit_deg:
                causes.append("hazard_slope")

    success = np.array([not causes for causes in causes_per_run])
    successes = int(success.sum())
    failed = (~success).astype(float)
    one_sigma = dispersion_ellipse(landing, 1.0)
    three_sigma = dispersion_ellipse(landing, 3.0)
    centre = np.array(one_sigma["center_m"])
    delta = landing - centre
    mahalanobis2 = np.einsum(
        "ij,jk,ik->i", delta, np.linalg.pinv(one_sigma["covariance_m2"]), delta
    )
    within = float((mahalanobis2 <= 9.0 + 1e-9).mean())

    threshold = 2 / math.sqrt(request.runs)
    sensitivity = []
    for column, source in enumerate(SOURCES):
        enabled = bool(sigmas[column] > 0)
        row = {
            "source": source,
            "sigma": float(sigmas[column]),
            "unit": SOURCE_UNITS[source],
            "enabled": enabled,
            "margin_correlation": None,
            "failure_correlation": None,
            "failure_tail_correlation": None,
            "failure_association": None,
            "significant": False,
        }
        if enabled:
            z = normals[:, column]
            row["margin_correlation"] = rank_correlation(z, margins)
            row["failure_correlation"] = rank_correlation(z, failed)
            row["failure_tail_correlation"] = rank_correlation(np.abs(z), failed)
            values = [
                abs(v)
                for v in (row["failure_correlation"], row["failure_tail_correlation"])
                if v is not None
            ]
            row["failure_association"] = max(values) if values else None
            row["significant"] = (
                row["failure_association"] is not None
                and row["failure_association"] > threshold
            )
        sensitivity.append(row)
    ranked = [row for row in sensitivity if row["significant"]]
    dominant = (
        max(ranked, key=lambda row: row["failure_association"])["source"]
        if ranked
        else None
    )

    counts, edges = np.histogram(remaining, bins=HISTOGRAM_BINS)
    hazard = {
        "evaluated": slope_map is not None,
        "slope_limit_deg": request.slope_limit_deg,
        "ellipse_3sigma_cells": (
            _ellipse_cells(slope_map, request, one_sigma["covariance_m2"], centre)
            if slope_map is not None
            else None
        ),
    }
    if slope_map is not None:
        hazard["pixel_m"] = float(slope_map.pixel_m)
    result = {
        "site": {
            "site_id": request.site_id,
            "latitude_deg": request.latitude_deg,
            "longitude_deg_east": request.longitude_deg_east,
            "terrain_height_m": float(terrain_height_m),
        },
        "settings": request.model_dump(mode="json"),
        "runs": request.runs,
        "seed": request.seed,
        "nominal": {
            "feasible": nominal["feasible"],
            "limiting_factors": nominal["limiting_factors"],
            "pdi_range_m": pdi_range,
            "propellant_margin_kg": nominal["totals"]["propellant_margin_kg"],
            "touchdown": nominal["touchdown"],
        },
        "success": {
            "count": successes,
            "rate": successes / request.runs,
            "wilson95": list(wilson_interval(successes, request.runs)),
        },
        "failure_causes": {
            cause: sum(cause in causes for causes in causes_per_run)
            for cause in FAILURE_CAUSES
        },
        "landing": {
            "frame": "downrange along the approach azimuth, crossrange to its right (m)",
            "approach_azimuth_deg": request.approach_azimuth_deg,
            "points": [
                {
                    "down_m": float(landing[i, 0]),
                    "cross_m": float(landing[i, 1]),
                    "success": bool(success[i]),
                }
                for i in range(min(request.runs, MAX_REPORTED_POINTS))
            ],
            "mean_down_m": float(centre[0]),
            "mean_cross_m": float(centre[1]),
            "ellipse_1sigma": one_sigma,
            "ellipse_3sigma": three_sigma,
            "within_3sigma_fraction": within,
        },
        "propellant": {
            "remaining_kg": _stats(remaining),
            "margin_above_reserve_kg": _stats(margins),
            "reserve_delta_v_mps": request.reserve_delta_v_mps,
            "histogram": {
                "edges_kg": [float(e) for e in edges],
                "counts": [int(c) for c in counts],
            },
        },
        "hazard": hazard,
        "sensitivity": sensitivity,
        "dominant_failure_source": dominant,
        "noise_threshold": threshold,
        "integration_step_s": MC_STEP_S,
        "assumptions": list(ASSUMPTIONS),
        "standard_gravity_mps2": STANDARD_GRAVITY_M_S2,
    }
    identity = json.dumps(result, sort_keys=True, allow_nan=False).encode()
    result["result_id"] = hashlib.sha256(identity).hexdigest()
    return result


def _site_xy(dataset, longitude_deg_east: float, latitude_deg: float):
    x, y = transform(
        f"+proj=longlat +R={MOON_REFERENCE_RADIUS_M} +no_defs",
        dataset.crs,
        [longitude_deg_east],
        [latitude_deg],
    )
    return float(x[0]), float(y[0])


def _local_unit(dataset, site: ObserverSite, site_xy, azimuth_deg: float):
    """Projected direction of a 1 km step along the given azimuth."""
    longitude, latitude = ray_coordinates(site, math.radians(azimuth_deg), [1000.0])
    xy = _site_xy(
        dataset, math.degrees(float(longitude[0])) % 360, math.degrees(latitude[0])
    )
    vector = np.array([xy[0] - site_xy[0], xy[1] - site_xy[1]])
    return tuple(float(v) for v in vector / np.linalg.norm(vector))


def load_slope_map(
    data_dir: Path, latitude_deg: float, longitude_deg_east: float, half_width_m: float
) -> LocalSlopeMap:
    """Verified cached DEM window around the site with Horn slopes.

    FileNotFoundError means terrain must be acquired first; RiskError marks a
    site outside coverage. The window is clipped to the raster.
    """
    try:
        path = _cached_dem(data_dir)
    except TerrainSafetyError as exc:
        raise RiskError(str(exc)) from exc
    half = min(max(float(half_width_m), DEM_PIXEL_M), MAX_SLOPE_MAP_HALF_WIDTH_M)
    with rasterio.open(path) as dataset:
        try:
            _validate_dataset(dataset)
        except ValueError as exc:
            raise RiskError(str(exc)) from exc
        site_xy = _site_xy(dataset, longitude_deg_east, latitude_deg)
        row, col = dataset.index(*site_xy)
        if not (0 <= row < dataset.height and 0 <= col < dataset.width):
            raise RiskError("Site is outside terrain coverage")
        pixels = math.ceil(half / DEM_PIXEL_M) + 1
        row0, col0 = max(row - pixels, 0), max(col - pixels, 0)
        row1 = min(row + pixels + 1, dataset.height)
        col1 = min(col + pixels + 1, dataset.width)
        window = Window(col0, row0, col1 - col0, row1 - row0)
        heights = dataset.read(1, window=window, masked=True).astype(float)
        site = ObserverSite(
            site_id="risk-map",
            longitude_deg_east=float(longitude_deg_east),
            latitude_deg=float(latitude_deg),
            altitude_km=0.0,
        )
        north = _local_unit(dataset, site, site_xy, 0.0)
        east = _local_unit(dataset, site, site_xy, 90.0)
        window_transform = dataset.window_transform(window)
    slopes = slope_grid_deg(np.ma.filled(heights, np.nan), DEM_PIXEL_M)
    return LocalSlopeMap(
        slope_deg=slopes,
        transform=window_transform,
        site_xy_m=site_xy,
        north_unit_xy=north,
        east_unit_xy=east,
        pixel_m=DEM_PIXEL_M,
    )


def register_risk_routes(app, data_dir: Path) -> None:
    """POST /descents/risk: 409 without cached terrain, 422 for invalid plans."""

    @app.post("/descents/risk")
    def descent_risk(body: RiskRequest):
        try:
            height = terrain_height_m(
                data_dir, body.latitude_deg, body.longitude_deg_east
            )
            half_width = max(
                5 * body.uncertainty.navigation_sigma_m, MIN_SLOPE_MAP_HALF_WIDTH_M
            )
            slope_map = load_slope_map(
                data_dir, body.latitude_deg, body.longitude_deg_east, half_width
            )
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (DescentError, RiskError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return run_monte_carlo(body, height, slope_map)
        except RiskError as exc:
            raise HTTPException(422, str(exc)) from exc
