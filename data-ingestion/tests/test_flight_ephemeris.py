"""Flight ephemeris worker: Horizons VECTORS for Moon (301) and Sun (10), offline.

Fixtures are verbatim Horizons captures (see fixtures/README.md).
"""

import asyncio
import hashlib
import json
import math
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from app import flight_ephemeris as f
from shared.flight_ephemeris import FlightEphemerisEvent, FlightEphemerisRequest

FIXTURES = Path(__file__).parent / "fixtures"


def make_request(**changes):
    body = dict(
        request_id=str(uuid4()),
        start_utc="2026-11-03T22:00:00Z",
        end_utc="2026-11-04T00:00:00Z",
        step_s=1800,
    )
    body.update(changes)
    return FlightEphemerisRequest.model_validate(body)


def payload(target):
    return json.loads((FIXTURES / f"flight_vectors_{target}.json").read_text())


def target_of(req):
    return req.url.params["COMMAND"].strip("'")


def fixture_handler(calls=None):
    def handle(req):
        if calls is not None:
            calls.append(req)
        return httpx.Response(
            200, content=(FIXTURES / f"flight_vectors_{target_of(req)}.json").read_bytes()
        )

    return handle


def client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def no_network(req):
    raise AssertionError("Network must not be used")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda seconds: None)


# ---------------------------------------------------------------- query


def test_query_matches_the_captured_reference():
    assert f.build_vector_params(make_request(), "301") == {
        "format": "json",
        "COMMAND": "'301'",
        "MAKE_EPHEM": "YES",
        "OBJ_DATA": "NO",
        "EPHEM_TYPE": "VECTORS",
        "CENTER": "'500@399'",
        "REF_PLANE": "ECLIPTIC",
        "REF_SYSTEM": "ICRF",
        "VEC_TABLE": "1",
        "VEC_LABELS": "NO",
        "OUT_UNITS": "KM-S",
        "CSV_FORMAT": "YES",
        "TIME_TYPE": "UT",
        "START_TIME": "'2026-11-03 22:00:00'",
        "STOP_TIME": "'2026-11-04 00:00:00'",
        "STEP_SIZE": "'30 m'",
    }
    assert f.build_vector_params(make_request(step_s=600), "10")["STEP_SIZE"] == "'10 m'"
    assert f.build_vector_params(make_request(), "10")["COMMAND"] == "'10'"


def test_unknown_target_is_rejected():
    with pytest.raises(ValueError):
        f.build_vector_params(make_request(), "399")


def test_cache_key_ignores_request_id():
    a, b = make_request(), make_request()
    assert f.flight_cache_key(a) == f.flight_cache_key(b)
    key = f.flight_cache_key(a)
    assert len(key) == 64 and int(key, 16) >= 0
    assert key != f.flight_cache_key(make_request(step_s=600))
    assert key != f.flight_cache_key(make_request(end_utc="2026-11-04T00:30:00Z"))


# ---------------------------------------------------------------- parsing


def test_moon_vectors_parse_on_the_request_grid():
    rows = f.parse_vector_response(payload("301"), make_request(), "301")
    assert [r["utc"] for r in rows] == [
        "2026-11-03T22:00:00Z",
        "2026-11-03T22:30:00Z",
        "2026-11-03T23:00:00Z",
        "2026-11-03T23:30:00Z",
        "2026-11-04T00:00:00Z",
    ]
    assert rows[0]["x_km"] == pytest.approx(-3.500858371816163e05, rel=1e-15)
    assert rows[0]["y_km"] == pytest.approx(1.470126199578525e05, rel=1e-15)
    assert rows[0]["z_km"] == pytest.approx(-6.215623113172187e03, rel=1e-15)
    for row in rows:
        distance = math.hypot(row["x_km"], row["y_km"], row["z_km"])
        assert 350_000 < distance < 410_000


