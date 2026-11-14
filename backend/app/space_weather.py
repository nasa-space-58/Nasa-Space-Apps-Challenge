"""Solar flare and proton storm context for a saved analysis (NOAA SWPC).

The worker stores a checksummed SWPC snapshot; this module reports the current
GOES conditions and the SWPC 3-day forecast days that overlap the analysis
interval. Nothing is extrapolated beyond the forecast horizon.

Thresholds: NOAA Space Weather Scales (https://www.swpc.noaa.gov/noaa-scales-explanation).
Flare classes use the GOES 0.1-0.8 nm X-ray flux in W/m²; the radiation storm
scale uses >=10 MeV integral proton flux in pfu.
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
from shared.space_weather import PRODUCTS, SWPC_BASE_URL, SpaceWeatherRequest

from app.submit_space_weather import submit_space_weather

# Lower bounds of the GOES 0.1-0.8 nm flare classes in W/m².
FLARE_CLASS_BASES_W_M2 = (
    ("X", 1e-4),
    ("M", 1e-5),
    ("C", 1e-6),
    ("B", 1e-7),
    ("A", 1e-8),
)
# NOAA R1..R5 lower bounds in W/m² (M1, M5, X1, X10, X20).
RADIO_BLACKOUT_THRESHOLDS_W_M2 = (1e-5, 5e-5, 1e-4, 1e-3, 2e-3)
# NOAA S1..S5 lower bounds in pfu at >=10 MeV.
RADIATION_STORM_THRESHOLDS_PFU = (1e1, 1e2, 1e3, 1e4, 1e5)
MAX_ACTIVE_REGIONS = 5
MAX_ACTIVE_JOBS = 4
SNAPSHOT_VERSION = 1
ACTIVE = ("pending", "acquiring", "computing")
STATUSES = (*ACTIVE, "ready", "failed")
SNAPSHOT_PATH = re.compile(r"^space-weather/[0-9a-f]{64}$")
FORECAST_FIELDS = (
    ("max_daily_c_class_percent", "c_class_percent"),
    ("max_daily_m_class_percent", "m_class_percent"),
    ("max_daily_x_class_percent", "x_class_percent"),
    ("max_daily_proton_10mev_percent", "proton_10mev_percent"),
)
LIMITATIONS = (
    "Solar flares cannot be predicted with certainty; SWPC gives daily probabilities for at most 3 days.",
    "Analysis time outside the SWPC forecast days has no forecast; nothing is extrapolated.",
    "Current conditions describe the Sun when the snapshot was fetched, not at the landing time.",
    "GOES measures near Earth; proton flux is a proxy for the lunar surface, not a dose estimate.",
    "The radio blackout scale describes HF radio on Earth's sunlit side, not DSN links.",
)


def _parse_utc(value) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"Timestamp must be explicit UTC: {value}")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _flux(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"Flux must be a number: {value!r}")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Flux must be finite and non-negative: {value!r}")
    return float(value)


def flare_class(flux_w_m2: float) -> str:
    """GOES flare class such as 'B6.7' or 'X12.3' (one decimal, floored)."""
    flux = _flux(flux_w_m2)
    letter, base = FLARE_CLASS_BASES_W_M2[-1]
    for candidate, lower in FLARE_CLASS_BASES_W_M2:
        if flux >= lower:
            letter, base = candidate, lower
            break
    # Round away binary noise before flooring (6.8e-7 / 1e-7 = 6.7999...).
    tenths = math.floor(round(flux / base * 10, 6))
    if letter != "X":
        tenths = min(tenths, 99)  # never print C10.0 for flux just below M1
    return f"{letter}{tenths / 10:.1f}"


def radio_blackout_level(flux_w_m2: float) -> int:
    """NOAA R level 0-5 for a 0.1-0.8 nm X-ray flux in W/m²."""
    flux = _flux(flux_w_m2)
    return sum(flux >= lower for lower in RADIO_BLACKOUT_THRESHOLDS_W_M2)


def radiation_storm_level(flux_pfu: float) -> int:
    """NOAA S level 0-5 for a >=10 MeV integral proton flux in pfu."""
    flux = _flux(flux_pfu)
    return sum(flux >= lower for lower in RADIATION_STORM_THRESHOLDS_PFU)


def load_snapshot(data_dir: Path, snapshot_path: str) -> dict:
    """Read a verified space-weather/<sha256> bundle; ValueError on any defect."""
    if not isinstance(snapshot_path, str) or not SNAPSHOT_PATH.match(snapshot_path):
        raise ValueError(f"Invalid space weather reference: {snapshot_path!r}")
    root = Path(data_dir) / snapshot_path
    try:
        manifest = json.loads((root / "manifest.json").read_text())
        hashes = manifest["sha256"]
        if not isinstance(hashes, dict) or "snapshot.json" not in hashes:
            raise ValueError("Space weather manifest does not cover snapshot.json")
        for name, expected in hashes.items():
            target = (root / name).resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError(f"Space weather manifest path escapes bundle: {name}")
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Space weather checksum mismatch: {name}")
        snapshot = json.loads((root / "snapshot.json").read_text())
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Unreadable space weather bundle {snapshot_path}: {exc}"
        ) from exc
    if not isinstance(snapshot, dict):
        raise ValueError("Space weather snapshot must be an object")
    return snapshot


def _latest(samples: list[dict], field: str) -> dict | None:
    measured = [s for s in samples if s[field] is not None]
    return measured[-1] if measured else None


def _peak(samples: list[dict], field: str) -> dict | None:
    measured = [s for s in samples if s[field] is not None]
    return max(measured, key=lambda s: s[field]) if measured else None


def _xray(sample: dict | None) -> dict | None:
    if sample is None:
        return None
    flux = sample["flux_w_m2"]
    return {
        "time_utc": sample["time_utc"],
        "flux_w_m2": flux,
        "flare_class": flare_class(flux),
        "radio_blackout_level": radio_blackout_level(flux),
    }


def _protons(sample: dict | None) -> dict | None:
    if sample is None:
        return None
    flux = sample["flux_pfu"]
    return {
        "time_utc": sample["time_utc"],
        "flux_pfu": flux,
        "radiation_storm_level": radiation_storm_level(flux),
    }


def _forecast(probabilities: dict, start_utc: datetime, end_utc: datetime) -> dict:
    days = probabilities["days"]
    if not days:
        raise ValueError("Space weather snapshot has no forecast days")
    overlapping, covered_s = [], 0.0
    for day in days:
        lower = max(_parse_utc(day["start_utc"]), start_utc)
        upper = min(_parse_utc(day["end_utc"]), end_utc)
        if upper > lower:
            overlapping.append(dict(day))
            covered_s += (upper - lower).total_seconds()
    total_s = (end_utc - start_utc).total_seconds()
    coverage = (
        "none" if covered_s == 0 else "full" if covered_s == total_s else "partial"
    )
    result = {
        "forecast_start_utc": _iso(min(_parse_utc(d["start_utc"]) for d in days)),
        "forecast_end_utc": _iso(max(_parse_utc(d["end_utc"]) for d in days)),
        "polar_cap_absorption": probabilities.get("polar_cap_absorption"),
        "coverage": coverage,
        "covered_duration_s": covered_s,
        "days": overlapping,
    }
    for name, field in FORECAST_FIELDS:
        result[name] = max((d[field] for d in overlapping), default=None)
    return result


def _rank(region: dict) -> tuple:
    def value(key):
        return -1 if region[key] is None else region[key]

    return (
        -value("x_flare_percent"),
        -value("m_flare_percent"),
        -value("c_flare_percent"),
        region["region"],
    )


def assess_space_weather(
    snapshot: dict, start_utc: datetime, end_utc: datetime
) -> dict:
    """Current GOES conditions and forecast days overlapping [start, end).

    Result keys: acquired_at_utc, current (xray, xray_peak, protons_10mev,
    protons_10mev_peak), forecast (forecast_start_utc, forecast_end_utc,
    coverage 'full'/'partial'/'none', covered_duration_s, days and the
    max_daily_* percents, None without overlap), active_regions, limitations.
    """
    for value in (start_utc, end_utc):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Interval bounds must be timezone-aware UTC")
    if not start_utc < end_utc:
        raise ValueError("Require start_utc < end_utc")
    if snapshot.get("snapshot_version") != SNAPSHOT_VERSION:
        raise ValueError(
            f"Unsupported space weather snapshot version: {snapshot.get('snapshot_version')!r}"
        )
    xrays = snapshot["xrays_w_m2"]
    protons = snapshot["protons_10mev_pfu"]
    regions = snapshot["active_regions"]
    return {
        "acquired_at_utc": snapshot["acquired_at_utc"],
        "current": {
            "xray": _xray(_latest(xrays, "flux_w_m2")),
            "xray_peak": _xray(_peak(xrays, "flux_w_m2")),
            "protons_10mev": _protons(_latest(protons, "flux_pfu")),
            "protons_10mev_peak": _protons(_peak(protons, "flux_pfu")),
        },
        "forecast": _forecast(snapshot["flare_probabilities"], start_utc, end_utc),
        "active_regions": {
            "observed_date_utc": regions["observed_date_utc"],
            "region_count": len(regions["regions"]),
            "regions": sorted(regions["regions"], key=_rank)[:MAX_ACTIVE_REGIONS],
        },
        "limitations": list(LIMITATIONS),
    }


def space_weather_request_for(job_id: str) -> SpaceWeatherRequest:
    """Request whose UUID derives from the job ID (replay-safe recovery)."""
    return SpaceWeatherRequest(request_id=uuid5(UUID(job_id), "space-weather"))


class SpaceWeatherStore:
    """Space weather jobs in the analysis SQLite database."""

    def __init__(self, jobs):
        self.jobs = jobs
        with jobs.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS space_weather (id TEXT PRIMARY KEY, created TEXT, analysis_id TEXT, status TEXT, result TEXT, error TEXT)"
            )

    def create(self, analysis_id: str) -> dict:
        job_id = str(uuid4())
        placeholders = ",".join("?" * len(ACTIVE))
        with self.jobs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                f"SELECT count(*) FROM space_weather WHERE status IN ({placeholders})",
                ACTIVE,
            ).fetchone()[0]
            if active >= MAX_ACTIVE_JOBS:
                raise ValueError("Space weather queue is full; wait for a running job")
            db.execute(
                "INSERT INTO space_weather VALUES (?,?,?,?,NULL,NULL)",
                (
                    job_id,
                    datetime.now(timezone.utc).isoformat(),
                    analysis_id,
                    "pending",
                ),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self.jobs.connect() as db:
            row = db.execute(
                "SELECT * FROM space_weather WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["result"] = json.loads(job["result"]) if job["result"] else None
        return job

    def update(self, job_id: str, status: str, result=None, error=None) -> None:
        if status not in STATUSES:
            raise ValueError(f"Unsupported space weather status: {status}")
        with self.jobs.connect() as db:
            db.execute(
                "UPDATE space_weather SET status=?,result=?,error=? WHERE id=?",
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
                    "SELECT id FROM space_weather WHERE status='pending' ORDER BY rowid"
                )
            ]
        return [self.get(job_id) for job_id in ids]

    def recover(self) -> None:
        with self.jobs.connect() as db:
            db.execute(
                "UPDATE space_weather SET status='pending' WHERE status IN ('acquiring','computing')"
            )


async def execute_space_weather(store, analyses, job, data_dir, broker_url) -> None:
    """Acquire a snapshot via RabbitMQ and persist the assessment or the failure.

    The request UUID derives from the job ID, so a recovered job replays the
    worker's stored outcome instead of fetching a second snapshot.
    """
    job_id = job["id"]
    try:
        analysis = analyses.get(job["analysis_id"])
        if analysis is None:
            raise ValueError(f"Analysis {job['analysis_id']} not found")
        if analysis["status"] not in ("ready", "insufficient_data"):
            raise ValueError(f"Analysis {job['analysis_id']} is not complete")
        store.update(job_id, "acquiring")
        event = await submit_space_weather(
            space_weather_request_for(job_id), broker_url
        )
        if event.status != "ready":
            raise ValueError(f"Space weather acquisition failed: {event.error_message}")
        store.update(job_id, "computing")
        snapshot = load_snapshot(data_dir, event.snapshot_path)
        submission = analysis["submission"]
        start = _parse_utc(submission["start_utc"])
        end = _parse_utc(submission["end_utc"])
        result = {
            "analysis_id": job["analysis_id"],
            "start_utc": _iso(start),
            "end_utc": _iso(end),
            **assess_space_weather(snapshot, start, end),
            "source": {
                "provider": "NOAA Space Weather Prediction Center",
                "products": {
                    name: SWPC_BASE_URL + path for name, path in PRODUCTS.items()
                },
                "snapshot_path": event.snapshot_path,
                "scales": "NOAA Space Weather Scales (R: GOES 0.1-0.8 nm X-ray; S: >=10 MeV protons)",
            },
        }
        store.update(job_id, "ready", result=result)
    except Exception as exc:
        # Job boundary: failures are logged and persisted, never silently dropped.
        logging.exception("Space weather job %s failed", job_id)
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else f"{type(exc).__name__}: service unavailable; check worker logs and retry"
        )
        store.update(job_id, "failed", error=message[:600])


def register_space_weather_routes(app, analyses, store) -> None:
    """POST /analyses/{id}/space-weather and GET /space-weather/{id}."""

    @app.post("/analyses/{analysis_id}/space-weather", status_code=202)
    def create_space_weather(analysis_id: str):
        analysis = analyses.get(analysis_id)
        if analysis is None:
            raise HTTPException(404, "Analysis not found")
        if analysis["status"] not in ("ready", "insufficient_data"):
            raise HTTPException(409, "Analysis is not complete")
        try:
            return store.create(analysis_id)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.get("/space-weather/{job_id}")
    def get_space_weather(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Space weather assessment not found")
        return job
