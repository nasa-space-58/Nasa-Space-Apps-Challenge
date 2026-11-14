"""Persistent LOLA raw source and native-resolution cropped terrain bundles."""

import math
import os
from pathlib import Path
import tempfile

import httpx
import numpy as np
import rasterio
from rasterio.warp import transform
from rasterio.windows import Window, bounds
from shared.contracts import ObserverSite
from shared.constants import MOON_REFERENCE_RADIUS_M as MOON_RADIUS_M
from app.cache import digest, identity, locked, utc_now, verify_bundle, write_json

SOURCE_URL = "https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_80MPP_ADJ.TIF"
PRODUCT_ID = "LDEM_80S_80MPP_ADJ"
MAX_SOURCE_BYTES = 200_000_000
VERSION = 1
# NASA PGDA product 90 and Barker et al. 2023, doi:10.3847/PSJ/acf3e1.
LUNAR_CRS = f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={MOON_RADIUS_M} +units=m +no_defs"


class TerrainError(ValueError):
    """Invalid terrain source, incomplete coverage or corrupt cache."""


def _supported_crs(crs) -> bool:
    if crs is None:
        return False
    params = crs.to_dict()
    # GDAL may serialize unit polar scale as k=1 or lat_ts=-90.
    expected = {
        "proj": "stere",
        "lat_0": -90,
        "lon_0": 0,
        "x_0": 0,
        "y_0": 0,
        "R": MOON_RADIUS_M,
        "units": "m",
    }
    return (
        all(params.get(key) == value for key, value in expected.items())
        and params.get("k", 1) == 1
        and params.get("lat_ts", -90) == -90
    )


def _verify(path: Path) -> dict:
    try:
        return verify_bundle(path)
    except ValueError as exc:
        raise TerrainError(str(exc)) from exc


def acquire_source(data_dir: Path, client: httpx.Client) -> Path:
    """Cache the fixed PGDA product, bounded to 200 MB, without modifying raw bytes.

    Local checksums detect corruption, not publisher authenticity. Source headers
    and URL identify the acquired snapshot; existing snapshots are never refreshed.
    """
    key = identity({"url": SOURCE_URL, "product": PRODUCT_ID, "version": VERSION})
    root = Path(data_dir) / "terrain" / "raw"
    root.mkdir(parents=True, exist_ok=True)
    final = root / key
    with locked(root / f"{key}.lock"):
        if final.exists():
            _verify(final)
            return final / "source.tif"
        with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
            stage = Path(temporary) / "bundle"
            stage.mkdir()
            path = stage / "source.tif"
            with client.stream("GET", SOURCE_URL, timeout=120) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise TerrainError("Expected complete HTTP 200 source")
                length = response.headers.get("content-length")
                expected = int(length) if length is not None else None
                if expected is not None and not 0 < expected <= MAX_SOURCE_BYTES:
                    raise TerrainError("Source exceeds size limit")
                size = 0
                with path.open("wb") as target:
                    for chunk in response.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_SOURCE_BYTES:
                            raise TerrainError("Source exceeds size limit")
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if size == 0 or (expected is not None and size != expected):
                    raise TerrainError("Truncated terrain source")
                write_json(
                    stage / "manifest.json",
                    {
                        "product_id": PRODUCT_ID,
                        "source_url": SOURCE_URL,
                        "source_documentation": "https://pgda.gsfc.nasa.gov/products/90",
                        "data_doi": "10.60903/gsfcpgda-lola-spole",
                        "acquired_at_utc": utc_now(),
                        "etag": response.headers.get("etag"),
                        "last_modified": response.headers.get("last-modified"),
                        "size_bytes": size,
                        "sha256": {"source.tif": digest(path)},
                    },
                )
            os.rename(stage, final)
        return final / "source.tif"


