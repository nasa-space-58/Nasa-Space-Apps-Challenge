"""Reproduce Task 2 radius/sampling checks and independent Cartesian geometry.

Run in backend via stdin, with an existing Task 1 terrain reference as argument.
Print a compact JSON report; never download or modify the cache.
"""

import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import rasterio
from rasterio.warp import transform

from app.horizon import profile_from_bundle
from shared.constants import MOON_REFERENCE_RADIUS_M as R
from shared.horizon import HorizonSettings


def main():
    root = Path(os.environ.get("DATA_DIR", "/data"))
    ref = sys.argv[1]
    profiles = {}
    summaries = {}
    configurations = {
        "20km": dict(search_distance_m=20000),
        "50km": dict(search_distance_m=50000),
        "100km": {},
        "150km": dict(search_distance_m=150000),
        "100km-20m": dict(radial_step_m=20),
        "100km-half-degree": dict(azimuth_step_deg=0.5),
        "crop-100km": dict(source_extent="crop"),
    }
    for name, options in configurations.items():
        start = time.monotonic()
        result = profile_from_bundle(root, ref, HorizonSettings(**options))
        profiles[name] = result
        known = [
            row["elevation_deg"]
            for row in result["directions"]
            if row["elevation_deg"] is not None
        ]
        summaries[name] = {
            "profile_id": result["profile_id"],
            "settings": result["settings"],
            "complete_directions": len(known),
            "total_directions": len(result["directions"]),
            "min_elevation_deg": min(known) if known else None,
            "max_elevation_deg": max(known) if known else None,
            "elapsed_s": round(time.monotonic() - start, 3),
        }
    base = profiles["100km"]
    comparisons = {}
    for name in ["20km", "50km", "150km", "100km-20m"]:
        differences = [
            abs(a["elevation_deg"] - b["elevation_deg"])
            for a, b in zip(base["directions"], profiles[name]["directions"])
            if a["elevation_deg"] is not None and b["elevation_deg"] is not None
        ]
        comparisons[name] = {
            "common_complete_directions": len(differences),
            "max_absolute_delta_deg": max(differences) if differences else None,
        }
    finer = profiles["100km-half-degree"]["directions"]
    midpoint_errors = []
    for i in range(360):
        left = base["directions"][i]["elevation_deg"]
        right = base["directions"][(i + 1) % 360]["elevation_deg"]
        actual = finer[2 * i + 1]["elevation_deg"]
        if left is not None and right is not None and actual is not None:
            midpoint_errors.append(abs(actual - (left + right) / 2))
    comparisons["half_degree_vs_interpolated_midpoints"] = {
        "common_complete_directions": len(midpoint_errors),
        "max_absolute_delta_deg": max(midpoint_errors) if midpoint_errors else None,
    }
    # Independent geometry: scalar destination formula + Cartesian subtraction
    # and explicit local ENU dot products. Do not call production ray/elevation helpers.
    manifest = json.loads((root / ref / "manifest.json").read_text())
    site = base["site"]
    lat, lon = np.deg2rad([site["latitude_deg"], site["longitude_deg_east"]])
    up = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    east = np.array([-np.sin(lon), np.cos(lon), 0])
    north = np.cross(up, east)
    origin = (R + site["altitude_km"] * 1000) * up
    distances_m = np.unique(
        np.r_[np.arange(1, 1001), np.arange(40, 100000, 40), 100000.0]
    )
    theta = distances_m / R
    reference_checks = []
    with rasterio.open(root / manifest["source_path"]) as ds:
        for azimuth_deg in [0, 90, 180, 270]:
            azimuth = np.deg2rad(azimuth_deg)
            target_lat = np.arcsin(
                np.sin(lat) * np.cos(theta)
                + np.cos(lat) * np.sin(theta) * np.cos(azimuth)
            )
            target_lon = lon + np.arctan2(
                np.sin(azimuth) * np.sin(theta) * np.cos(lat),
                np.cos(theta) - np.sin(lat) * np.sin(target_lat),
            )
            x, y = transform(
                f"+proj=longlat +R={R}",
                ds.crs,
                np.rad2deg(target_lon),
                np.rad2deg(target_lat),
            )
            heights = np.array([v[0] for v in ds.sample(zip(x, y))])
            positions = (R + heights.astype(float))[:, None] * np.column_stack(
                [
                    np.cos(target_lat) * np.cos(target_lon),
                    np.cos(target_lat) * np.sin(target_lon),
                    np.sin(target_lat),
                ]
            )
            vectors = positions - origin
            angles = np.rad2deg(
                np.arctan2(vectors @ up, np.hypot(vectors @ east, vectors @ north))
            )
            reference_deg = float(np.max(angles))
            computed_deg = base["directions"][azimuth_deg]["elevation_deg"]
            difference_deg = abs(reference_deg - computed_deg)
            if difference_deg > 1e-7:
                raise AssertionError(
                    f"Cartesian reference mismatch at {azimuth_deg}: {difference_deg}"
                )
            reference_checks.append(
                dict(
                    azimuth_deg=azimuth_deg,
                    reference_elevation_deg=reference_deg,
                    computed_elevation_deg=computed_deg,
                    absolute_delta_deg=difference_deg,
                )
            )
    repeated = profile_from_bundle(root, ref, HorizonSettings())
    if repeated != base:
        raise AssertionError("Repeated profile is not identical")
    print(
        json.dumps(
            {
                "verified_on": "2026-11-14",
                "terrain_reference": ref,
                "source_sha256": base["source_sha256"],
                "site": site,
                "model_version": base["model_version"],
                "software": base["software"],
                "observer_height_above_terrain_m": base[
                    "observer_height_above_terrain_m"
                ],
                "profiles": summaries,
                "comparisons_to_100km": comparisons,
                "independent_cartesian_checks": reference_checks,
                "repeat_identical": True,
                "limitations": "Finite sampled horizon only; convergence here does not bound unseen terrain or subpixel peaks.",
            },
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
