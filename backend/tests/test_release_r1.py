"""R1 contracts: exact exports, portable metadata, deficits and bounded lessons."""

from copy import deepcopy
import csv
import hashlib
import io
import json

from fastapi.testclient import TestClient
import pytest

from app.analysis_api import Submission, create_app
from app.release_r1 import (
    classroom_lessons,
    export_csv,
    export_report,
    rank_candidates,
    scenario_manifest,
    validate_manifest,
    register_r1_routes,
)


def saved():
    submission = Submission(
        site_id="malapert",
        latitude_deg=-85.9948,
        longitude_deg_east=359.76422,
        start_utc="2026-11-04T00:00:00Z",
        end_utc="2026-11-05T00:00:00Z",
    ).model_dump(mode="json")
    metrics = dict(
        visible_duration_s=3600,
        visible_percent_of_interval=100 / 24,
        longest_blocked_s=82800,
        longest_visible_s=3600,
        unknown_duration_s=0,
    )
    body = dict(
        azimuth_deg=12.5,
        elevation_deg=-0.2,
        horizon_deg=0.5,
        state="dark",
        next_setting=dict(status="no_event_in_range"),
    )
    result = dict(
        analysis=dict(
            analysis_id="abc",
            model_version="visibility-v1",
            request=dict(
                site=dict(
                    site_id="malapert",
                    latitude_deg=-85.9948,
                    longitude_deg_east=359.76422,
                    reference_frame="MOON_ME",
                ),
                start_utc=submission["start_utc"],
                end_utc=submission["end_utc"],
                step_s=3600,
            ),
            sun=dict(metrics=deepcopy(metrics), intervals=[]),
            earth=dict(metrics=deepcopy(metrics), intervals=[]),
            refinement=dict(status="completed"),
            spatial_sensitivity=dict(offset_deg=0.75),
            provenance=dict(
                ephemerides=[
                    dict(
                        reference="fixture",
                        source="NASA",
                        acquired_at_utc="2026-11-01T00:00:00Z",
                    )
                ]
            ),
        ),
        horizon=dict(
            model_version="horizon-v1",
            source_sha256="a" * 64,
            source_url="https://example.org/terrain",
            observer_height_above_terrain_m=2,
            settings=dict(search_distance_m=100000),
            directions=[],
        ),
        frames=[
            dict(utc=submission["start_utc"], sun=deepcopy(body), earth=deepcopy(body)),
            dict(utc="2026-11-04T00:05:00Z", sun=deepcopy(body), earth=deepcopy(body)),
        ],
    )
    result["frames"][1]["earth"].update(
        state="unknown", elevation_deg=None, horizon_deg=None
    )
    return dict(id="saved-job", status="ready", submission=submission, result=result)


def condition(name, actual, threshold, operator):
    return dict(
        name=name,
        actual=actual,
        threshold=threshold,
        operator=operator,
        passed=actual >= threshold if operator == ">=" else actual <= threshold,
    )


def candidate(job_id, sun=70, dark=6, earth=4, state="not_met"):
    return dict(
        job_id=job_id,
        state=state,
        start_utc="2026-11-04T00:00:00Z",
        end_utc="2026-11-05T00:00:00Z",
        conditions=[
            condition("sunlight_percent", sun, 70, ">="),
            condition("longest_darkness_hours", dark, 6, "<="),
            condition("earth_visible_hours", earth, 4, ">="),
        ],
    )


def test_deficit_ranking_is_explained_stable_and_excludes_unknown():
    search = dict(
        candidates=[
            candidate("far", sun=20),
            candidate("near", sun=60, dark=8),
            candidate("exact", state="match"),
            candidate("unknown", state="unknown"),
            candidate("failed", state="failed"),
            candidate("waiting", state="waiting"),
        ]
    )
    before = deepcopy(search)
    ranked = rank_candidates(search)
    assert [r["job_id"] for r in ranked["ranked"]] == ["exact", "near", "far"]
    assert ranked["ranked"][1]["score"] == pytest.approx((0.1 + 2 / 24) / 3)
    deficits = ranked["ranked"][1]["deficits"]
    assert deficits[0]["shortfall"] == pytest.approx(10)
    assert deficits[1]["shortfall"] == pytest.approx(2)
    assert {r["job_id"] for r in ranked["excluded"]} == {"unknown", "failed", "waiting"}
    assert search == before
    assert rank_candidates(search) == ranked
    assert ranked["method"]


def test_empty_and_tied_ranking():
    assert rank_candidates(dict(candidates=[]))["ranked"] == []
    ranked = rank_candidates(dict(candidates=[candidate("b"), candidate("a")]))
    assert [r["job_id"] for r in ranked["ranked"]] == ["a", "b"]
    assert all(r["score"] == pytest.approx(0) for r in ranked["ranked"])


