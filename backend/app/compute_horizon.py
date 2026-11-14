"""Read-only command line terrain horizon computation; JSON goes to stdout."""

import argparse
import json
import os
from pathlib import Path

from app.horizon import HorizonError, profile_from_bundle
from shared.horizon import HorizonSettings


def main():
    """Compute one cached site's horizon; invalid inputs exit nonzero."""
    parser = argparse.ArgumentParser(
        description="Compute a bounded lunar terrain horizon"
    )
    parser.add_argument(
        "terrain_reference", help="terrain/processed/<SHA256> from a ready event"
    )
    parser.add_argument("--search-distance-m", type=int, default=100000)
    parser.add_argument("--azimuth-step-deg", type=float, default=1.0)
    parser.add_argument("--radial-step-m", type=int, default=40)
    parser.add_argument("--source-extent", choices=["raw", "crop"], default="raw")
    args = parser.parse_args()
    try:
        settings = HorizonSettings(
            search_distance_m=args.search_distance_m,
            azimuth_step_deg=args.azimuth_step_deg,
            radial_step_m=args.radial_step_m,
            source_extent=args.source_extent,
        )
        result = profile_from_bundle(
            Path(os.environ.get("DATA_DIR", "/data")), args.terrain_reference, settings
        )
    except (HorizonError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
