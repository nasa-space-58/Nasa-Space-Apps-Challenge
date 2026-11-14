"""NOAA SWPC space weather snapshot worker tests, offline.

Fixtures are trimmed captures of the four SWPC products fetched on 2026-11-14
(see fixtures/README.md). The 3-day forecast issued on 2026-11-14 12:30 UTC lists
R1-R2 25 %, R3+ 5 % and S1+ 5 % for 14-16 Nov, matching the fixture entry dated
2026-11-14; that entry's date is therefore the first forecast UTC day.
"""

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from app import space_weather as sw
from shared.space_weather import (
    PRODUCTS,
    SWPC_BASE_URL,
    SpaceWeatherEvent,
    SpaceWeatherRequest,
)

FIXTURES = Path(__file__).parent / "fixtures"
FILES = {
    "solar_probabilities": "swpc_solar_probabilities.json",
    "xrays": "swpc_xrays.json",
    "protons": "swpc_protons.json",
    "solar_regions": "swpc_solar_regions.json",
}
ACQUIRED = "2026-11-14T20:45:00Z"
BY_PATH = {path: name for name, path in PRODUCTS.items()}


def raw() -> dict[str, bytes]:
    return {name: (FIXTURES / file).read_bytes() for name, file in FILES.items()}


def doc(name):
    return json.loads(raw()[name])


def fixture_handler(calls=None, overrides=None):
    def handle(req):
        if calls is not None:
            calls.append(str(req.url))
        name = BY_PATH[req.url.path]
        if overrides and name in overrides:
            return overrides[name](req)
        return httpx.Response(
            200, content=raw()[name], headers={"content-type": "application/json"}
        )

    return handle


def client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def no_network(req):
    raise AssertionError("Network must not be used")


def make_request():
    return SpaceWeatherRequest(request_id=uuid4())


# ------------------------------------------------------------------ products


def test_product_urls():
    assert SWPC_BASE_URL == "https://services.swpc.noaa.gov"
    assert sw.product_url("xrays") == (
        "https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json"
    )
    assert sw.product_url("solar_probabilities") == (
        "https://services.swpc.noaa.gov/json/solar_probabilities.json"
    )
    with pytest.raises(KeyError):
        sw.product_url("kp_index")


# ------------------------------------------------------------- probabilities


def test_probabilities_use_newest_entry_as_first_forecast_day():
    expected = {
        "forecast_start_utc": "2026-11-14T00:00:00Z",
        "polar_cap_absorption": "green",
        "days": [
            {
                "start_utc": f"2026-11-{d:02d}T00:00:00Z",
                "end_utc": f"2026-11-{d + 1:02d}T00:00:00Z",
                "c_class_percent": 90,
                "m_class_percent": 25,
                "x_class_percent": 5,
                "proton_10mev_percent": 5,
            }
            for d in (14, 15, 16)
        ],
    }
    assert sw.parse_probabilities(doc("solar_probabilities")) == expected
    # Order in the document does not matter: the newest date wins.
    assert sw.parse_probabilities(doc("solar_probabilities")[::-1]) == expected


def synthetic_issue(date="2026-11-20T00:00:00"):
    return {
        "date": date,
        "c_class_1_day": 10,
        "c_class_2_day": 20,
        "c_class_3_day": 30,
        "m_class_1_day": 1,
        "m_class_2_day": 2,
        "m_class_3_day": 3,
        "x_class_1_day": 0,
        "x_class_2_day": 1,
        "x_class_3_day": 100,
        "10mev_protons_1_day": 4,
        "10mev_protons_2_day": 5,
        "10mev_protons_3_day": 6,
        "polar_cap_absorption": "yellow",
    }


def test_probability_days_map_to_consecutive_utc_days():
    result = sw.parse_probabilities([synthetic_issue()])
    assert [d["start_utc"] for d in result["days"]] == [
        "2026-11-20T00:00:00Z",
        "2026-11-21T00:00:00Z",
        "2026-11-22T00:00:00Z",
    ]
    assert [d["end_utc"] for d in result["days"]] == [
        "2026-11-21T00:00:00Z",
        "2026-11-22T00:00:00Z",
        "2026-11-23T00:00:00Z",
    ]
    assert [d["c_class_percent"] for d in result["days"]] == [10, 20, 30]
    assert [d["m_class_percent"] for d in result["days"]] == [1, 2, 3]
    assert [d["x_class_percent"] for d in result["days"]] == [0, 1, 100]
    assert [d["proton_10mev_percent"] for d in result["days"]] == [4, 5, 6]
    assert result["polar_cap_absorption"] == "yellow"


