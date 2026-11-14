"""R2/S4 communication windows: station intervals, intersections, jobs and API.

Reference crossings: JPL Horizons Moon (301) elevations from DSN stations
(DSN 810-005 module 301 Rev. P Table 5 sites), captured 2026-11-14 at 5-minute
and 1-minute steps. The 1-minute crossing is linearly interpolated within one
minute; at these rates (~0.2 deg/min, nearly linear) the 5-minute estimate must
agree within 10 s, well below the 5-minute sampling interval.
"""

import asyncio
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.communication as module
from app.analysis_api import JobStore, Submission, create_app
from app.communication import (
    DEFAULT_MIN_ELEVATION_DEG,
    LIMITATIONS,
    CommunicationRequest,
    CommunicationStore,
    communication_windows,
    dsn_request_for,
    execute_communication,
    load_dsn_samples,
    station_intervals,
)
from shared.dsn import DsnEvent, DsnRequest

T0 = datetime(2026, 11, 4, tzinfo=timezone.utc)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() == timedelta(0)
    return parsed


def samples(*pairs):
    return [
        {"sampled_at_utc": iso(at(m)), "azimuth_deg": 100.0, "elevation_deg": e}
        for m, e in pairs
    ]


def states(intervals):
    return [(parse(i["start_utc"]), parse(i["end_utc"]), i["state"]) for i in intervals]


def assert_partition(intervals, start, end):
    assert parse(intervals[0]["start_utc"]) == start
    assert parse(intervals[-1]["end_utc"]) == end
    for left, right in zip(intervals, intervals[1:]):
        assert left["end_utc"] == right["start_utc"]
        assert left["state"] != right["state"] or left.get("station_ids") != right.get(
            "station_ids"
        )
    for interval in intervals:
        duration = (
            parse(interval["end_utc"]) - parse(interval["start_utc"])
        ).total_seconds()
        assert duration > 0
        assert abs(interval["duration_s"] - duration) < 1e-6


# ---------------------------------------------------------------- station intervals


@pytest.mark.parametrize(
    "station, rows, reference, rising",
    [
        # (start, elevation) pairs from the 5-minute fixtures; reference from 1-minute data.
        (
            "DSS-14",
            ((610, 5.276262695), (615, 6.253229782)),
            "2026-11-04T10:13:42.25Z",
            True,
        ),
        (
            "DSS-43",
            ((180, 6.554200843), (185, 5.603912847)),
            "2026-11-04T03:02:55.06Z",
            False,
        ),
        (
            "DSS-63",
            ((140, 5.689413234), (145, 6.598615388)),
            "2026-11-04T02:21:42.51Z",
            True,
        ),
    ],
)
def test_reference_crossings_match_one_minute_horizons(
    station, rows, reference, rising
):
    start, end = at(rows[0][0]), at(rows[1][0])
    result = station_intervals(samples(*rows), 6.0, start, end)
    assert_partition(result, start, end)
    assert [i["state"] for i in result] == (
        ["below", "above"] if rising else ["above", "below"]
    )
    crossing = parse(result[0]["end_utc"])
    assert abs((crossing - parse(reference)).total_seconds()) < 10


def test_linear_crossing_is_exact_for_linear_motion():
    result = station_intervals(samples((0, 0.0), (10, 10.0)), 6.0, at(0), at(10))
    assert states(result) == [(at(0), at(6), "below"), (at(6), at(10), "above")]


def test_threshold_equality_counts_as_above_and_endpoint_touch_has_no_sliver():
    result = station_intervals(
        samples((0, 6.0), (5, 6.0), (10, 8.0)), 6.0, at(0), at(10)
    )
    assert states(result) == [(at(0), at(10), "above")]
    result = station_intervals(samples((0, 6.0), (5, 2.0)), 6.0, at(0), at(5))
    assert states(result) == [(at(0), at(5), "below")]
    result = station_intervals(samples((0, 2.0), (5, 6.0)), 6.0, at(0), at(5))
    assert states(result) == [(at(0), at(5), "below")]


