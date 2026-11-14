"""Persistent local analysis jobs and read-only terrain observer resolution."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import sqlite3
from uuid import UUID, uuid4, uuid5

import numpy as np
import rasterio
from rasterio.warp import transform
from fastapi import FastAPI, HTTPException
from app.horizon import profile_from_bundle, interpolate_horizon, _validate_dataset
from app.analyze_visibility import run_analysis
from app.submit_ingestion import submit
from app.visibility import load_ephemeris, _next_setting, utc
from shared.constants import MOON_REFERENCE_RADIUS_M
from shared.ingestion import IngestionRequest
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shared.contracts import AnalysisRequest, ObserverSite
from shared.horizon import HorizonSettings


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = "custom"
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    observer_height_m: float = Field(default=2, ge=0.1, le=100)
    start_utc: datetime
    end_utc: datetime
    step_s: int = 3600
    refine: bool = True
    horizon: HorizonSettings = Field(default_factory=HorizonSettings)

    @model_validator(mode="after")
    def validate_analysis(self):
        AnalysisRequest(
            site=ObserverSite(
                site_id=self.site_id,
                latitude_deg=self.latitude_deg,
                longitude_deg_east=self.longitude_deg_east,
                altitude_km=0.0,
            ),
            start_utc=self.start_utc,
            end_utc=self.end_utc,
            step_s=self.step_s,
        )
        return self


ACTIVE = ("pending", "preparing", "analyzing", "refining")


class JobStore:
    """SQLite status/results in a separate writable application-state volume."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created TEXT, status TEXT, submission TEXT, result TEXT, error TEXT)"
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def create(self, submission: Submission) -> dict:
        job_id = str(uuid4())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('pending','preparing','analyzing','refining')"
            ).fetchone()[0]
            if count >= 8:
                raise ValueError("Analysis queue is full; wait for a running job")
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,NULL,NULL)",
                (
                    job_id,
                    datetime.now(timezone.utc).isoformat(),
                    "pending",
                    submission.model_dump_json(),
                ),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["submission"] = json.loads(result["submission"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def update(self, job_id: str, status: str, result=None, error=None):
        if status not in (*ACTIVE, "ready", "insufficient_data", "failed"):
            raise ValueError("Unsupported job status")
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?,result=?,error=? WHERE id=?",
                (
                    status,
                    json.dumps(result, allow_nan=False) if result is not None else None,
                    error,
                    job_id,
                ),
            )

    def recover(self):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status='pending' WHERE status IN ('preparing','analyzing','refining')"
            )

    def pending(self) -> list:
        with self.connect() as db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM jobs WHERE status='pending' ORDER BY created"
                )
            ]
        return [self.get(job_id) for job_id in ids]


def presets() -> list:
    """Source-backed MOON_ME presets, with provenance and provisional AGL inputs."""
    return json.loads(
        (Path(__file__).resolve().parents[2] / "shared/presets.json").read_text()
    )["sites"]


def resolve_observer(data_dir: Path, submission: Submission) -> ObserverSite:
    """Resolve AGL meters to reference-sphere km from a verified cached native pixel.

    FileNotFoundError means acquisition is needed. ValueError denotes incompatible,
    corrupt or missing terrain. This function never downloads or changes data.
    """
    candidates = sorted(Path(data_dir).glob("terrain/raw/*/source.tif"))
    if not candidates:
        raise FileNotFoundError(
            "Terrain not cached yet; start a preset analysis to acquire it"
        )
    path = candidates[0]
    manifest = json.loads((path.parent / "manifest.json").read_text())
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != manifest["sha256"]["source.tif"]:
        raise ValueError("Terrain checksum mismatch")
    if manifest["product_id"] != "LDEM_80S_80MPP_ADJ":
        raise ValueError("Unsupported terrain product")
    with rasterio.open(path) as ds:
        _validate_dataset(ds)
        x, y = transform(
            f"+proj=longlat +R={MOON_REFERENCE_RADIUS_M}",
            ds.crs,
            [submission.longitude_deg_east],
            [submission.latitude_deg],
        )
        row, col = ds.index(x[0], y[0])
        if not 0 <= row < ds.height or not 0 <= col < ds.width:
            raise ValueError("Coordinates outside terrain coverage")
        value = ds.read(1, window=rasterio.windows.Window(col, row, 1, 1), masked=True)[
            0, 0
        ]
        if np.ma.is_masked(value) or not np.isfinite(value):
            raise ValueError("No terrain height at selected coordinates")
    return ObserverSite(
        site_id=submission.site_id,
        latitude_deg=submission.latitude_deg,
        longitude_deg_east=submission.longitude_deg_east,
        altitude_km=(float(value) + submission.observer_height_m) / 1000,
    )


