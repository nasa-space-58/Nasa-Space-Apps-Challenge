"""Real Moon/Sun positions and Earth rotation for the illustrative flight.

The flight is placed so it lands at a chosen UTC time, using Apollo 11 elapsed
times for its phases. Moon and Sun positions are JPL Horizons geocentric
vectors (ecliptic of J2000.0, km); Earth's rotation is the IERS Earth Rotation
Angle with UT1 approximated by UTC. The spacecraft path itself stays
illustrative and is drawn by the interface.
"""

import hashlib
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from shared.constants import (
    ERA_AT_J2000_TURNS,
    ERA_RATE_TURNS_PER_UT1_DAY,
)
from shared.flight_ephemeris import FlightEphemerisRequest, load_mission_timeline

from app.submit_flight_ephemeris import submit_flight_ephemeris

STEP_S = 1800
PHASE_IDS = ("launch", "parking_orbit", "translunar_coast", "lunar_orbit", "descent")
EARLIEST_LANDING_UTC = "2020-01-01T00:00:00Z"
LATEST_LANDING_UTC = "2030-01-01T00:00:00Z"
LIMITATIONS = (
    "Moon and Sun positions are JPL Horizons geocentric vectors; the spacecraft path is illustrative, not a computed trajectory.",
    "Phase durations follow Apollo 11 (launch to landing 102 h 45 min 40 s); a real mission's timeline differs.",
    "Earth rotation uses the IERS Earth Rotation Angle with UTC for UT1 (|UT1-UTC| < 0.9 s).",
    "The Moon is shown in synchronous rotation facing Earth; libration is not modelled.",
)
ACTIVE = ("pending", "acquiring", "computing")
STATUSES = (*ACTIVE, "ready", "failed")
MAX_ACTIVE_JOBS = 4
PROVIDER = "NASA/JPL Horizons API (DE441), VECTORS from 500@399"
REFERENCE = re.compile(r"^flight_ephemeris/([0-9a-f]{64})$")
J2000_UT1 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
# Sanity bounds on the geocentric Moon distance (perigee/apogee extremes ~356 400
# and ~406 700 km); anything outside indicates corrupt or mislabelled data.
MOON_DISTANCE_RANGE_KM = (350_000.0, 410_000.0)
_BODY_NAMES = ("moon", "sun")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"Timestamp must be UTC: {value}")
    return parsed


