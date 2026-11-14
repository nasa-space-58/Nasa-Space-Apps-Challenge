"""NOAA SWPC space weather: contracts, scales, forecast overlap, jobs and API.

Thresholds: NOAA Space Weather Scales (https://www.swpc.noaa.gov/noaa-scales-explanation)
and GOES 0.1-0.8 nm flare classes (A 1e-8, B 1e-7, C 1e-6, M 1e-5, X 1e-4 W/m²).
Snapshots below are synthetic; the worker tests cover real SWPC captures.
"""

import asyncio
import hashlib
import json
import math
from datetime import datetime, timedelta
from uuid import UUID, uuid4, uuid5

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.space_weather as module
from app.analysis_api import JobStore, Submission, create_app
from app.space_weather import (
    LIMITATIONS,
    MAX_ACTIVE_REGIONS,
    SpaceWeatherStore,
    assess_space_weather,
    execute_space_weather,
    flare_class,
    load_snapshot,
    radiation_storm_level,
    radio_blackout_level,
    space_weather_request_for,
)
from shared.space_weather import (
    PRODUCTS,
    SPACE_WEATHER_DEAD_QUEUE,
    SPACE_WEATHER_REQUEST_QUEUE,
    SWPC_BASE_URL,
    SpaceWeatherEvent,
    SpaceWeatherRequest,
)

# ---------------------------------------------------------------- contracts


def test_queue_names_and_products():
    assert SPACE_WEATHER_REQUEST_QUEUE == "lunar.space-weather.requests.v1"
    assert SPACE_WEATHER_DEAD_QUEUE == "lunar.space-weather.dead.v1"
    assert SWPC_BASE_URL.startswith("https://services.swpc.noaa.gov")
    assert set(PRODUCTS) == {"solar_probabilities", "xrays", "protons", "solar_regions"}
    assert all(path.startswith("/json/") for path in PRODUCTS.values())


def test_request_contract():
    request_id = uuid4()
    request = SpaceWeatherRequest(request_id=request_id)
    assert request.schema_version == 1
    assert SpaceWeatherRequest.model_validate_json(request.model_dump_json()) == request
    with pytest.raises(ValidationError):
        SpaceWeatherRequest()
    with pytest.raises(ValidationError):
        SpaceWeatherRequest(request_id=request_id, products=["xrays"])
    with pytest.raises(ValidationError):
        SpaceWeatherRequest(request_id=request_id, schema_version=2)


def test_event_contract():
    rid = uuid4()
    ok = SpaceWeatherEvent(
        request_id=rid, status="ready", snapshot_path="space-weather/" + "f" * 64
    )
    assert ok.error_code is None
    failed = SpaceWeatherEvent(
        request_id=rid, status="failed", error_code="x", error_message="y"
    )
    assert failed.snapshot_path is None
    bad = [
        dict(status="ready"),
        dict(status="ready", snapshot_path="space-weather/abc"),
        dict(status="ready", snapshot_path="dsn/" + "f" * 64),
        dict(
            status="ready",
            snapshot_path="space-weather/" + "f" * 64,
            error_code="x",
            error_message="y",
        ),
        dict(status="failed"),
        dict(status="failed", error_code="x"),
        dict(
            status="failed",
            error_code="x",
            error_message="y",
            snapshot_path="space-weather/" + "f" * 64,
        ),
        dict(status="unknown", error_code="x", error_message="y"),
    ]
    for fields in bad:
        with pytest.raises(ValidationError):
            SpaceWeatherEvent(request_id=rid, **fields)


# ------------------------------------------------------------------- scales


@pytest.mark.parametrize(
    "flux, expected",
    [
        (0.0, "A0.0"),
        (5e-9, "A0.5"),
        (1e-8, "A1.0"),
        (9.99e-8, "A9.9"),
        (1e-7, "B1.0"),
        (6.798833851462405e-07, "B6.7"),
        (1.5e-6, "C1.5"),
        (9.99e-6, "C9.9"),
        (1e-5, "M1.0"),
        (2.3e-5, "M2.3"),
        (9.99e-5, "M9.9"),
        (1e-4, "X1.0"),
        (2.3e-3, "X23.0"),
    ],
)
def test_flare_class(flux, expected):
    assert flare_class(flux) == expected


