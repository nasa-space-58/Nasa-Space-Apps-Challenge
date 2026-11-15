"""Bounded terrain horizon in DE421 MOON_ME; all geometry uses a lunar sphere.

Derivation and approximation limits: docs/terrain-horizon.md. Source convention:
NASA PGDA product 90. Elevation is atan2(up, horizontal), consistent with NAIF
recazl; azimuth is clockwise from local north. No atmosphere/refraction model.
"""

import hashlib
import json
from pathlib import Path
import re

import numpy as np
import rasterio
from rasterio.warp import transform

from shared.constants import MOON_REFERENCE_RADIUS_M as R
from shared.contracts import ObserverSite
from shared.horizon import HorizonSettings

MODEL_VERSION = "spherical-ray-nearest-v1"
PRODUCT_ID = "LDEM_80S_80MPP_ADJ"
NEAR_FIELD_DISTANCE_M = 1000
NEAR_FIELD_STEP_M = 1
GEOGRAPHIC_CRS = f"+proj=longlat +R={R} +no_defs"


class HorizonError(ValueError):
    """Invalid source, observer or incomplete metadata."""


def ray_coordinates(site: ObserverSite, azimuth_rad: float, distances_m):
    """Return MOON_ME longitude/latitude radians on a reference-sphere geodesic.

    Distances are surface arc meters on R, azimuth radians east of local north.
    At the exact pole, the supplied longitude fixes the local north meridian.
    A vector basis avoids the singular scalar bearing formula at the poles.
    """
    latitude_rad = np.deg2rad(site.latitude_deg)
    longitude_rad = np.deg2rad(site.longitude_deg_east)
    sin_lat, cos_lat = np.sin(latitude_rad), np.cos(latitude_rad)
    sin_lon, cos_lon = np.sin(longitude_rad), np.cos(longitude_rad)
    up = np.array([cos_lat * cos_lon, cos_lat * sin_lon, sin_lat])
    north = np.array([-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat])
    east = np.array([-sin_lon, cos_lon, 0.0])
    tangent = north * np.cos(azimuth_rad) + east * np.sin(azimuth_rad)
    angle_rad = np.asarray(distances_m, dtype=float) / R
    point = np.cos(angle_rad)[..., None] * up + np.sin(angle_rad)[..., None] * tangent
    longitude = np.arctan2(point[..., 1], point[..., 0])
    latitude = np.arctan2(point[..., 2], np.hypot(point[..., 0], point[..., 1]))
    return longitude, latitude


def elevation_angles(distances_m, terrain_height_m, observer_altitude_m):
    """Return elevation radians using radial heights above R, all inputs in meters.

    Equivalent to Cartesian target-minus-observer projected onto local up and
    horizontal. The half-angle identity avoids cancellation for nearby samples.
    """
    angle_rad = np.asarray(distances_m, dtype=float) / R
    terrain_height_m = np.asarray(terrain_height_m, dtype=float)
    target_radius_m = R + terrain_height_m
    up_m = (
        terrain_height_m
        - observer_altitude_m
        - 2 * target_radius_m * np.sin(angle_rad / 2) ** 2
    )
    horizontal_m = target_radius_m * np.sin(angle_rad)
    return np.arctan2(up_m, horizontal_m)


def _validate_dataset(dataset):
    params = dataset.crs.to_dict() if dataset.crs else {}
    expected = {
        "proj": "stere",
        "lat_0": -90,
        "lon_0": 0,
        "x_0": 0,
        "y_0": 0,
        "R": R,
        "units": "m",
    }
    if (
        not all(params.get(k) == v for k, v in expected.items())
        or params.get("k", 1) != 1
        or params.get("lat_ts", -90) != -90
        or dataset.count != 1
        or dataset.res != (80, 80)
        or dataset.dtypes != ("float32",)
        or dataset.scales != (1.0,)
        or dataset.offsets != (0.0,)
        or dataset.transform.b != 0
        or dataset.transform.d != 0
        or dataset.transform.a <= 0
        or dataset.transform.e >= 0
        or dataset.width * dataset.height > 7600**2
    ):
        raise HorizonError(
            "Unsupported DEM CRS, radius, units, 80 m grid or dimensions"
        )


