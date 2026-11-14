"""R2 direct-to-Earth communication windows (scenario S4).

A window requires the Earth center above the local lunar terrain horizon (from a
saved analysis) and the Moon above a DSN station's elevation limit. Results are
geometric only: no station horizon masks, keyhole, scheduling or link budget.
Station rise/set times are linearly interpolated between Horizons samples.
"""

import bisect
import hashlib
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from shared.dsn import STATIONS_FILE, DsnRequest, load_stations

from app.submit_dsn import submit_dsn

DEFAULT_MIN_ELEVATION_DEG = 6.0
LIMITATIONS = (
    "Geometric line of sight only; not a guaranteed radio link.",
    "Generic DSN elevation limit; station-specific horizon masks are not applied.",
    "Antenna keyhole, station scheduling, availability and link budget are not modeled.",
    "Earth visibility refers to the Earth center above the local terrain horizon.",
)
ACTIVE = ("pending", "acquiring", "computing")
STATUSES = (*ACTIVE, "ready", "insufficient_data", "failed")
MAX_ACTIVE_JOBS = 4
EARTH_STATES = {"visible": True, "blocked": False, "boundary": False, "unknown": None}
STATION_STATES = {"above": True, "below": False, "unknown": None}
DSN_PATH = re.compile(r"^dsn/[0-9a-f]{64}$")