def test_manifest_preserves_settings_versions_and_exact_result_fingerprint():
    job = saved()
    before = deepcopy(job)
    manifest = scenario_manifest([job])
    assert manifest["schema_version"] == 1
    expected = manifest["scenarios"][0]["expected"]
    assert expected["model_version"] == "visibility-v1"
    assert expected["horizon_model_version"] == "horizon-v1"
    digest = hashlib.sha256(
        json.dumps(
            job["result"], sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    assert expected["result_sha256"] == digest
    assert expected["provenance"] == job["result"]["analysis"]["provenance"]
    assert validate_manifest(json.loads(json.dumps(manifest))) == manifest
    assert job == before


@pytest.mark.parametrize(
    "change", ["version", "empty", "excess", "extra", "bad_date", "hash", "latitude"]
)
def test_invalid_manifests_are_rejected(change):
    manifest = scenario_manifest([saved()])
    if change == "version":
        manifest["schema_version"] = 99
    elif change == "empty":
        manifest["scenarios"] = []
    elif change == "excess":
        manifest["scenarios"] *= 3
    elif change == "extra":
        manifest["command"] = "do not execute"
    elif change == "bad_date":
        manifest["scenarios"][0]["submission"]["start_utc"] = "not-a-date"
    elif change == "hash":
        manifest["scenarios"][0]["expected"]["result_sha256"] = "invalid"
    else:
        manifest["scenarios"][0]["submission"]["latitude_deg"] = -60
    with pytest.raises(ValueError):
        validate_manifest(manifest)


def test_csv_keeps_actual_irregular_samples_nulls_units_and_metadata():
    job = saved()
    job["submission"]["site_id"] = '=HYPERLINK("https://bad.example")'
    rows = list(csv.DictReader(io.StringIO(export_csv([job]))))
    assert len(rows) == 2
    assert rows[0]["utc"] == "2026-11-04T00:00:00Z"
    assert rows[1]["utc"] == "2026-11-04T00:05:00Z"
    assert float(rows[0]["sun_elevation_deg"]) == pytest.approx(-0.2)
    assert rows[1]["earth_state"] == "unknown"
    assert rows[1]["earth_elevation_deg"] == ""
    assert rows[0]["site_id"].startswith("'")
    assert json.loads(rows[0]["submission_json"])["step_s"] == 3600
    assert json.loads(rows[0]["provenance_json"])["ephemerides"][0]["source"] == "NASA"
    assert rows[0]["reference_frame"] == "MOON_ME"
    assert rows[0]["model_version"] == "visibility-v1"


def test_report_escapes_untrusted_text_and_retains_unknown_and_sources():
    job = saved()
    job["submission"]["site_id"] = '<script>alert("x")</script>'
    report = export_report([job])
    assert "<script>" not in report
    assert "&lt;script&gt;" in report
    assert "unknown_duration_s" in report
    assert "NASA" in report
    assert "visibility-v1" in report
    assert "@media print" in report


def test_two_scenario_exports_include_comparison_and_both_csv_groups():
    a = saved()
    b = deepcopy(a)
    b["id"] = "second"
    report = export_report([a, b])
    assert "B minus A" in report
    rows = list(csv.DictReader(io.StringIO(export_csv([a, b]))))
    assert {r["scenario"] for r in rows} == {"A", "B"}
    assert len(validate_manifest(scenario_manifest([a, b]))["scenarios"]) == 2


def test_lessons_are_bounded_source_backed_and_explicit_about_model_limits():
    lessons = classroom_lessons()
    assert len(lessons) >= 2
    assert len({r["id"] for r in lessons}) == len(lessons)
    for lesson in lessons:
        submission = Submission.model_validate(lesson["submission"])
        assert (submission.end_utc - submission.start_utc).total_seconds() <= 86400
        assert lesson["source_url"].startswith("https://")
        assert lesson["cards"] and lesson["limitation"]


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    # Registration is idempotent when create_app already wires these routes.
    if not any(r.path == "/classroom" for r in app.routes):
        register_r1_routes(app, app.state.store, app.state.searches)
    with TestClient(app) as c:
        yield c


def test_export_api_status_download_headers_and_validation_does_not_queue(client):
    assert client.get("/analyses/missing/export/csv").status_code == 404
    job = client.app.state.store.create(
        Submission.model_validate(saved()["submission"])
    )
    path = f"/analyses/{job['id']}/export/"
    assert client.get(path + "csv").status_code == 409
    client.app.state.store.update(job["id"], "ready", result=saved()["result"])
    response = client.get(path + "csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "text/csv" in response.headers["content-type"]
    assert client.get(path + "report").status_code == 200
    manifest = client.get(path + "scenario").json()
    assert client.post("/scenarios/validate", json=manifest).json() == manifest
    assert client.post("/scenarios/validate", json={}).status_code == 422
    assert client.get(path + "exe").status_code == 422
    assert client.app.state.store.pending() == []
    check = client.post(
        "/scenarios/check", json=dict(manifest=manifest, job_ids=[job["id"]])
    )
    assert check.status_code == 200
    assert check.json()["matches"] is True
    altered = deepcopy(saved()["result"])
    altered["analysis"]["model_version"] = "changed"
    client.app.state.store.update(job["id"], "ready", result=altered)
    assert (
        client.post(
            "/scenarios/check", json=dict(manifest=manifest, job_ids=[job["id"]])
        ).json()["matches"]
        is False
    )
    assert client.get("/searches/missing/ranking").status_code == 404


def test_classroom_reuses_identical_active_or_complete_job_but_not_failed(client):
    lesson = client.get("/classroom").json()["lessons"][0]
    path = "/classroom/" + lesson["id"]
    first = client.post(path).json()
    assert client.post(path).json()["id"] == first["id"]
    client.app.state.store.update(first["id"], "ready", result=saved()["result"])
    assert client.post(path).json()["id"] == first["id"]
    client.app.state.store.update(first["id"], "failed", error="source offline")
    assert client.post(path).json()["id"] != first["id"]
    assert client.post("/classroom/unknown").status_code == 404