def _sample(dataset, values, longitude_rad, latitude_rad):
    x_m, y_m = transform(
        GEOGRAPHIC_CRS, dataset.crs, np.rad2deg(longitude_rad), np.rad2deg(latitude_rad)
    )
    inverse = ~dataset.transform
    cols = np.floor(inverse.a * np.asarray(x_m) + inverse.c).astype(int)
    rows = np.floor(inverse.e * np.asarray(y_m) + inverse.f).astype(int)
    inside = (
        (rows >= 0)
        & (rows < dataset.height)
        & (cols >= 0)
        & (cols < dataset.width)
        & (np.asarray(latitude_rad) <= np.deg2rad(-80))
    )
    heights = np.full(len(rows), np.nan)
    heights[inside] = values[rows[inside], cols[inside]]
    return heights


def compute_profile(dataset, site: ObserverSite, settings: HorizonSettings) -> dict:
    """Return one finite-distance profile from an already-open local native DEM.

    Input site altitude is absolute radial meters/1000 above R, NOT AGL. It must
    lie above the containing terrain cell. Each ray samples every meter through 1 km, every radial
    step beyond that, and the exact outer radius. Any missing sample makes its elevation
    unknown (None); the maximum of available samples remains diagnostic only.
    Raises HorizonError on invalid observer, metadata or local raster read errors.
    """
    _validate_dataset(dataset)
    if not -90 <= site.latitude_deg <= -80:
        raise HorizonError("Observer outside nominal 80–90 degrees south coverage")
    try:
        # The fixed source is at most 231 MB of float32 cells; retain one grid.
        values = dataset.read(1, masked=True).filled(np.nan)
    except rasterio.errors.RasterioError as exc:
        raise HorizonError(f"Cannot read local DEM: {exc}") from exc
    surface_height_m = float(
        _sample(
            dataset,
            values,
            np.array([np.deg2rad(site.longitude_deg_east)]),
            np.array([np.deg2rad(site.latitude_deg)]),
        )[0]
    )
    observer_altitude_m = site.altitude_km * 1000
    if not np.isfinite(surface_height_m) or observer_altitude_m <= surface_height_m:
        raise HorizonError("Observer must be above a valid containing terrain pixel")
    distances_m = np.unique(
        np.r_[
            np.arange(
                1,
                min(NEAR_FIELD_DISTANCE_M, settings.search_distance_m) + 1,
                NEAR_FIELD_STEP_M,
            ),
            np.arange(
                settings.radial_step_m,
                settings.search_distance_m,
                settings.radial_step_m,
            ),
            float(settings.search_distance_m),
        ]
    )
    directions = []
    for index in range(round(360 / settings.azimuth_step_deg)):
        azimuth_deg = index * settings.azimuth_step_deg
        longitude_rad, latitude_rad = ray_coordinates(
            site, np.deg2rad(azimuth_deg), distances_m
        )
        heights_m = _sample(dataset, values, longitude_rad, latitude_rad)
        valid = np.isfinite(heights_m)
        valid_count = int(valid.sum())
        elevation_deg = limiting_distance_m = None
        if valid_count:
            angles_rad = elevation_angles(
                distances_m[valid], heights_m[valid], observer_altitude_m
            )
            maximum = int(np.argmax(angles_rad))
            elevation_deg = float(np.rad2deg(angles_rad[maximum]))
            limiting_distance_m = float(distances_m[valid][maximum])
        complete = bool(valid.all())
        directions.append(
            {
                "azimuth_deg": azimuth_deg,
                "elevation_deg": elevation_deg if complete else None,
                "sampled_max_elevation_deg": elevation_deg,
                "limiting_distance_m": limiting_distance_m,
                "coverage": "complete_within_radius" if complete else "incomplete",
                "valid_samples": valid_count,
                "requested_samples": len(distances_m),
                "first_missing_distance_m": None
                if complete
                else float(distances_m[~valid][0]),
            }
        )
    return {
        "model_version": MODEL_VERSION,
        "site": site.model_dump(mode="json"),
        "settings": settings.model_dump(mode="json"),
        "reference_frame": "MOON_ME",
        "frame_realization": "DE421 mean Earth",
        "reference_sphere_radius_m": R,
        "surface_height_m": surface_height_m,
        "observer_height_above_terrain_m": observer_altitude_m - surface_height_m,
        "terrain_resolution_m": list(dataset.res),
        "azimuth_convention": "clockwise from local north; at pole, supplied longitude defines north",
        "sampling": "containing native pixel; 1 m steps through 1 km, radial steps and endpoint; no interpolation",
        "near_field_distance_m": NEAR_FIELD_DISTANCE_M,
        "near_field_step_m": NEAR_FIELD_STEP_M,
        "beyond_search_radius": "not_evaluated",
        "quality": {
            "terrain_model": "piecewise_constant_80m_cells",
            "subpixel_terrain_resolved": False,
            "azimuth_interpolation_is_approximate": True,
            "global_horizon_verified": False,
        },
        "directions": directions,
    }


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def profile_from_bundle(
    data_dir: Path, terrain_reference: str, settings: HorizonSettings
) -> dict:
    """Verify Task 1 provenance and hashes, then compute without network or writes.

    Only a relative terrain/processed/<SHA256> reference is accepted. Both its
    crop and raw source are verified before use. Corrupt or mismatched metadata
    raises HorizonError; incomplete directional coverage is returned explicitly.
    """
    if not re.fullmatch(r"terrain/processed/[a-f0-9]{64}", terrain_reference):
        raise HorizonError("Expected terrain/processed/<SHA256> reference")
    root = Path(data_dir).resolve()
    bundle = (root / terrain_reference).resolve()
    if not bundle.is_relative_to(root):
        raise HorizonError("Terrain reference escapes data directory")
    try:
        manifest = json.loads((bundle / "manifest.json").read_text())
        expected = {
            "product_id": PRODUCT_ID,
            "reference_frame": "MOON_ME",
            "frame_realization": "DE421 mean Earth",
            "reference_sphere_radius_m": R,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise HorizonError("Incompatible product, frame or height reference")
        source_ref = manifest["source_path"]
        if not re.fullmatch(r"terrain/raw/[a-f0-9]{64}/source\.tif", source_ref):
            raise HorizonError("Invalid raw source reference")
        raw = (root / source_ref).resolve()
        crop = (bundle / "terrain.tif").resolve()
        if not raw.is_relative_to(root) or not crop.is_relative_to(bundle):
            raise HorizonError("Terrain file escapes data directory")
        raw_hash, crop_hash = _digest(raw), _digest(crop)
        if (
            raw_hash != manifest["source_sha256"]
            or crop_hash != manifest["sha256"]["terrain.tif"]
        ):
            raise HorizonError("Terrain checksum mismatch")
        site = ObserverSite.model_validate(manifest["site"])
        path = raw if settings.source_extent == "raw" else crop
        with rasterio.open(path) as dataset:
            result = compute_profile(dataset, site, settings)
        result.update(
            {
                "terrain_reference": terrain_reference,
                "source_sha256": raw_hash,
                "sampled_file_sha256": raw_hash
                if settings.source_extent == "raw"
                else crop_hash,
                "source_url": manifest["source_url"],
                "product_id": PRODUCT_ID,
                "software": {
                    "numpy": np.__version__,
                    "rasterio": rasterio.__version__,
                    "gdal": rasterio.__gdal_version__,
                },
            }
        )
        identity = json.dumps(result, sort_keys=True, allow_nan=False).encode()
        result["profile_id"] = hashlib.sha256(identity).hexdigest()
        return result
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        rasterio.errors.RasterioError,
    ) as exc:
        raise HorizonError(f"Invalid terrain bundle: {exc}") from exc


def interpolate_horizon(profile: dict, azimuth_deg: float) -> float | None:
    """Circular linear interpolation in degrees; unknown neighbors yield None.

    This interpolates computed rays, not terrain. It provides no sub-grid peak
    guarantee. Exact ray queries return that ray, even if a neighbor is unknown.
    """
    from shared.horizon_geometry import interpolate_horizon as interpolate

    try:
        return interpolate(profile, azimuth_deg)
    except ValueError as exc:
        raise HorizonError(str(exc)) from exc