@pytest.mark.parametrize(
    "flux, level",
    [
        (0.0, 0),
        (9.99e-6, 0),
        (1e-5, 1),
        (4.99e-5, 1),
        (5e-5, 2),
        (9.99e-5, 2),
        (1e-4, 3),
        (1e-3, 4),
        (2e-3, 5),
        (1e-2, 5),
    ],
)
def test_radio_blackout_level(flux, level):
    assert radio_blackout_level(flux) == level


@pytest.mark.parametrize(
    "flux, level",
    [
        (0.23, 0),
        (9.99, 0),
        (10, 1),
        (99.9, 1),
        (100, 2),
        (1e3, 3),
        (1e4, 4),
        (1e5, 5),
        (1e7, 5),
    ],
)
def test_radiation_storm_level(flux, level):
    assert radiation_storm_level(flux) == level


@pytest.mark.parametrize("bad", [-1e-9, math.nan, math.inf, None, "1e-6"])
def test_scales_reject_invalid_flux(bad):
    for function in (flare_class, radio_blackout_level, radiation_storm_level):
        with pytest.raises((ValueError, TypeError)):
            function(bad)


def test_levels_are_monotonic():
    fluxes = [10 ** (e / 10) for e in range(-90, -10)]
    levels = [radio_blackout_level(f) for f in fluxes]
    assert levels == sorted(levels) and levels[0] == 0 and levels[-1] == 5
    pfu = [10 ** (e / 10) for e in range(-20, 70)]
    levels = [radiation_storm_level(f) for f in pfu]
    assert levels == sorted(levels) and levels[0] == 0 and levels[-1] == 5


# --------------------------------------------------------------- assessment


