"""Analytic sphere, ENU geometry and synthetic DEM checks, independent of NASA I/O.

Analytic tolerances separate floating point (1e-8 rad) from radial sampling
(0.0001 degree on the uniform sphere). Real DEM evidence is recorded separately.
"""

import hashlib
import json

import numpy as np
import pytest
import rasterio
from pydantic import ValidationError
from rasterio.transform import from_origin

from app.horizon import (
    HorizonError,
    compute_profile,
    elevation_angles,
    interpolate_horizon,
    profile_from_bundle,
    ray_coordinates,
)
from shared.constants import MOON_REFERENCE_RADIUS_M as R
from shared.contracts import ObserverSite
from shared.horizon import HorizonSettings

CRS = f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m +no_defs"


def observer(**changes):
    return ObserverSite(
        **(
            dict(
                site_id="synthetic",
                latitude_deg=-90.0,
                longitude_deg_east=0.0,
                altitude_km=0.102,
            )
            | changes
        )
    )


def settings(**changes):
    return HorizonSettings(
        **(
            dict(azimuth_step_deg=90.0, search_distance_m=3000, radial_step_m=40)
            | changes
        )
    )


def write_dem(path, values=None, crs=CRS, transform=None):
    values = np.full((101, 101), 100, dtype="float32") if values is None else values
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform or from_origin(-4040, 4040, 80, 80),
        nodata=np.nan,
    ) as ds:
        ds.write(values, 1)
    return path


@pytest.fixture
def dem(tmp_path):
    return write_dem(tmp_path / "source.tif")


def profile(dem, **changes):
    with rasterio.open(dem) as ds:
        return compute_profile(ds, observer(), settings(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"azimuth_step_deg": 7.0},
        {"azimuth_step_deg": 0.0},
        {"azimuth_step_deg": float("nan")},
        {"radial_step_m": 9},
        {"search_distance_m": 300001},
        {"search_distance_m": True},
        {"radial_step_m": 81},
        {"source_extent": "remote"},
        {"extra": 1},
        {"azimuth_step_deg": 0.25, "search_distance_m": 300000, "radial_step_m": 10},
    ],
)
def test_invalid_settings(changes):
    with pytest.raises(ValidationError):
        settings(**changes)


def test_default_and_small_settings():
    assert HorizonSettings().search_distance_m == 100000
    assert settings(search_distance_m=80, radial_step_m=80).radial_step_m == 80


@pytest.mark.parametrize("azimuth", [0, np.pi / 2, np.pi, 3 * np.pi / 2, 2 * np.pi])
def test_great_circle_uses_true_north_and_east(azimuth):
    site = observer(latitude_deg=-85.0, longitude_deg_east=359.9)
    distance = np.array([1.0, 1000.0, 100000.0])
    lon, lat = ray_coordinates(site, azimuth, distance)
    phi, lam = np.deg2rad([-85.0, 359.9])
    up = np.array([np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)])
    north = np.array(
        [-np.sin(phi) * np.cos(lam), -np.sin(phi) * np.sin(lam), np.cos(phi)]
    )
    east = np.array([-np.sin(lam), np.cos(lam), 0])
    points = np.column_stack(
        [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]
    )
    assert points @ up == pytest.approx(np.cos(distance / R), abs=1e-12)
    assert points @ north == pytest.approx(
        np.sin(distance / R) * np.cos(azimuth), abs=1e-12
    )
    assert points @ east == pytest.approx(
        np.sin(distance / R) * np.sin(azimuth), abs=1e-12
    )


@pytest.mark.parametrize("longitude", [0.0, 31.0, 359.99])
def test_exact_south_pole_has_explicit_meridian(longitude):
    lon, lat = ray_coordinates(
        observer(longitude_deg_east=longitude), 0, np.array([1000.0])
    )
    assert np.rad2deg(lat[0]) == pytest.approx(-90 + np.rad2deg(1000 / R), abs=1e-9)
    assert np.rad2deg(lon[0]) % 360 == pytest.approx(longitude, abs=1e-9)