def prepare_terrain(
    site: ObserverSite, radius_m: int, data_dir: Path, client: httpx.Client
) -> dict:
    """Crop a square of projected half-width radius_m around the site.

    Preserve native 80 m cells; reject missing coverage or incompatible metadata.
    This is data preparation, not horizon or landing-safety analysis.
    """
    if not -90 <= site.latitude_deg <= -80:
        raise TerrainError("Site outside nominal 80–90 degrees south coverage")
    if type(radius_m) is not int or not 80 <= radius_m <= 20000:
        raise TerrainError("terrain radius must be 80..20000 projected meters")
    source = acquire_source(data_dir, client)
    source_manifest = _verify(source.parent)
    source_hash = source_manifest["sha256"]["source.tif"]
    params = {
        "site": site.model_dump(mode="json"),
        "radius_m": radius_m,
        "source_sha256": source_hash,
        "version": VERSION,
        "rasterio": rasterio.__version__,
    }
    key = identity(params)
    root = Path(data_dir) / "terrain" / "processed"
    root.mkdir(parents=True, exist_ok=True)
    final = root / key
    with locked(root / f"{key}.lock"):
        if final.exists():
            _verify(final)
            return {"path": str(final), "cache_hit": True}
        try:
            with rasterio.open(source) as ds:
                if (
                    not _supported_crs(ds.crs)
                    or ds.count != 1
                    or ds.res != (80, 80)
                    or ds.dtypes != ("float32",)
                    or ds.scales != (1.0,)
                    or ds.offsets != (0.0,)
                    or ds.transform.b != 0
                    or ds.transform.d != 0
                    or ds.transform.e >= 0
                ):
                    raise TerrainError("Unexpected DEM CRS, units, scale or grid")
                x, y = transform(
                    f"+proj=longlat +R={MOON_RADIUS_M} +no_defs",
                    ds.crs,
                    [site.longitude_deg_east],
                    [site.latitude_deg],
                )
                row, col = ds.index(x[0], y[0])
                half = math.ceil(radius_m / ds.res[0])
                window = Window(col - half, row - half, half * 2 + 1, half * 2 + 1)
                if (
                    window.col_off < 0
                    or window.row_off < 0
                    or window.col_off + window.width > ds.width
                    or window.row_off + window.height > ds.height
                ):
                    raise TerrainError("Requested terrain window exceeds source bounds")
                values = ds.read(1, window=window, masked=True)
                if (
                    np.ma.getmaskarray(values).any()
                    or not np.isfinite(values.data).all()
                ):
                    raise TerrainError("Requested terrain window contains NoData")
                with tempfile.TemporaryDirectory(
                    prefix=".pending-", dir=root
                ) as temporary:
                    stage = Path(temporary) / "bundle"
                    stage.mkdir()
                    profile = ds.profile.copy()
                    profile.update(
                        width=int(window.width),
                        height=int(window.height),
                        transform=ds.window_transform(window),
                        compress="deflate",
                    )
                    with rasterio.open(stage / "terrain.tif", "w", **profile) as target:
                        target.write(values.data, 1)
                    write_json(
                        stage / "manifest.json",
                        {
                            **params,
                            "product_id": PRODUCT_ID,
                            "reference_frame": "MOON_ME",
                            "frame_realization": "DE421 mean Earth",
                            "reference_sphere_radius_m": MOON_RADIUS_M,
                            "source_path": str(source.relative_to(data_dir)),
                            "source_url": SOURCE_URL,
                            "created_at_utc": utc_now(),
                            "resolution_m": list(ds.res),
                            "crs_wkt": ds.crs.to_wkt(),
                            "window_bounds_m": list(bounds(window, ds.transform)),
                            "window_pixels": [
                                int(window.col_off),
                                int(window.row_off),
                                int(window.width),
                                int(window.height),
                            ],
                            "valid_cells": int(values.size),
                            "surface_height_m": float(values[half, half]),
                            "height_sampling": "containing native pixel, no interpolation",
                            "sha256": {"terrain.tif": digest(stage / "terrain.tif")},
                        },
                    )
                    os.rename(stage, final)
        except rasterio.errors.RasterioError as exc:
            raise TerrainError(f"Invalid DEM: {exc}") from exc
        return {"path": str(final), "cache_hit": False}
