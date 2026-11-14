"""Independent linear-clearance cases, missing data and cache/refinement contracts."""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.visibility import (
    VisibilityError,
    analyze_visibility,
    load_ephemeris,
    refinement_requests,
)
from shared.contracts import AnalysisRequest, ObserverSite
from shared.horizon import HorizonSettings
from shared.ingestion import IngestionEvent

START = datetime(2026, 11, 14, tzinfo=timezone.utc)


def stamp(seconds):
    return (START + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def request(**changes):
    base = dict(
        site=ObserverSite(
            site_id="test",
            latitude_deg=-85.0,
            longitude_deg_east=0.0,
            altitude_km=1.002,
        ),
        start_utc=START,
        end_utc=START + timedelta(hours=2),
        step_s=3600,
    )
    return AnalysisRequest(**(base | changes))


def profile():
    return dict(
        site=request().site.model_dump(mode="json"),
        reference_frame="MOON_ME",
        profile_id="test-profile",
        quality={"global_horizon_verified": False},
        directions=[
            dict(azimuth_deg=float(i), elevation_deg=0.0) for i in range(0, 360, 45)
        ],
    )


def rows(sun=(1.0, -1.0, -1.0), earth=(1.0, -1.0, -1.0)):
    return [
        dict(
            site_id="test",
            sampled_at_utc=stamp(i * 3600),
            sun_azimuth_deg=0.0,
            sun_elevation_deg=s,
            sun_angular_diameter_arcsec=1800.0,
            earth_azimuth_deg=0.0,
            earth_elevation_deg=e,
            earth_angular_diameter_arcsec=6000.0,
        )
        for i, (s, e) in enumerate(zip(sun, earth))
    ]


def analyze(data=None, **kwargs):
    return analyze_visibility(
        request(), data if data is not None else rows(), profile(), **kwargs
    )


def test_linear_solar_disk_intervals_and_metrics():
    result = analyze()
    assert [r["state"] for r in result["sun"]["intervals"]] == [
        "full",
        "partial",
        "dark",
    ]
    metrics = result["sun"]["metrics"]
    assert metrics["visible_duration_s"] == pytest.approx(2250)
    assert metrics["full_duration_s"] == pytest.approx(1350)
    assert metrics["partial_duration_s"] == pytest.approx(900)
    assert metrics["blocked_duration_s"] == pytest.approx(4950)
    assert metrics["visible_percent_of_interval"] == pytest.approx(31.25)
    assert metrics["longest_visible_s"] == pytest.approx(2250)
    assert metrics["longest_blocked_s"] == pytest.approx(4950)
    assert metrics["unknown_duration_s"] == 0
    assert result["earth"]["metrics"]["visible_duration_s"] == pytest.approx(1800)
    event = result["sun"]["next_setting"]
    assert event["status"] == "found"
    assert event["estimated_at_utc"] == stamp(2250)
    assert event["bracket_start_utc"] == stamp(0)
    assert event["bracket_end_utc"] == stamp(3600)
    assert event["time_until_estimate_s"] == pytest.approx(2250)
    assert result["model"]["durations_are_estimates"] is True
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "value,state",
    [
        (2.0, "full"),
        (-2.0, "dark"),
        (0.0, "partial"),
        (0.25, "boundary"),
        (-0.25, "boundary"),
    ],
)
def test_full_interval_and_exact_limb_boundaries(value, state):
    result = analyze(rows((value,) * 3, (0.0,) * 3))
    assert result["sun"]["intervals"][0]["state"] == state
    assert sum(r["duration_s"] for r in result["sun"]["intervals"]) == pytest.approx(
        7200
    )
    assert result["earth"]["metrics"]["unknown_duration_s"] == pytest.approx(7200)
    assert result["earth"]["next_setting"]["status"] == "insufficient_data"
    if state == "boundary":
        assert result["sun"]["metrics"]["unknown_duration_s"] == pytest.approx(7200)


def test_missing_sun_does_not_erase_earth_or_become_dark():
    data = rows()
    data[1]["sun_elevation_deg"] = None
    result = analyze(data)
    assert result["sun"]["metrics"]["unknown_duration_s"] == pytest.approx(7200)
    assert result["sun"]["metrics"]["blocked_duration_s"] == 0
    assert result["earth"]["metrics"]["visible_duration_s"] == pytest.approx(1800)
    assert result["sun"]["next_setting"]["status"] == "insufficient_data"


def test_missing_or_zero_solar_diameter_is_unknown():
    for diameter in [None, 0.0]:
        data = rows()
        for row in data:
            row["sun_angular_diameter_arcsec"] = diameter
        assert analyze(data)["sun"]["metrics"]["unknown_duration_s"] == pytest.approx(
            7200
        )


