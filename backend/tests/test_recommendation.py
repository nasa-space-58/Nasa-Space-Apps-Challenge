"""R3 recommendations: criterion scores, hard constraints, weighted ranking,
rescoring (S9), schedule comparison, job orchestration and API.

Score rules (documented in docs/recommendations.md):
energy = sunlight % / 100; darkness = 1 - longest darkness / window;
communication = combined DTE % / 100 (R2); terrain_safety = safety_score().
Totals are weight-normalized means, so contributions sum to the total.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.recommendation as module
from app.analysis_api import JobStore, create_app
from app.communication import CommunicationStore
from app.recommendation import (
    CRITERIA,
    NOT_MODELED,
    Constraints,
    RecommendationRequest,
    RecommendationStore,
    Weights,
    candidate_ids,
    compare_schedules,
    criterion_scores,
    evaluate,
    rank,
    rescore,
)

DAY_S = 86400.0
SCORE_TOL = 1e-12


def analysis(
    sun_pct=50.0, longest_dark_s=21600.0, sun_unknown_s=0.0, earth_unknown_s=0.0
):
    return {
        "request": {
            "start_utc": "2026-11-04T00:00:00Z",
            "end_utc": "2026-11-05T00:00:00Z",
        },
        "sun": {
            "metrics": {
                "visible_percent_of_interval": sun_pct,
                "visible_duration_s": sun_pct / 100 * DAY_S,
                "longest_blocked_s": longest_dark_s,
                "unknown_duration_s": sun_unknown_s,
            }
        },
        "earth": {"metrics": {"unknown_duration_s": earth_unknown_s}},
    }


def communication(pct=25.0, unknown_s=0.0):
    return {
        "windows": {
            "combined": {
                "metrics": {
                    "available_percent_of_interval": pct,
                    "available_duration_s": pct / 100 * DAY_S,
                    "unknown_duration_s": unknown_s,
                }
            }
        }
    }


def safety(slope=2.0, hazard=None, unknown=0.0, covered=True):
    return {
        "covered": covered,
        "site_slope_deg": slope,
        "max_landing_slope_deg": slope,
        "hazard_distance_m": hazard,
        "unknown_landing_fraction": unknown,
        "unknown_search_fraction": 0.0,
    }


def candidate(site_id, lat=-85.0, lon=10.0):
    return {"site_id": site_id, "latitude_deg": lat, "longitude_deg_east": lon}


def request_body(**changes):
    body = {
        "candidates": [
            candidate("a", -85.9948, 359.76422),
            candidate("b", -84.6, 31.0),
        ],
        "start_utc": "2026-11-04T00:00:00Z",
        "end_utc": "2026-11-05T00:00:00Z",
        "refine": False,
    }
    body.update(changes)
    return body


# ------------------------------------------------------------------ input models


def test_defaults():
    weights = Weights()
    assert [getattr(weights, c) for c in CRITERIA] == [1.0, 1.0, 1.0, 1.0]
    constraints = Constraints()
    assert constraints.slope_limit_deg == 10.0
    assert constraints.min_sunlight_percent is None
    assert constraints.min_communication_h is None
    request = RecommendationRequest.model_validate(request_body())
    assert request.min_elevation_deg == 6.0
    assert candidate_ids(request) == ["c1", "c2"]
    assert set(NOT_MODELED) == {"fuel", "science"}


@pytest.mark.parametrize(
    "weights",
    [
        {"energy": 0, "darkness": 0, "communication": 0, "terrain_safety": 0},
        {"energy": -1},
        {"energy": 11},
        {"science": 1},
    ],
)
def test_invalid_weights(weights):
    with pytest.raises(ValidationError):
        Weights(**weights)


@pytest.mark.parametrize(
    "changes",
    [
        {"candidates": [candidate("a")]},
        {"candidates": [candidate(str(i), -85 - i * 0.1) for i in range(7)]},
        {"candidates": [candidate("a"), candidate("a")]},
        {"candidates": [candidate("a", -70.0), candidate("b")]},
        {"start_utc": "2026-11-05T00:00:00Z"},
        {"start_utc": "2026-11-04T00:00:00"},
        {"end_utc": "2028-01-02T00:00:00Z"},
        {"step_s": 7},
        {"min_elevation_deg": 5},
        {"constraints": {"slope_limit_deg": 50}},
        {"constraints": {"min_sunlight_percent": 101}},
        {"unexpected": True},
    ],
)
def test_invalid_requests(changes):
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate(request_body(**changes))


# ------------------------------------------------------------- criterion scores


def test_criterion_scores_follow_documented_rules():
    scores = criterion_scores(
        analysis(sun_pct=20.5, longest_dark_s=68760.0),
        communication(pct=26.0),
        safety(slope=5.0, hazard=1000.0),
        Constraints(),
    )
    assert set(scores) == set(CRITERIA)
    assert scores["energy"]["score"] == pytest.approx(0.205, abs=SCORE_TOL)
    assert scores["darkness"]["score"] == pytest.approx(
        1 - 68760 / DAY_S, abs=SCORE_TOL
    )
    assert scores["communication"]["score"] == pytest.approx(0.26, abs=SCORE_TOL)
    # 0.5 * (1 - 5/10) + 0.5 * (1000/2000)
    assert scores["terrain_safety"]["score"] == pytest.approx(0.5, abs=SCORE_TOL)
    for entry in scores.values():
        assert 0 <= entry["score"] <= 1
        assert entry["unit"] and entry["rule"]
    assert scores["energy"]["value"] == 20.5
    assert scores["darkness"]["value"] == 68760.0


@pytest.mark.parametrize("dark", [0.0, DAY_S, 2 * DAY_S])
def test_darkness_score_is_clamped(dark):
    scores = criterion_scores(
        analysis(longest_dark_s=dark), communication(), safety(), Constraints()
    )
    assert 0 <= scores["darkness"]["score"] <= 1


# ----------------------------------------------------------- hard constraints


def test_clean_candidate_is_ranked():
    result = evaluate("c1", analysis(), communication(), safety(), Constraints())
    assert result["candidate_id"] == "c1"
    assert result["status"] == "ranked"
    assert result["failures"] == [] and result["unknowns"] == []
    assert set(result["criteria"]) == set(CRITERIA)


def test_steep_landing_area_is_rejected_with_values():
    result = evaluate(
        "c1", analysis(), communication(), safety(slope=12.5), Constraints()
    )
    assert result["status"] == "rejected"
    assert result["failures"] == [
        {"constraint": "slope", "actual": 12.5, "limit": 10.0, "unit": "deg"}
    ]


def test_slope_at_limit_passes():
    assert (
        evaluate("c1", analysis(), communication(), safety(slope=10.0), Constraints())[
            "status"
        ]
        == "ranked"
    )


def test_missing_terrain_coverage_is_rejected():
    result = evaluate(
        "c1", analysis(), communication(), safety(covered=False), Constraints()
    )
    assert result["status"] == "rejected"
    assert result["failures"][0]["constraint"] == "terrain_coverage"


def test_all_failures_are_listed():
    constraints = Constraints(min_sunlight_percent=60, min_communication_h=10)
    result = evaluate(
        "c1", analysis(sun_pct=20), communication(pct=10), safety(slope=15), constraints
    )
    assert result["status"] == "rejected"
    assert [f["constraint"] for f in result["failures"]] == [
        "slope",
        "sunlight",
        "communication",
    ]
    sunlight = result["failures"][1]
    assert (
        sunlight["actual"] == 20 and sunlight["limit"] == 60 and sunlight["unit"] == "%"
    )
    comm = result["failures"][2]
    assert (
        comm["actual"] == pytest.approx(2.4)
        and comm["limit"] == 10
        and comm["unit"] == "h"
    )


def test_minimums_met_pass():
    constraints = Constraints(min_sunlight_percent=40, min_communication_h=5)
    assert (
        evaluate(
            "c1", analysis(sun_pct=50), communication(pct=25), safety(), constraints
        )["status"]
        == "ranked"
    )


def test_uncertain_minimum_is_insufficient_not_rejected():
    # 40% visible + 1 day * 20% unknown could still reach 50%.
    constraints = Constraints(min_sunlight_percent=50)
    result = evaluate(
        "c1",
        analysis(sun_pct=40, sun_unknown_s=0.2 * DAY_S),
        communication(),
        safety(),
        constraints,
    )
    assert result["status"] == "insufficient_data"
    assert result["failures"] == []
    assert "sunlight" in result["unknowns"]


def test_certain_failure_despite_unknown_is_rejected():
    constraints = Constraints(min_sunlight_percent=80)
    result = evaluate(
        "c1",
        analysis(sun_pct=40, sun_unknown_s=0.1 * DAY_S),
        communication(),
        safety(),
        constraints,
    )
    assert result["status"] == "rejected"


@pytest.mark.parametrize(
    "inputs, unknown",
    [
        ({"analysis": analysis(sun_unknown_s=60)}, "sunlight"),
        ({"analysis": analysis(earth_unknown_s=60)}, "communication"),
        ({"communication": communication(unknown_s=60)}, "communication"),
        ({"safety": safety(unknown=0.2)}, "terrain"),
    ],
)
def test_unknown_inputs_are_insufficient(inputs, unknown):
    args = {
        "analysis": analysis(),
        "communication": communication(),
        "safety": safety(),
    } | inputs
    result = evaluate(
        "c1", args["analysis"], args["communication"], args["safety"], Constraints()
    )
    assert result["status"] == "insufficient_data"
    assert unknown in result["unknowns"]


def test_known_steep_slope_rejects_even_with_unknown_pixels():
    result = evaluate(
        "c1", analysis(), communication(), safety(slope=20, unknown=0.4), Constraints()
    )
    assert result["status"] == "rejected"


# --------------------------------------------------------------------- ranking


def evaluations():
    return [
        # c1 scores: energy 0.95, darkness 0.9167, communication 0.10, terrain 0.90.
        evaluate(
            "c1",
            analysis(sun_pct=95, longest_dark_s=7200),
            communication(pct=10),
            safety(slope=2),
            Constraints(),
        ),
        evaluate(
            "c2",
            analysis(sun_pct=40, longest_dark_s=7200),
            communication(pct=60),
            safety(slope=4),
            Constraints(),
        ),
        evaluate(
            "c3",
            analysis(sun_pct=20, longest_dark_s=60000),
            communication(pct=5),
            safety(slope=8),
            Constraints(),
        ),
        evaluate("c4", analysis(), communication(), safety(slope=25), Constraints()),
        evaluate(
            "c5", analysis(sun_unknown_s=600), communication(), safety(), Constraints()
        ),
    ]


def test_ranking_is_weighted_mean_with_contributions():
    result = rank(evaluations(), Weights())
    ranked = result["ranked"]
    assert [r["candidate_id"] for r in ranked] == ["c1", "c2", "c3"]
    assert [r["rank"] for r in ranked] == [1, 2, 3]
    for row in ranked:
        scores = row["criteria"]
        expected = sum(scores[c]["score"] for c in CRITERIA) / 4
        assert row["total_score"] == pytest.approx(expected, abs=SCORE_TOL)
        assert sum(row["contributions"].values()) == pytest.approx(
            row["total_score"], abs=SCORE_TOL
        )
        assert 0 <= row["total_score"] <= 1
        assert row["explanation"]
    assert [r["candidate_id"] for r in result["rejected"]] == ["c4"]
    assert result["rejected"][0]["failures"][0]["constraint"] == "slope"
    assert [r["candidate_id"] for r in result["insufficient_data"]] == ["c5"]
    assert result["weights"] == pytest.approx({c: 0.25 for c in CRITERIA})
    assert result["not_modeled"] == NOT_MODELED


def test_explanation_names_strongest_and_weakest_criteria():
    row = rank(evaluations(), Weights())["ranked"][0]
    # c1: energy 0.95 is strongest; communication 0.10 is weakest.
    assert "energy" in row["explanation"]
    assert "communication" in row["explanation"]
    assert row["strongest"] == "energy" and row["weakest"] == "communication"


def test_zero_weight_removes_contribution():
    row = rank(evaluations(), Weights(communication=0))["ranked"][0]
    assert row["contributions"]["communication"] == 0
    assert (
        rank(evaluations(), Weights(communication=0))["weights"]["communication"] == 0
    )


def test_ties_break_by_candidate_id():
    same = [
        evaluate(cid, analysis(), communication(), safety(), Constraints())
        for cid in ("c2", "c1")
    ]
    assert [r["candidate_id"] for r in rank(same, Weights())["ranked"]] == ["c1", "c2"]


def test_communication_priority_reorders_and_explains():
    previous, current = (
        Weights(),
        Weights(energy=0, darkness=0, communication=10, terrain_safety=0),
    )
    result = rescore(evaluations(), previous, current)
    assert result["previous"] == rank(evaluations(), previous)
    assert [r["candidate_id"] for r in result["current"]["ranked"]] == [
        "c2",
        "c1",
        "c3",
    ]
    changes = {c["candidate_id"]: c for c in result["changes"]}
    assert changes["c2"]["previous_rank"] == 2 and changes["c2"]["new_rank"] == 1
    assert changes["c2"]["reason"] == "communication"
    assert changes["c1"]["previous_rank"] == 1 and changes["c1"]["new_rank"] == 2
    assert changes["c3"]["previous_rank"] == changes["c3"]["new_rank"] == 3
    assert changes["c3"]["reason"] is None
    assert "c4" not in changes and "c5" not in changes


def test_rescore_with_same_weights_changes_nothing():
    result = rescore(evaluations(), Weights(), Weights())
    assert all(c["previous_rank"] == c["new_rank"] for c in result["changes"])


# ------------------------------------------------------------ schedule change


def finished(ranked_ids, rejected_ids=()):
    rows = [
        {"candidate_id": cid, "rank": i + 1, "total_score": 1 - i / 10}
        for i, cid in enumerate(ranked_ids)
    ]
    return {
        "id": "x",
        "request": {"start_utc": "2026-11-04T00:00:00Z"},
        "candidates": [
            {"candidate_id": cid, "site": {"site_id": f"site-{cid}"}}
            for cid in [*ranked_ids, *rejected_ids]
        ],
        "ranking": {
            "ranked": rows,
            "rejected": [
                {"candidate_id": cid, "failures": [{"constraint": "sunlight"}]}
                for cid in rejected_ids
            ],
            "insufficient_data": [],
        },
    }


def test_schedule_change_suggests_new_best_site():
    result = compare_schedules(finished(["c1", "c2"]), finished(["c2"], ["c1"]))
    assert result["best_before"] == "c1"
    assert result["best_after"] == "c2"
    changes = {c["candidate_id"]: c for c in result["changes"]}
    assert changes["c1"]["previous_rank"] == 1 and changes["c1"]["new_rank"] is None
    assert changes["c1"]["new_status"] == "rejected"
    assert changes["c2"]["previous_rank"] == 2 and changes["c2"]["new_rank"] == 1
    assert "site-c2" in result["suggestion"]


def test_schedule_change_without_any_ranked_site():
    result = compare_schedules(finished(["c1"]), finished([], ["c1"]))
    assert result["best_after"] is None
    assert result["suggestion"]


# ----------------------------------------------------------- store orchestration


@pytest.fixture
def stores(tmp_path):
    jobs = JobStore(tmp_path / "jobs.sqlite")
    comms = CommunicationStore(jobs)
    return jobs, comms, RecommendationStore(jobs, comms)


def view(**kwargs):
    return {"analysis": analysis(**kwargs), "horizon": {}, "frames": []}


def advance(
    jobs,
    comms,
    store,
    data_dir,
    rec_id,
    monkeypatch,
    safety_by_lat=None,
    fail_first=False,
):
    monkeypatch.setattr(
        module,
        "terrain_safety",
        lambda root, lat, lon, settings: {**safety(), "score": 1.0}
        | (safety_by_lat or {}).get(lat, {}),
    )
    store.tick(data_dir)
    rec = store.get(rec_id)
    for index, cand in enumerate(rec["candidates"]):
        if fail_first and index == 0:
            jobs.update(cand["analysis_job_id"], "failed", error="Horizons unavailable")
        else:
            jobs.update(
                cand["analysis_job_id"], "ready", result=view(sun_pct=30 + 10 * index)
            )
    store.tick(data_dir)
    for cand in store.get(rec_id)["candidates"]:
        if cand.get("communication_job_id"):
            comms.update(
                cand["communication_job_id"], "ready", result=communication(pct=20)
            )
    store.tick(data_dir)
    return store.get(rec_id)


def test_tick_runs_analysis_then_communication_then_ranking(
    stores, tmp_path, monkeypatch
):
    jobs, comms, store = stores
    request = RecommendationRequest.model_validate(request_body(min_elevation_deg=10))
    rec = store.create(request)
    assert rec["status"] == "pending"
    assert [c["candidate_id"] for c in rec["candidates"]] == ["c1", "c2"]
    assert all(c["state"] == "waiting" for c in rec["candidates"])
    monkeypatch.setattr(module, "terrain_safety", lambda *a: pytest.fail("too early"))
    store.tick(tmp_path)
    rec = store.get(rec["id"])
    assert rec["status"] == "running"
    assert all(c["state"] == "analyzing" for c in rec["candidates"])
    pending = jobs.pending()
    assert len(pending) == 2
    submitted = {j["id"]: j["submission"] for j in pending}
    first = submitted[rec["candidates"][0]["analysis_job_id"]]
    assert first["site_id"] == "a" and first["latitude_deg"] == -85.9948
    assert (
        first["start_utc"].startswith("2026-11-04T00:00:00")
        and first["refine"] is False
    )

    for cand in rec["candidates"]:
        jobs.update(cand["analysis_job_id"], "ready", result=view())
    store.tick(tmp_path)
    rec = store.get(rec["id"])
    assert all(c["state"] == "communicating" for c in rec["candidates"])
    comm_jobs = [comms.get(c["communication_job_id"]) for c in rec["candidates"]]
    assert all(j["request"] == {"min_elevation_deg": 10.0} for j in comm_jobs)
    assert [j["analysis_id"] for j in comm_jobs] == [
        c["analysis_job_id"] for c in rec["candidates"]
    ]

    seen = []

    def fake_safety(root, lat, lon, settings):
        seen.append((lat, lon, settings.slope_limit_deg))
        return {**safety(), "score": 1.0}

    monkeypatch.setattr(module, "terrain_safety", fake_safety)
    for cand in rec["candidates"]:
        comms.update(cand["communication_job_id"], "ready", result=communication())
    store.tick(tmp_path)
    rec = store.get(rec["id"])
    assert rec["status"] == "ready"
    assert all(c["state"] == "evaluated" for c in rec["candidates"])
    assert sorted(seen) == sorted([(-85.9948, 359.76422, 10.0), (-84.6, 31.0, 10.0)])
    assert len(rec["ranking"]["ranked"]) == 2
    assert rec["candidates"][0]["evaluation"]["status"] == "ranked"


def test_failed_candidate_makes_partial_result(stores, tmp_path, monkeypatch):
    jobs, comms, store = stores
    rec = store.create(RecommendationRequest.model_validate(request_body()))
    rec = advance(jobs, comms, store, tmp_path, rec["id"], monkeypatch, fail_first=True)
    assert rec["status"] == "partial"
    assert rec["candidates"][0]["state"] == "failed"
    assert "Horizons unavailable" in rec["candidates"][0]["error"]
    assert [r["candidate_id"] for r in rec["ranking"]["ranked"]] == ["c2"]
    assert rec["ranking"]["failed"] == [
        {"candidate_id": "c1", "error": rec["candidates"][0]["error"]}
    ]


def test_terrain_rejection_flows_into_ranking(stores, tmp_path, monkeypatch):
    jobs, comms, store = stores
    rec = store.create(RecommendationRequest.model_validate(request_body()))
    rec = advance(
        jobs,
        comms,
        store,
        tmp_path,
        rec["id"],
        monkeypatch,
        safety_by_lat={-84.6: safety(slope=14.0)},
    )
    assert rec["status"] == "ready"
    assert [r["candidate_id"] for r in rec["ranking"]["rejected"]] == ["c2"]


def test_active_recommendations_are_bounded(stores):
    _, _, store = stores
    request = RecommendationRequest.model_validate(request_body())
    store.create(request)
    store.create(request)
    with pytest.raises(ValueError):
        store.create(request)


def test_reschedule_shifts_window_and_links_previous(stores, tmp_path, monkeypatch):
    jobs, comms, store = stores
    base = store.create(RecommendationRequest.model_validate(request_body()))
    with pytest.raises(ValueError):
        store.reschedule(base["id"], 48)  # not finished yet
    advance(jobs, comms, store, tmp_path, base["id"], monkeypatch)
    for bad in (0, -1, 721):
        with pytest.raises(ValueError):
            store.reschedule(base["id"], bad)
    moved = store.reschedule(base["id"], 48)
    assert moved["previous_id"] == base["id"]
    start = datetime.fromisoformat(moved["request"]["start_utc"].replace("Z", "+00:00"))
    assert start == datetime(2026, 11, 6, tzinfo=timezone.utc)
    end = datetime.fromisoformat(moved["request"]["end_utc"].replace("Z", "+00:00"))
    assert end - start == timedelta(days=1)
    assert moved["request"]["weights"] == base["request"]["weights"]
    assert store.get(moved["id"])["schedule_change"] is None
    advance(jobs, comms, store, tmp_path, moved["id"], monkeypatch)
    change = store.get(moved["id"])["schedule_change"]
    assert change["best_before"] and change["best_after"]
    assert change["suggestion"]


def test_reschedule_respects_supported_dates(stores, tmp_path, monkeypatch):
    jobs, comms, store = stores
    late = request_body(
        start_utc="2027-12-30T00:00:00Z", end_utc="2027-12-31T00:00:00Z"
    )
    rec = store.create(RecommendationRequest.model_validate(late))
    advance(jobs, comms, store, tmp_path, rec["id"], monkeypatch)
    with pytest.raises(ValueError):
        store.reschedule(rec["id"], 48)


# -------------------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as c:
        yield c


def test_routes(client, tmp_path, monkeypatch):
    assert (
        client.post("/recommendations", json=request_body(candidates=[])).status_code
        == 422
    )
    response = client.post("/recommendations", json=request_body())
    assert response.status_code == 202
    rec = response.json()
    assert rec["status"] == "pending"
    assert client.get(f"/recommendations/{rec['id']}").json()["id"] == rec["id"]
    assert client.get("/recommendations/missing").status_code == 404
    assert (
        client.post(f"/recommendations/{rec['id']}/rescore", json={}).status_code == 409
    )
    assert (
        client.post(
            f"/recommendations/{rec['id']}/reschedule", json={"delay_hours": 48}
        ).status_code
        == 409
    )
    assert client.post("/recommendations/missing/rescore", json={}).status_code == 404

    state = client.app.state
    advance(
        state.store,
        state.communications,
        state.recommendations,
        tmp_path / "data",
        rec["id"],
        monkeypatch,
    )
    rescored = client.post(
        f"/recommendations/{rec['id']}/rescore",
        json={"energy": 0, "darkness": 0, "communication": 1, "terrain_safety": 0},
    )
    assert rescored.status_code == 200
    body = rescored.json()
    assert set(body) >= {"previous", "current", "changes"}
    assert (
        client.post(
            f"/recommendations/{rec['id']}/rescore", json={"energy": -1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/recommendations/{rec['id']}/reschedule", json={"delay_hours": 0}
        ).status_code
        == 422
    )
    moved = client.post(
        f"/recommendations/{rec['id']}/reschedule", json={"delay_hours": 48}
    )
    assert moved.status_code == 202
    assert moved.json()["previous_id"] == rec["id"]
    client.post("/recommendations", json=request_body())
    assert client.post("/recommendations", json=request_body()).status_code == 429
