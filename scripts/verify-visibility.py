"""Check generated coarse/refined/replayed reports and print compact evidence.

Usage: python scripts/verify-visibility.py coarse.json refined.json replay.json
The inputs are produced by the documented backend CLI, not downloaded here.
"""

import json
from pathlib import Path
import sys


def main():
    coarse, refined, replay = [
        json.loads(Path(name).read_text()) for name in sys.argv[1:]
    ]
    if refined != replay:
        raise AssertionError("Cached replay differs from refined result")
    if coarse["request"] != refined["request"] or not refined["events"]:
        raise AssertionError("Expected matching real-data requests with transitions")
    if refined["refinement"]["status"] != "completed":
        raise AssertionError("Refinement did not complete")
    widths = [event["bracket_width_s"] for event in refined["events"]]
    if max(widths) > 120:
        raise AssertionError("Transition brackets exceed two one-minute samples")
    from datetime import datetime

    request = refined["request"]
    duration_s = (
        datetime.fromisoformat(request["end_utc"])
        - datetime.fromisoformat(request["start_utc"])
    ).total_seconds()
    for result in [coarse, refined]:
        for target in ["sun", "earth"]:
            metrics = result[target]["metrics"]
            accounted_s = sum(
                metrics[key]
                for key in [
                    "visible_duration_s",
                    "blocked_duration_s",
                    "unknown_duration_s",
                ]
            )
            if abs(accounted_s - duration_s) > 1e-5:
                raise AssertionError(f"Unaccounted {target} duration")
            intervals = result[target]["intervals"]
            if any(
                a["end_utc"] != b["start_utc"] for a, b in zip(intervals, intervals[1:])
            ):
                raise AssertionError("Intervals have a gap or overlap")
    shifts = []
    for old, new in zip(coarse["events"], refined["events"]):
        if old["kind"] == new["kind"]:
            delta_s = abs(
                (
                    datetime.fromisoformat(old["estimated_at_utc"])
                    - datetime.fromisoformat(new["estimated_at_utc"])
                ).total_seconds()
            )
            shifts.append(delta_s)
    summary = {
        "verified_on": "2026-11-14",
        "request": request,
        "coarse_analysis_id": coarse["analysis_id"],
        "refined_analysis_id": refined["analysis_id"],
        "horizon_profile_id": refined["horizon_profile_id"],
        "coarse_samples": coarse["sample_count"],
        "refined_samples": refined["sample_count"],
        "coarse_event_count": len(coarse["events"]),
        "refined_event_count": len(refined["events"]),
        "max_refined_bracket_s": max(widths),
        "max_matched_event_shift_s": max(shifts) if shifts else None,
        "refinement": refined["refinement"],
        "metrics": {target: refined[target]["metrics"] for target in ["sun", "earth"]},
        "next_setting": {
            target: refined[target]["next_setting"] for target in ["sun", "earth"]
        },
        "spatial_sensitivity": refined["spatial_sensitivity"],
        "provenance": refined["provenance"],
        "cached_replay_identical": True,
        "duration_conservation": "passed within 1e-5 seconds",
        "limitations": "Linear temporal model; finite sampled terrain; only detected transitions refined; no eclipse or radio link model.",
    }
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
