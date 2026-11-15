"""Flight ephemeris: Earth rotation, Apollo 11 mission window, bundle loading,
result assembly, jobs and API.

ERA reference: IERS Conventions (2010) eq. 5.15 gives 2*pi*0.7790572732640 rad
(280.46061837504 deg) at JD_UT1 2451545.0 (2000-01-01 12:00 UT1), and the
angle repeats after one sidereal rotation of 86400 / 1.00273781191135448 s.
"""

import asyncio
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.flight_ephemeris as module
from app.analysis_api import JobStore, create_app
from app.flight_ephemeris import (
    LIMITATIONS,
    PHASE_IDS,
    STEP_S,
    FlightEphemerisDataError,
    FlightEphemerisStore,
    earth_rotation_deg,
    ephemeris_request_for,
    execute_flight_ephemeris,
    flight_result,
    load_flight_ephemeris,
    mission_window,
)
from shared.flight_ephemeris import (
    FlightEphemerisEvent,
    FlightEphemerisRequest,
    load_mission_timeline,
)

UTC = timezone.utc
LANDING = datetime(2026, 11, 4, tzinfo=UTC)
LAUNCH_TO_LANDING_S = 102 * 3600 + 45 * 60 + 39.9


def parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() == timedelta(0)
    return parsed


# ---------------------------------------------------------------- Earth rotation


def test_era_at_j2000():
    assert earth_rotation_deg(datetime(2000, 1, 1, 12, tzinfo=UTC)) == pytest.approx(
        280.46061837504, abs=1e-8
    )


def test_era_repeats_after_one_sidereal_rotation():
    start = datetime(2026, 11, 4, tzinfo=UTC)
    sidereal_s = 86400 / 1.00273781191135448
    later = start + timedelta(seconds=sidereal_s)
    delta = (earth_rotation_deg(later) - earth_rotation_deg(start) + 180) % 360 - 180
    assert abs(delta) < 1e-6


def test_era_advances_about_361_degrees_per_solar_day():
    a = earth_rotation_deg(LANDING)
    b = earth_rotation_deg(LANDING + timedelta(hours=6))
    assert (b - a) % 360 == pytest.approx(90.2464, abs=1e-3)


def test_era_is_normalized_and_requires_utc():
    for hours in range(0, 48, 5):
        value = earth_rotation_deg(LANDING + timedelta(hours=hours))
        assert 0 <= value < 360
    with pytest.raises(ValueError):
        earth_rotation_deg(datetime(2026, 11, 4))


# ---------------------------------------------------------------- mission window


def test_mission_window_follows_apollo_11_elapsed_times():
    window = mission_window(LANDING)
    timeline = load_mission_timeline()
    launch = parse(window["launch_utc"])
    assert abs((LANDING - launch).total_seconds() - LAUNCH_TO_LANDING_S) < 1e-3
    assert window["launch_to_landing_s"] == pytest.approx(LAUNCH_TO_LANDING_S)
    assert parse(window["landing_utc"]) == LANDING
    phases = window["phases"]
    assert [p["id"] for p in phases] == list(PHASE_IDS)
    assert parse(phases[0]["start_utc"]) == launch
    assert parse(phases[-1]["end_utc"]) == LANDING
    for left, right in zip(phases, phases[1:]):
        assert left["end_utc"] == right["start_utc"]
    boundaries = [parse(p["end_utc"]) for p in phases[:-1]]
    expected = [
        timeline["earth_orbit_insertion"],
        timeline["translunar_injection"],
        timeline["lunar_orbit_insertion"],
        timeline["powered_descent_initiation"],
    ]
    for moment, elapsed in zip(boundaries, expected):
        assert abs((moment - launch).total_seconds() - elapsed) < 1e-3


def test_mission_window_requires_utc():
    with pytest.raises(ValueError):
        mission_window(datetime(2026, 11, 4))


def test_request_covers_launch_to_landing_on_the_grid():
    request_id = uuid4()
    request = ephemeris_request_for(LANDING, request_id)
    assert isinstance(request, FlightEphemerisRequest)
    assert request.request_id == request_id
    assert request.step_s == STEP_S == 1800
    assert request.start_utc == datetime(2026, 10, 30, 17, 0, tzinfo=UTC)
    assert request.end_utc == LANDING
    off_grid = LANDING + timedelta(minutes=10)
    shifted = ephemeris_request_for(off_grid, uuid4())
    assert shifted.end_utc == LANDING + timedelta(minutes=30)
    launch = parse(mission_window(off_grid)["launch_utc"])
    assert shifted.start_utc <= launch < shifted.start_utc + timedelta(seconds=STEP_S)


# ---------------------------------------------------------------- bundle loading


