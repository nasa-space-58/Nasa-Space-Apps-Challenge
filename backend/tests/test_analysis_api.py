"""Task 4 API lifecycle, persistence, input validation and observer resolution."""

import asyncio
import hashlib
import json

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from app.analysis_api import (
    JobStore,
    Submission,
    create_app,
    execute_job,
    resolve_observer,
)
from shared.constants import MOON_REFERENCE_RADIUS_M as R
from shared.ingestion import IngestionEvent


def payload(**changes):
    return (
        dict(
            site_id="malapert-massif-r23",
            latitude_deg=-85.9948,
            longitude_deg_east=359.76422,
            observer_height_m=2.0,
            start_utc="2026-11-14T00:00:00Z",
            end_utc="2026-11-15T00:00:00Z",
            refine=False,
        )
        | changes
    )


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as c:
        yield c


def test_presets_have_sources_and_same_coordinate_convention(client):
    response = client.get("/presets")
    assert response.status_code == 200
    sites = response.json()["sites"]
    assert len(sites) == 2
    assert all(
        s["runtime_preset_enabled"]
        and s["coordinate_source_url"].startswith("https://")
        for s in sites
    )
    assert all(
        0 <= s["longitude_deg_east"] < 360 and s["reference_frame"] == "MOON_ME"
        for s in sites
    )
    assert client.get("/health").json() == {"status": "ok", "service": "backend"}


def test_create_poll_result_and_missing_job(client):
    response = client.post("/analyses", json=payload())
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "pending"
    assert client.get("/analyses/" + job["id"]).json()["status"] == "pending"
    assert client.get("/analyses/" + job["id"] + "/result").status_code == 409
    assert client.get("/analyses/not-present").status_code == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"latitude_deg": -70},
        {"longitude_deg_east": 360},
        {"observer_height_m": 0},
        {"observer_height_m": 101},
        {"start_utc": "2026-11-14T00:00:00"},
        {"end_utc": "2026-11-13T00:00:00Z"},
        {"step_s": 1},
        {"extra": "bad"},
    ],
)
def test_invalid_inputs_never_queue(client, changes):
    assert client.post("/analyses", json=payload(**changes)).status_code == 422


def test_capacity_is_bounded(client):
    for _ in range(8):
        assert client.post("/analyses", json=payload()).status_code == 202
    assert client.post("/analyses", json=payload()).status_code == 429


def test_persistent_results_failures_and_restart_recovery(tmp_path):
    path = tmp_path / "jobs.sqlite"
    store = JobStore(path)
    job = store.create(Submission(**payload()))
    store.update(job["id"], "analyzing")
    restarted = JobStore(path)
    restarted.recover()
    assert restarted.get(job["id"])["status"] == "pending"
    result = {"analysis": {"sample_count": 25}, "frames": []}
    restarted.update(job["id"], "ready", result=result)
    again = JobStore(path)
    assert again.get(job["id"])["result"] == result
    again.recover()
    assert again.get(job["id"])["status"] == "ready"
    assert again.pending() == []
    failed = again.create(Submission(**payload()))
    again.update(failed["id"], "failed", error="Upstream unavailable")
    assert JobStore(path).get(failed["id"])["error"] == "Upstream unavailable"


def test_result_endpoint_ready_and_insufficient(tmp_path):
    app = create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    with TestClient(app) as c:
        for status in ["ready", "insufficient_data"]:
            job = c.post("/analyses", json=payload()).json()
            app.state.store.update(job["id"], status, result={"frames": []})
            assert c.get("/analyses/" + job["id"] + "/result").json() == {"frames": []}


def test_observer_resolution_uses_verified_pixel_and_height(tmp_path):
    root = tmp_path / "terrain/raw" / ("a" * 64)
    root.mkdir(parents=True)
    path = root / "source.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=11,
        height=11,
        count=1,
        dtype="float32",
        crs=f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m",
        transform=from_origin(-440, 440, 80, 80),
        nodata=np.nan,
    ) as ds:
        ds.write(np.full((11, 11), 100, dtype="float32"), 1)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "product_id": "LDEM_80S_80MPP_ADJ",
                "sha256": {"source.tif": hashlib.sha256(path.read_bytes()).hexdigest()},
            }
        )
    )
    req = Submission(**payload(latitude_deg=-90, longitude_deg_east=0))
    observer = resolve_observer(tmp_path, req)
    assert observer.altitude_km == pytest.approx(0.102)
    assert observer.latitude_deg == -90
    with pytest.raises(ValueError):
        resolve_observer(tmp_path, Submission(**payload(latitude_deg=-80)))
    with path.open("ab") as f:
        f.write(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        resolve_observer(tmp_path, req)


def test_missing_terrain_is_explicit(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_observer(tmp_path, Submission(**payload()))


def test_job_pipeline_and_failure_are_visible(tmp_path, monkeypatch):
    import app.analysis_api as module
    from shared.contracts import ObserverSite

    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.create(Submission(**payload()))
    site = ObserverSite(
        site_id="malapert-massif-r23",
        latitude_deg=-85.9948,
        longitude_deg_east=359.76422,
        altitude_km=4.78662451171875,
    )
    monkeypatch.setattr(module, "resolve_observer", lambda *args: site)

    async def submit(envelope, url):
        return IngestionEvent(
            request_id=envelope.request_id,
            status="ready",
            terrain_path="terrain/processed/" + "a" * 64,
            horizons_path="horizons/" + "b" * 64,
        )

    async def analyze(*args, **kwargs):
        return {
            "sun": {"metrics": {"unknown_duration_s": 0}},
            "earth": {"metrics": {"unknown_duration_s": 0}},
        }

    monkeypatch.setattr(module, "submit", submit)
    monkeypatch.setattr(module, "run_analysis", analyze)
    monkeypatch.setattr(module, "profile_from_bundle", lambda *args: {})
    monkeypatch.setattr(
        module,
        "presentation",
        lambda root, result, profile: {"analysis": result, "frames": []},
    )
    asyncio.run(execute_job(store, job, tmp_path, "amqp://test"))
    assert store.get(job["id"])["status"] == "ready"

    async def fail(*args):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(module, "submit", fail)
    job = store.create(Submission(**payload()))
    asyncio.run(execute_job(store, job, tmp_path, "amqp://test"))
    assert store.get(job["id"])["status"] == "failed"
    assert store.get(job["id"])["error"]