def utc(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    assert parsed.utcoffset() == timedelta(0)
    return parsed


def day(d, c, m, x, p):
    return {
        "start_utc": f"2026-11-{d:02d}T00:00:00Z",
        "end_utc": f"2026-11-{d + 1:02d}T00:00:00Z",
        "c_class_percent": c,
        "m_class_percent": m,
        "x_class_percent": x,
        "proton_10mev_percent": p,
    }


def region(number, c, m, x, p=1):
    return {
        "region": number,
        "location": "N10E10",
        "spot_class": "Dso",
        "area_msh": 20,
        "c_flare_percent": c,
        "m_flare_percent": m,
        "x_flare_percent": x,
        "proton_percent": p,
    }


def snapshot():
    return {
        "snapshot_version": 1,
        "acquired_at_utc": "2026-11-14T20:45:00Z",
        "flare_probabilities": {
            "forecast_start_utc": "2026-11-14T00:00:00Z",
            "polar_cap_absorption": "green",
            "days": [
                day(14, 90, 25, 5, 5),
                day(15, 80, 40, 10, 15),
                day(16, 70, 30, 1, 1),
            ],
        },
        "xrays_w_m2": [
            {"time_utc": "2026-11-14T20:20:00Z", "flux_w_m2": 7.1e-7},
            {"time_utc": "2026-11-14T20:30:00Z", "flux_w_m2": 2.3e-5},
            {"time_utc": "2026-11-14T20:38:00Z", "flux_w_m2": 6.8e-7},
            {"time_utc": "2026-11-14T20:39:00Z", "flux_w_m2": None},
        ],
        "protons_10mev_pfu": [
            {"time_utc": "2026-11-14T20:05:00Z", "flux_pfu": 12.0},
            {"time_utc": "2026-11-14T20:10:00Z", "flux_pfu": None},
            {"time_utc": "2026-11-14T20:15:00Z", "flux_pfu": 0.26},
        ],
        "active_regions": {
            "observed_date_utc": "2026-11-14T00:00:00Z",
            "regions": [
                region(4566, 10, 0, 0),
                region(4568, 20, 5, 1),
                region(4572, 10, 5, 1),
                region(4573, None, None, None, None),
                region(4574, 20, 5, 1),
                region(4576, 55, 10, 1),
                region(4577, 20, 5, 1),
                region(4578, 30, 15, 1),
                region(4580, 5, 1, 2),
            ],
        },
    }


def test_current_conditions_use_latest_and_peak_measured_values():
    current = assess_space_weather(
        snapshot(), utc("2026-11-15T06:00:00Z"), utc("2026-11-15T18:00:00Z")
    )["current"]
    assert current["xray"] == {
        "time_utc": "2026-11-14T20:38:00Z",
        "flux_w_m2": 6.8e-7,
        "flare_class": "B6.8",
        "radio_blackout_level": 0,
    }
    assert current["xray_peak"] == {
        "time_utc": "2026-11-14T20:30:00Z",
        "flux_w_m2": 2.3e-5,
        "flare_class": "M2.3",
        "radio_blackout_level": 1,
    }
    assert current["protons_10mev"] == {
        "time_utc": "2026-11-14T20:15:00Z",
        "flux_pfu": 0.26,
        "radiation_storm_level": 0,
    }
    assert current["protons_10mev_peak"] == {
        "time_utc": "2026-11-14T20:05:00Z",
        "flux_pfu": 12.0,
        "radiation_storm_level": 1,
    }


def forecast(start, end):
    return assess_space_weather(snapshot(), utc(start), utc(end))["forecast"]


def test_window_inside_one_forecast_day():
    result = forecast("2026-11-15T06:00:00Z", "2026-11-15T18:00:00Z")
    assert result["forecast_start_utc"] == "2026-11-14T00:00:00Z"
    assert result["forecast_end_utc"] == "2026-11-17T00:00:00Z"
    assert result["coverage"] == "full"
    assert result["covered_duration_s"] == 43200
    assert result["days"] == [day(15, 80, 40, 10, 15)]
    assert result["max_daily_c_class_percent"] == 80
    assert result["max_daily_m_class_percent"] == 40
    assert result["max_daily_x_class_percent"] == 10
    assert result["max_daily_proton_10mev_percent"] == 15


def test_window_spanning_all_days_reports_daily_maxima():
    result = forecast("2026-11-14T12:00:00Z", "2026-11-16T12:00:00Z")
    assert result["coverage"] == "full"
    assert result["days"] == snapshot()["flare_probabilities"]["days"]
    assert result["max_daily_c_class_percent"] == 90
    assert result["max_daily_m_class_percent"] == 40
    assert result["max_daily_x_class_percent"] == 10
    assert result["max_daily_proton_10mev_percent"] == 15


def test_window_leaving_the_forecast_horizon_is_partial():
    result = forecast("2026-11-16T12:00:00Z", "2026-11-18T00:00:00Z")
    assert result["coverage"] == "partial"
    assert result["covered_duration_s"] == 43200
    assert result["days"] == [day(16, 70, 30, 1, 1)]
    assert result["max_daily_m_class_percent"] == 30


def test_window_starting_before_the_forecast_is_partial():
    result = forecast("2026-11-13T12:00:00Z", "2026-11-14T06:00:00Z")
    assert result["coverage"] == "partial"
    assert result["covered_duration_s"] == 21600
    assert result["days"] == [day(14, 90, 25, 5, 5)]


def test_window_outside_the_forecast_has_no_forecast():
    for start, end in [
        ("2026-11-04T00:00:00Z", "2026-11-05T00:00:00Z"),
        ("2027-03-01T00:00:00Z", "2027-03-02T00:00:00Z"),
        ("2026-11-17T00:00:00Z", "2026-11-18T00:00:00Z"),
    ]:
        result = forecast(start, end)
        assert result["coverage"] == "none"
        assert result["covered_duration_s"] == 0
        assert result["days"] == []
        for key in (
            "max_daily_c_class_percent",
            "max_daily_m_class_percent",
            "max_daily_x_class_percent",
            "max_daily_proton_10mev_percent",
        ):
            assert result[key] is None


def test_touching_a_day_boundary_does_not_include_the_next_day():
    result = forecast("2026-11-14T18:00:00Z", "2026-11-15T00:00:00Z")
    assert result["days"] == [day(14, 90, 25, 5, 5)]


def test_active_regions_are_ranked_and_bounded():
    regions = assess_space_weather(
        snapshot(), utc("2026-11-15T06:00:00Z"), utc("2026-11-15T18:00:00Z")
    )["active_regions"]
    assert regions["observed_date_utc"] == "2026-11-14T00:00:00Z"
    assert regions["region_count"] == 9
    assert MAX_ACTIVE_REGIONS == 5
    # X desc, then M desc, then C desc, then region number; unknown ranks last.
    assert [r["region"] for r in regions["regions"]] == [4580, 4578, 4576, 4568, 4574]


def test_assessment_carries_limitations_and_snapshot_time():
    result = assess_space_weather(
        snapshot(), utc("2026-11-15T06:00:00Z"), utc("2026-11-15T18:00:00Z")
    )
    assert result["acquired_at_utc"] == "2026-11-14T20:45:00Z"
    assert result["limitations"] == list(LIMITATIONS)
    assert result["forecast"]["polar_cap_absorption"] == "green"
    json.dumps(result, allow_nan=False)


def test_series_without_values_report_none():
    data = snapshot()
    data["xrays_w_m2"] = [{"time_utc": "2026-11-14T20:20:00Z", "flux_w_m2": None}]
    data["protons_10mev_pfu"] = []
    current = assess_space_weather(
        data, utc("2026-11-15T06:00:00Z"), utc("2026-11-15T18:00:00Z")
    )["current"]
    assert current == {
        "xray": None,
        "xray_peak": None,
        "protons_10mev": None,
        "protons_10mev_peak": None,
    }


def test_assessment_rejects_invalid_windows():
    start = utc("2026-11-15T06:00:00Z")
    with pytest.raises(ValueError):
        assess_space_weather(snapshot(), start, start)
    with pytest.raises(ValueError):
        assess_space_weather(snapshot(), start + timedelta(hours=1), start)
    with pytest.raises(ValueError):
        assess_space_weather(
            snapshot(), start.replace(tzinfo=None), start + timedelta(hours=1)
        )
    broken = snapshot()
    broken["snapshot_version"] = 2
    with pytest.raises(ValueError):
        assess_space_weather(broken, start, start + timedelta(hours=1))


# ------------------------------------------------------------------ bundles


def write_bundle(tmp_path, document=None):
    key = hashlib.sha256(json.dumps(document or snapshot()).encode()).hexdigest()
    root = tmp_path / "space-weather" / key
    (root / "raw").mkdir(parents=True)
    (root / "snapshot.json").write_text(json.dumps(document or snapshot()))
    (root / "raw" / "xrays.json").write_text("[]")
    hashes = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("snapshot.json", "raw/xrays.json")
    }
    (root / "manifest.json").write_text(
        json.dumps({"cache_key": key, "sha256": hashes})
    )
    return f"space-weather/{key}"