def presentation(data_dir: Path, result: dict, profile: dict) -> dict:
    """Prepare scientific playback states in Python; UI only renders these frames."""
    rows = []
    for source in result["provenance"]["ephemerides"]:
        request, new, _ = load_ephemeris(data_dir, source["reference"])
        rows = [
            row
            for row in rows
            if not request.start_utc <= utc(row["sampled_at_utc"]) <= request.end_utc
        ] + new
    rows.sort(key=lambda row: utc(row["sampled_at_utc"]))
    end = utc(result["request"]["end_utc"])
    frames = []
    for row in rows:
        time = utc(row["sampled_at_utc"])
        if time >= end:
            continue
        frame = {"utc": row["sampled_at_utc"]}
        for target in ("sun", "earth"):
            az, el = row[f"{target}_azimuth_deg"], row[f"{target}_elevation_deg"]
            horizon = interpolate_horizon(profile, az) if az is not None else None
            clearance = el - horizon if el is not None and horizon is not None else None
            state = "unknown"
            if clearance is not None:
                if target == "earth":
                    state = (
                        "visible"
                        if clearance > 0
                        else "blocked"
                        if clearance < 0
                        else "boundary"
                    )
                else:
                    diameter = row["sun_angular_diameter_arcsec"]
                    if diameter and diameter > 0:
                        radius = diameter / 7200
                        state = (
                            "full"
                            if clearance > radius
                            else "dark"
                            if clearance < -radius
                            else "partial"
                            if -radius < clearance < radius
                            else "boundary"
                        )
            frame[target] = {
                "azimuth_deg": az,
                "elevation_deg": el,
                "horizon_deg": horizon,
                "state": state,
                "next_setting": _next_setting(
                    result["events"],
                    result[target]["intervals"],
                    time,
                    end,
                    "sunset" if target == "sun" else "earthset",
                ),
            }
        frames.append(frame)
    return {"analysis": result, "horizon": profile, "frames": frames}


async def execute_job(store, job, data_dir, broker_url):
    """Persist phase/failure states; immutable ingestion IDs survive app restarts."""
    job_id = job["id"]
    try:
        body = Submission.model_validate(job["submission"])
        store.update(job_id, "preparing")
        try:
            site = resolve_observer(data_dir, body)
        except FileNotFoundError:
            # Bootstrap the fixed documented observer only to populate the raw cache.
            # Its ephemerides never stand in for the user's requested coordinates.
            preset = presets()[-1]
            baseline = AnalysisRequest(
                site=ObserverSite(
                    site_id=preset["site_id"],
                    latitude_deg=preset["latitude_deg"],
                    longitude_deg_east=preset["longitude_deg_east"],
                    altitude_km=preset["provisional_horizons_altitude_km"],
                ),
                start_utc="2026-11-14T00:00:00Z",
                end_utc="2026-11-15T00:00:00Z",
            )
            boot = await submit(
                IngestionRequest(
                    request_id=uuid5(UUID(job_id), "bootstrap"), analysis=baseline
                ),
                broker_url,
            )
            if boot.status != "ready":
                raise ValueError(f"Terrain acquisition failed: {boot.error_message}")
            site = resolve_observer(data_dir, body)
        request = AnalysisRequest(
            site=site,
            start_utc=body.start_utc,
            end_utc=body.end_utc,
            step_s=body.step_s,
        )
        event = await submit(
            IngestionRequest(
                request_id=uuid5(UUID(job_id), "analysis"), analysis=request
            ),
            broker_url,
        )
        if event.status != "ready":
            raise ValueError(f"Data acquisition failed: {event.error_message}")
        store.update(job_id, "refining" if body.refine else "analyzing")
        result = await run_analysis(
            data_dir,
            event.terrain_path,
            event.horizons_path,
            body.horizon,
            refine=body.refine,
            broker_url=broker_url,
        )
        profile = profile_from_bundle(data_dir, event.terrain_path, body.horizon)
        view = presentation(data_dir, result, profile)
        status = (
            "insufficient_data"
            if any(
                result[target]["metrics"]["unknown_duration_s"] > 0
                for target in ("sun", "earth")
            )
            else "ready"
        )
        store.update(job_id, status, result=view)
    except Exception as exc:
        # Job boundary: failures are logged and persisted, never silently dropped.
        logging.exception("Analysis job %s failed", job_id)
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else f"{type(exc).__name__}: service unavailable; check worker logs and retry"
        )
        store.update(job_id, "failed", error=message[:600])


