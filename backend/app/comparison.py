"""Comparable single-site results, metric deltas and elapsed-time alignment."""

from bisect import bisect_right
from math import isclose

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app.visibility import utc

METRICS = (
    "visible_duration_s",
    "visible_percent_of_interval",
    "longest_blocked_s",
    "longest_visible_s",
    "unknown_duration_s",
)


def compare_results(a: dict, b: dict) -> dict:
    """Compare compatible completed results without interpolating sky samples.

    Delta is B minus A. Timelines compare elapsed time since each start. Cursor
    positions retain the closest preceding sample plus its age, never claiming
    that a coarse sample is a new physical calculation at the cursor instant.
    """
    analyses = [a["analysis"], b["analysis"]]
    requests = [item["request"] for item in analyses]
    starts = [utc(item["start_utc"]) for item in requests]
    durations = [
        (utc(item["end_utc"]) - start).total_seconds()
        for item, start in zip(requests, starts)
    ]
    if durations[0] <= 0 or durations[0] != durations[1]:
        raise ValueError("Comparison requires the same positive observation duration")
    same_site = all(
        requests[0]["site"][key] == requests[1]["site"][key]
        for key in ("latitude_deg", "longitude_deg_east", "reference_frame")
    )
    same_start = starts[0] == starts[1]
    if not same_site and not same_start:
        raise ValueError("Change either location or start date, not both")
    compatible = (
        requests[0]["step_s"] == requests[1]["step_s"]
        and a["horizon"]["settings"] == b["horizon"]["settings"]
        and a["horizon"].get("source_sha256") == b["horizon"].get("source_sha256")
        and a["horizon"]["model_version"] == b["horizon"]["model_version"]
        and analyses[0]["model_version"] == analyses[1]["model_version"]
        and analyses[0]["refinement"]["status"] == analyses[1]["refinement"]["status"]
        and analyses[0]["spatial_sensitivity"]["offset_deg"]
        == analyses[1]["spatial_sensitivity"]["offset_deg"]
        and isclose(
            a["horizon"]["observer_height_above_terrain_m"],
            b["horizon"]["observer_height_above_terrain_m"],
            abs_tol=1e-6,
            rel_tol=0,
        )
    )
    if not compatible:
        raise ValueError(
            "Use matching sampling, horizon, height, refinement and model settings"
        )
    offsets = []
    for result, start in zip((a, b), starts):
        times = [
            (utc(frame["utc"]) - start).total_seconds() for frame in result["frames"]
        ]
        if (
            not times
            or times != sorted(set(times))
            or times[0] < 0
            or times[-1] >= durations[0]
        ):
            raise ValueError(
                "Playback samples must be nonempty, ordered and inside the interval"
            )
        offsets.append(times)
    cursor = []
    for elapsed in sorted({0.0, *offsets[0], *offsets[1]}):
        row = {"elapsed_s": elapsed}
        for name, times in zip(("a", "b"), offsets):
            index = bisect_right(times, elapsed) - 1
            row[f"{name}_index"] = index if index >= 0 else None
            row[f"{name}_sample_age_s"] = elapsed - times[index] if index >= 0 else None
        cursor.append(row)
    deltas = {
        target: {
            metric: analyses[1][target]["metrics"][metric]
            - analyses[0][target]["metrics"][metric]
            for metric in METRICS
        }
        for target in ("sun", "earth")
    }
    changes = {}
    for target in ("sun", "earth"):
        timelines = [
            [
                (
                    (utc(row["start_utc"]) - start).total_seconds(),
                    (utc(row["end_utc"]) - start).total_seconds(),
                    row["state"],
                )
                for row in result[target]["intervals"]
            ]
            for result, start in zip(analyses, starts)
        ]
        edges = sorted(
            {
                0.0,
                durations[0],
                *(
                    max(0, min(durations[0], point))
                    for rows in timelines
                    for row in rows
                    for point in row[:2]
                ),
            }
        )
        changes[target] = []
        positions = [0, 0]
        for left, right in zip(edges, edges[1:]):
            states = []
            for index, rows in enumerate(timelines):
                while (
                    positions[index] < len(rows) and rows[positions[index]][1] <= left
                ):
                    positions[index] += 1
                position = positions[index]
                states.append(
                    rows[position][2]
                    if position < len(rows)
                    and rows[position][0] <= left < rows[position][1]
                    else "unknown"
                )
            if states[0] != states[1]:
                changes[target].append(
                    dict(
                        start_elapsed_s=left,
                        end_elapsed_s=right,
                        a_state=states[0],
                        b_state=states[1],
                    )
                )
    incomplete = any(
        result[target]["metrics"]["unknown_duration_s"] > 0
        for result in analyses
        for target in ("sun", "earth")
    )
    explanations = []
    if incomplete:
        explanations.append(
            "Unknown coverage is present. Differences are not a reliable ranking of the scenarios."
        )
    for target, label in [("sun", "Sunlight"), ("earth", "Earth-center visibility")]:
        delta = deltas[target]["visible_duration_s"] / 3600
        difference = (
            f"B has {abs(delta):.2f} h {'more' if delta > 0 else 'less'} modeled visibility than A"
            if delta else "A and B have the same modeled visibility duration"
        )
        explanations.append(
            f"{label}: {difference} over the same {durations[0] / 3600:g} h window."
        )
        explanations.append(
            f"{label}: {len(changes[target])} elapsed-time segment(s) have different states; see both UTC timelines. Estimates inherit terrain and sampling limits."
        )
    return dict(
        mode="identical"
        if same_site and same_start
        else "dates"
        if same_site
        else "sites",
        status="insufficient_data" if incomplete else "ready",
        alignment="shared_utc" if same_start else "elapsed_since_start",
        duration_s=durations[0],
        start_offset_s=(starts[1] - starts[0]).total_seconds(),
        deltas=deltas,
        changes=changes,
        cursor=cursor,
        explanations=explanations,
        analysis_ids=[item["analysis_id"] for item in analyses],
    )


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a_id: str = Field(min_length=1, max_length=100)
    b_id: str = Field(min_length=1, max_length=100)


def register_comparison_routes(app, store):
    """Compare immutable saved results, leaving pending and failed jobs explicit."""

    @app.post("/comparisons")
    def compare(body: ComparisonRequest):
        jobs = [store.get(body.a_id), store.get(body.b_id)]
        if any(job is None for job in jobs):
            raise HTTPException(404, "Analysis not found")
        if any(job["status"] not in ("ready", "insufficient_data") for job in jobs):
            raise HTTPException(409, "Both analyses must have completed results")
        try:
            return compare_results(jobs[0]["result"], jobs[1]["result"]) | {
                "job_ids": body.model_dump()
            }
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, f"Incompatible results: {exc}") from exc
