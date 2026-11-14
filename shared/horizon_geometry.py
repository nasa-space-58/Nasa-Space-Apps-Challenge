"""Raster-independent interpolation of existing terrain horizon directions."""

import bisect
import math


def interpolate_horizon(profile, azimuth_deg):
    """Circular linear interpolation in degrees; unknown neighbors remain unknown."""
    if not math.isfinite(azimuth_deg):
        raise ValueError("Azimuth must be finite")
    directions = profile["directions"]
    if len(directions) < 2:
        raise ValueError("At least two horizon directions required")
    azimuth_deg %= 360
    index = bisect.bisect_right([d["azimuth_deg"] for d in directions], azimuth_deg) - 1
    left = directions[index]
    if azimuth_deg == left["azimuth_deg"]:
        return left["elevation_deg"]
    right = directions[(index + 1) % len(directions)]
    if left["elevation_deg"] is None or right["elevation_deg"] is None:
        return None
    width = (right["azimuth_deg"] - left["azimuth_deg"]) % 360
    return left["elevation_deg"] + (
        (azimuth_deg - left["azimuth_deg"]) % 360
    ) / width * (right["elevation_deg"] - left["elevation_deg"])