def test_samples_are_clipped_to_the_requested_interval():
    rows = samples((0, 0.0), (10, 10.0), (20, 20.0))
    result = station_intervals(rows, 6.0, at(3), at(15))
    assert states(result) == [(at(3), at(6), "below"), (at(6), at(15), "above")]
    assert_partition(result, at(3), at(15))


def test_missing_elevation_marks_adjacent_segments_unknown():
    rows = samples((0, 10.0), (5, None), (10, 10.0), (15, 10.0))
    result = station_intervals(rows, 6.0, at(0), at(15))
    assert states(result) == [(at(0), at(10), "unknown"), (at(10), at(15), "above")]


def test_higher_threshold_never_increases_coverage():
    rows = samples(*[(5 * i, 20 * math.sin(i / 4)) for i in range(40)])

    def above(limit):
        return sum(
            i["duration_s"]
            for i in station_intervals(rows, limit, at(0), at(195))
            if i["state"] == "above"
        )

    assert above(10.0) <= above(6.0) <= above(-90.0) == 195 * 60


@pytest.mark.parametrize(
    "rows, start, end",
    [
        ([], at(0), at(5)),
        (samples((0, 1.0), (5, 2.0)), at(0), at(10)),
        (samples((5, 1.0), (10, 2.0)), at(0), at(10)),
        (samples((0, 1.0), (0, 2.0), (5, 3.0)), at(0), at(5)),
        (samples((5, 1.0), (0, 2.0)), at(0), at(5)),
        (samples((0, 1.0), (5, 2.0)), at(5), at(5)),
        (samples((0, 1.0), (5, 2.0)), at(5), at(0)),
    ],
)
def test_invalid_station_samples_fail_loudly(rows, start, end):
    with pytest.raises(ValueError):
        station_intervals(rows, 6.0, start, end)


def test_naive_bounds_are_rejected():
    with pytest.raises(ValueError):
        station_intervals(
            samples((0, 1.0), (5, 2.0)), 6.0, at(0).replace(tzinfo=None), at(5)
        )


# ---------------------------------------------------------- communication windows


def interval(a, b, state, **extra):
    return {
        "start_utc": iso(at(a)),
        "end_utc": iso(at(b)),
        "state": state,
        "duration_s": (b - a) * 60.0,
        **extra,
    }


def test_window_requires_both_earth_and_station():
    earth = [
        interval(0, 30, "blocked"),
        interval(30, 90, "visible"),
        interval(90, 120, "blocked"),
    ]
    stations = {
        "DSS-14": [interval(0, 60, "below"), interval(60, 120, "above")],
        "DSS-63": [interval(0, 45, "above"), interval(45, 120, "below")],
    }
    result = communication_windows(earth, stations, at(0), at(120))
    assert states(result["stations"]["DSS-14"]["intervals"]) == [
        (at(0), at(60), "unavailable"),
        (at(60), at(90), "available"),
        (at(90), at(120), "unavailable"),
    ]
    assert states(result["stations"]["DSS-63"]["intervals"]) == [
        (at(0), at(30), "unavailable"),
        (at(30), at(45), "available"),
        (at(45), at(120), "unavailable"),
    ]
    combined = result["combined"]["intervals"]
    assert_partition(combined, at(0), at(120))
    assert [
        (parse(i["start_utc"]), parse(i["end_utc"]), i["state"], i["station_ids"])
        for i in combined
    ] == [
        (at(0), at(30), "unavailable", []),
        (at(30), at(45), "available", ["DSS-63"]),
        (at(45), at(60), "unavailable", []),
        (at(60), at(90), "available", ["DSS-14"]),
        (at(90), at(120), "unavailable", []),
    ]
    metrics = result["combined"]["metrics"]
    assert metrics["available_duration_s"] == 45 * 60
    assert metrics["unavailable_duration_s"] == 75 * 60
    assert metrics["unknown_duration_s"] == 0
    assert metrics["window_count"] == 2
    assert metrics["longest_available_s"] == 30 * 60
    assert abs(metrics["available_percent_of_interval"] - 37.5) < 1e-9
    station = result["stations"]["DSS-63"]["metrics"]
    assert station["available_duration_s"] == 15 * 60 and station["window_count"] == 1