def test_gap_and_non_aligned_tail_are_not_extrapolated():
    data = rows()
    data.pop(1)
    assert analyze(data)["sun"]["metrics"]["unknown_duration_s"] == pytest.approx(7200)
    req = request(end_utc=START + timedelta(seconds=9000))
    result = analyze_visibility(req, rows((2.0,) * 3), profile())
    assert result["sun"]["metrics"]["unknown_duration_s"] == pytest.approx(1800)
    assert result["sun"]["metrics"]["visible_duration_s"] == pytest.approx(7200)


def test_clip_bounds_selected_time_and_no_event():
    req = request(
        start_utc=START + timedelta(seconds=1800),
        end_utc=START + timedelta(seconds=5400),
    )
    result = analyze_visibility(
        req, rows((2.0,) * 3, (2.0,) * 3), profile(), selected_utc=stamp(3600)
    )
    assert result["sun"]["metrics"]["visible_duration_s"] == pytest.approx(3600)
    assert result["sun"]["next_setting"]["status"] == "no_event_in_range"
    result = analyze(selected_utc=stamp(2400))
    assert (
        result["sun"]["next_setting"]["status"] == "transition_overlaps_selected_time"
    )


def test_boundary_touch_is_not_a_setting_and_sign_crossing_is_one_event():
    result = analyze(rows((1.0, -0.25, 1.0), (1.0, 0.0, 1.0)))
    assert not [e for e in result["events"] if e["kind"] in ("sunset", "earthset")]
    result = analyze(rows((1.0, -0.25, -1.0), (1.0, 0.0, -1.0)))
    assert len([e for e in result["events"] if e["kind"] == "sunset"]) == 1
    assert len([e for e in result["events"] if e["kind"] == "earthset"]) == 1


def test_wrap_and_unknown_swept_horizon():
    data = rows((2.0,) * 3, (2.0,) * 3)
    for row, az in zip(data, [359.0, 0.0, 1.0]):
        row["sun_azimuth_deg"] = az
    assert analyze(data)["sun"]["metrics"]["visible_duration_s"] == pytest.approx(7200)
    terrain = profile()
    terrain["directions"][2]["elevation_deg"] = None
    for row, az in zip(data, [0.0, 160.0, 160.0]):
        row["sun_azimuth_deg"] = az
    result = analyze_visibility(request(), data, terrain)
    assert result["sun"]["metrics"]["unknown_duration_s"] == pytest.approx(3600)


def test_unknown_before_later_setting_does_not_claim_next_event():
    data = rows((None, 1.0, -1.0), (1.0, 1.0, 1.0))
    result = analyze(data)
    assert result["sun"]["next_setting"]["status"] == "candidate_after_unknown"


def test_spatial_sensitivity_is_not_an_error_bound():
    result = analyze(sensitivity_deg=0.75)
    sensitivity = result["spatial_sensitivity"]
    assert sensitivity["is_error_bound"] is False
    assert sensitivity["horizon_raised"]["sun_visible_duration_s"] <= 2250
    assert sensitivity["horizon_lowered"]["sun_visible_duration_s"] >= 2250
    assert result["horizon_quality"]["global_horizon_verified"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"sun_elevation_deg": float("nan")},
        {"earth_azimuth_deg": 361.0},
        {"sun_angular_diameter_arcsec": -1.0},
        {"sampled_at_utc": "2026-11-14T00:00:00"},
        {"site_id": "other"},
    ],
)
def test_invalid_rows_rejected(change):
    data = rows()
    data[0].update(change)
    with pytest.raises(VisibilityError):
        analyze(data)


def test_duplicate_unsorted_rows_and_wrong_site_rejected():
    for data in [rows() + [rows()[0]], list(reversed(rows()))]:
        with pytest.raises(VisibilityError):
            analyze(data)
    terrain = profile()
    terrain["site"]["altitude_km"] = 0.0
    with pytest.raises(VisibilityError):
        analyze_visibility(request(), rows(), terrain)
    with pytest.raises(VisibilityError):
        analyze(selected_utc=stamp(-1))
    with pytest.raises(VisibilityError):
        analyze(sensitivity_deg=-1)


