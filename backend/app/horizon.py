"""Terrain horizon on the 1737.4 km Moon sphere, in the DE421 MOON_ME frame."""

from pathlib import Path

from shared.contracts import ObserverSite
from shared.horizon import HorizonSettings


class HorizonError(ValueError):
    """Invalid source, observer or incomplete metadata."""


def ray_coordinates(site: ObserverSite, azimuth_rad: float, distances_m):
    """Return MOON_ME longitude/latitude radians along a great-circle ray."""
    raise NotImplementedError


def elevation_angles(distances_m, terrain_height_m, observer_altitude_m):
    """Return radians above local horizontal, including spherical curvature."""
    raise NotImplementedError


def compute_profile(dataset, site: ObserverSite, settings: HorizonSettings) -> dict:
    """Read a local DEM and return a bounded horizon with per-direction coverage."""
    raise NotImplementedError


def profile_from_bundle(
    data_dir: Path, terrain_reference: str, settings: HorizonSettings
) -> dict:
    """Verify a Task 1 bundle and compute from its raw source or crop, read-only."""
    raise NotImplementedError


def interpolate_horizon(profile: dict, azimuth_deg: float) -> float | None:
    """Circular linear interpolation in degrees; unknown neighbors yield None."""
    raise NotImplementedError