def test_overlapping_stations_split_coverage_but_count_one_window():
    earth = [interval(0, 60, "visible")]
    stations = {
        "DSS-14": [interval(0, 40, "above"), interval(40, 60, "below")],
        "DSS-43": [interval(0, 20, "below"), interval(20, 60, "above")],
    }
    result = communication_windows(earth, stations, at(0), at(60))
    combined = result["combined"]["intervals"]
    assert [(i["state"], i["station_ids"]) for i in combined] == [
        ("available", ["DSS-14"]),
        ("available", ["DSS-14", "DSS-43"]),
        ("available", ["DSS-43"]),
    ]
    assert result["combined"]["metrics"]["window_count"] == 1
    assert result["combined"]["metrics"]["longest_available_s"] == 3600
    assert result["combined"]["metrics"]["available_percent_of_interval"] == 100


@pytest.mark.parametrize(
    "earth_state, station_state, expected",
    [
        ("visible", "above", "available"),
        ("visible", "below", "unavailable"),
        ("visible", "unknown", "unknown"),
        ("blocked", "above", "unavailable"),
        ("blocked", "unknown", "unavailable"),
        ("boundary", "above", "unavailable"),
        ("unknown", "above", "unknown"),
        ("unknown", "below", "unavailable"),
        ("unknown", "unknown", "unknown"),
    ],
)
def test_state_truth_table(earth_state, station_state, expected):
    result = communication_windows(
        [interval(0, 10, earth_state)],
        {"DSS-14": [interval(0, 10, station_state)]},
        at(0),
        at(10),
    )
    assert result["stations"]["DSS-14"]["intervals"][0]["state"] == expected
    assert result["combined"]["intervals"][0]["state"] == expected


def test_any_available_station_beats_unknown_in_combination():
    result = communication_windows(
        [interval(0, 10, "visible")],
        {"DSS-14": [interval(0, 10, "unknown")], "DSS-63": [interval(0, 10, "above")]},
        at(0),
        at(10),
    )
    combined = result["combined"]["intervals"]
    assert [(i["state"], i["station_ids"]) for i in combined] == [
        ("available", ["DSS-63"])
    ]
    assert result["combined"]["metrics"]["unknown_duration_s"] == 0


def test_metrics_partition_the_interval_and_stay_physical():
    earth = [
        interval(0, 10, "unknown"),
        interval(10, 50, "visible"),
        interval(50, 60, "blocked"),
    ]
    stations = {"DSS-43": [interval(0, 25, "above"), interval(25, 60, "below")]}
    result = communication_windows(earth, stations, at(0), at(60))
    for part in [result["combined"], result["stations"]["DSS-43"]]:
        m = part["metrics"]
        total = (
            m["available_duration_s"]
            + m["unavailable_duration_s"]
            + m["unknown_duration_s"]
        )
        assert abs(total - 3600) < 1e-6
        assert 0 <= m["available_percent_of_interval"] <= 100
        assert m["longest_available_s"] <= m["available_duration_s"]
    assert result["combined"]["metrics"]["unknown_duration_s"] == 600


@pytest.mark.parametrize(
    "earth, stations",
    [
        ([interval(0, 10, "visible")], {}),
        ([interval(0, 5, "visible")], {"DSS-14": [interval(0, 10, "above")]}),
        ([interval(0, 10, "visible")], {"DSS-14": [interval(0, 5, "above")]}),
        (
            [interval(0, 4, "visible"), interval(5, 10, "visible")],
            {"DSS-14": [interval(0, 10, "above")]},
        ),
        ([interval(0, 10, "sunny")], {"DSS-14": [interval(0, 10, "above")]}),
        ([interval(0, 10, "visible")], {"DSS-14": [interval(0, 10, "available")]}),
    ],
)
def test_inconsistent_inputs_fail_loudly(earth, stations):
    with pytest.raises(ValueError):
        communication_windows(earth, stations, at(0), at(10))


