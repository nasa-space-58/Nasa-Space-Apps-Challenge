"""Threshold boundaries, bounded grids, durable progress and queue backpressure."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from app.analysis_api import JobStore, Submission, create_app
from app.window_search import (
    SearchRequest,
    SearchStore,
    candidate_submissions,
    assess_result,
)


def body(**patch):
    return (
        dict(
            template=dict(
                site_id="malapert",
                latitude_deg=-85.9948,
                longitude_deg_east=359.76422,
                observer_height_m=2,
                start_utc="2026-11-04T00:00:00Z",
                end_utc="2026-11-05T00:00:00Z",
                refine=True,
            ),
            last_start_utc="2026-11-06T00:00:00Z",
            start_step_hours=24,
            min_sunlight_percent=70,
            max_darkness_hours=6,
            min_earth_hours=4,
        )
        | patch
    )


def result(sun=70, dark=21600, earth=14400, unknown_sun=0, unknown_earth=0):
    return dict(
        analysis=dict(
            analysis_id="fixture",
            sun=dict(
                metrics=dict(
                    visible_percent_of_interval=sun,
                    longest_blocked_s=dark,
                    unknown_duration_s=unknown_sun,
                )
            ),
            earth=dict(
                metrics=dict(visible_duration_s=earth, unknown_duration_s=unknown_earth)
            ),
        )
    )


def test_grid_is_inclusive_preserves_settings_and_does_not_mutate():
    request = SearchRequest.model_validate(body())
    before = request.model_dump()
    candidates = candidate_submissions(request)
    assert len(candidates) == 3
    assert [x.start_utc.day for x in candidates] == [4, 5, 6]
    assert [x.end_utc.day for x in candidates] == [5, 6, 7]
    assert all(x.refine and x.observer_height_m == 2 for x in candidates)
    assert request.model_dump() == before


def test_non_divisible_end_does_not_invent_an_extra_start():
    request = SearchRequest.model_validate(body(last_start_utc="2026-11-06T12:00:00Z"))
    assert len(candidate_submissions(request)) == 3


@pytest.mark.parametrize(
    "patch",
    [
        dict(last_start_utc="2026-11-03T00:00:00Z"),
        dict(last_start_utc="2026-11-06T00:00:00"),
        dict(last_start_utc="2026-11-06T00:00:00+03:00"),
        dict(last_start_utc="2026-12-06T00:00:00Z"),
        dict(start_step_hours=1),
    ],
)
def test_invalid_or_excessive_grid_rejected(patch):
    with pytest.raises(ValueError):
        candidate_submissions(SearchRequest.model_validate(body(**patch)))


def test_last_mission_end_must_remain_supported():
    values = body(last_start_utc="2028-01-01T00:00:00Z")
    values["template"].update(
        start_utc="2027-12-30T00:00:00Z", end_utc="2027-12-31T00:00:00Z"
    )
    with pytest.raises(ValueError):
        candidate_submissions(SearchRequest.model_validate(values))


def test_thresholds_include_exact_boundaries_and_report_each_condition():
    request = SearchRequest.model_validate(body())
    actual = result()
    before = deepcopy(actual)
    assessment = assess_result(actual, request)
    assert assessment["state"] == "match"
    assert all(row["passed"] is True for row in assessment["conditions"])
    assert actual == before
    assert assessment == assess_result(actual, request)


@pytest.mark.parametrize(
    "changes", [dict(sun=69.9), dict(dark=21601), dict(earth=14399)]
)
def test_any_unmet_threshold_rejects_candidate(changes):
    assessment = assess_result(result(**changes), SearchRequest.model_validate(body()))
    assert assessment["state"] == "not_met"
    assert any(row["passed"] is False for row in assessment["conditions"])


@pytest.mark.parametrize("changes", [dict(unknown_sun=1), dict(unknown_earth=1)])
def test_unknown_never_matches_even_with_permissive_thresholds(changes):
    assessment = assess_result(
        result(**changes),
        SearchRequest.model_validate(
            body(min_sunlight_percent=0, max_darkness_hours=744, min_earth_hours=0)
        ),
    )
    assert assessment["state"] == "unknown"
    assert any(row["passed"] is None for row in assessment["conditions"])


def test_durable_tick_reuses_job_and_resumes_after_reopen(tmp_path):
    jobs = JobStore(tmp_path / "jobs.sqlite")
    searches = SearchStore(jobs)
    search = searches.create(SearchRequest.model_validate(body()))
    searches.tick()
    first = searches.get(search["id"])
    first_id = first["candidates"][0]["job_id"]
    assert jobs.get(first_id)["status"] == "pending"
    searches.tick()
    assert len(jobs.pending()) == 1
    jobs.update(first_id, "ready", result=result())
    reopened = SearchStore(JobStore(tmp_path / "jobs.sqlite"))
    reopened.tick()
    current = reopened.get(search["id"])
    assert current["completed"] == 1
    assert current["matches"] == 1
    second_id = current["candidates"][1]["job_id"]
    jobs.update(second_id, "failed", error="Injected source failure")
    reopened.tick()
    third_id = reopened.get(search["id"])["candidates"][2]["job_id"]
    jobs.update(third_id, "insufficient_data", result=result(unknown_earth=100))
    reopened.tick()
    final = reopened.get(search["id"])
    assert final["status"] == "partial"
    assert final["completed"] == 3
    assert [c["state"] for c in final["candidates"]] == ["match", "failed", "unknown"]
    reopened.tick()
    assert reopened.get(search["id"]) == final


def test_queue_capacity_waits_without_losing_candidate(tmp_path):
    jobs = JobStore(tmp_path / "jobs.sqlite")
    searches = SearchStore(jobs)
    ids = [
        jobs.create(Submission.model_validate(body()["template"]))["id"]
        for _ in range(8)
    ]
    search = searches.create(SearchRequest.model_validate(body()))
    searches.tick()
    candidate = searches.get(search["id"])["candidates"][0]
    assert candidate["state"] == "waiting"
    assert jobs.get(candidate["job_id"]) is None
    jobs.update(ids[0], "failed", error="fixture")
    searches.tick()
    assert jobs.get(candidate["job_id"])["status"] == "pending"


def test_active_search_limit_and_api_statuses(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as client:
        assert client.get("/searches/missing").status_code == 404
        assert (
            client.post("/searches", json=body(start_step_hours=1)).status_code == 422
        )
        assert (
            client.post("/searches", json=body(min_sunlight_percent=101)).status_code
            == 422
        )
        response = client.post("/searches", json=body())
        assert response.status_code == 202
        search = response.json()
        assert search["total"] == 3
        assert search["completed"] == 0
        assert client.get("/searches/" + search["id"]).json()["id"] == search["id"]
        assert client.post("/searches", json=body()).status_code == 202
        assert client.post("/searches", json=body()).status_code == 429