def test_refinement_plans_are_deterministic_merged_and_bounded():
    result = analyze()
    plans = refinement_requests(result, request(), 300)
    assert len(plans) == 1
    assert plans == refinement_requests(result, request(), 300)
    assert plans[0].analysis.start_utc == START
    assert plans[0].analysis.end_utc == START + timedelta(hours=1)
    assert plans[0].analysis.step_s == 300
    assert plans[0].analysis.site == request().site
    assert (
        refinement_requests(analyze(rows((2.0,) * 3, (2.0,) * 3)), request(), 300) == []
    )
    with pytest.raises(VisibilityError):
        refinement_requests(result, request(), 30)


@pytest.fixture
def bundle(tmp_path):
    key = "a" * 64
    root = tmp_path / "horizons" / key
    (root / "raw").mkdir(parents=True)
    (root / "raw/10.json").write_text("{}")
    (root / "raw/399.json").write_text("{}")
    (root / "samples.json").write_text(json.dumps(rows()))
    manifest = dict(
        cache_key=key,
        cache_version=1,
        request=request().model_dump(mode="json"),
        sample_count=3,
        terrain_applied=False,
        sources={
            t: dict(
                time_scale="UTC",
                reference_radius_km=1737.4,
                orientation="MEAN_ME (high precision)",
            )
            for t in ["10", "399"]
        },
        sha256={
            p: hashlib.sha256((root / p).read_bytes()).hexdigest()
            for p in ["raw/10.json", "raw/399.json", "samples.json"]
        },
    )
    (root / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path, "horizons/" + key


def test_loader_validates_read_only_bundle(bundle):
    root, ref = bundle
    req, data, manifest = load_ephemeris(root, ref)
    assert req == request() and data == rows() and manifest["sample_count"] == 3
    with (root / ref / "samples.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(VisibilityError, match="checksum"):
        load_ephemeris(root, ref)


@pytest.mark.parametrize(
    "field,value",
    [("sample_count", 99), ("terrain_applied", True), ("cache_version", 2)],
)
def test_loader_rejects_wrong_manifest(bundle, field, value):
    root, ref = bundle
    p = root / ref / "manifest.json"
    m = json.loads(p.read_text())
    m[field] = value
    p.write_text(json.dumps(m))
    with pytest.raises(VisibilityError):
        load_ephemeris(root, ref)


def test_loader_path_escape_and_grid_validation(bundle):
    root, ref = bundle
    with pytest.raises(VisibilityError):
        load_ephemeris(root, "../bad")
    p = root / ref / "samples.json"
    p.write_text(json.dumps(rows()[:2]))
    mpath = root / ref / "manifest.json"
    m = json.loads(mpath.read_text())
    m["sha256"]["samples.json"] = hashlib.sha256(p.read_bytes()).hexdigest()
    mpath.write_text(json.dumps(m))
    with pytest.raises(VisibilityError):
        load_ephemeris(root, ref)


def test_refinement_orchestration_and_failure(monkeypatch, tmp_path):
    import app.analyze_visibility as module

    store = {"initial": (request(), rows(), {})}
    calls = []
    monkeypatch.setattr(module, "profile_from_bundle", lambda *args: profile())
    monkeypatch.setattr(module, "load_ephemeris", lambda root, ref: store[ref])

    async def submit(job, broker_url):
        calls.append(job)
        req = job.analysis
        data = []
        t = req.start_utc
        while t <= req.end_utc:
            elapsed = (t - START).total_seconds()
            row = rows()[0].copy()
            row["sampled_at_utc"] = stamp(elapsed)
            row["sun_elevation_deg"] = row["earth_elevation_deg"] = 1 - elapsed / 1800
            data.append(row)
            t += timedelta(seconds=req.step_s)
        ref = "horizons/" + uuid4().hex * 2
        store[ref] = (req, data, {})
        return IngestionEvent(
            request_id=job.request_id,
            status="ready",
            horizons_path=ref,
            terrain_path="terrain/processed/" + "b" * 64,
        )

    monkeypatch.setattr(module, "submit", submit)
    result = asyncio.run(
        module.run_analysis(
            tmp_path,
            "terrain",
            "initial",
            HorizonSettings(),
            refine=True,
            broker_url="amqp://test",
        )
    )
    assert {job.analysis.step_s for job in calls} == {300, 60}
    assert max(e["bracket_width_s"] for e in result["events"]) <= 120
    assert result["refinement"]["status"] == "completed"

    async def failed(job, broker_url):
        return IngestionEvent(
            request_id=job.request_id,
            status="failed",
            error_code="source",
            error_message="unavailable",
        )

    monkeypatch.setattr(module, "submit", failed)
    with pytest.raises(VisibilityError):
        asyncio.run(
            module.run_analysis(
                tmp_path,
                "terrain",
                "initial",
                HorizonSettings(),
                refine=True,
                broker_url="amqp://test",
            )
        )