def test_sun_vectors_parse():
    rows = f.parse_vector_response(payload("10"), make_request(), "10")
    assert len(rows) == 5
    assert rows[0]["x_km"] == pytest.approx(-1.117827688418785e08, rel=1e-15)
    for row in rows:
        assert 1.46e8 < math.hypot(row["x_km"], row["y_km"], row["z_km"]) < 1.53e8


def test_target_mismatch_is_rejected():
    with pytest.raises(f.FlightEphemerisSourceError):
        f.parse_vector_response(payload("10"), make_request(), "301")


def test_grid_mismatch_is_rejected():
    with pytest.raises(f.FlightEphemerisSourceError):
        f.parse_vector_response(payload("301"), make_request(step_s=600), "301")
    with pytest.raises(f.FlightEphemerisSourceError):
        f.parse_vector_response(
            payload("301"), make_request(end_utc="2026-11-04T00:30:00Z"), "301"
        )


@pytest.mark.parametrize(
    "old,new",
    [
        ("Reference frame : Ecliptic of J2000.0", "Reference frame : ICRF"),
        ("Output units    : KM-S", "Output units    : AU-D"),
        ("Center body name: Earth (399)", "Center body name: Mars (499)"),
        ("JDUT ,", "JDTDB,"),
        ("$$SOE", "$$XXX"),
    ],
)
def test_unexpected_metadata_is_rejected(old, new):
    body = payload("301")
    assert old in body["result"]
    body["result"] = body["result"].replace(old, new, 1)
    with pytest.raises(f.FlightEphemerisSourceError):
        f.parse_vector_response(body, make_request(), "301")


def test_non_finite_values_are_rejected():
    body = payload("301")
    body["result"] = body["result"].replace("-3.500858371816163E+05", "nan", 1)
    with pytest.raises(f.FlightEphemerisSourceError):
        f.parse_vector_response(body, make_request(), "301")


def test_api_error_payload_is_rejected():
    with pytest.raises(f.FlightEphemerisSourceError):
        f.parse_vector_response({"error": "bad"}, make_request(), "301")


# ---------------------------------------------------------------- download


def test_download_writes_checksummed_bundle(tmp_path):
    calls = []
    request = make_request()
    with client_with(fixture_handler(calls)) as client:
        result = f.download_flight_ephemeris(request, tmp_path, client)
    key = f.flight_cache_key(request)
    assert result == {
        "cache_key": key,
        "path": str(tmp_path / "flight_ephemeris" / key),
        "cache_hit": False,
    }
    assert sorted(target_of(c) for c in calls) == ["10", "301"]
    bundle = Path(result["path"])
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["cache_key"] == key
    assert set(manifest["sha256"]) == {"raw/301.json", "raw/10.json", "samples.json"}
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == digest
    samples = json.loads((bundle / "samples.json").read_text())
    assert set(samples) == {"moon", "sun"}
    assert samples["moon"] == f.parse_vector_response(payload("301"), request, "301")
    assert samples["sun"] == f.parse_vector_response(payload("10"), request, "10")
    assert manifest["time_scale"] == "UTC"


def test_cached_bundle_is_reused_without_network(tmp_path):
    with client_with(fixture_handler()) as client:
        first = f.download_flight_ephemeris(make_request(), tmp_path, client)
    with client_with(no_network) as client:
        second = f.download_flight_ephemeris(make_request(), tmp_path, client)
    assert second == {**first, "cache_hit": True}


def test_corrupt_cache_fails_loudly_and_is_kept(tmp_path):
    request = make_request()
    with client_with(fixture_handler()) as client:
        result = f.download_flight_ephemeris(request, tmp_path, client)
    samples = Path(result["path"]) / "samples.json"
    samples.write_text("{}")
    with client_with(no_network) as client:
        with pytest.raises(f.FlightEphemerisSourceError):
            f.download_flight_ephemeris(request, tmp_path, client)
    assert samples.read_text() == "{}"


def test_invalid_response_leaves_no_bundle(tmp_path):
    def handle(req):
        if target_of(req) == "10":
            return httpx.Response(200, json={"error": "No ephemeris"})
        return fixture_handler()(req)

    request = make_request()
    with client_with(handle) as client:
        with pytest.raises(f.FlightEphemerisSourceError):
            f.download_flight_ephemeris(request, tmp_path, client)
    assert not (tmp_path / "flight_ephemeris" / f.flight_cache_key(request)).exists()