def test_earth_intervals_may_extend_beyond_the_window():
    result = communication_windows(
        [interval(-10, 30, "visible")],
        {"DSS-14": [interval(0, 20, "above")]},
        at(0),
        at(20),
    )
    assert states(result["combined"]["intervals"]) == [(at(0), at(20), "available")]


# -------------------------------------------------------------- request and samples


def analysis_job(**submission):
    body = (
        dict(
            site_id="malapert-massif-r23",
            latitude_deg=-85.9948,
            longitude_deg_east=359.76422,
            start_utc="2026-11-04T00:02:00Z",
            end_utc="2026-11-05T00:02:00Z",
            step_s=3600,
        )
        | submission
    )
    return {"id": str(uuid4()), "status": "ready", "submission": body}


def test_dsn_request_covers_the_analysis_on_an_aligned_grid():
    request_id = uuid4()
    request = dsn_request_for(analysis_job(), request_id)
    assert isinstance(request, DsnRequest)
    assert request.request_id == request_id
    assert request.step_s == 300
    assert request.start_utc == datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
    assert request.end_utc == datetime(2026, 11, 5, 0, 5, tzinfo=timezone.utc)
    assert request.station_ids == ("DSS-14", "DSS-43", "DSS-63")
    aligned = dsn_request_for(
        analysis_job(start_utc="2026-11-04T00:00:00Z", end_utc="2026-11-05T00:00:00Z"),
        request_id,
    )
    assert aligned.end_utc == datetime(2026, 11, 5, tzinfo=timezone.utc)
    fine = dsn_request_for(
        analysis_job(
            step_s=60, start_utc="2026-11-04T00:02:00Z", end_utc="2026-11-04T06:02:00Z"
        ),
        request_id,
    )
    assert fine.step_s == 60 and fine.start_utc == at(2) and fine.end_utc == at(362)


def write_bundle(root, data, tamper=None):
    key = hashlib.sha256(b"bundle").hexdigest()
    path = root / "dsn" / key
    path.mkdir(parents=True)
    (path / "samples.json").write_text(json.dumps(data))
    hashes = {
        "samples.json": hashlib.sha256((path / "samples.json").read_bytes()).hexdigest()
    }
    if tamper:
        hashes = tamper(hashes)
    (path / "manifest.json").write_text(
        json.dumps({"cache_key": key, "sha256": hashes})
    )
    return "dsn/" + key


def test_load_verified_samples(tmp_path):
    data = {"DSS-14": samples((0, 1.0), (5, 2.0))}
    assert load_dsn_samples(tmp_path, write_bundle(tmp_path, data)) == data


@pytest.mark.parametrize(
    "tamper",
    [
        lambda h: {"samples.json": "0" * 64},
        lambda h: {**h, "../escape.json": "0" * 64},
        lambda h: {},
    ],
)
def test_corrupt_bundles_are_rejected(tmp_path, tamper):
    ref = write_bundle(tmp_path, {"DSS-14": []}, tamper)
    with pytest.raises(ValueError):
        load_dsn_samples(tmp_path, ref)


@pytest.mark.parametrize("ref", ["dsn/../x", "horizons/" + "a" * 64, "dsn/" + "a" * 64])
def test_bad_or_missing_references_are_rejected(tmp_path, ref):
    with pytest.raises(ValueError):
        load_dsn_samples(tmp_path, ref)


def test_request_model_bounds():
    assert CommunicationRequest().min_elevation_deg == DEFAULT_MIN_ELEVATION_DEG == 6.0
    assert CommunicationRequest(min_elevation_deg=10.0).min_elevation_deg == 10.0
    for value in [5.9, 90.1, float("nan"), "high"]:
        with pytest.raises(ValidationError):
            CommunicationRequest(min_elevation_deg=value)


# ------------------------------------------------------------------- store and jobs


@pytest.fixture
def stores(tmp_path):
    analyses = JobStore(tmp_path / "jobs.sqlite")
    return analyses, CommunicationStore(analyses)


