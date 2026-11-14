"""R2 worker tests: Moon seen from DSN stations via JPL Horizons, offline.

Reference responses were captured on 2026-11-14 (see fixtures/README.md).
Station coordinates: DSN 810-005 module 301 Rev. P, Table 5 (WGS84).
"""

import asyncio
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from app import dsn as d
from shared.dsn import DsnEvent, DsnRequest, load_stations

FIXTURES = Path(__file__).parent / "fixtures"


def station(station_id):
    return load_stations()[station_id]


SITE_COORDS = {
    "DSS-14": "'243.110461788889,35.4259008666667,1.00139'",
    "DSS-43": "'148.981267308333,-35.4024242333333,0.688867'",
    "DSS-63": "'355.751991455556,40.43120975,0.864816'",
}
BY_SITE = {coord: station_id for station_id, coord in SITE_COORDS.items()}
# Values printed by Horizons with EXTRA_PREC; 1e-9 deg is a parsing tolerance.
ANGLE_TOL = 1e-9


def make_request(**changes):
    body = dict(
        request_id=str(uuid4()),
        start_utc="2026-11-04T00:00:00Z",
        end_utc="2026-11-04T12:00:00Z",
        step_s=300,
        station_ids=["DSS-14", "DSS-43", "DSS-63"],
    )
    body.update(changes)
    return DsnRequest.model_validate(body)


def payload(station_id):
    return json.loads((FIXTURES / f"dsn_{station_id}.json").read_text())


def fixture_handler(calls=None):
    def handle(req):
        if calls is not None:
            calls.append(req)
        station_id = BY_SITE[req.url.params["SITE_COORD"]]
        return httpx.Response(
            200, content=(FIXTURES / f"dsn_{station_id}.json").read_bytes()
        )

    return handle


def client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def no_network(req):
    raise AssertionError("Network must not be used")


def test_query_contract_matches_captured_reference():
    request = make_request()
    for station_id, coord in SITE_COORDS.items():
        params = d.build_station_params(request, station(station_id))
        assert params == {
            "format": "json",
            "COMMAND": "'301'",
            "MAKE_EPHEM": "YES",
            "OBJ_DATA": "NO",
            "EPHEM_TYPE": "OBSERVER",
            "CENTER": "coord@399",
            "COORD_TYPE": "GEODETIC",
            "SITE_COORD": coord,
            "START_TIME": "'2026-11-04 00:00:00'",
            "STOP_TIME": "'2026-11-04 12:00:00'",
            "STEP_SIZE": "'5 m'",
            "QUANTITIES": "'4'",
            "APPARENT": "AIRLESS",
            "CSV_FORMAT": "YES",
            "EXTRA_PREC": "YES",
            "ELEV_CUT": "-90",
            "SKIP_DAYLT": "NO",
            "TIME_TYPE": "UT",
            "TIME_DIGITS": "SECONDS",
        }


def test_one_minute_step():
    request = make_request(step_s=60, end_utc="2026-11-04T01:00:00Z")
    assert d.build_station_params(request, station("DSS-14"))["STEP_SIZE"] == "'1 m'"


def test_station_must_belong_to_request():
    request = make_request(station_ids=["DSS-14"])
    with pytest.raises(ValueError):
        d.build_station_params(request, station("DSS-43"))


def test_cache_key_identity():
    base = make_request()
    key = d.dsn_cache_key(base)
    assert len(key) == 64 and int(key, 16) >= 0
    assert d.dsn_cache_key(make_request()) == key  # request UUID is excluded
    assert (
        d.dsn_cache_key(make_request(station_ids=["DSS-63", "DSS-43", "DSS-14"])) == key
    )
    for changes in [
        {"station_ids": ["DSS-14"]},
        {"step_s": 60, "end_utc": "2026-11-04T01:00:00Z"},
        {"end_utc": "2026-11-04T11:55:00Z"},
        {"start_utc": "2026-11-04T00:05:00Z"},
    ]:
        assert d.dsn_cache_key(make_request(**changes)) != key