def test_explicit_utc_dates_are_accepted_and_month_ends_roll_over():
    for date in ("2026-11-30T00:00:00Z", "2026-11-30T00:00:00+00:00"):
        result = sw.parse_probabilities([synthetic_issue(date)])
        assert result["forecast_start_utc"] == "2026-11-30T00:00:00Z"
        assert result["days"][1]["start_utc"] == "2026-12-01T00:00:00Z"


def _broken(change):
    issue = synthetic_issue()
    change(issue)
    return [issue]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"date": "2026-11-20T00:00:00"},
        None,
        _broken(lambda i: i.pop("m_class_2_day")),
        _broken(lambda i: i.update(m_class_1_day=101)),
        _broken(lambda i: i.update(x_class_1_day=-1)),
        _broken(lambda i: i.update(c_class_1_day=True)),
        _broken(lambda i: i.update(c_class_1_day=25.5)),
        _broken(lambda i: i.update(c_class_1_day=None)),
        _broken(lambda i: i.update(c_class_1_day="25")),
        _broken(lambda i: i.update(date="2026-11-20T12:00:00")),
        _broken(lambda i: i.update(date="2026-11-20T00:00:00+03:00")),
        _broken(lambda i: i.update(date="20 Nov 2026")),
        _broken(lambda i: i.pop("date")),
        [synthetic_issue(), synthetic_issue()],
        ["not an object"],
    ],
)
def test_invalid_probabilities_fail_loudly(payload):
    with pytest.raises(sw.SpaceWeatherSourceError):
        sw.parse_probabilities(payload)


def test_only_the_newest_entry_is_validated_for_percentages():
    old = synthetic_issue("2026-11-19T00:00:00")
    old["m_class_1_day"] = None  # older issues are not used
    result = sw.parse_probabilities([old, synthetic_issue()])
    assert result["forecast_start_utc"] == "2026-11-20T00:00:00Z"


# ------------------------------------------------------------ GOES series


def test_xrays_use_long_channel_in_time_order():
    samples = sw.parse_xrays(doc("xrays"))
    assert len(samples) == 20
    assert samples[0] == {
        "time_utc": "2026-11-14T20:20:00Z",
        "flux_w_m2": 7.078050998643448e-07,
    }
    assert samples[-1] == {
        "time_utc": "2026-11-14T20:39:00Z",
        "flux_w_m2": 6.798833851462405e-07,
    }
    long_channel = [e for e in doc("xrays") if e["energy"] == "0.1-0.8nm"]
    assert [s["flux_w_m2"] for s in samples] == [e["flux"] for e in long_channel]
    times = [s["time_utc"] for s in samples]
    assert times == sorted(times) and len(set(times)) == len(times)


def test_protons_use_ten_mev_channel():
    samples = sw.parse_protons(doc("protons"))
    assert [s["time_utc"] for s in samples] == [
        f"2026-11-14T20:{m:02d}:00Z" for m in (5, 10, 15, 20, 25, 30)
    ]
    assert samples[0]["flux_pfu"] == 0.23539841175079346
    assert samples[2]["flux_pfu"] == 0.2642415463924408
    assert samples[-1]["flux_pfu"] == 0.23382656276226044
    assert set(samples[0]) == {"time_utc", "flux_pfu"}


def xray_row(minute, flux, energy="0.1-0.8nm", tag=None):
    return {
        "time_tag": tag or f"2026-11-14T20:{minute:02d}:00Z",
        "satellite": 18,
        "flux": flux,
        "energy": energy,
    }


def test_null_flux_is_a_gap_not_a_value():
    samples = sw.parse_xrays([xray_row(0, 1e-6), xray_row(1, None), xray_row(2, 2e-6)])
    assert [s["flux_w_m2"] for s in samples] == [1e-6, None, 2e-6]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        None,
        [xray_row(0, 1e-6, energy="0.05-0.4nm")],
        [xray_row(0, None), xray_row(1, None)],
        [xray_row(0, -1e-9)],
        [xray_row(0, float("nan"))],
        [xray_row(0, float("inf"))],
        [xray_row(0, True)],
        [xray_row(0, "1e-6")],
        [xray_row(0, 1e-6), xray_row(0, 2e-6)],
        [xray_row(1, 1e-6), xray_row(0, 2e-6)],
        [xray_row(0, 1e-6, tag="2026-11-14T20:00:00")],
        [xray_row(0, 1e-6, tag="2026-11-14T23:00:00+03:00")],
        [xray_row(0, 1e-6, tag="yesterday")],
        [{"satellite": 18, "flux": 1e-6, "energy": "0.1-0.8nm"}],
        ["row"],
    ],
)
def test_invalid_xray_series_fail_loudly(rows):
    with pytest.raises(sw.SpaceWeatherSourceError):
        sw.parse_xrays(rows)