def rows(start, count, radius_km, step_s=STEP_S, phase=0.0):
    out = []
    for i in range(count):
        angle = phase + i * 0.005
        out.append(
            {
                "utc": (start + timedelta(seconds=i * step_s)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "x_km": radius_km * math.cos(angle),
                "y_km": radius_km * math.sin(angle),
                "z_km": 1000.0,
            }
        )
    return out


def write_bundle(data_dir, samples, key="a" * 64, tamper=None):
    root = data_dir / "flight_ephemeris" / key
    (root / "raw").mkdir(parents=True)
    files = {
        "raw/301.json": b'{"result": "moon"}',
        "raw/10.json": b'{"result": "sun"}',
        "samples.json": json.dumps(samples).encode(),
    }
    for name, content in files.items():
        (root / name).write_bytes(content)
    manifest = {
        "cache_key": key,
        "sha256": {name: hashlib.sha256(c).hexdigest() for name, c in files.items()},
    }
    if tamper:
        tamper(manifest)
    (root / "manifest.json").write_text(json.dumps(manifest))
    return f"flight_ephemeris/{key}"


def good_samples(count=5, start=LANDING - timedelta(hours=2)):
    return {"moon": rows(start, count, 384_000.0), "sun": rows(start, count, 1.48e8, phase=2.0)}


def test_load_returns_verified_samples(tmp_path):
    samples = good_samples()
    path = write_bundle(tmp_path, samples)
    assert load_flight_ephemeris(tmp_path, path) == samples


def test_load_rejects_checksum_mismatch(tmp_path):
    path = write_bundle(tmp_path, good_samples())
    (tmp_path / path / "samples.json").write_text("{}")
    with pytest.raises(FlightEphemerisDataError):
        load_flight_ephemeris(tmp_path, path)


def test_load_rejects_mismatched_key_and_missing_bodies(tmp_path):
    path = write_bundle(tmp_path, good_samples(), tamper=lambda m: m.update(cache_key="b" * 64))
    with pytest.raises(FlightEphemerisDataError):
        load_flight_ephemeris(tmp_path, path)
    only_moon = {"moon": good_samples()["moon"]}
    path = write_bundle(tmp_path, only_moon, key="c" * 64)
    with pytest.raises(FlightEphemerisDataError):
        load_flight_ephemeris(tmp_path, path)


@pytest.mark.parametrize("path", ["../etc", "flight_ephemeris/abc", "dsn/" + "a" * 64])
def test_load_rejects_invalid_references(tmp_path, path):
    with pytest.raises(FlightEphemerisDataError):
        load_flight_ephemeris(tmp_path, path)


def test_load_rejects_missing_bundle(tmp_path):
    with pytest.raises(FlightEphemerisDataError):
        load_flight_ephemeris(tmp_path, "flight_ephemeris/" + "d" * 64)


# ---------------------------------------------------------------- result


def test_flight_result_aligns_samples_and_adds_rotation():
    samples = good_samples()
    result = flight_result(LANDING, samples)
    assert result["window"] == mission_window(LANDING)
    assert result["frame"]["center"] == "Earth (399)"
    assert result["frame"]["plane"] == "Ecliptic of J2000.0"
    assert result["frame"]["units"] == "km"
    assert result["limitations"] == list(LIMITATIONS)
    assert len(result["samples"]) == 5
    first = result["samples"][0]
    assert first["utc"] == samples["moon"][0]["utc"]
    assert first["moon_geocentric_km"] == [
        samples["moon"][0]["x_km"],
        samples["moon"][0]["y_km"],
        samples["moon"][0]["z_km"],
    ]
    assert first["sun_geocentric_km"][0] == samples["sun"][0]["x_km"]
    assert first["earth_rotation_deg"] == pytest.approx(
        earth_rotation_deg(parse(first["utc"]))
    )


def test_flight_result_rejects_misaligned_bodies():
    samples = good_samples()
    samples["sun"] = samples["sun"][1:]
    with pytest.raises(FlightEphemerisDataError):
        flight_result(LANDING, samples)
    samples = good_samples()
    samples["sun"][2]["utc"] = "2026-11-03T23:10:00Z"
    with pytest.raises(FlightEphemerisDataError):
        flight_result(LANDING, samples)


@pytest.mark.parametrize("radius_km", [200_000.0, 500_000.0])
def test_flight_result_rejects_implausible_moon_distance(radius_km):
    samples = good_samples()
    samples["moon"] = rows(LANDING - timedelta(hours=2), 5, radius_km)
    with pytest.raises(FlightEphemerisDataError):
        flight_result(LANDING, samples)


def test_flight_result_rejects_non_finite_values():
    samples = good_samples()
    samples["sun"][0]["x_km"] = float("nan")
    with pytest.raises(FlightEphemerisDataError):
        flight_result(LANDING, samples)


# ---------------------------------------------------------------- store and jobs


@pytest.fixture
def store(tmp_path):
    return FlightEphemerisStore(JobStore(tmp_path / "jobs.sqlite"))


def test_store_lifecycle_and_recovery(store):
    job = store.create(LANDING)
    assert job["status"] == "pending"
    assert parse(job["landing_utc"]) == LANDING
    assert [j["id"] for j in store.pending()] == [job["id"]]
    store.update(job["id"], "acquiring")
    assert store.pending() == []
    store.recover()
    assert store.get(job["id"])["status"] == "pending"
    assert store.get(str(uuid4())) is None
    with pytest.raises(ValueError):
        store.update(job["id"], "exploded")


def test_store_reuses_active_or_ready_jobs_for_the_same_landing(store):
    first = store.create(LANDING)
    assert store.create(LANDING)["id"] == first["id"]
    store.update(first["id"], "ready", result={"x": 1})
    assert store.create(LANDING)["id"] == first["id"]
    other = store.create(LANDING + timedelta(hours=1))
    assert other["id"] != first["id"]


def test_store_retries_after_failure(store):
    first = store.create(LANDING)
    store.update(first["id"], "failed", error="HTTP 503")
    assert store.create(LANDING)["id"] != first["id"]


def test_store_limits_active_jobs(store):
    for hours in range(module.MAX_ACTIVE_JOBS):
        store.create(LANDING + timedelta(hours=hours))
    with pytest.raises(ValueError):
        store.create(LANDING + timedelta(hours=99))


def test_job_computes_and_stores_the_result(store, tmp_path, monkeypatch):
    request_holder = []

    async def fake_submit(request, broker_url):
        request_holder.append(request)
        count = int((request.end_utc - request.start_utc).total_seconds() // request.step_s) + 1
        path = write_bundle(tmp_path, good_samples(count, request.start_utc))
        return FlightEphemerisEvent(
            request_id=request.request_id, status="ready", ephemeris_path=path
        )

    monkeypatch.setattr(module, "submit_flight_ephemeris", fake_submit)
    job = store.create(LANDING)
    asyncio.run(execute_flight_ephemeris(store, job, tmp_path, "amqp://x"))
    stored = store.get(job["id"])
    assert stored["status"] == "ready", stored["error"]
    result = stored["result"]
    assert result["window"] == mission_window(LANDING)
    assert result["samples"][0]["utc"] == "2026-10-30T17:00:00Z"
    assert result["samples"][-1]["utc"] == "2026-11-04T00:00:00Z"
    assert result["source"]["ephemeris_path"] == "flight_ephemeris/" + "a" * 64
    assert "Horizons" in result["source"]["provider"]
    assert request_holder[0].start_utc == datetime(2026, 10, 30, 17, tzinfo=UTC)


def test_request_id_is_stable_per_job(store, tmp_path, monkeypatch):
    ids = []

    async def failing(request, broker_url):
        ids.append(request.request_id)
        return FlightEphemerisEvent(
            request_id=request.request_id,
            status="failed",
            error_code="source_unavailable",
            error_message="HTTP 503",
        )

    monkeypatch.setattr(module, "submit_flight_ephemeris", failing)
    job = store.create(LANDING)
    for _ in range(2):
        asyncio.run(execute_flight_ephemeris(store, job, tmp_path, "amqp://x"))
    assert ids[0] == ids[1]
    assert store.get(job["id"])["status"] == "failed"
    assert "HTTP 503" in store.get(job["id"])["error"]


def test_job_fails_visibly_on_broker_errors(store, tmp_path, monkeypatch):
    async def broken(request, broker_url):
        raise ConnectionError("broker down")

    monkeypatch.setattr(module, "submit_flight_ephemeris", broken)
    job = store.create(LANDING)
    asyncio.run(execute_flight_ephemeris(store, job, tmp_path, "amqp://x"))
    assert store.get(job["id"])["status"] == "failed"


# ---------------------------------------------------------------- API


def test_api_creates_reuses_and_reads_jobs(tmp_path):
    with TestClient(create_app(tmp_path / "state", tmp_path / "data", start_worker=False)) as client:
        response = client.post("/flight-ephemeris", json={"landing_utc": "2026-11-04T00:00:00Z"})
        assert response.status_code == 202
        job = response.json()
        assert job["status"] == "pending"
        again = client.post("/flight-ephemeris", json={"landing_utc": "2026-11-04T00:00:00+00:00"})
        assert again.status_code == 202 and again.json()["id"] == job["id"]
        fetched = client.get(f"/flight-ephemeris/{job['id']}")
        assert fetched.status_code == 200 and fetched.json()["id"] == job["id"]
        assert client.get(f"/flight-ephemeris/{uuid4()}").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"landing_utc": "2026-11-04T00:00:00"},
        {"landing_utc": "2026-11-04T00:00:00.250Z"},
        {"landing_utc": "2040-01-01T00:00:00Z"},
        {"landing_utc": "2019-12-31T00:00:00Z"},
        {"landing_utc": "2026-11-04T00:00:00Z", "extra": 1},
        {},
    ],
)
def test_api_rejects_invalid_landing_times(tmp_path, body):
    with TestClient(create_app(tmp_path / "state", tmp_path / "data", start_worker=False)) as client:
        assert client.post("/flight-ephemeris", json=body).status_code == 422