def _iso_ms(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _iso_s(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must carry a UTC offset")
    return value.astimezone(timezone.utc)


class FlightEphemerisDataError(ValueError):
    """Missing, corrupt or inconsistent cached flight ephemeris."""


class FlightRequest(BaseModel):
    """Landing time (explicit UTC offset, whole seconds) the flight ends at."""

    model_config = ConfigDict(extra="forbid")
    landing_utc: datetime

    @field_validator("landing_utc")
    @classmethod
    def require_supported_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("landing_utc must include UTC (Z or +00:00)")
        if value.microsecond:
            raise ValueError("landing_utc must be a whole UTC second")
        if not _parse_utc(EARLIEST_LANDING_UTC) <= value < _parse_utc(LATEST_LANDING_UTC):
            raise ValueError("landing_utc must be in 2020-2029")
        return value


def earth_rotation_deg(utc: datetime) -> float:
    """IERS Earth Rotation Angle in degrees [0, 360) with UT1 = UTC."""
    days = (_require_aware(utc) - J2000_UT1).total_seconds() / 86400
    # Split the rate so the large whole-turn part does not cost precision.
    turns = ERA_AT_J2000_TURNS + days + (ERA_RATE_TURNS_PER_UT1_DAY - 1) * days
    return (turns % 1.0) * 360.0


def mission_window(landing_utc: datetime) -> dict:
    """Launch time and UTC bounds of each flight phase ending at landing_utc."""
    landing = _require_aware(landing_utc)
    timeline = load_mission_timeline()
    total_s = timeline["landing"]
    launch = landing - timedelta(seconds=total_s)
    elapsed = [
        0.0,
        timeline["earth_orbit_insertion"],
        timeline["translunar_injection"],
        timeline["lunar_orbit_insertion"],
        timeline["powered_descent_initiation"],
    ]
    bounds = [_iso_ms(launch + timedelta(seconds=s)) for s in elapsed] + [_iso_ms(landing)]
    return {
        "launch_utc": bounds[0],
        "landing_utc": bounds[-1],
        "launch_to_landing_s": total_s,
        "phases": [
            {"id": pid, "start_utc": bounds[i], "end_utc": bounds[i + 1]}
            for i, pid in enumerate(PHASE_IDS)
        ],
        "timeline_reference": "Apollo 11 Mission Report (MSC-00171), Table 3-I",
    }


def ephemeris_request_for(landing_utc: datetime, request_id: UUID) -> FlightEphemerisRequest:
    """Request on the STEP_S grid covering launch to landing."""
    landing = _require_aware(landing_utc)
    launch = _parse_utc(mission_window(landing)["launch_utc"])
    start_s = math.floor(launch.timestamp() / STEP_S) * STEP_S
    end_s = math.ceil(landing.timestamp() / STEP_S) * STEP_S
    return FlightEphemerisRequest(
        request_id=request_id,
        start_utc=datetime.fromtimestamp(start_s, timezone.utc),
        end_utc=datetime.fromtimestamp(end_s, timezone.utc),
        step_s=STEP_S,
    )


def load_flight_ephemeris(data_dir: Path, path: str) -> dict[str, list[dict]]:
    """Verified rows per body ('moon', 'sun') from a cached bundle."""
    match = REFERENCE.match(path) if isinstance(path, str) else None
    if not match:
        raise FlightEphemerisDataError(f"Invalid flight ephemeris reference {path!r}")
    root = Path(data_dir) / path
    try:
        manifest = json.loads((root / "manifest.json").read_text())
        if manifest["cache_key"] != match.group(1):
            raise FlightEphemerisDataError("Flight ephemeris manifest key mismatch")
        hashes = manifest["sha256"]
        if "samples.json" not in hashes:
            raise FlightEphemerisDataError("Flight ephemeris bundle lacks samples")
        for name, expected in hashes.items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                raise FlightEphemerisDataError(f"Flight ephemeris checksum mismatch: {name}")
        samples = json.loads((root / "samples.json").read_text())
    except FlightEphemerisDataError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise FlightEphemerisDataError(f"Cannot read flight ephemeris {path}: {exc}") from exc
    if not isinstance(samples, dict) or any(
        not isinstance(samples.get(body), list) for body in _BODY_NAMES
    ):
        raise FlightEphemerisDataError("Flight ephemeris must hold moon and sun rows")
    return {body: samples[body] for body in _BODY_NAMES}


def _vector(row: dict) -> list[float]:
    try:
        values = [float(row["x_km"]), float(row["y_km"]), float(row["z_km"])]
    except (KeyError, TypeError, ValueError) as exc:
        raise FlightEphemerisDataError(f"Malformed ephemeris row: {row}") from exc
    if not all(math.isfinite(v) for v in values):
        raise FlightEphemerisDataError("Ephemeris row has a non-finite position")
    return values


def flight_result(landing_utc: datetime, rows: dict[str, list[dict]]) -> dict:
    """Window, aligned samples with Earth rotation, frame and limitations."""
    try:
        moon, sun = rows["moon"], rows["sun"]
    except (KeyError, TypeError) as exc:
        raise FlightEphemerisDataError("Ephemeris lacks moon or sun rows") from exc
    if not moon or [r.get("utc") for r in moon] != [r.get("utc") for r in sun]:
        raise FlightEphemerisDataError("Moon and Sun samples are not aligned")
    samples = []
    for moon_row, sun_row in zip(moon, sun):
        moon_km, sun_km = _vector(moon_row), _vector(sun_row)
        distance = math.hypot(*moon_km)
        if not MOON_DISTANCE_RANGE_KM[0] <= distance <= MOON_DISTANCE_RANGE_KM[1]:
            raise FlightEphemerisDataError(f"Implausible Moon distance {distance:.0f} km")
        try:
            moment = _parse_utc(moon_row["utc"])
        except ValueError as exc:
            raise FlightEphemerisDataError(str(exc)) from exc
        samples.append(
            {
                "utc": moon_row["utc"],
                "moon_geocentric_km": moon_km,
                "sun_geocentric_km": sun_km,
                "earth_rotation_deg": earth_rotation_deg(moment),
            }
        )
    return {
        "window": mission_window(landing_utc),
        "frame": {
            "center": "Earth (399)",
            "plane": "Ecliptic of J2000.0",
            "units": "km",
            "axes": "x towards the J2000 equinox, z towards ecliptic north",
        },
        "samples": samples,
        "limitations": list(LIMITATIONS),
    }


class FlightEphemerisStore:
    """Flight ephemeris jobs in the analysis SQLite database."""

    def __init__(self, jobs):
        self.jobs = jobs
        with jobs.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS flight_ephemeris_jobs (id TEXT PRIMARY KEY, created TEXT, landing_utc TEXT, status TEXT, result TEXT, error TEXT)"
            )

    def create(self, landing_utc: datetime) -> dict:
        landing = _iso_s(_require_aware(landing_utc))
        reusable = ",".join("?" * (len(ACTIVE) + 1))
        active = ",".join("?" * len(ACTIVE))
        with self.jobs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                f"SELECT id FROM flight_ephemeris_jobs WHERE landing_utc=? AND status IN ({reusable}) ORDER BY rowid DESC LIMIT 1",
                (landing, *ACTIVE, "ready"),
            ).fetchone()
            if existing:
                job_id = existing[0]
            else:
                count = db.execute(
                    f"SELECT count(*) FROM flight_ephemeris_jobs WHERE status IN ({active})",
                    ACTIVE,
                ).fetchone()[0]
                if count >= MAX_ACTIVE_JOBS:
                    raise ValueError("Flight ephemeris queue is full; wait for a running job")
                job_id = str(uuid4())
                db.execute(
                    "INSERT INTO flight_ephemeris_jobs VALUES (?,?,?,?,NULL,NULL)",
                    (job_id, datetime.now(timezone.utc).isoformat(), landing, "pending"),
                )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self.jobs.connect() as db:
            row = db.execute(
                "SELECT * FROM flight_ephemeris_jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["result"] = json.loads(job["result"]) if job["result"] else None
        return job

    def update(self, job_id: str, status: str, result=None, error=None) -> None:
        if status not in STATUSES:
            raise ValueError(f"Unsupported flight ephemeris status: {status}")
        with self.jobs.connect() as db:
            db.execute(
                "UPDATE flight_ephemeris_jobs SET status=?,result=?,error=? WHERE id=?",
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
                    "SELECT id FROM flight_ephemeris_jobs WHERE status='pending' ORDER BY rowid"
                )
            ]
        return [self.get(job_id) for job_id in ids]

    def recover(self) -> None:
        with self.jobs.connect() as db:
            db.execute(
                "UPDATE flight_ephemeris_jobs SET status='pending' WHERE status IN ('acquiring','computing')"
            )