def create_app(state_dir=None, data_dir=None, broker_url=None, start_worker=True):
    """Single-worker local API; SQLite lives outside the read-only acquisition cache."""
    state_dir = Path(
        state_dir or os.environ.get("STATE_DIR", "/tmp/lunar-analysis-state")
    )
    data_dir = Path(data_dir or os.environ.get("DATA_DIR", "/data"))
    broker_url = broker_url or os.environ.get("RABBITMQ_URL", "")
    store = JobStore(state_dir / "jobs.sqlite")
    from app.window_search import SearchStore, register_search_routes

    searches = SearchStore(store)
    from app.communication import (
        CommunicationStore,
        execute_communication,
        register_communication_routes,
    )

    communications = CommunicationStore(store)
    from app.recommendation import (
        RecommendationStore,
        register_recommendation_routes,
    )

    recommendations = RecommendationStore(store, communications)

    async def worker():
        while True:
            searches.tick()
            await asyncio.to_thread(recommendations.tick, data_dir)
            jobs = store.pending()
            links = [] if jobs else communications.pending()
            if jobs:
                await asyncio.to_thread(
                    lambda: asyncio.run(
                        execute_job(store, jobs[0], data_dir, broker_url)
                    )
                )
            elif links:
                await asyncio.to_thread(
                    lambda: asyncio.run(
                        execute_communication(
                            communications, store, links[0], data_dir, broker_url
                        )
                    )
                )
            else:
                await asyncio.sleep(0.5)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        communications.recover()
        task = asyncio.create_task(worker()) if start_worker else None
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Lunar Landing Navigator", lifespan=lifespan)
    app.state.store = store
    app.state.searches = searches
    app.state.communications = communications
    app.state.recommendations = recommendations
    register_search_routes(app, searches)
    register_communication_routes(app, store, communications)
    register_recommendation_routes(app, recommendations)
    from app.descent import register_descent_routes

    register_descent_routes(app, data_dir)
    from app.fuel_optimization import register_fuel_routes

    register_fuel_routes(app, data_dir)
    from app.risk import register_risk_routes

    register_risk_routes(app, data_dir)
    from app.alternatives import register_alternative_routes

    register_alternative_routes(app, data_dir)
    from app.surface_routes import register_surface_routes

    register_surface_routes(app, data_dir, store)
    from app.twin import register_twin_routes

    register_twin_routes(app, data_dir)

    from app.solver_arena import register_arena_routes

    register_arena_routes(app)
    from app.shared_antenna import register_campaign_routes

    register_campaign_routes(app)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "backend"}

    @app.get("/presets")
    def get_presets():
        return {"sites": presets()}

    @app.post("/observers/resolve")
    def resolve(body: Submission):
        try:
            return resolve_observer(data_dir, body).model_dump(mode="json")
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/analyses", status_code=202)
    def create(body: Submission):
        try:
            job = store.create(body)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc
        return {k: v for k, v in job.items() if k != "result"}

    @app.get("/analyses/{job_id}")
    def status(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Analysis not found")
        return {k: v for k, v in job.items() if k != "result"}

    @app.get("/analyses/{job_id}/result")
    def result(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Analysis not found")
        if job["status"] not in ("ready", "insufficient_data"):
            raise HTTPException(409, {"status": job["status"], "error": job["error"]})
        return job["result"]

    from app.comparison import register_comparison_routes

    register_comparison_routes(app, store)
    from app.release_r1 import register_r1_routes

    register_r1_routes(app, store, searches)
    return app
