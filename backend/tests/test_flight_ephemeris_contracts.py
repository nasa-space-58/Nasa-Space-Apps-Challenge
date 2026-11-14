"""Shared flight-ephemeris contract, Apollo 11 timeline and queue topology."""

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.flight_ephemeris import (
    BODIES,
    EVENT_ORDER,
    FLIGHT_EPHEMERIS_DEAD_QUEUE,
    FLIGHT_EPHEMERIS_REQUEST_QUEUE,
    FlightEphemerisEvent,
    FlightEphemerisRequest,
    declare_flight_ephemeris_topology,
    load_mission_timeline,
)
from shared.ingestion import EVENT_EXCHANGE


def request(**changes):
    body = dict(
        request_id=str(uuid4()),
        start_utc="2026-10-31T01:00:00Z",
        end_utc="2026-11-04T00:00:00Z",
        step_s=1800,
    )
    body.update(changes)
    return FlightEphemerisRequest.model_validate(body)


def test_bodies_are_moon_and_sun():
    assert BODIES == {"301": "moon", "10": "sun"}


def test_valid_request():
    parsed = request()
    assert parsed.step_s == 1800
    assert parsed.schema_version == 1


@pytest.mark.parametrize("step", [60, 300, 3600, 0])
def test_unsupported_steps_are_rejected(step):
    with pytest.raises(ValidationError):
        request(step_s=step)


def test_ten_minute_step_is_supported():
    assert request(step_s=600).step_s == 600


@pytest.mark.parametrize(
    "start",
    ["2026-10-31T01:00:00", "2026-10-31T03:00:00+02:00", "2026-10-31T01:00:00.500Z"],
)
def test_timestamps_must_be_whole_utc_seconds(start):
    with pytest.raises(ValidationError):
        request(start_utc=start)


def test_end_must_follow_start():
    with pytest.raises(ValidationError):
        request(end_utc="2026-10-31T01:00:00Z")
    with pytest.raises(ValidationError):
        request(end_utc="2026-10-30T23:00:00Z")


def test_duration_is_bounded_to_seven_days():
    request(start_utc="2026-10-28T00:00:00Z", end_utc="2026-11-04T00:00:00Z")
    with pytest.raises(ValidationError):
        request(start_utc="2026-10-27T23:30:00Z", end_utc="2026-11-04T00:00:00Z")


def test_end_must_lie_on_the_step_grid():
    with pytest.raises(ValidationError):
        request(end_utc="2026-11-04T00:10:00Z")


def test_extra_fields_are_forbidden():
    with pytest.raises(ValidationError):
        request(target="301")


def test_ready_event_requires_reference_only():
    path = "flight_ephemeris/" + "a" * 64
    assert (
        FlightEphemerisEvent(request_id=uuid4(), status="ready", ephemeris_path=path).ephemeris_path
        == path
    )
    with pytest.raises(ValidationError):
        FlightEphemerisEvent(request_id=uuid4(), status="ready")
    with pytest.raises(ValidationError):
        FlightEphemerisEvent(
            request_id=uuid4(), status="ready", ephemeris_path=path, error_code="x", error_message="y"
        )


def test_failed_event_requires_error_only():
    FlightEphemerisEvent(
        request_id=uuid4(), status="failed", error_code="source_unavailable", error_message="503"
    )
    with pytest.raises(ValidationError):
        FlightEphemerisEvent(request_id=uuid4(), status="failed", error_code="x")
    with pytest.raises(ValidationError):
        FlightEphemerisEvent(
            request_id=uuid4(),
            status="failed",
            error_code="x",
            error_message="y",
            ephemeris_path="flight_ephemeris/" + "a" * 64,
        )


@pytest.mark.parametrize("path", ["flight_ephemeris/abc", "dsn/" + "a" * 64, "../x"])
def test_reference_pattern_is_enforced(path):
    with pytest.raises(ValidationError):
        FlightEphemerisEvent(request_id=uuid4(), status="ready", ephemeris_path=path)


def test_apollo_11_timeline_is_loaded_in_order():
    timeline = load_mission_timeline()
    assert tuple(timeline) == EVENT_ORDER
    assert timeline["earth_orbit_insertion"] == pytest.approx(709.9)
    assert timeline["translunar_injection"] == pytest.approx(2 * 3600 + 44 * 60 + 16.2)
    assert timeline["lunar_orbit_insertion"] == pytest.approx(75 * 3600 + 49 * 60 + 50.4)
    assert timeline["powered_descent_initiation"] == pytest.approx(102 * 3600 + 33 * 60 + 5.0)
    assert timeline["landing"] == pytest.approx(102 * 3600 + 45 * 60 + 39.9)


def write_timeline(tmp_path, events):
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps({"events_s": events}))
    return path


def test_timeline_must_be_strictly_increasing(tmp_path):
    events = dict(load_mission_timeline())
    events["lunar_orbit_insertion"] = events["translunar_injection"]
    with pytest.raises(ValueError):
        load_mission_timeline(write_timeline(tmp_path, events))


def test_timeline_must_contain_every_event(tmp_path):
    events = dict(load_mission_timeline())
    del events["landing"]
    with pytest.raises(ValueError):
        load_mission_timeline(write_timeline(tmp_path, events))


def test_unreadable_timeline_fails(tmp_path):
    with pytest.raises(ValueError):
        load_mission_timeline(tmp_path / "missing.json")


def test_topology_declares_durable_dead_lettered_queue_and_shared_exchange():
    channel = AsyncMock()
    queue, exchange = asyncio.run(declare_flight_ephemeris_topology(channel))
    declared = {call.args[0]: call.kwargs for call in channel.declare_queue.call_args_list}
    assert FLIGHT_EPHEMERIS_REQUEST_QUEUE == "lunar.flight-ephemeris.requests.v1"
    assert declared[FLIGHT_EPHEMERIS_DEAD_QUEUE]["durable"] is True
    assert declared[FLIGHT_EPHEMERIS_REQUEST_QUEUE]["arguments"] == {
        "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": FLIGHT_EPHEMERIS_DEAD_QUEUE,
    }
    assert channel.declare_exchange.call_args.args[0] == EVENT_EXCHANGE
    assert queue is channel.declare_queue.return_value
    assert exchange is channel.declare_exchange.return_value