# ---------------------------------------------------------------- jobs


def test_job_returns_ready_reference_and_replays(tmp_path):
    request = make_request()
    with client_with(fixture_handler()) as client:
        event = f.process_flight_ephemeris_job(request, tmp_path, client)
    assert event == FlightEphemerisEvent(
        request_id=request.request_id,
        status="ready",
        ephemeris_path=f"flight_ephemeris/{f.flight_cache_key(request)}",
    )
    with client_with(no_network) as client:
        assert f.process_flight_ephemeris_job(request, tmp_path, client) == event


def test_reused_request_id_conflicts(tmp_path):
    request = make_request()
    with client_with(fixture_handler()) as client:
        f.process_flight_ephemeris_job(request, tmp_path, client)
    changed = make_request(request_id=str(request.request_id), step_s=600)
    with client_with(no_network) as client:
        with pytest.raises(f.FlightEphemerisRequestConflict):
            f.process_flight_ephemeris_job(changed, tmp_path, client)


def test_http_failure_becomes_failed_event(tmp_path):
    def handle(req):
        return httpx.Response(400, text="bad request")

    with client_with(handle) as client:
        event = f.process_flight_ephemeris_job(make_request(), tmp_path, client)
    assert event.status == "failed" and event.error_code == "source_unavailable"


def test_invalid_source_becomes_failed_event(tmp_path):
    def handle(req):
        return httpx.Response(200, content=b"not json")

    with client_with(handle) as client:
        event = f.process_flight_ephemeris_job(make_request(), tmp_path, client)
    assert event.status == "failed" and event.error_code == "source_invalid"


# ---------------------------------------------------------------- messages


def message(body: bytes):
    msg = Mock(body=body)
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()
    return msg


@pytest.mark.parametrize("body", [b"not json", b'{"schema_version": 1}', b"x" * 70000])
def test_invalid_messages_are_dead_lettered(tmp_path, body):
    msg, exchange = message(body), Mock(publish=AsyncMock())
    asyncio.run(f.handle_flight_ephemeris_message(msg, exchange, tmp_path))
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()
    exchange.publish.assert_not_awaited()


def test_event_is_published_before_ack(tmp_path, monkeypatch):
    request = make_request()
    ready = FlightEphemerisEvent(
        request_id=request.request_id,
        status="ready",
        ephemeris_path="flight_ephemeris/" + "c" * 64,
    )
    monkeypatch.setattr(f, "process_flight_ephemeris_job", lambda req, root, client=None: ready)
    order = []
    msg = message(request.model_dump_json().encode())
    msg.ack = AsyncMock(side_effect=lambda: order.append("ack"))
    exchange = Mock(publish=AsyncMock(side_effect=lambda *a, **k: order.append("publish")))
    asyncio.run(f.handle_flight_ephemeris_message(msg, exchange, tmp_path))
    assert order == ["publish", "ack"]
    published = exchange.publish.call_args
    assert published.kwargs["routing_key"] == str(request.request_id)
    assert published.kwargs["mandatory"] is True
    assert FlightEphemerisEvent.model_validate_json(published.args[0].body) == ready


def test_conflict_is_published_as_failed_event(tmp_path, monkeypatch):
    request = make_request()

    def conflict(req, root, client=None):
        raise f.FlightEphemerisRequestConflict("reused")

    monkeypatch.setattr(f, "process_flight_ephemeris_job", conflict)
    msg, exchange = message(request.model_dump_json().encode()), Mock(publish=AsyncMock())
    asyncio.run(f.handle_flight_ephemeris_message(msg, exchange, tmp_path))
    event = FlightEphemerisEvent.model_validate_json(exchange.publish.call_args.args[0].body)
    assert event.status == "failed" and event.error_code == "request_conflict"
    msg.ack.assert_awaited_once()