class CommunicationRequest(BaseModel):
    """Station elevation limit in degrees; 6.0 receive, 10.0 low-power transmit."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    min_elevation_deg: float = Field(default=DEFAULT_MIN_ELEVATION_DEG, ge=6, le=90)


def _parse_utc(value) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"Timestamp must be explicit UTC: {value}")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_window(start_utc: datetime, end_utc: datetime) -> None:
    for value in (start_utc, end_utc):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Interval bounds must be timezone-aware UTC")
    if not start_utc < end_utc:
        raise ValueError("Require start_utc < end_utc")


def dsn_request_for(analysis_job: dict, request_id) -> DsnRequest:
    """Build the station ephemeris request covering a saved analysis interval.

    The grid is 60 s for one-minute analyses and 300 s otherwise; the start is
    floored and the end ceiled to that grid so samples bracket the analysis.
    """
    submission = analysis_job["submission"]
    step_s = 60 if submission.get("step_s") == 60 else 300
    start = _parse_utc(submission["start_utc"])
    end = _parse_utc(submission["end_utc"])
    start_s = math.floor(start.timestamp() / step_s) * step_s
    end_s = math.ceil(end.timestamp() / step_s) * step_s
    return DsnRequest(
        request_id=request_id,
        start_utc=datetime.fromtimestamp(start_s, timezone.utc),
        end_utc=datetime.fromtimestamp(end_s, timezone.utc),
        step_s=step_s,
        station_ids=tuple(load_stations()),
    )


def load_dsn_samples(data_dir: Path, dsn_path: str) -> dict[str, list[dict]]:
    """Read a verified dsn/<sha256> bundle; ValueError on checksum or path errors."""
    if not isinstance(dsn_path, str) or not DSN_PATH.match(dsn_path):
        raise ValueError(f"Invalid DSN bundle reference: {dsn_path!r}")
    root = Path(data_dir) / dsn_path
    try:
        manifest = json.loads((root / "manifest.json").read_text())
        hashes = manifest["sha256"]
        if not isinstance(hashes, dict) or "samples.json" not in hashes:
            raise ValueError("DSN manifest does not cover samples.json")
        for name, expected in hashes.items():
            target = (root / name).resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError(f"DSN manifest path escapes bundle: {name}")
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError(f"DSN checksum mismatch: {name}")
        samples = json.loads((root / "samples.json").read_text())
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable DSN bundle {dsn_path}: {exc}") from exc
    if not isinstance(samples, dict):
        raise ValueError("DSN samples must map station IDs to sample lists")
    return samples


def _merge(pieces: list[tuple[datetime, datetime, str]]) -> list[dict]:
    merged: list[list] = []
    for start, end, state in pieces:
        if end <= start:
            continue
        if merged and merged[-1][2] == state and merged[-1][1] == start:
            merged[-1][1] = end
        else:
            merged.append([start, end, state])
    return [
        {
            "start_utc": _iso(a),
            "end_utc": _iso(b),
            "state": state,
            "duration_s": (b - a).total_seconds(),
        }
        for a, b, state in merged
    ]


def station_intervals(
    samples: list[dict],
    min_elevation_deg: float,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict]:
    """Split [start, end] into above/below/unknown intervals for one station.

    Elevation is linearly interpolated between samples; reaching the limit counts
    as above. A segment touching a missing elevation is unknown. Samples must be
    strictly increasing and bracket the interval, otherwise ValueError.
    """
    _require_window(start_utc, end_utc)
    if len(samples) < 2:
        raise ValueError("At least two station samples are required")
    times = [_parse_utc(s["sampled_at_utc"]) for s in samples]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Station samples must be strictly increasing in time")
    if times[0] > start_utc or times[-1] < end_utc:
        raise ValueError("Station samples do not cover the requested interval")
    limit = float(min_elevation_deg)
    elevations = [s["elevation_deg"] for s in samples]

    def clip(moment: datetime) -> datetime:
        return min(max(moment, start_utc), end_utc)

    pieces = []
    for t0, t1, a, b in zip(times, times[1:], elevations, elevations[1:]):
        if t1 <= start_utc or t0 >= end_utc:
            continue
        if a is None or b is None:
            pieces.append((clip(t0), clip(t1), "unknown"))
        elif (a - limit) * (b - limit) < 0:
            span_s = (t1 - t0).total_seconds()
            crossing = t0 + timedelta(seconds=(limit - a) * span_s / (b - a))
            first = "above" if a > limit else "below"
            second = "below" if first == "above" else "above"
            pieces.append((clip(t0), clip(crossing), first))
            pieces.append((clip(crossing), clip(t1), second))
        else:
            state = "above" if (a + b) / 2 >= limit else "below"
            pieces.append((clip(t0), clip(t1), state))
    return _merge(pieces)


def _timeline(intervals: list[dict], allowed: dict, start, end, label: str):
    """Validate a contiguous interval list covering [start, end]."""
    if not intervals:
        raise ValueError(f"{label} intervals are empty")
    rows = []
    for item in intervals:
        a, b = _parse_utc(item["start_utc"]), _parse_utc(item["end_utc"])
        if item["state"] not in allowed:
            raise ValueError(f"Unsupported {label} state: {item['state']}")
        if b <= a:
            raise ValueError(f"Empty or reversed {label} interval")
        if rows and rows[-1][1] != a:
            raise ValueError(f"{label} intervals are not contiguous")
        rows.append((a, b, allowed[item["state"]]))
    if rows[0][0] > start or rows[-1][1] < end:
        raise ValueError(f"{label} intervals do not cover the requested window")
    return rows


def _value_at(rows, starts, moment):
    return rows[bisect.bisect_right(starts, moment) - 1][2]


def _metrics(intervals: list[dict], total_s: float) -> dict:
    durations = {"available": 0.0, "unavailable": 0.0, "unknown": 0.0}
    spans, current = [], 0.0
    for item in intervals:
        durations[item["state"]] += item["duration_s"]
        if item["state"] == "available":
            current += item["duration_s"]
        elif current:
            spans.append(current)
            current = 0.0
    if current:
        spans.append(current)
    return {
        "available_duration_s": durations["available"],
        "unavailable_duration_s": durations["unavailable"],
        "unknown_duration_s": durations["unknown"],
        "available_percent_of_interval": 100.0 * durations["available"] / total_s,
        "longest_available_s": max(spans, default=0.0),
        "window_count": len(spans),
    }


def _combine(earth, station):
    if earth is False or station is False:
        return "unavailable"
    if earth is True and station is True:
        return "available"
    return "unknown"


def communication_windows(
    earth_intervals: list[dict],
    stations: dict[str, list[dict]],
    start_utc: datetime,
    end_utc: datetime,
) -> dict:
    """Intersect Earth visibility with each station and combine all stations.

    Per station: available only when Earth is visible and the station is above
    its limit; unavailable when either is definitively false; otherwise unknown.
    Combined: available when any station is available (listing which ones),
    unknown when none is available but some are unknown, else unavailable.
    """
    _require_window(start_utc, end_utc)
    if not stations:
        raise ValueError("At least one station timeline is required")
    earth = _timeline(earth_intervals, EARTH_STATES, start_utc, end_utc, "Earth")
    timelines = {
        sid: _timeline(rows, STATION_STATES, start_utc, end_utc, sid)
        for sid, rows in sorted(stations.items())
    }
    cuts = {start_utc, end_utc}
    for rows in [earth, *timelines.values()]:
        cuts |= {t for a, b, _ in rows for t in (a, b) if start_utc < t < end_utc}
    edges = sorted(cuts)
    earth_starts = [a for a, _, _ in earth]
    starts = {sid: [a for a, _, _ in rows] for sid, rows in timelines.items()}
    per_station = {sid: [] for sid in timelines}
    combined = []
    for a, b in zip(edges, edges[1:]):
        middle = a + (b - a) / 2
        earth_value = _value_at(earth, earth_starts, middle)
        states = {
            sid: _combine(earth_value, _value_at(rows, starts[sid], middle))
            for sid, rows in timelines.items()
        }
        for sid, state in states.items():
            per_station[sid].append((a, b, state))
        available = sorted(sid for sid, s in states.items() if s == "available")
        state = (
            "available"
            if available
            else "unknown"
            if "unknown" in states.values()
            else "unavailable"
        )
        if combined and combined[-1][2] == state and combined[-1][3] == available:
            combined[-1][1] = b
        else:
            combined.append([a, b, state, available])
    total_s = (end_utc - start_utc).total_seconds()
    station_view = {}
    for sid, pieces in per_station.items():
        intervals = _merge(pieces)
        station_view[sid] = {
            "intervals": intervals,
            "metrics": _metrics(intervals, total_s),
        }
    combined_intervals = [
        {
            "start_utc": _iso(a),
            "end_utc": _iso(b),
            "state": state,
            "duration_s": (b - a).total_seconds(),
            "station_ids": ids,
        }
        for a, b, state, ids in combined
    ]
    return {
        "stations": station_view,
        "combined": {
            "intervals": combined_intervals,
            "metrics": _metrics(combined_intervals, total_s),
        },
    }


class CommunicationStore:
    """Communication jobs in the analysis SQLite database."""

    def __init__(self, jobs):
        self.jobs = jobs
        with jobs.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS communications (id TEXT PRIMARY KEY, created TEXT, analysis_id TEXT, request TEXT, status TEXT, result TEXT, error TEXT)"
            )

    def create(self, analysis_id: str, request: CommunicationRequest) -> dict:
        job_id = str(uuid4())
        placeholders = ",".join("?" * len(ACTIVE))
        with self.jobs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                f"SELECT count(*) FROM communications WHERE status IN ({placeholders})",
                ACTIVE,
            ).fetchone()[0]
            if active >= MAX_ACTIVE_JOBS:
                raise ValueError("Communication queue is full; wait for a running job")
            db.execute(
                "INSERT INTO communications VALUES (?,?,?,?,?,NULL,NULL)",
                (
                    job_id,
                    datetime.now(timezone.utc).isoformat(),
                    analysis_id,
                    request.model_dump_json(),
                    "pending",
                ),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self.jobs.connect() as db:
            row = db.execute(
                "SELECT * FROM communications WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["request"] = json.loads(job["request"])
        job["result"] = json.loads(job["result"]) if job["result"] else None
        return job

    def update(self, job_id: str, status: str, result=None, error=None) -> None:
        if status not in STATUSES:
            raise ValueError(f"Unsupported communication status: {status}")
        with self.jobs.connect() as db:
            db.execute(
                "UPDATE communications SET status=?,result=?,error=? WHERE id=?",
                (
                    status,
                    json.dumps(result, allow_nan=False) if result is not None else None,
                    error,
                    job_id,
                ),
            )

    def pending(self) -> list[dict]:
        with self.jobs.connect() as db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM communications WHERE status='pending' ORDER BY rowid"
                )
            ]
        return [self.get(job_id) for job_id in ids]

    def recover(self) -> None:
        with self.jobs.connect() as db:
            db.execute(
                "UPDATE communications SET status='pending' WHERE status IN ('acquiring','computing')"
            )


async def execute_communication(store, analyses, job, data_dir, broker_url) -> None:
    """Acquire station ephemerides via RabbitMQ and persist the windows.

    The DSN request UUID derives from the job ID, so a recovered job replays the
    worker's stored outcome instead of creating duplicate work.
    """
    job_id = job["id"]
    try:
        analysis = analyses.get(job["analysis_id"])
        if analysis is None:
            raise ValueError(f"Analysis {job['analysis_id']} not found")
        if analysis["status"] not in ("ready", "insufficient_data"):
            raise ValueError(f"Analysis {job['analysis_id']} is not complete")
        request = CommunicationRequest.model_validate(job["request"])
        store.update(job_id, "acquiring")
        dsn_request = dsn_request_for(analysis, uuid5(UUID(job_id), "dsn"))
        event = await submit_dsn(dsn_request, broker_url)
        if event.status != "ready":
            raise ValueError(
                f"Station ephemeris acquisition failed: {event.error_message}"
            )
        store.update(job_id, "computing")
        samples = load_dsn_samples(data_dir, event.dsn_path)
        submission = analysis["submission"]
        start = _parse_utc(submission["start_utc"])
        end = _parse_utc(submission["end_utc"])
        missing = set(dsn_request.station_ids) - set(samples)
        if missing:
            raise ValueError(f"DSN bundle lacks station(s): {sorted(missing)}")
        timelines = {
            sid: station_intervals(samples[sid], request.min_elevation_deg, start, end)
            for sid in dsn_request.station_ids
        }
        earth = analysis["result"]["analysis"]["earth"]["intervals"]
        windows = communication_windows(earth, timelines, start, end)
        stations = load_stations()
        source = json.loads(STATIONS_FILE.read_text())["source"]
        result = {
            "analysis_id": job["analysis_id"],
            "min_elevation_deg": request.min_elevation_deg,
            "start_utc": _iso(start),
            "end_utc": _iso(end),
            "stations": [
                stations[sid].model_dump(mode="json") for sid in dsn_request.station_ids
            ],
            "windows": windows,
            "source": {
                "station_document": source["document"],
                "station_url": source["url"],
                "ephemeris": "NASA/JPL Horizons API, Moon (301) from coord@399, airless",
                "dsn_path": event.dsn_path,
                "sample_step_s": dsn_request.step_s,
                "interpolation": "Linear in elevation between station samples",
            },
            "limitations": list(LIMITATIONS),
        }
        unknown = windows["combined"]["metrics"]["unknown_duration_s"]
        store.update(
            job_id, "insufficient_data" if unknown > 0 else "ready", result=result
        )
    except Exception as exc:
        # Job boundary: failures are logged and persisted, never silently dropped.
        logging.exception("Communication job %s failed", job_id)
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else f"{type(exc).__name__}: service unavailable; check worker logs and retry"
        )
        store.update(job_id, "failed", error=message[:600])


def register_communication_routes(app, analyses, communications) -> None:
    """POST /analyses/{id}/communication and GET /communications/{id}."""

    @app.post("/analyses/{analysis_id}/communication", status_code=202)
    def create_communication(analysis_id: str, body: CommunicationRequest):
        analysis = analyses.get(analysis_id)
        if analysis is None:
            raise HTTPException(404, "Analysis not found")
        if analysis["status"] not in ("ready", "insufficient_data"):
            raise HTTPException(409, "Analysis is not complete")
        try:
            return communications.create(analysis_id, body)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.get("/communications/{job_id}")
    def get_communication(job_id: str):
        job = communications.get(job_id)
        if job is None:
            raise HTTPException(404, "Communication analysis not found")
        return job
