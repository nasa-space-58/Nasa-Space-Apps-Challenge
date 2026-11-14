"""R2 shared DSN contracts: sourced station data, request bounds and events.

Station reference values: DSN 810-005 module 301 Rev. P (2026-05-22), Table 5,
WGS84 geodetic coordinates; generic elevation limits from section 2.2.3.
"""

import asyncio
import json
import math
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared import dsn
from shared.dsn import (
    DSN_DEAD_QUEUE,
    DSN_REQUEST_QUEUE,
    DsnEvent,
    DsnRequest,
    DsnStation,
    declare_dsn_topology,
    dms_to_deg,
    generic_elevation_limits_deg,
    load_stations,
)
from shared.ingestion import EVENT_EXCHANGE

# Table 5 values converted by hand: deg + min/60 + sec/3600 (southern sign applied).
REFERENCE = {
    "DSS-14": (35.425900866667, 243.110461788889, 1001.39, "Goldstone"),
    "DSS-43": (-35.402424233333, 148.981267308333, 688.867, "Canberra"),
    "DSS-63": (40.431209750000, 355.751991455556, 864.816, "Madrid"),
}
# 0.001 arcsec (Table 5 accuracy) is 2.8e-7 degrees; 1e-9 only absorbs float error.
DEG_TOL = 1e-9


def request(**changes):
    body = dict(
        request_id=str(uuid4()),
        start_utc="2026-11-04T00:00:00Z",
        end_utc="2026-11-04T12:00:00Z",
        step_s=300,
        station_ids=["DSS-14", "DSS-43", "DSS-63"],
    )
    body.update(changes)
    return DsnRequest.model_validate(body)


@pytest.mark.parametrize(
    "value, expected",
    [
        ([35, 25, 33.24312], 35.425900866667),
        ([-35, -24, -8.72724], -35.402424233333),
        ([0, 0, 0], 0.0),
        ([243, 6, 37.66244], 243.110461788889),
        ([0, -30, 0], -0.5),
    ],
)
def test_dms_conversion(value, expected):
    assert math.isclose(dms_to_deg(value), expected, rel_tol=0, abs_tol=DEG_TOL)


@pytest.mark.parametrize(
    "value",
    [[-35, 24, 8.7], [35, -24, 0], [35, 60, 0], [35, 0, 60], [35, 0], [35, 0, 0, 0]],
)
def test_dms_rejects_mixed_signs_and_overflow(value):
    with pytest.raises(ValueError):
        dms_to_deg(value)


def test_sourced_stations_match_table_5():
    stations = load_stations()
    assert set(stations) == set(REFERENCE)
    for station_id, (lat, lon, height, complex_name) in REFERENCE.items():
        station = stations[station_id]
        assert isinstance(station, DsnStation)
        assert station.station_id == station_id
        assert station.complex == complex_name
        assert station.antenna == "70-m"
        assert station.ellipsoid == "WGS84"
        assert math.isclose(station.latitude_deg, lat, rel_tol=0, abs_tol=DEG_TOL)
        assert math.isclose(station.longitude_deg_east, lon, rel_tol=0, abs_tol=DEG_TOL)
        assert math.isclose(station.height_m, height, rel_tol=0, abs_tol=1e-9)
        # Physical sanity: longitude convention and plausible antenna heights.
        assert 0 <= station.longitude_deg_east < 360
        assert 0 < station.height_m < 2000


def test_station_file_records_source():
    document = json.loads(dsn.STATIONS_FILE.read_text())
    source = document["source"]
    assert source["url"].startswith(
        "https://deepspace.jpl.nasa.gov/dsndocs/810-005/301/"
    )
    assert "Rev. P" in source["document"]
    assert "Table 5" in source["table"]
    assert "WGS84" in source["ellipsoid"]


def test_generic_elevation_limits_are_sourced():
    assert generic_elevation_limits_deg() == {
        "receive": 6.0,
        "low_power_transmit": 10.0,
        "high_power_transmit": 13.5,
    }


def station_document(stations):
    return {
        "source": {"document": "test"},
        "generic_elevation_limits_deg": {
            "receive": 6.0,
            "low_power_transmit": 10.0,
            "high_power_transmit": 13.5,
        },
        "stations": stations,
    }


GOOD_STATION = {
    "station_id": "DSS-14",
    "complex": "Goldstone",
    "antenna": "70-m",
    "latitude_dms": [35, 25, 33.24312],
    "longitude_east_dms": [243, 6, 37.66244],
    "height_m": 1001.39,
}