def test_curvature_at_equal_height_and_independent_cartesian_reference():
    distances = np.array([80.0, 1000.0, 100000.0])
    assert elevation_angles(distances, np.zeros(3), 0) == pytest.approx(
        -distances / (2 * R), abs=1e-10
    )
    target = np.array([100.0, 500.0, -1000.0])
    theta = distances / R
    expected = np.arctan2(
        (R + target) * np.cos(theta) - (R + 102), (R + target) * np.sin(theta)
    )
    assert elevation_angles(distances, target, 102) == pytest.approx(expected, abs=1e-8)


def test_uniform_sphere_dip_and_reproducibility(dem):
    result = profile(dem)
    expected_deg = -np.rad2deg(np.arccos((R + 100) / (R + 102)))
    assert len(result["directions"]) == 4
    for direction in result["directions"]:
        assert direction["coverage"] == "complete_within_radius"
        assert direction["elevation_deg"] == pytest.approx(expected_deg, abs=0.0001)
        assert direction["valid_samples"] == direction["requested_samples"]
    assert result["observer_height_above_terrain_m"] == pytest.approx(2)
    assert result == profile(dem)
    json.dumps(result, allow_nan=False)


def test_cardinal_ridge_only_blocks_north(tmp_path):
    values = np.full((101, 101), 100, dtype="float32")
    values[39:42, 49:52] = 1000
    result = profile(write_dem(tmp_path / "ridge.tif", values))
    assert 40 < result["directions"][0]["elevation_deg"] < 60
    assert all(row["elevation_deg"] < 0 for row in result["directions"][1:])
    assert 600 < result["directions"][0]["limiting_distance_m"] < 1000


def test_missing_terrain_is_unknown_not_clear(tmp_path):
    values = np.full((101, 101), 100, dtype="float32")
    values[39:42, 49:52] = np.nan
    result = profile(write_dem(tmp_path / "missing.tif", values))
    north = result["directions"][0]
    assert north["coverage"] == "incomplete"
    assert north["elevation_deg"] is None
    assert north["sampled_max_elevation_deg"] is not None
    assert north["valid_samples"] < north["requested_samples"]
    assert all(
        row["coverage"] == "complete_within_radius" for row in result["directions"][1:]
    )


def test_outside_raster_is_unknown(dem):
    result = profile(dem, search_distance_m=8000)
    assert all(row["elevation_deg"] is None for row in result["directions"])


@pytest.mark.parametrize(
    "change", [{"altitude_km": 0.099}, {"altitude_km": 0.100}, {"latitude_deg": -70.0}]
)
def test_invalid_observer_rejected(dem, change):
    with rasterio.open(dem) as ds, pytest.raises(HorizonError):
        compute_profile(ds, observer(**change), settings())


def test_invalid_crs_and_grid_rejected(tmp_path):
    for index, kwargs in enumerate(
        [{"crs": "EPSG:4326"}, {"transform": from_origin(-4040, 4040, 40, 40)}]
    ):
        path = write_dem(tmp_path / f"bad{index}.tif", **kwargs)
        with rasterio.open(path) as ds, pytest.raises(HorizonError):
            compute_profile(ds, observer(), settings())


def test_missing_observer_pixel_rejected(tmp_path):
    values = np.full((101, 101), 100, dtype="float32")
    values[50, 50] = np.nan
    with (
        rasterio.open(write_dem(tmp_path / "bad.tif", values)) as ds,
        pytest.raises(HorizonError),
    ):
        compute_profile(ds, observer(), settings())