def ready_analysis(analyses, earth_intervals, status="ready"):
    submission = Submission(
        site_id="malapert-massif-r23",
        latitude_deg=-85.9948,
        longitude_deg_east=359.76422,
        start_utc="2026-11-04T00:00:00Z",
        end_utc="2026-11-04T01:00:00Z",
        step_s=3600,
        refine=False,
    )
    job = analyses.create(submission)
    analyses.update(
        job["id"],
        status,
        result={"analysis": {"earth": {"intervals": earth_intervals}}, "frames": []},
    )
    return job["id"]


def test_store_lifecycle_and_recovery(stores):
    analyses, communications = stores
    job = communications.create(
        "analysis-1", CommunicationRequest(min_elevation_deg=10)
    )
    assert job["status"] == "pending"
    assert job["analysis_id"] == "analysis-1"
    assert job["request"] == {"min_elevation_deg": 10.0}
    assert job["result"] is None and job["error"] is None
    assert communications.get(job["id"]) == job
    assert communications.get("missing") is None
    assert [j["id"] for j in communications.pending()] == [job["id"]]
    communications.update(job["id"], "acquiring")
    assert communications.pending() == []
    communications.recover()
    assert communications.get(job["id"])["status"] == "pending"
    communications.update(job["id"], "ready", result={"ok": True})
    assert communications.get(job["id"])["result"] == {"ok": True}
    with pytest.raises(ValueError):
        communications.update(job["id"], "exploded")


def test_store_bounds_active_jobs(stores):
    _, communications = stores
    for _ in range(4):
        communications.create("a", CommunicationRequest())
    with pytest.raises(ValueError):
        communications.create("a", CommunicationRequest())


def dsn_rows(elevation):
    return samples(*[(m, elevation) for m in range(0, 65, 5)])


def run_job(
    stores, tmp_path, monkeypatch, earth, station_rows, analysis_status="ready"
):
    analyses, communications = stores
    analysis_id = ready_analysis(analyses, earth, analysis_status)
    ref = write_bundle(tmp_path, station_rows)
    seen = []

    async def fake_submit(request, broker_url):
        seen.append(request)
        return DsnEvent(request_id=request.request_id, status="ready", dsn_path=ref)

    monkeypatch.setattr(module, "submit_dsn", fake_submit)
    job = communications.create(analysis_id, CommunicationRequest())
    asyncio.run(
        execute_communication(communications, analyses, job, tmp_path, "amqp://t")
    )
    return communications.get(job["id"]), seen


def test_job_computes_and_stores_windows(stores, tmp_path, monkeypatch):
    earth = [interval(0, 30, "visible"), interval(30, 60, "blocked")]
    rows = {"DSS-14": dsn_rows(20.0), "DSS-43": dsn_rows(-5.0), "DSS-63": dsn_rows(3.0)}
    job, seen = run_job(stores, tmp_path, monkeypatch, earth, rows)
    assert job["status"] == "ready", job["error"]
    assert seen[0].station_ids == ("DSS-14", "DSS-43", "DSS-63")
    result = job["result"]
    assert result["min_elevation_deg"] == 6.0
    assert result["start_utc"] == "2026-11-04T00:00:00Z"
    assert result["end_utc"] == "2026-11-04T01:00:00Z"
    assert [s["station_id"] for s in result["stations"]] == [
        "DSS-14",
        "DSS-43",
        "DSS-63",
    ]
    assert result["stations"][0]["complex"] == "Goldstone"
    assert "810-005" in result["source"]["station_document"]
    assert result["limitations"] == list(LIMITATIONS)
    combined = result["windows"]["combined"]
    assert [(i["state"], i["station_ids"]) for i in combined["intervals"]] == [
        ("available", ["DSS-14"]),
        ("unavailable", []),
    ]
    assert combined["metrics"]["available_duration_s"] == 1800


def test_unknown_coverage_is_reported_as_insufficient(stores, tmp_path, monkeypatch):
    earth = [interval(0, 60, "unknown")]
    rows = {k: dsn_rows(20.0) for k in ("DSS-14", "DSS-43", "DSS-63")}
    job, _ = run_job(stores, tmp_path, monkeypatch, earth, rows)
    assert job["status"] == "insufficient_data"
    assert job["result"]["windows"]["combined"]["metrics"]["unknown_duration_s"] == 3600