def test_invalid_proton_series_fail_loudly():
    rows = [xray_row(0, 1.0, energy=">=10 MeV"), xray_row(0, 2.0, energy=">=10 MeV")]
    with pytest.raises(sw.SpaceWeatherSourceError):
        sw.parse_protons(rows)
    with pytest.raises(sw.SpaceWeatherSourceError):
        sw.parse_protons([xray_row(0, 1.0, energy=">=100 MeV")])


# ---------------------------------------------------------------- regions


def test_regions_use_newest_report_sorted_by_number():
    result = sw.parse_regions(doc("solar_regions"))
    assert result["observed_date_utc"] == "2026-11-14T00:00:00Z"
    numbers = [r["region"] for r in result["regions"]]
    assert numbers == sorted(numbers) and len(numbers) == 12
    assert {4574, 4576, 4579} <= set(numbers)
    by_number = {r["region"]: r for r in result["regions"]}
    assert by_number[4576] == {
        "region": 4576,
        "location": "S17W39",
        "spot_class": "Dai",
        "area_msh": 30,
        "c_flare_percent": 55,
        "m_flare_percent": 10,
        "x_flare_percent": 1,
        "proton_percent": 1,
    }
    assert by_number[4573]["spot_class"] is None
    assert by_number[4573]["area_msh"] is None


def region(number, date="2026-11-14", **changes):
    row = {
        "observed_date": date,
        "region": number,
        "location": "N10E10",
        "spot_class": "Dso",
        "area": 20,
        "c_flare_probability": 20,
        "m_flare_probability": 5,
        "x_flare_probability": 1,
        "proton_probability": 1,
    }
    row.update(changes)
    return row


def test_missing_region_probabilities_stay_unknown():
    result = sw.parse_regions([region(1, c_flare_probability=None)])
    assert result["regions"][0]["c_flare_percent"] is None
    assert result["regions"][0]["m_flare_percent"] == 5


@pytest.mark.parametrize(
    "rows",
    [
        [],
        None,
        [region(1), region(1)],
        [region(1, m_flare_probability=150)],
        [region(1, m_flare_probability=-5)],
        [region(1, m_flare_probability=True)],
        [region(1, date="14/11/2026")],
        [region(0)],
        [region("4576")],
        ["row"],
    ],
)
def test_invalid_regions_fail_loudly(rows):
    with pytest.raises(sw.SpaceWeatherSourceError):
        sw.parse_regions(rows)


def test_older_region_reports_may_repeat_numbers():
    result = sw.parse_regions(
        [region(1, date="2026-11-13"), region(1, date="2026-11-14")]
    )
    assert result["observed_date_utc"] == "2026-11-14T00:00:00Z"
    assert len(result["regions"]) == 1


# --------------------------------------------------------------- snapshot


def test_snapshot_combines_all_products():
    snapshot = sw.build_snapshot(raw(), ACQUIRED)
    assert snapshot == {
        "snapshot_version": sw.SNAPSHOT_VERSION,
        "acquired_at_utc": ACQUIRED,
        "flare_probabilities": sw.parse_probabilities(doc("solar_probabilities")),
        "xrays_w_m2": sw.parse_xrays(doc("xrays")),
        "protons_10mev_pfu": sw.parse_protons(doc("protons")),
        "active_regions": sw.parse_regions(doc("solar_regions")),
    }
    json.dumps(snapshot, allow_nan=False)


def test_snapshot_rejects_bad_inputs():
    broken = raw()
    broken["xrays"] = b"<html>maintenance</html>"
    with pytest.raises(sw.SpaceWeatherSourceError):
        sw.build_snapshot(broken, ACQUIRED)
    missing = raw()
    missing.pop("protons")
    with pytest.raises(sw.SpaceWeatherSourceError):
        sw.build_snapshot(missing, ACQUIRED)
    with pytest.raises(ValueError):
        sw.build_snapshot(raw(), "2026-11-14T20:45:00")