def test_circular_interpolation_and_unknown_neighbors():
    result = {
        "directions": [
            {"azimuth_deg": 0, "elevation_deg": 10.0},
            {"azimuth_deg": 90, "elevation_deg": None},
            {"azimuth_deg": 180, "elevation_deg": 2.0},
            {"azimuth_deg": 270, "elevation_deg": 6.0},
        ]
    }
    assert interpolate_horizon(result, 315) == pytest.approx(8.0)
    assert interpolate_horizon(result, -45) == pytest.approx(8.0)
    assert interpolate_horizon(result, 360) == pytest.approx(10.0)
    assert interpolate_horizon(result, 45) is None
    assert interpolate_horizon(result, 90) is None
    with pytest.raises(HorizonError):
        interpolate_horizon(result, float("nan"))


@pytest.fixture
def bundle(tmp_path):
    raw = tmp_path / "terrain/raw" / ("a" * 64)
    crop = tmp_path / "terrain/processed" / ("b" * 64)
    raw.mkdir(parents=True)
    crop.mkdir(parents=True)
    source = write_dem(raw / "source.tif")
    (crop / "terrain.tif").write_bytes(source.read_bytes())
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = dict(
        site=observer().model_dump(),
        source_path=str(source.relative_to(tmp_path)),
        source_sha256=sha,
        source_url="https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_80MPP_ADJ.TIF",
        product_id="LDEM_80S_80MPP_ADJ",
        reference_frame="MOON_ME",
        frame_realization="DE421 mean Earth",
        reference_sphere_radius_m=R,
        sha256={"terrain.tif": sha},
    )
    (crop / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path, str(crop.relative_to(tmp_path))


def test_verified_bundle_reproducible_read_only_and_parameter_identity(bundle):
    root, ref = bundle
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = profile_from_bundle(root, ref, settings())
    assert (
        result["profile_id"] == profile_from_bundle(root, ref, settings())["profile_id"]
    )
    assert (
        result["profile_id"]
        != profile_from_bundle(root, ref, settings(search_distance_m=2000))[
            "profile_id"
        ]
    )
    assert (
        result["profile_id"]
        != profile_from_bundle(root, ref, settings(source_extent="crop"))["profile_id"]
    )
    assert (
        result["source_sha256"]
        == hashlib.sha256(
            next(root.glob("terrain/raw/*/source.tif")).read_bytes()
        ).hexdigest()
    )
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("target", ["raw", "crop"])
def test_corrupt_bundle_rejected(bundle, target):
    root, ref = bundle
    path = (
        next(root.glob("terrain/raw/*/source.tif"))
        if target == "raw"
        else root / ref / "terrain.tif"
    )
    with path.open("ab") as stream:
        stream.write(b"corrupted")
    with pytest.raises(HorizonError, match="checksum"):
        profile_from_bundle(root, ref, settings())


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference_frame", "J2000"),
        ("reference_sphere_radius_m", 6371000),
        ("frame_realization", "unknown"),
        ("product_id", "other"),
    ],
)
def test_incompatible_manifest_rejected(bundle, field, value):
    root, ref = bundle
    path = root / ref / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(HorizonError):
        profile_from_bundle(root, ref, settings())


def test_escaping_reference_and_source_rejected(bundle):
    root, ref = bundle
    with pytest.raises(HorizonError):
        profile_from_bundle(root, "../../source.tif", settings())
    path = root / ref / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["source_path"] = "/tmp/outside.tif"
    path.write_text(json.dumps(manifest))
    with pytest.raises(HorizonError):
        profile_from_bundle(root, ref, settings())


def test_cli_emits_strict_json_without_writing_data(bundle, monkeypatch, capsys):
    from app.compute_horizon import main

    root, ref = bundle
    monkeypatch.setenv("DATA_DIR", str(root))
    monkeypatch.setattr(
        "sys.argv",
        [
            "compute_horizon",
            ref,
            "--search-distance-m",
            "3000",
            "--azimuth-step-deg",
            "90",
        ],
    )
    main()
    result = json.loads(capsys.readouterr().out)
    assert len(result["directions"]) == 4
    assert result["settings"]["search_distance_m"] == 3000
