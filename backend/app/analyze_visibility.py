"""Analyze local caches, optionally refining transitions through RabbitMQ only."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from app.horizon import profile_from_bundle
from app.submit_ingestion import submit
from app.visibility import (
    VisibilityError,
    analyze_visibility,
    load_ephemeris,
    refinement_requests,
    utc,
)
from shared.horizon import HorizonSettings


async def run_analysis(
    data_dir,
    terrain_reference,
    horizons_reference,
    settings,
    selected_utc=None,
    sensitivity_deg=0.75,
    refine=False,
    broker_url=None,
):
    """Analyze once, then optionally acquire 5-minute/1-minute transition brackets.

    Refined bundles must describe the exact planned site and interval. Errors
    propagate instead of pretending refinement succeeded. Downloads stay in the
    worker. All cached reads and scientific calculations run in the backend.
    """
    profile = profile_from_bundle(data_dir, terrain_reference, settings)
    request, samples, manifest = load_ephemeris(data_dir, horizons_reference)
    result = analyze_visibility(
        request, samples, profile, selected_utc, sensitivity_deg
    )
    sources = [{"reference": horizons_reference, "sha256": manifest.get("sha256", {})}]
    passes = []
    if refine:
        if not broker_url:
            raise VisibilityError("RabbitMQ URL required for refinement")
        for step_s in (300, 60):
            plans = refinement_requests(result, request, step_s)
            for job in plans:
                event = await submit(job, broker_url)
                if event.status != "ready":
                    raise VisibilityError(
                        f"Refinement failed: {event.error_code}: {event.error_message}"
                    )
                refined, rows, metadata = load_ephemeris(data_dir, event.horizons_path)
                if refined != job.analysis:
                    raise VisibilityError("Refined bundle differs from planned request")
                samples = [
                    row
                    for row in samples
                    if not refined.start_utc
                    <= utc(row["sampled_at_utc"])
                    <= refined.end_utc
                ] + rows
                samples.sort(key=lambda row: utc(row["sampled_at_utc"]))
                sources.append(
                    {
                        "reference": event.horizons_path,
                        "sha256": metadata.get("sha256", {}),
                    }
                )
            passes.append({"step_s": step_s, "windows": len(plans)})
            result = analyze_visibility(
                request, samples, profile, selected_utc, sensitivity_deg
            )
    result["provenance"] = {
        "terrain_reference": terrain_reference,
        "ephemerides": sources,
    }
    result["refinement"] = {
        "status": "completed" if refine else "not_requested",
        "passes": passes,
        "scope": "detected transition brackets only; not a full fine-grid search",
    }
    result["analysis_id"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    return result


def main():
    """Print visibility JSON; invalid inputs or failed refinement exit nonzero."""
    parser = argparse.ArgumentParser(description="Analyze cached lunar visibility")
    parser.add_argument("terrain_reference")
    parser.add_argument("horizons_reference")
    parser.add_argument("--selected-utc")
    parser.add_argument("--refine", action="store_true")
    parser.add_argument("--sensitivity-deg", type=float, default=0.75)
    parser.add_argument("--search-distance-m", type=int, default=100000)
    parser.add_argument("--azimuth-step-deg", type=float, default=1.0)
    parser.add_argument("--radial-step-m", type=int, default=40)
    args = parser.parse_args()
    try:
        settings = HorizonSettings(
            search_distance_m=args.search_distance_m,
            azimuth_step_deg=args.azimuth_step_deg,
            radial_step_m=args.radial_step_m,
        )
        result = asyncio.run(
            run_analysis(
                Path(os.environ.get("DATA_DIR", "/data")),
                args.terrain_reference,
                args.horizons_reference,
                settings,
                args.selected_utc,
                args.sensitivity_deg,
                args.refine,
                os.environ.get("RABBITMQ_URL"),
            )
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
