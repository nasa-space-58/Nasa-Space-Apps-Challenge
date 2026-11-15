"""H9 acceptance: independent interval oracle and evidence association checks."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from itertools import product

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import shared_antenna as campaign
from app.hybrid_planning import select_independent, solve_exact, validate_plan
from app.solver_arena import ArenaRequest, compare_scenario
from shared.hybrid import Plan, fingerprint

START = datetime(2026, 11, 4, tzinfo=timezone.utc)


def window(hours, duration=0.5):
    return dict(
        start_utc=(START + timedelta(hours=hours)).isoformat(),
        end_utc=(START + timedelta(hours=hours + duration)).isoformat(),
    )


@pytest.fixture
def evidence():
    snapshots = []
    alternatives = []
    for i, hour in enumerate((0, 1, 24, 25), 1):
        site = dict(
            site_id=f"site-{i}",
            latitude_deg=-84.6,
            longitude_deg_east=31.0,
            reference_frame="MOON_ME",
            altitude_km=5.0,
        )
        interval = window(hour)
        analysis = dict(
            id=f"analysis-{i}",
            status="ready",
            submission={
                **interval,
                **{
                    k: site[k]
                    for k in ("site_id", "latitude_deg", "longitude_deg_east")
                },
            },
            result={
                "analysis": dict(
                    request={"site": site, **interval},
                    sun={
                        "intervals": [{**interval, "state": "full", "duration_s": 1800}]
                    },
                    earth={
                        "intervals": [
                            {**interval, "state": "visible", "duration_s": 1800}
                        ]
                    },
                    provenance={"terrain_reference": "test-terrain", "ephemerides": []},
                )
            },
        )
        communication = dict(
            id=f"comm-{i}",
            analysis_id=analysis["id"],
            status="ready",
            result=dict(
                analysis_id=analysis["id"],
                **interval,
                min_elevation_deg=6,
                windows={
                    "stations": {
                        "DSS-43": {
                            "intervals": [
                                {**interval, "state": "available", "duration_s": 1800}
                            ]
                        }
                    }
                },
                source={"station_url": "https://example.test/fixture"},
            ),
        )
        snapshots.append(dict(analysis=analysis, communication=communication))
        alternatives.append(dict(index=i, analysis_id=analysis["id"], window=interval))
    return dict(
        version="h9-evidence-v1",
        station_id="DSS-43",
        snapshots=snapshots,
        alternatives=alternatives,
    )


def test_independent_conflicts_but_joint_plan_matches_interval_oracle(evidence):
    scenario = campaign.build_campaign(campaign.CampaignRequest(), evidence)
    independent = select_independent(scenario)
    assert independent.validation.status == "infeasible"
    assert any(i.code == "capacity" for i in independent.validation.issues)
    exact = solve_exact(scenario)
    options = {
        m.mission_id: [c for c in scenario.candidates if c.mission_id == m.mission_id]
        for m in scenario.missions
    }
    feasible_scores = []
    for assignment in product(*options.values()):
        # Each contact requests one antenna. Compare interval overlap directly,
        # without using H1's validator, H3 resources or the QUBO encoding.
        if any(
            max(a.window.start_utc, b.window.start_utc)
            < min(a.window.end_utc, b.window.end_utc)
            for i, a in enumerate(assignment)
            for b in assignment[i + 1 :]
        ):
            continue
        score = sum(
            Fraction(str(o.weight))
            * Fraction(str(c.metrics[o.metric]))
            / Fraction(str(o.scale))
            for c in assignment
            for o in scenario.objectives
        )
        feasible_scores.append(score)
    assert exact.status == "optimal"
    assert exact.evaluated_combinations == 64
    assert exact.feasible_count == len(feasible_scores) == 24
    assert Fraction(exact.validation.exact_score) == max(feasible_scores)


@pytest.mark.parametrize(
    "mode,status",
    [("closed", "infeasible"), ("deadline", "infeasible"), ("unknown", "incomplete")],
)
def test_unavailable_or_unknown_is_not_a_solution(evidence, mode, status):
    scenario = campaign.build_campaign(campaign.CampaignRequest(mode=mode), evidence)
    assert solve_exact(scenario).status == status
    result = compare_scenario(scenario, ArenaRequest(evaluations=2, shots=32))
    assert result["quantum"]["status"] == "not_run"
    assert result["hardware_jobs"] == 0


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_pinned_crew_is_preserved_and_changes_problem_identity(evidence, index):
    original = campaign.build_campaign(campaign.CampaignRequest(), evidence)
    revised = campaign.build_campaign(
        campaign.CampaignRequest(crew_option=index), evidence
    )
    assert fingerprint(original) != fingerprint(revised)
    solved = solve_exact(revised)
    assert solved.status == "optimal"
    assert f"crew-{index}" in solved.plan.selected_option_ids
    with pytest.raises(ValueError):
        validate_plan(solved.plan, original)


def test_no_mutation_determinism_and_same_input_for_both_solvers(evidence):
    before = deepcopy(evidence)
    problem = campaign.build_campaign(campaign.CampaignRequest(), evidence)
    again = campaign.build_campaign(campaign.CampaignRequest(), deepcopy(evidence))
    assert fingerprint(problem) == fingerprint(again)
    assert evidence == before
    result = compare_scenario(problem, ArenaRequest(evaluations=8, shots=128, seed=17))
    assert result["input_fingerprint"] == fingerprint(problem)
    assert result["quantum"]["result"]["logical_qubits"] == 6
    assert result["quantum"]["validation"]["status"] == "feasible"
    assert sum(s["count"] for s in result["quantum"]["result"]["samples"]) == 128
    duplicate = compare_scenario(
        problem, ArenaRequest(evaluations=8, shots=128, seed=17)
    )
    assert (
        result["quantum"]["result"]["samples"]
        == duplicate["quantum"]["result"]["samples"]
    )


@pytest.mark.parametrize(
    "failure",
    [
        "site",
        "dates",
        "request_site",
        "comm_id",
        "comm_dates",
        "status",
        "sun_gap",
        "sun_state",
        "sun_overlap",
        "sun_duration",
        "missing_station",
        "outside",
        "duplicate",
        "nan",
    ],
)
def test_rejects_wrong_or_incomplete_evidence_association(evidence, failure):
    entry = evidence["snapshots"][0]
    if failure == "site":
        entry["analysis"]["submission"]["latitude_deg"] = -82.0
    elif failure == "dates":
        entry["analysis"]["submission"]["end_utc"] = window(2)["end_utc"]
    elif failure == "request_site":
        entry["analysis"]["result"]["analysis"]["request"]["site"][
            "reference_frame"
        ] = "OTHER"
    elif failure == "comm_id":
        entry["communication"]["result"]["analysis_id"] = "other"
    elif failure == "comm_dates":
        entry["communication"]["result"]["end_utc"] = window(2)["end_utc"]
    elif failure == "status":
        entry["communication"]["status"] = "pending"
    elif failure.startswith("sun_"):
        rows = entry["analysis"]["result"]["analysis"]["sun"]["intervals"]
        if failure == "sun_gap":
            rows.clear()
        elif failure == "sun_state":
            rows[0]["state"] = "made_up"
        elif failure == "sun_overlap":
            rows.append(deepcopy(rows[0]))
        elif failure == "sun_duration":
            rows[0]["duration_s"] = 1
    elif failure == "missing_station":
        entry["communication"]["result"]["windows"]["stations"].clear()
    elif failure == "outside":
        evidence["alternatives"][0]["window"] = window(3)
    elif failure == "duplicate":
        evidence["snapshots"].append(deepcopy(entry))
    elif failure == "nan":
        entry["analysis"]["submission"]["latitude_deg"] = float("nan")
    with pytest.raises(ValueError):
        campaign.build_campaign(campaign.CampaignRequest(), evidence)


def test_unknown_geometry_is_preserved(evidence):
    evidence["snapshots"][0]["communication"]["result"]["windows"]["stations"][
        "DSS-43"
    ]["intervals"][0]["state"] = "unknown"
    problem = campaign.build_campaign(campaign.CampaignRequest(), evidence)
    assert solve_exact(problem).status == "incomplete"


def test_blocked_geometry_is_not_erased(evidence):
    for entry in evidence["snapshots"]:
        entry["communication"]["result"]["windows"]["stations"]["DSS-43"]["intervals"][
            0
        ]["state"] = "unavailable"
    problem = campaign.build_campaign(campaign.CampaignRequest(), evidence)
    assert solve_exact(problem).status == "infeasible"


def test_timeline_records_real_occupancy_and_rejects_stale(evidence):
    problem = campaign.build_campaign(campaign.CampaignRequest(), evidence)
    choice = select_independent(problem)
    timeline = campaign.campaign_timeline(problem, choice.plan)
    assert timeline["validation"]["status"] == "infeasible"
    assert len(timeline["rows"]) == 3
    assert any(
        s["required_channels"] == 3 and s["conflict"] for s in timeline["occupancy"]
    )
    solved = solve_exact(problem)
    clean = campaign.campaign_timeline(problem, solved.plan)
    assert all(
        s["required_channels"] <= 1 and not s["conflict"] for s in clean["occupancy"]
    )
    assert all(
        0 <= row["left_percent"] < 100 and row["width_percent"] > 0
        for row in clean["rows"]
    )
    changed = campaign.build_campaign(campaign.CampaignRequest(crew_option=4), evidence)
    with pytest.raises(ValueError):
        campaign.campaign_timeline(changed, solved.plan)


def test_adjacent_half_open_contacts_do_not_conflict(evidence):
    for i, item in enumerate(evidence["alternatives"]):
        shift = window(i * 0.5)
        item["window"] = shift
        entry = evidence["snapshots"][i]
        entry["analysis"]["submission"].update(shift)
        entry["analysis"]["result"]["analysis"]["request"].update(shift)
        for name in ("sun", "earth"):
            entry["analysis"]["result"]["analysis"][name]["intervals"][0].update(shift)
        entry["communication"]["result"].update(shift)
        entry["communication"]["result"]["windows"]["stations"]["DSS-43"]["intervals"][
            0
        ].update(shift)
    problem = campaign.build_campaign(campaign.CampaignRequest(), evidence)
    assert solve_exact(problem).feasible_count == 24


@pytest.mark.parametrize(
    "data",
    [
        {"crew_option": -1},
        {"crew_option": 5},
        {"crew_option": True},
        {"mode": "../../secret"},
        {"evaluations": 257},
        {"shots": 0},
        {"depth": 0},
        {"seed": -1},
        {"hardware": True},
    ],
)
def test_invalid_controls(data):
    with pytest.raises(ValidationError):
        campaign.CampaignRequest(**data)


def test_bundle_sources_and_catalog_are_reproducible():
    evidence = campaign.load_campaign_evidence()
    problem = campaign.build_campaign(campaign.CampaignRequest(), evidence)
    assert len(problem.missions) == 3
    assert len(problem.candidates) == 12
    assert any(s.kind == "derived" and s.sha256 for s in problem.sources)
    catalog = campaign.campaign_catalog()
    assert len(catalog["options"]) == 4
    assert catalog["illustrative"] is True
    assert catalog["station_id"] == "DSS-43"


def test_comparison_has_three_explained_plans_and_no_cloud(monkeypatch, evidence):
    monkeypatch.setattr(campaign, "load_campaign_evidence", lambda: evidence)
    result = campaign.compare_campaign(
        campaign.CampaignRequest(evaluations=8, shots=128, crew_option=4)
    )
    assert (
        result["comparison"]["hardware_jobs"]
        == result["comparison"]["credits_spent"]
        == 0
    )
    assert result["comparison"]["request"]["preset_id"] == "h9"
    assert result["timelines"]["classical"]["validation"]["status"] == "feasible"
    assert "crew-4" in result["independent"]["plan"]["selected_option_ids"]
    assert result["campaign_request"]["crew_option"] == 4


def test_api_validation_errors_and_admission(monkeypatch, evidence):
    monkeypatch.setattr(campaign, "load_campaign_evidence", lambda: evidence)
    app = FastAPI()
    campaign.register_campaign_routes(app)
    client = TestClient(app)
    assert client.get("/campaign/catalog").status_code == 200
    assert client.post("/campaign/compare", json={"hardware": True}).status_code == 422
    assert (
        client.post(
            "/campaign/compare", json={"evaluations": 2, "shots": 32}
        ).status_code
        == 200
    )
    from app.solver_arena import _RUN_LOCK

    with _RUN_LOCK:
        assert client.post("/campaign/compare", json={}).status_code == 429

    def invalid(_):
        raise ValueError("Invalid evidence")

    monkeypatch.setattr(campaign, "compare_campaign", invalid)
    assert client.post("/campaign/compare", json={}).status_code == 400
    assert _RUN_LOCK.acquire(blocking=False)
    _RUN_LOCK.release()


def test_optional_hardware_has_explicit_state_without_submission():
    result = campaign.campaign_hardware()
    assert result["status"] in ("not_run", "completed")
    assert result["new_jobs_submitted"] == 0
    if result["status"] == "completed":
        assert result["execution_mode"] == "hardware"
        assert sum(result["counts"].values()) == result["shots"]
        problem = campaign.build_campaign(
            campaign.CampaignRequest(), campaign.load_campaign_evidence()
        )
        assert result["input_fingerprint"] == fingerprint(problem)
        if result["best_plan"]:
            assert (
                validate_plan(Plan.model_validate(result["best_plan"]), problem).status
                == "feasible"
            )