@pytest.mark.parametrize(
    "stations",
    [
        [GOOD_STATION, GOOD_STATION],
        [GOOD_STATION | {"station_id": "Goldstone"}],
        [GOOD_STATION | {"latitude_dms": [95, 0, 0]}],
        [GOOD_STATION | {"longitude_east_dms": [360, 0, 0]}],
        [{k: v for k, v in GOOD_STATION.items() if k != "height_m"}],
        [],
    ],
)
def test_malformed_station_files_fail_loudly(tmp_path, stations):
    path = tmp_path / "stations.json"
    path.write_text(json.dumps(station_document(stations)))
    with pytest.raises(ValueError):
        load_stations(path)


def test_valid_request_normalizes_station_order():
    value = request(station_ids=["DSS-63", "DSS-14"])
    assert value.station_ids == ("DSS-14", "DSS-63")
    assert value.schema_version == 1
    assert value.start_utc.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("step", [60, 300])
def test_supported_steps(step):
    assert request(step_s=step).step_s == step


@pytest.mark.parametrize(
    "changes",
    [
        {"start_utc": "2026-11-04T00:00:00"},
        {"start_utc": "2026-11-04T03:00:00+03:00"},
        {"end_utc": "2026-11-04T12:00:00.500000Z"},
        {"step_s": 3600},
        {"step_s": 0},
        {"step_s": True},
        {"step_s": "300"},
        {"station_ids": []},
        {"station_ids": ["DSS-99"]},
        {"station_ids": ["DSS-14", "DSS-14"]},
        {"end_utc": "2026-11-04T00:00:00Z"},
        {"end_utc": "2026-11-03T00:00:00Z"},
        {"start_utc": "2025-12-31T23:55:00Z"},
        {"end_utc": "2028-01-01T00:05:00Z", "start_utc": "2027-12-31T00:00:00Z"},
        {"end_utc": "2026-12-05T00:05:00Z"},
        {"end_utc": "2026-11-04T12:02:00Z"},
        {"step_s": 60, "end_utc": "2026-11-11T22:41:00Z"},
        {"schema_version": 2},
        {"unexpected": 1},
        {"request_id": "not-a-uuid"},
    ],
)
def test_invalid_requests_are_rejected(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_request_boundaries_are_inclusive():
    assert (
        request(
            start_utc="2026-01-01T00:00:00Z", end_utc="2026-02-01T00:00:00Z"
        ).end_utc.isoformat()
        == "2026-02-01T00:00:00+00:00"
    )
    assert request(start_utc="2027-12-31T00:00:00Z", end_utc="2028-01-01T00:00:00Z")
    # 10000 one-minute samples is the largest accepted grid (9999 steps).
    assert request(
        step_s=60, start_utc="2026-11-04T00:00:00Z", end_utc="2026-11-10T22:39:00Z"
    )


def test_ready_and_failed_events():
    request_id = uuid4()
    ready = DsnEvent(request_id=request_id, status="ready", dsn_path="dsn/" + "a" * 64)
    assert ready.error_code is None
    failed = DsnEvent(
        request_id=request_id,
        status="failed",
        error_code="source_unavailable",
        error_message="timeout",
    )
    assert failed.dsn_path is None
    assert DsnEvent.model_validate_json(ready.model_dump_json()) == ready


@pytest.mark.parametrize(
    "body",
    [
        {"status": "ready"},
        {"status": "ready", "dsn_path": "dsn/" + "a" * 64, "error_code": "x"},
        {"status": "ready", "dsn_path": "horizons/" + "a" * 64},
        {"status": "ready", "dsn_path": "dsn/../" + "a" * 61},
        {"status": "failed", "error_code": "x"},
        {"status": "failed", "error_message": "x"},
        {
            "status": "failed",
            "error_code": "x",
            "error_message": "y",
            "dsn_path": "dsn/" + "a" * 64,
        },
        {"status": "pending", "dsn_path": "dsn/" + "a" * 64},
    ],
)
def test_inconsistent_events_are_rejected(body):
    with pytest.raises(ValidationError):
        DsnEvent(request_id=uuid4(), **body)


def test_topology_declares_durable_dead_lettered_queue_and_shared_exchange():
    channel = AsyncMock()
    queue, exchange = asyncio.run(declare_dsn_topology(channel))
    declared = {
        call.args[0]: call.kwargs for call in channel.declare_queue.call_args_list
    }
    assert declared[DSN_DEAD_QUEUE]["durable"] is True
    assert declared[DSN_REQUEST_QUEUE]["durable"] is True
    assert declared[DSN_REQUEST_QUEUE]["arguments"] == {
        "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": DSN_DEAD_QUEUE,
    }
    assert channel.declare_exchange.call_args.args[0] == EVENT_EXCHANGE
    assert channel.declare_exchange.call_args.kwargs["durable"] is True
    assert queue is channel.declare_queue.return_value
    assert exchange is channel.declare_exchange.return_value