def test_load_verified_snapshot(tmp_path):
    ref = write_bundle(tmp_path)
    assert load_snapshot(tmp_path, ref) == snapshot()


@pytest.mark.parametrize(
    "tamper", ["snapshot", "raw", "manifest", "uncovered", "escape"]
)
def test_corrupt_bundles_are_rejected(tmp_path, tamper):
    ref = write_bundle(tmp_path)
    root = tmp_path / ref
    manifest = json.loads((root / "manifest.json").read_text())
    if tamper == "snapshot":
        (root / "snapshot.json").write_text("{}")
    elif tamper == "raw":
        (root / "raw" / "xrays.json").write_text("[1]")
    elif tamper == "manifest":
        (root / "manifest.json").write_text("not json")
    elif tamper == "uncovered":
        manifest["sha256"].pop("snapshot.json")
        (root / "manifest.json").write_text(json.dumps(manifest))
    else:
        manifest["sha256"]["../outside.json"] = "0" * 64
        (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_snapshot(tmp_path, ref)


@pytest.mark.parametrize(
    "ref",
    [
        "space-weather/abc",
        "../space-weather/" + "a" * 64,
        "dsn/" + "a" * 64,
        None,
        "space-weather/" + "a" * 64,
    ],
)
def test_bad_or_missing_references_are_rejected(tmp_path, ref):
    with pytest.raises(ValueError):
        load_snapshot(tmp_path, ref)


def test_request_uuid_derives_from_the_job():
    job_id = str(uuid4())
    request = space_weather_request_for(job_id)
    assert request.request_id == uuid5(UUID(job_id), "space-weather")
    assert space_weather_request_for(job_id) == request
    assert request.request_id != uuid5(UUID(job_id), "dsn")


# ------------------------------------------------------------- store and jobs


@pytest.fixture
def stores(tmp_path):
    analyses = JobStore(tmp_path / "jobs.sqlite")
    return analyses, SpaceWeatherStore(analyses)


def ready_analysis(
    analyses,
    status="ready",
    start="2026-11-15T06:00:00Z",
    end="2026-11-15T18:00:00Z",
):
    submission = Submission(
        site_id="malapert-massif-r23",
        latitude_deg=-85.9948,
        longitude_deg_east=359.76422,
        start_utc=start,
        end_utc=end,
        step_s=3600,
        refine=False,
    )
    job = analyses.create(submission)
    analyses.update(job["id"], status, result={"analysis": {}, "frames": []})
    return job["id"]


def test_store_lifecycle_and_recovery(stores):
    _, store = stores
    job = store.create("analysis-1")
    assert job["status"] == "pending"
    assert job["analysis_id"] == "analysis-1"
    assert job["result"] is None and job["error"] is None
    assert job["created"]
    assert store.get(job["id"]) == job
    assert store.get("missing") is None
    assert [j["id"] for j in store.pending()] == [job["id"]]
    store.update(job["id"], "acquiring")
    assert store.pending() == []
    store.recover()
    assert store.get(job["id"])["status"] == "pending"
    store.update(job["id"], "computing")
    store.recover()
    assert store.get(job["id"])["status"] == "pending"
    store.update(job["id"], "ready", result={"ok": True})
    assert store.get(job["id"])["result"] == {"ok": True}
    store.recover()
    assert store.get(job["id"])["status"] == "ready"
    with pytest.raises(ValueError):
        store.update(job["id"], "insufficient_data")


def test_store_bounds_active_jobs(stores):
    _, store = stores
    jobs = [store.create("a") for _ in range(4)]
    with pytest.raises(ValueError):
        store.create("a")
    store.update(jobs[0]["id"], "failed", error="x")
    assert store.create("a")["status"] == "pending"


def run_job(stores, tmp_path, monkeypatch, **analysis):
    analyses, store = stores
    analysis_id = ready_analysis(analyses, **analysis)
    ref = write_bundle(tmp_path)
    seen = []

    async def fake_submit(request, broker_url):
        seen.append(request)
        return SpaceWeatherEvent(
            request_id=request.request_id, status="ready", snapshot_path=ref
        )

    monkeypatch.setattr(module, "submit_space_weather", fake_submit)
    job = store.create(analysis_id)
    asyncio.run(execute_space_weather(store, analyses, job, tmp_path, "amqp://t"))
    return store.get(job["id"]), seen, ref, analysis_id


def test_job_computes_and_stores_assessment(stores, tmp_path, monkeypatch):
    job, seen, ref, analysis_id = run_job(stores, tmp_path, monkeypatch)
    assert job["status"] == "ready", job["error"]
    assert seen[0].request_id == uuid5(UUID(job["id"]), "space-weather")
    result = job["result"]
    expected = assess_space_weather(
        snapshot(), utc("2026-11-15T06:00:00Z"), utc("2026-11-15T18:00:00Z")
    )
    for key, value in expected.items():
        assert result[key] == value
    assert result["analysis_id"] == analysis_id
    assert result["start_utc"] == "2026-11-15T06:00:00Z"
    assert result["end_utc"] == "2026-11-15T18:00:00Z"
    assert result["source"]["provider"] == "NOAA Space Weather Prediction Center"
    assert result["source"]["snapshot_path"] == ref
    assert result["source"]["products"] == {
        name: SWPC_BASE_URL + path for name, path in PRODUCTS.items()
    }


def test_outside_forecast_is_still_a_ready_result(stores, tmp_path, monkeypatch):
    job, _, _, _ = run_job(
        stores,
        tmp_path,
        monkeypatch,
        start="2026-11-04T00:00:00Z",
        end="2026-11-04T12:00:00Z",
    )
    assert job["status"] == "ready"
    assert job["result"]["forecast"]["coverage"] == "none"


def test_job_fails_visibly(stores, tmp_path, monkeypatch):
    analyses, store = stores
    job = store.create("missing")
    asyncio.run(execute_space_weather(store, analyses, job, tmp_path, "amqp://t"))
    assert store.get(job["id"])["status"] == "failed"
    assert "Analysis" in store.get(job["id"])["error"]

    pending = ready_analysis(analyses, status="pending")
    job = store.create(pending)
    asyncio.run(execute_space_weather(store, analyses, job, tmp_path, "amqp://t"))
    assert store.get(job["id"])["status"] == "failed"

    analysis_id = ready_analysis(analyses)

    async def failed(request, broker_url):
        return SpaceWeatherEvent(
            request_id=request.request_id,
            status="failed",
            error_code="source_unavailable",
            error_message="SWPC timeout",
        )

    monkeypatch.setattr(module, "submit_space_weather", failed)
    job = store.create(analysis_id)
    asyncio.run(execute_space_weather(store, analyses, job, tmp_path, "amqp://t"))
    stored = store.get(job["id"])
    assert stored["status"] == "failed" and "SWPC timeout" in stored["error"]

    async def broken(request, broker_url):
        raise RuntimeError("broker down")

    monkeypatch.setattr(module, "submit_space_weather", broken)
    job = store.create(analysis_id)
    asyncio.run(execute_space_weather(store, analyses, job, tmp_path, "amqp://t"))
    assert store.get(job["id"])["status"] == "failed"
    assert store.get(job["id"])["error"]

    async def corrupt(request, broker_url):
        return SpaceWeatherEvent(
            request_id=request.request_id,
            status="ready",
            snapshot_path="space-weather/" + "b" * 64,
        )

    monkeypatch.setattr(module, "submit_space_weather", corrupt)
    job = store.create(analysis_id)
    asyncio.run(execute_space_weather(store, analyses, job, tmp_path, "amqp://t"))
    assert store.get(job["id"])["status"] == "failed"


def test_request_id_is_stable_per_job(stores, tmp_path, monkeypatch):
    analyses, store = stores
    analysis_id = ready_analysis(analyses)
    ref = write_bundle(tmp_path)
    seen = []

    async def fake_submit(request, broker_url):
        seen.append(request.request_id)
        return SpaceWeatherEvent(
            request_id=request.request_id, status="ready", snapshot_path=ref
        )

    monkeypatch.setattr(module, "submit_space_weather", fake_submit)
    job = store.create(analysis_id)
    for _ in range(2):
        asyncio.run(execute_space_weather(store, analyses, job, tmp_path, "amqp://t"))
    assert seen[0] == seen[1]


# ---------------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as c:
        yield c


def test_routes(client):
    assert client.post("/analyses/missing/space-weather").status_code == 404
    analyses = client.app.state.store
    pending = ready_analysis(analyses, status="pending")
    assert client.post(f"/analyses/{pending}/space-weather").status_code == 409
    ready_id = ready_analysis(analyses)
    response = client.post(f"/analyses/{ready_id}/space-weather")
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "pending" and job["analysis_id"] == ready_id
    fetched = client.get(f"/space-weather/{job['id']}")
    assert fetched.status_code == 200 and fetched.json()["id"] == job["id"]
    assert client.get("/space-weather/missing").status_code == 404
    insufficient = ready_analysis(analyses, status="insufficient_data")
    assert client.post(f"/analyses/{insufficient}/space-weather").status_code == 202
    for _ in range(2):
        assert client.post(f"/analyses/{ready_id}/space-weather").status_code == 202
    assert client.post(f"/analyses/{ready_id}/space-weather").status_code == 429
    assert client.app.state.space_weather.get(job["id"])["status"] == "pending"