async def execute_flight_ephemeris(store, job, data_dir, broker_url) -> None:
    """Acquire vectors via RabbitMQ and persist the flight result.

    The request UUID derives from the job ID, so a recovered job replays the
    worker's stored outcome instead of creating duplicate work.
    """
    job_id = job["id"]
    try:
        landing = _parse_utc(job["landing_utc"])
        store.update(job_id, "acquiring")
        request = ephemeris_request_for(landing, uuid5(UUID(job_id), "flight-ephemeris"))
        event = await submit_flight_ephemeris(request, broker_url)
        if event.status != "ready":
            raise ValueError(f"Flight ephemeris acquisition failed: {event.error_message}")
        store.update(job_id, "computing")
        rows = load_flight_ephemeris(data_dir, event.ephemeris_path)
        result = flight_result(landing, rows) | {
            "source": {
                "provider": PROVIDER,
                "ephemeris_path": event.ephemeris_path,
                "step_s": request.step_s,
                "interpolation": "Linear between samples for display",
            }
        }
        store.update(job_id, "ready", result=result)
    except Exception as exc:
        # Job boundary: failures are logged and persisted, never silently dropped.
        logging.exception("Flight ephemeris job %s failed", job_id)
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else f"{type(exc).__name__}: service unavailable; check worker logs and retry"
        )
        store.update(job_id, "failed", error=message[:600])


def register_flight_ephemeris_routes(app, store) -> None:
    """POST /flight-ephemeris and GET /flight-ephemeris/{job_id}."""

    @app.post("/flight-ephemeris", status_code=202)
    def create_flight_ephemeris(body: FlightRequest):
        try:
            return store.create(body.landing_utc)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.get("/flight-ephemeris/{job_id}")
    def get_flight_ephemeris(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Flight ephemeris job not found")
        return job