def test_snapshot_key_is_content_addressed():
    key = sw.snapshot_key(raw())
    assert len(key) == 64 and int(key, 16) >= 0
    assert sw.snapshot_key(dict(reversed(list(raw().items())))) == key
    changed = raw()
    changed["xrays"] = changed["xrays"].replace(b"20:39:00Z", b"20:39:01Z")
    assert sw.snapshot_key(changed) != key


# ---------------------------------------------------------------- download


def test_download_writes_checksummed_bundle(tmp_path):
    calls = []
    with client_with(fixture_handler(calls)) as client:
        result = sw.download_snapshot(tmp_path, client)
    assert sorted(calls) == sorted(sw.product_url(name) for name in PRODUCTS)
    key = sw.snapshot_key(raw())
    bundle = tmp_path / "space-weather" / key
    assert result == {"cache_key": key, "path": str(bundle), "cache_hit": False}
    for name, file in FILES.items():
        assert (bundle / "raw" / f"{name}.json").read_bytes() == raw()[name]
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["cache_key"] == key
    assert manifest["snapshot_version"] == sw.SNAPSHOT_VERSION
    assert manifest["time_scale"] == "UTC"
    assert set(manifest["sha256"]) == {f"raw/{n}.json" for n in PRODUCTS} | {
        "snapshot.json"
    }
    for name, expected in manifest["sha256"].items():
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == expected
    for name in PRODUCTS:
        assert manifest["sources"][name]["url"] == sw.product_url(name)
    snapshot = json.loads((bundle / "snapshot.json").read_text())
    assert snapshot["acquired_at_utc"].endswith("Z")
    assert snapshot["acquired_at_utc"] == manifest["acquired_at_utc"]
    expected = sw.build_snapshot(raw(), snapshot["acquired_at_utc"])
    assert snapshot == expected
    leftovers = [p.name for p in (tmp_path / "space-weather").iterdir()]
    assert [n for n in leftovers if n.startswith(".pending")] == []


def test_identical_content_reuses_the_bundle(tmp_path):
    with client_with(fixture_handler()) as client:
        first = sw.download_snapshot(tmp_path, client)
        before = (Path(first["path"]) / "snapshot.json").read_bytes()
        second = sw.download_snapshot(tmp_path, client)
    assert second == {**first, "cache_hit": True}
    assert (Path(first["path"]) / "snapshot.json").read_bytes() == before


def test_new_content_creates_a_new_bundle(tmp_path):
    with client_with(fixture_handler()) as client:
        first = sw.download_snapshot(tmp_path, client)
    changed = raw()["protons"].replace(b"20:30:00Z", b"20:30:01Z")
    override = {"protons": lambda req: httpx.Response(200, content=changed)}
    with client_with(fixture_handler(overrides=override)) as client:
        second = sw.download_snapshot(tmp_path, client)
    assert second["cache_key"] != first["cache_key"]
    assert Path(first["path"]).exists() and Path(second["path"]).exists()


def test_corrupt_existing_bundle_is_not_replaced(tmp_path):
    with client_with(fixture_handler()) as client:
        first = sw.download_snapshot(tmp_path, client)
        target = Path(first["path"]) / "snapshot.json"
        target.write_text("{}")
        with pytest.raises(sw.SpaceWeatherSourceError):
            sw.download_snapshot(tmp_path, client)
    assert target.read_text() == "{}"