def test_job_fails_visibly(stores, tmp_path, monkeypatch):
    analyses, communications = stores
    job = communications.create("missing", CommunicationRequest())
    asyncio.run(
        execute_communication(communications, analyses, job, tmp_path, "amqp://t")
    )
    assert communications.get(job["id"])["status"] == "failed"
    assert "Analysis" in communications.get(job["id"])["error"]

    analysis_id = ready_analysis(analyses, [interval(0, 60, "visible")])

    async def failed(request, broker_url):
        return DsnEvent(
            request_id=request.request_id,
            status="failed",
            error_code="source_unavailable",
            error_message="Horizons timeout",
        )

    monkeypatch.setattr(module, "submit_dsn", failed)
    job = communications.create(analysis_id, CommunicationRequest())
    asyncio.run(
        execute_communication(communications, analyses, job, tmp_path, "amqp://t")
    )
    stored = communications.get(job["id"])
    assert stored["status"] == "failed" and "Horizons timeout" in stored["error"]

    async def broken(request, broker_url):
        raise RuntimeError("broker down")

    monkeypatch.setattr(module, "submit_dsn", broken)
    job = communications.create(analysis_id, CommunicationRequest())
    asyncio.run(
        execute_communication(communications, analyses, job, tmp_path, "amqp://t")
    )
    assert communications.get(job["id"])["status"] == "failed"
    assert communications.get(job["id"])["error"]


def test_request_id_is_stable_per_job(stores, tmp_path, monkeypatch):
    rows = {k: dsn_rows(20.0) for k in ("DSS-14", "DSS-43", "DSS-63")}
    analyses, communications = stores
    analysis_id = ready_analysis(analyses, [interval(0, 60, "visible")])
    ref = write_bundle(tmp_path, rows)
    seen = []

    async def fake_submit(request, broker_url):
        seen.append(request.request_id)
        return DsnEvent(request_id=request.request_id, status="ready", dsn_path=ref)

    monkeypatch.setattr(module, "submit_dsn", fake_submit)
    job = communications.create(analysis_id, CommunicationRequest())
    for _ in range(2):
        asyncio.run(
            execute_communication(communications, analyses, job, tmp_path, "amqp://t")
        )
    assert seen[0] == seen[1]


# ------------------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as c:
        yield c


def test_routes(client):
    assert client.post("/analyses/missing/communication", json={}).status_code == 404
    analyses = client.app.state.store
    pending = analyses.create(
        Submission(
            latitude_deg=-85.9948,
            longitude_deg_east=359.76422,
            start_utc="2026-11-04T00:00:00Z",
            end_utc="2026-11-04T01:00:00Z",
        )
    )
    assert (
        client.post(f"/analyses/{pending['id']}/communication", json={}).status_code
        == 409
    )
    ready_id = ready_analysis(analyses, [interval(0, 60, "visible")])
    response = client.post(
        f"/analyses/{ready_id}/communication", json={"min_elevation_deg": 10}
    )
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "pending" and job["request"] == {"min_elevation_deg": 10.0}
    fetched = client.get(f"/communications/{job['id']}")
    assert fetched.status_code == 200 and fetched.json()["id"] == job["id"]
    assert client.get("/communications/missing").status_code == 404
    assert (
        client.post(
            f"/analyses/{ready_id}/communication", json={"min_elevation_deg": 2}
        ).status_code
        == 422
    )
    insufficient = ready_analysis(
        analyses, [interval(0, 60, "unknown")], "insufficient_data"
    )
    assert (
        client.post(f"/analyses/{insufficient}/communication", json={}).status_code
        == 202
    )
    for _ in range(2):
        assert (
            client.post(f"/analyses/{ready_id}/communication", json={}).status_code
            == 202
        )
    assert (
        client.post(f"/analyses/{ready_id}/communication", json={}).status_code == 429
    )
    assert client.app.state.communications.get(job["id"])["analysis_id"] == ready_id
