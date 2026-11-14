"""Sampled, airless terrain-relative solar disk and Earth-center visibility.

Durations use piecewise-linear clearance between validated samples, not an exact
continuous ephemeris. Sources and limits: docs/visibility-metrics.md.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from app.horizon import interpolate_horizon
from shared.constants import MOON_REFERENCE_RADIUS_KM
from shared.contracts import AnalysisRequest
from shared.ingestion import IngestionRequest

MODEL_VERSION = "linear-clearance-v1"


class VisibilityError(ValueError):
    """Invalid ephemeris, profile or analysis interval."""


def utc(value):
    """Parse an explicit zero-offset UTC timestamp, without implicit local time."""
    try:
        result = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
        if result.utcoffset() != timedelta(0):
            raise ValueError("UTC offset required")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise VisibilityError(f"Invalid UTC timestamp: {value}") from exc


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def _validate_rows(rows, request):
    if not isinstance(rows, list) or len(rows) > 100000:
        raise VisibilityError("Expected at most 100000 ephemeris rows")
    previous = None
    times = []
    for row in rows:
        time = utc(row["sampled_at_utc"])
        if previous is not None and time <= previous:
            raise VisibilityError("Duplicate or unordered sample times")
        if row["site_id"] != request.site.site_id:
            raise VisibilityError("Sample site differs from request")
        for target in ["sun", "earth"]:
            for field, lower, upper in [
                ("azimuth_deg", 0, 360),
                ("elevation_deg", -90, 90),
                ("angular_diameter_arcsec", 0, 1296000),
            ]:
                value = row[f"{target}_{field}"]
                if value is not None and (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not lower <= value <= upper
                ):
                    raise VisibilityError(f"Invalid {target}_{field}")
        previous = time
        times.append(time)
    return times


def load_ephemeris(data_dir: Path, reference: str):
    """Verify local file hashes, UTC/frame metadata and exact requested sample grid.

    Returns (AnalysisRequest, normalized rows, manifest). Missing numeric values
    remain None; missing rows or corrupt metadata raise VisibilityError.
    """
    if not re.fullmatch(r"horizons/[a-f0-9]{64}", reference):
        raise VisibilityError("Expected horizons/<SHA256> reference")
    root = Path(data_dir).resolve()
    path = (root / reference).resolve()
    if not path.is_relative_to(root):
        raise VisibilityError("Horizons reference escapes data directory")
    try:
        manifest = json.loads((path / "manifest.json").read_text())
        names = {"samples.json", "raw/10.json", "raw/399.json"}
        if (
            manifest["cache_key"] != reference.split("/")[1]
            or manifest["cache_version"] != 1
            or manifest["terrain_applied"] is not False
            or set(manifest["sha256"]) != names
        ):
            raise VisibilityError("Incompatible Horizons manifest")
        for name in names:
            target = (path / name).resolve()
            if not target.is_relative_to(path):
                raise VisibilityError("Ephemeris file escapes bundle")
            if (
                hashlib.sha256(target.read_bytes()).hexdigest()
                != manifest["sha256"][name]
            ):
                raise VisibilityError("Ephemeris checksum mismatch")
        for target in ["10", "399"]:
            source = manifest["sources"][target]
            if (
                source["time_scale"] != "UTC"
                or source["reference_radius_km"] != MOON_REFERENCE_RADIUS_KM
                or "MEAN_ME (high precision)" not in source["orientation"]
            ):
                raise VisibilityError("Incompatible ephemeris time scale or frame")
        request = AnalysisRequest.model_validate(manifest["request"])
        rows = json.loads((path / "samples.json").read_text())
        times = _validate_rows(rows, request)
        count = (
            int((request.end_utc - request.start_utc).total_seconds() // request.step_s)
            + 1
        )
        if (
            len(times) != count
            or manifest["sample_count"] != count
            or any(
                time != request.start_utc + timedelta(seconds=i * request.step_s)
                for i, time in enumerate(times)
            )
        ):
            raise VisibilityError("Incomplete or unexpected ephemeris grid")
        return request, rows, manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise VisibilityError(f"Invalid Horizons bundle: {exc}") from exc


def _sweep_known(profile, left, right, target):
    a, b = left[f"{target}_azimuth_deg"], right[f"{target}_azimuth_deg"]
    if a is None or b is None:
        return False
    difference = (b - a + 180) % 360 - 180
    if abs(difference) == 180:
        return False
    for row in profile["directions"]:
        offset = (row["azimuth_deg"] - a + 180) % 360 - 180
        if (
            min(0, difference) <= offset <= max(0, difference)
            and row["elevation_deg"] is None
        ):
            return False
    return True


def _state(values, target):
    if any(value is None for value in values):
        return "unknown"
    if target == "earth":
        return (
            "visible" if values[0] > 0 else "blocked" if values[0] < 0 else "boundary"
        )
    upper, lower = values
    if upper < 0:
        return "dark"
    if lower > 0:
        return "full"
    return "partial" if upper > 0 and lower < 0 else "boundary"


def _append(intervals, start, end, state):
    if end <= start:
        return
    if (
        intervals
        and intervals[-1]["state"] == state
        and intervals[-1]["end_utc"] == stamp(start)
    ):
        intervals[-1]["end_utc"] = stamp(end)
        intervals[-1]["duration_s"] = (
            end - utc(intervals[-1]["start_utc"])
        ).total_seconds()
    else:
        intervals.append(
            dict(
                start_utc=stamp(start),
                end_utc=stamp(end),
                state=state,
                duration_s=(end - start).total_seconds(),
            )
        )


def _timeline(times, margins, pairs, request, target, offset=0.0):
    intervals = []
    cursor = request.start_utc
    for i in range(len(times) - 1):
        start, end = (
            max(times[i], request.start_utc),
            min(times[i + 1], request.end_utc),
        )
        if end <= start:
            continue
        _append(intervals, cursor, start, "unknown")
        if not pairs[i]:
            _append(intervals, start, end, "unknown")
        else:
            delta = (times[i + 1] - times[i]).total_seconds()
            left = [v - offset for v in margins[i]]
            right = [v - offset for v in margins[i + 1]]
            fractions = {
                (start - times[i]).total_seconds() / delta,
                (end - times[i]).total_seconds() / delta,
            }
            for a, b in zip(left, right):
                if a != b:
                    root = -a / (b - a)
                    if min(fractions) < root < max(fractions):
                        fractions.add(root)
            fractions = sorted(fractions)
            for a, b in zip(fractions, fractions[1:]):
                mid = (a + b) / 2
                values = [x + (y - x) * mid for x, y in zip(left, right)]
                _append(
                    intervals,
                    times[i] + timedelta(seconds=a * delta),
                    times[i] + timedelta(seconds=b * delta),
                    _state(values, target),
                )
        cursor = end
    _append(intervals, cursor, request.end_utc, "unknown")
    return intervals


def _metrics(intervals, duration_s):
    accepted = {"full", "partial", "visible"}
    blocked = {"dark", "blocked"}
    totals = {
        state: sum(row["duration_s"] for row in intervals if row["state"] == state)
        for state in [
            "full",
            "partial",
            "visible",
            "dark",
            "blocked",
            "unknown",
            "boundary",
        ]
    }
    visible = sum(totals[state] for state in accepted)
    longest = {"visible": 0.0, "blocked": 0.0}
    for key, states in [("visible", accepted), ("blocked", blocked)]:
        run = 0.0
        for row in intervals:
            run = run + row["duration_s"] if row["state"] in states else 0.0
            longest[key] = max(longest[key], run)
    return dict(
        visible_duration_s=visible,
        full_duration_s=totals["full"],
        partial_duration_s=totals["partial"],
        blocked_duration_s=sum(totals[s] for s in blocked),
        unknown_duration_s=totals["unknown"] + totals["boundary"],
        visible_percent_of_interval=100 * visible / duration_s,
        longest_visible_s=longest["visible"],
        longest_blocked_s=longest["blocked"],
    )


def _events(times, margins, pairs, request, target):
    channels = (
        [("sunrise", "sunset"), ("full_sun_start", "full_sun_end")]
        if target == "sun"
        else [("earthrise", "earthset")]
    )
    events = []
    for channel, (rising, setting) in enumerate(channels):
        anchor = None
        for i, values in enumerate(margins):
            value = values[channel]
            if i and not pairs[i - 1]:
                anchor = None
            if value is None:
                anchor = None
                continue
            if value == 0:
                continue
            if anchor is not None:
                j = anchor
                previous = margins[j][channel]
                if previous * value < 0:
                    if i > j + 1:
                        estimate = times[j + 1] + (times[i - 1] - times[j + 1]) / 2
                    else:
                        estimate = times[j] + (times[i] - times[j]) * (
                            -previous / (value - previous)
                        )
                    if request.start_utc <= estimate < request.end_utc:
                        start, end = (
                            max(times[j], request.start_utc),
                            min(times[i], request.end_utc),
                        )
                        events.append(
                            dict(
                                kind=rising if value > 0 else setting,
                                estimated_at_utc=stamp(estimate),
                                bracket_start_utc=stamp(start),
                                bracket_end_utc=stamp(end),
                                bracket_width_s=(end - start).total_seconds(),
                            )
                        )
            anchor = i
    return events


def _next_setting(events, intervals, selected, end, kind):
    candidates = [
        e for e in events if e["kind"] == kind and utc(e["bracket_end_utc"]) > selected
    ]
    candidate = (
        min(candidates, key=lambda e: utc(e["estimated_at_utc"]))
        if candidates
        else None
    )
    limit = utc(candidate["estimated_at_utc"]) if candidate else end
    unknown = any(
        row["state"] in {"unknown", "boundary"}
        and utc(row["end_utc"]) > selected
        and utc(row["start_utc"]) < limit
        for row in intervals
    )
    if candidate is None:
        return {"status": "insufficient_data" if unknown else "no_event_in_range"}
    status = "candidate_after_unknown" if unknown else "found"
    if utc(candidate["estimated_at_utc"]) < selected:
        status = "transition_overlaps_selected_time"
    return {
        **candidate,
        "status": status,
        "time_until_estimate_s": max(
            0.0, (utc(candidate["estimated_at_utc"]) - selected).total_seconds()
        ),
        "time_until_bracket_s": [
            max(0.0, (utc(candidate[key]) - selected).total_seconds())
            for key in ["bracket_start_utc", "bracket_end_utc"]
        ],
    }


def analyze_visibility(
    request: AnalysisRequest,
    samples: list,
    profile: dict,
    selected_utc=None,
    sensitivity_deg=0.75,
) -> dict:
    """Produce linear-model intervals/metrics and sampled event brackets in UTC.

    Unknown endpoints, time gaps and swept missing horizon sectors are unknown.
    The solar radius is diameter_arcsec/7200 degrees. A locally constant horizon
    across the disk is assumed; no fractional disk area or power is calculated.
    Sensitivity offsets are user/model scenarios, not measured error bounds.
    """
    try:
        times = _validate_rows(samples, request)
        if (
            profile["site"] != request.site.model_dump(mode="json")
            or profile["reference_frame"] != "MOON_ME"
        ):
            raise VisibilityError("Horizon and ephemeris observers/frames differ")
        directions = profile["directions"]
        azimuths = [r["azimuth_deg"] for r in directions]
        if (
            len(azimuths) < 2
            or azimuths[0] != 0
            or any(not math.isfinite(x) or not 0 <= x < 360 for x in azimuths)
            or any(a >= b for a, b in zip(azimuths, azimuths[1:]))
        ):
            raise VisibilityError("Invalid horizon azimuth grid")
        if any(
            r["elevation_deg"] is not None
            and (
                not math.isfinite(r["elevation_deg"])
                or not -90 <= r["elevation_deg"] <= 90
            )
            for r in directions
        ):
            raise VisibilityError("Invalid horizon elevations")
        selected = utc(selected_utc) if selected_utc is not None else request.start_utc
        if not request.start_utc <= selected < request.end_utc:
            raise VisibilityError(
                "Selected UTC instant must lie inside analysis interval"
            )
        if not math.isfinite(sensitivity_deg) or not 0 <= sensitivity_deg <= 5:
            raise VisibilityError("Sensitivity must be 0..5 degrees")
        result = {
            "model_version": MODEL_VERSION,
            "request": request.model_dump(mode="json"),
            "selected_utc": stamp(selected),
            "horizon_profile_id": profile["profile_id"],
            "horizon_quality": profile["quality"],
            "sample_count": len(samples),
            "model": {
                "durations_are_estimates": True,
                "temporal_model": "piecewise_linear_clearance",
                "solar_disk_model": "locally_constant_terrain_horizon",
                "earth_visibility": "geometric_center_only",
                "eclipses_modeled": False,
                "unseen_between_sample_events_possible": True,
            },
            "events": [],
        }
        sensitivity = {
            "offset_deg": sensitivity_deg,
            "is_error_bound": False,
            "horizon_raised": {},
            "horizon_lowered": {},
        }
        duration_s = (request.end_utc - request.start_utc).total_seconds()
        for target in ["sun", "earth"]:
            margins = []
            for row in samples:
                az, el = row[f"{target}_azimuth_deg"], row[f"{target}_elevation_deg"]
                horizon = interpolate_horizon(profile, az) if az is not None else None
                clearance = (
                    el - horizon if el is not None and horizon is not None else None
                )
                if target == "sun":
                    diameter = row["sun_angular_diameter_arcsec"]
                    margins.append(
                        [clearance + diameter / 7200, clearance - diameter / 7200]
                        if clearance is not None
                        and diameter is not None
                        and diameter > 0
                        else [None, None]
                    )
                else:
                    margins.append([clearance])
            pairs = [
                (times[i + 1] - times[i]).total_seconds() <= request.step_s
                and all(v is not None for v in margins[i] + margins[i + 1])
                and _sweep_known(profile, samples[i], samples[i + 1], target)
                for i in range(len(times) - 1)
            ]
            intervals = _timeline(times, margins, pairs, request, target)
            events = _events(times, margins, pairs, request, target)
            result["events"].extend(events)
            result[target] = {
                "intervals": intervals,
                "metrics": _metrics(intervals, duration_s),
                "next_setting": _next_setting(
                    events,
                    intervals,
                    selected,
                    request.end_utc,
                    "sunset" if target == "sun" else "earthset",
                ),
            }
            for name, offset in [
                ("horizon_raised", sensitivity_deg),
                ("horizon_lowered", -sensitivity_deg),
            ]:
                metrics = _metrics(
                    _timeline(times, margins, pairs, request, target, offset),
                    duration_s,
                )
                sensitivity[name][f"{target}_visible_duration_s"] = metrics[
                    "visible_duration_s"
                ]
        result["events"].sort(key=lambda e: (e["estimated_at_utc"], e["kind"]))
        result["spatial_sensitivity"] = sensitivity
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise VisibilityError(str(exc)) from exc


def refinement_requests(result: dict, request: AnalysisRequest, step_s: int):
    """Merge transition brackets and align them to finer 300/60-second UTC grids.

    UUIDs depend on request content. At most 32 windows are submitted per pass;
    excessive work fails explicitly. No source requests are sent by this function.
    """
    if step_s not in (300, 60):
        raise VisibilityError("Refinement step must be 300 or 60 seconds")
    windows = []
    for event in result["events"]:
        if event["bracket_width_s"] <= step_s:
            continue
        start = utc(event["bracket_start_utc"])
        end = utc(event["bracket_end_utc"])
        a = math.floor((start - request.start_utc).total_seconds() / step_s) * step_s
        b = math.ceil((end - request.start_utc).total_seconds() / step_s) * step_s
        windows.append(
            (
                max(request.start_utc, request.start_utc + timedelta(seconds=a)),
                min(request.end_utc, request.start_utc + timedelta(seconds=b)),
            )
        )
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    if len(merged) > 32:
        raise VisibilityError("Refinement exceeds 32 windows; shorten the analysis")
    jobs = []
    for start, end in merged:
        analysis = AnalysisRequest(
            site=request.site, start_utc=start, end_utc=end, step_s=step_s
        )
        identity = "lunar-visibility-refinement-v2:" + analysis.model_dump_json()
        jobs.append(
            IngestionRequest(
                request_id=uuid5(NAMESPACE_URL, identity), analysis=analysis
            )
        )
    return jobs