@pytest.mark.parametrize(
    "station_id, first, last",
    [
        ("DSS-14", (299.177147639, -24.812696132), (105.440267386, 26.601901521)),
        ("DSS-43", (318.060750884, 36.770357862), (135.315830763, -50.842755271)),
        ("DSS-63", (63.294937709, -18.676855972), (247.826954375, 29.106114138)),
    ],
)
def test_parse_reference_responses(station_id, first, last):
    samples = d.parse_station_response(
        payload(station_id), make_request(), station(station_id)
    )
    assert len(samples) == 145
    start = datetime(2026, 11, 4, tzinfo=timezone.utc)
    for index, sample in enumerate(samples):
        assert set(sample) == {"sampled_at_utc", "azimuth_deg", "elevation_deg"}
        assert sample["sampled_at_utc"] == (
            start + timedelta(minutes=5 * index)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Physical sanity for horizontal coordinates.
        assert 0 <= sample["azimuth_deg"] < 360
        assert -90 <= sample["elevation_deg"] <= 90
    for sample, (az, el) in [(samples[0], first), (samples[-1], last)]:
        assert math.isclose(sample["azimuth_deg"], az, rel_tol=0, abs_tol=ANGLE_TOL)
        assert math.isclose(sample["elevation_deg"], el, rel_tol=0, abs_tol=ANGLE_TOL)


def test_reference_six_degree_crossing_samples():
    # DSS-14 moonrise through 6 deg between 10:10 and 10:15 UTC (fixture rows 122-123).
    samples = d.parse_station_response(
        payload("DSS-14"), make_request(), station("DSS-14")
    )
    assert samples[122]["sampled_at_utc"] == "2026-11-04T10:10:00Z"
    assert math.isclose(samples[122]["elevation_deg"], 5.276262695, abs_tol=ANGLE_TOL)
    assert math.isclose(samples[123]["elevation_deg"], 6.253229782, abs_tol=ANGLE_TOL)


def mutate(station_id, old, new):
    body = payload(station_id)
    assert old in body["result"]
    body["result"] = body["result"].replace(old, new, 1)
    return body


@pytest.mark.parametrize(
    "body",
    [
        {"error": "Cannot interpret date"},
        {
            "result": "no table",
            "signature": {"source": "NASA/JPL Horizons API", "version": "1.2"},
        },
        {**payload("DSS-14"), "signature": {"source": "other", "version": "1"}},
        mutate(
            "DSS-14", "Target body name: Moon (301)", "Target body name: Mars (499)"
        ),
        mutate(
            "DSS-14", "Center body name: Earth (399)", "Center body name: Moon (301)"
        ),
        mutate(
            "DSS-14", "243.1104618,35.4259009,1.00139", "243.1105618,35.4259009,1.00139"
        ),
        mutate(
            "DSS-14", "243.1104618,35.4259009,1.00139", "243.1104618,35.4259009,1.00239"
        ),
        mutate(
            "DSS-14", "6378.137, 6378.137, 6356.752", "6378.137, 6378.137, 6378.137"
        ),
        mutate("DSS-14", "Atmos refraction: NO (AIRLESS)", "Atmos refraction: YES"),
        mutate("DSS-14", "Elevation_(a-app)", "Elev_(a-app)"),
        mutate("DSS-14", " 2026-Nov-04 00:05:00", " 2026-Nov-04 00:06:00"),
        mutate("DSS-14", "-24.812696132", "-94.812696132"),
        mutate("DSS-14", "-24.812696132", "abc"),
        mutate("DSS-14", "$$EOE", "$$EOE\n$$EOE"),
    ],
)
def test_invalid_responses_fail_loudly(body):
    with pytest.raises(d.DsnSourceError):
        d.parse_station_response(body, make_request(), station("DSS-14"))


def test_incomplete_grid_fails():
    shorter = make_request(end_utc="2026-11-04T12:05:00Z")
    with pytest.raises(d.DsnSourceError):
        d.parse_station_response(payload("DSS-14"), shorter, station("DSS-14"))


def test_unavailable_elevation_is_kept_unknown():
    body = mutate("DSS-63", "-18.676855972", "n.a.")
    samples = d.parse_station_response(body, make_request(), station("DSS-63"))
    assert samples[0]["elevation_deg"] is None
    assert samples[1]["elevation_deg"] is not None


def bundle_files(path):
    return sorted(
        str(p.relative_to(path)).replace("\\", "/")
        for p in path.rglob("*")
        if p.is_file()
    )


def test_download_writes_atomic_checksummed_bundle(tmp_path):
    request = make_request()
    calls = []
    with client_with(fixture_handler(calls)) as client:
        result = d.download_dsn(request, tmp_path, client)
    key = d.dsn_cache_key(request)
    path = tmp_path / "dsn" / key
    assert result == {"cache_key": key, "path": str(path), "cache_hit": False}
    assert len(calls) == 3
    assert bundle_files(path) == [
        "manifest.json",
        "raw/DSS-14.json",
        "raw/DSS-43.json",
        "raw/DSS-63.json",
        "samples.json",
    ]
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["cache_key"] == key
    assert manifest["cache_version"] == d.CACHE_VERSION
    assert manifest["parser_version"] == d.PARSER_VERSION
    assert "request_id" not in manifest["request"]
    assert set(manifest["sources"]) == {"DSS-14", "DSS-43", "DSS-63"}
    for station_id, source in manifest["sources"].items():
        assert source["params"]["SITE_COORD"] == SITE_COORDS[station_id]
        assert source["url"].startswith("https://ssd.jpl.nasa.gov/api/horizons.api")
        assert source["acquired_at_utc"].endswith("Z")
    assert manifest["stations"]["DSS-14"]["complex"] == "Goldstone"
    assert "810-005" in manifest["station_source"]["document"]
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((path / name).read_bytes()).hexdigest() == digest
    assert set(manifest["sha256"]) == {
        "raw/DSS-14.json",
        "raw/DSS-43.json",
        "raw/DSS-63.json",
        "samples.json",
    }
    samples = json.loads((path / "samples.json").read_text())
    assert set(samples) == {"DSS-14", "DSS-43", "DSS-63"}
    assert all(len(rows) == 145 for rows in samples.values())
    assert (path / "raw/DSS-14.json").read_bytes() == (
        FIXTURES / "dsn_DSS-14.json"
    ).read_bytes()
    assert not [
        p for p in (tmp_path / "dsn").iterdir() if p.name.startswith(".pending")
    ]


def test_cache_hit_uses_no_network(tmp_path):
    request = make_request()
    with client_with(fixture_handler()) as client:
        first = d.download_dsn(request, tmp_path, client)
    with client_with(no_network) as client:
        second = d.download_dsn(make_request(), tmp_path, client)
    assert second == {**first, "cache_hit": True}


def test_corrupt_cache_is_never_silently_replaced(tmp_path):
    request = make_request()
    with client_with(fixture_handler()) as client:
        result = d.download_dsn(request, tmp_path, client)
    (Path(result["path"]) / "samples.json").write_text("{}")
    with client_with(no_network) as client, pytest.raises(d.DsnSourceError):
        d.download_dsn(request, tmp_path, client)


def test_source_failure_leaves_no_bundle(tmp_path):
    def bad(req):
        station_id = BY_SITE[req.url.params["SITE_COORD"]]
        if station_id == "DSS-63":
            return httpx.Response(200, json={"error": "service failure"})
        return fixture_handler()(req)

    with client_with(bad) as client, pytest.raises(d.DsnSourceError):
        d.download_dsn(make_request(), tmp_path, client)
    root = tmp_path / "dsn"
    assert not root.exists() or not [p for p in root.iterdir() if p.is_dir()]


def test_http_error_propagates(tmp_path):
    with client_with(lambda req: httpx.Response(404)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            d.download_dsn(make_request(), tmp_path, client)


def test_job_is_idempotent_and_replays_without_network(tmp_path):
    request = make_request()
    with client_with(fixture_handler()) as client:
        event = d.process_dsn_job(request, tmp_path, client)
    assert isinstance(event, DsnEvent)
    assert event.status == "ready"
    assert event.request_id == request.request_id
    assert event.dsn_path == "dsn/" + d.dsn_cache_key(request)
    with client_with(no_network) as client:
        assert d.process_dsn_job(request, tmp_path, client) == event
    stored = tmp_path / "dsn-jobs" / str(request.request_id)
    assert json.loads((stored / "request.json").read_text()) == request.model_dump(
        mode="json"
    )
    assert DsnEvent.model_validate_json((stored / "result.json").read_text()) == event


def test_reused_uuid_with_other_parameters_conflicts(tmp_path):
    request = make_request()
    with client_with(fixture_handler()) as client:
        d.process_dsn_job(request, tmp_path, client)
        other = request.model_copy(update={"station_ids": ("DSS-14",)})
        with pytest.raises(d.DsnRequestConflict):
            d.process_dsn_job(other, tmp_path, client)


def test_source_and_network_failures_become_failed_events(tmp_path):
    with client_with(lambda req: httpx.Response(200, json={"error": "bad"})) as client:
        event = d.process_dsn_job(make_request(), tmp_path, client)
    assert event.status == "failed" and event.error_code == "source_invalid"
    assert event.error_message and len(event.error_message) <= 1000
    with client_with(lambda req: httpx.Response(404)) as client:
        event = d.process_dsn_job(make_request(), tmp_path, client)
    assert event.status == "failed" and event.error_code == "source_unavailable"


def message(body):
    msg = Mock()
    msg.body = body
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()
    return msg


@pytest.mark.parametrize("body", [b"not json", b'{"schema_version": 1}', b"x" * 70000])
def test_invalid_messages_are_dead_lettered(tmp_path, body):
    msg, exchange = message(body), Mock(publish=AsyncMock())
    asyncio.run(d.handle_dsn_message(msg, exchange, tmp_path))
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()
    exchange.publish.assert_not_awaited()


def test_event_is_published_before_ack(tmp_path, monkeypatch):
    request = make_request()
    ready = DsnEvent(
        request_id=request.request_id, status="ready", dsn_path="dsn/" + "c" * 64
    )
    monkeypatch.setattr(d, "process_dsn_job", lambda req, root, client=None: ready)
    order = []
    msg = message(request.model_dump_json().encode())
    msg.ack = AsyncMock(side_effect=lambda: order.append("ack"))
    exchange = Mock(
        publish=AsyncMock(side_effect=lambda *a, **k: order.append("publish"))
    )
    asyncio.run(d.handle_dsn_message(msg, exchange, tmp_path))
    assert order == ["publish", "ack"]
    published = exchange.publish.call_args
    assert published.kwargs["routing_key"] == str(request.request_id)
    assert published.kwargs["mandatory"] is True
    sent = published.args[0]
    assert DsnEvent.model_validate_json(sent.body) == ready
    assert sent.correlation_id == str(request.request_id)


def test_conflict_is_published_as_failed_event(tmp_path, monkeypatch):
    request = make_request()

    def conflict(req, root, client=None):
        raise d.DsnRequestConflict("request_id already used")

    monkeypatch.setattr(d, "process_dsn_job", conflict)
    msg, exchange = (
        message(request.model_dump_json().encode()),
        Mock(publish=AsyncMock()),
    )
    asyncio.run(d.handle_dsn_message(msg, exchange, tmp_path))
    event = DsnEvent.model_validate_json(exchange.publish.call_args.args[0].body)
    assert event.status == "failed" and event.error_code == "request_conflict"
    msg.ack.assert_awaited_once()