def test_http_errors_propagate_without_a_bundle(tmp_path):
    override = {"xrays": lambda req: httpx.Response(503, request=req)}
    with client_with(fixture_handler(overrides=override)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            sw.download_snapshot(tmp_path, client)
    root = tmp_path / "space-weather"
    assert (
        not root.exists()
        or [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
        == []
    )


def test_invalid_or_oversized_content_is_a_source_error(tmp_path, monkeypatch):
    override = {"solar_regions": lambda req: httpx.Response(200, content=b"[]")}
    with client_with(fixture_handler(overrides=override)) as client:
        with pytest.raises(sw.SpaceWeatherSourceError):
            sw.download_snapshot(tmp_path, client)
    monkeypatch.setattr(sw, "MAX_PRODUCT_BYTES", 100)
    with client_with(fixture_handler()) as client:
        with pytest.raises(sw.SpaceWeatherSourceError):
            sw.download_snapshot(tmp_path, client)


# -------------------------------------------------------------------- jobs


def test_job_is_ready_and_replays_without_network(tmp_path):
    request = make_request()
    with client_with(fixture_handler()) as client:
        event = sw.process_space_weather_job(request, tmp_path, client)
    assert event.status == "ready"
    assert event.snapshot_path == f"space-weather/{sw.snapshot_key(raw())}"
    assert (tmp_path / event.snapshot_path / "snapshot.json").exists()
    with client_with(no_network) as client:
        assert sw.process_space_weather_job(request, tmp_path, client) == event


def test_failures_are_classified_and_persisted(tmp_path):
    unavailable = make_request()
    override = {"xrays": lambda req: httpx.Response(503, request=req)}
    with client_with(fixture_handler(overrides=override)) as client:
        event = sw.process_space_weather_job(unavailable, tmp_path, client)
    assert event.status == "failed" and event.error_code == "source_unavailable"
    with client_with(no_network) as client:
        assert sw.process_space_weather_job(unavailable, tmp_path, client) == event

    invalid = make_request()
    override = {"protons": lambda req: httpx.Response(200, content=b"not json")}
    with client_with(fixture_handler(overrides=override)) as client:
        event = sw.process_space_weather_job(invalid, tmp_path, client)
    assert event.status == "failed" and event.error_code == "source_invalid"
    assert event.error_message


def test_reused_request_id_with_other_parameters_conflicts(tmp_path):
    request = make_request()
    with client_with(fixture_handler()) as client:
        sw.process_space_weather_job(request, tmp_path, client)
    stored = tmp_path / "space-weather-jobs" / str(request.request_id) / "request.json"
    document = json.loads(stored.read_text())
    document["schema_version"] = 99
    stored.write_text(json.dumps(document))
    with client_with(no_network) as client:
        with pytest.raises(sw.SpaceWeatherRequestConflict):
            sw.process_space_weather_job(request, tmp_path, client)


# ------------------------------------------------------------------ broker


def message(body):
    msg = Mock()
    msg.body = body
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()
    return msg


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps({"request_id": "not-a-uuid"}).encode(),
        json.dumps({"request_id": str(uuid4()), "extra": 1}).encode(),
        b"x" * (sw.MAX_MESSAGE_BYTES + 1),
    ],
    ids=["not-json", "bad-uuid", "extra-field", "oversized"],
)
def test_invalid_messages_are_dead_lettered(tmp_path, body):
    msg, exchange = message(body), Mock(publish=AsyncMock())
    asyncio.run(sw.handle_space_weather_message(msg, exchange, tmp_path))
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()
    exchange.publish.assert_not_awaited()


def test_event_is_published_before_ack(tmp_path, monkeypatch):
    request = make_request()
    ready = SpaceWeatherEvent(
        request_id=request.request_id,
        status="ready",
        snapshot_path="space-weather/" + "a" * 64,
    )
    monkeypatch.setattr(
        sw, "process_space_weather_job", lambda req, root, client=None: ready
    )
    order = []
    msg = message(request.model_dump_json().encode())
    msg.ack = AsyncMock(side_effect=lambda: order.append("ack"))
    exchange = Mock(
        publish=AsyncMock(side_effect=lambda *a, **k: order.append("publish"))
    )
    asyncio.run(sw.handle_space_weather_message(msg, exchange, tmp_path))
    assert order == ["publish", "ack"]
    published = exchange.publish.call_args
    assert published.kwargs["routing_key"] == str(request.request_id)
    assert published.kwargs["mandatory"] is True
    sent = published.args[0]
    assert SpaceWeatherEvent.model_validate_json(sent.body) == ready
    assert sent.correlation_id == str(request.request_id)


def test_conflict_is_published_as_failed_event(tmp_path, monkeypatch):
    request = make_request()

    def conflict(req, root, client=None):
        raise sw.SpaceWeatherRequestConflict("reused")

    monkeypatch.setattr(sw, "process_space_weather_job", conflict)
    msg, exchange = (
        message(request.model_dump_json().encode()),
        Mock(publish=AsyncMock()),
    )
    asyncio.run(sw.handle_space_weather_message(msg, exchange, tmp_path))
    event = SpaceWeatherEvent.model_validate_json(
        exchange.publish.call_args.args[0].body
    )
    assert event.status == "failed" and event.error_code == "request_conflict"
    msg.ack.assert_awaited_once()


def test_fixture_documents_are_not_mutated_by_parsing():
    payload = doc("solar_probabilities")
    before = copy.deepcopy(payload)
    sw.parse_probabilities(payload)
    assert payload == before
