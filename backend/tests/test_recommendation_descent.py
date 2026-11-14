"""R5 extension of R3 recommendations: opt-in descent reachability and fuel.

The R3 behavior without descent is covered by test_recommendation.py and must
stay unchanged; these tests cover only the opt-in fuel criterion.
fuel score = propellant margin / propellant available at PDI (clamped 0..1).
"""

import pytest
from pydantic import ValidationError

import app.recommendation as module
from app.analysis_api import JobStore
from app.communication import CommunicationStore
from app.recommendation import (
    CRITERIA,
    NOT_MODELED,
    Constraints,
    RecommendationRequest,
    RecommendationStore,
    Weights,
    descent_summary,
    evaluate,
    rank,
    rescore,
)
from tests.test_recommendation import (
    advance,
    analysis,
    communication,
    request_body,
    safety,
)


def plan(feasible=True, used=7369.0, available=8161.3):
    return {
        "feasible": feasible,
        "limiting_factors": []
        if feasible
        else [
            {"factor": "plane_change", "detail": "A 5.99 deg plane change is required."}
        ],
        "profile": {"usable_propellant_kg": available},
        "totals": {
            "propellant_used_kg": used,
            "propellant_margin_kg": available - used,
            "powered_delta_v_mps": 2023.0,
        },
    }


def test_fuel_weight_is_opt_in():
    weights = Weights()
    assert weights.fuel == 0.0
    assert (
        Weights(energy=0, darkness=0, communication=0, terrain_safety=0, fuel=1).fuel
        == 1
    )
    with pytest.raises(ValidationError):
        Weights(energy=0, darkness=0, communication=0, terrain_safety=0, fuel=0)
    with pytest.raises(ValidationError):
        Weights(fuel=-1)
    assert RecommendationRequest.model_validate(request_body()).include_descent is False
    assert CRITERIA == ("energy", "darkness", "communication", "terrain_safety")


def test_descent_summary():
    summary = descent_summary(plan())
    assert summary["feasible"] is True
    assert summary["propellant_used_kg"] == 7369.0
    assert summary["usable_propellant_kg"] == 8161.3
    assert summary["propellant_margin_kg"] == pytest.approx(792.3)
    assert summary["fuel_score"] == pytest.approx(792.3 / 8161.3, abs=1e-12)
    assert summary["powered_delta_v_mps"] == 2023.0
    blocked = descent_summary(plan(feasible=False))
    assert blocked["feasible"] is False
    assert "plane change" in blocked["detail"]
    assert descent_summary(plan(used=9000.0))["fuel_score"] == 0.0


def test_evaluate_adds_fuel_criterion():
    result = evaluate(
        "c1",
        analysis(),
        communication(),
        safety(),
        Constraints(),
        descent=descent_summary(plan()),
    )
    assert result["status"] == "ranked"
    assert set(result["criteria"]) == set(CRITERIA) | {"fuel"}
    fuel = result["criteria"]["fuel"]
    assert fuel["score"] == pytest.approx(792.3 / 8161.3, abs=1e-12)
    assert fuel["value"] == pytest.approx(792.3)
    assert fuel["unit"] and fuel["rule"]


def test_unreachable_descent_is_a_hard_constraint():
    result = evaluate(
        "c1",
        analysis(),
        communication(),
        safety(),
        Constraints(),
        descent=descent_summary(plan(feasible=False)),
    )
    assert result["status"] == "rejected"
    failure = result["failures"][-1]
    assert failure["constraint"] == "descent"
    explanation = rank([result], Weights())["rejected"][0]["explanation"]
    assert "plane change" in explanation


def evaluations_with_fuel():
    return [
        evaluate(
            "c1",
            analysis(sun_pct=90),
            communication(pct=10),
            safety(),
            Constraints(),
            descent=descent_summary(plan(used=8000.0)),
        ),
        evaluate(
            "c2",
            analysis(sun_pct=40),
            communication(pct=10),
            safety(),
            Constraints(),
            descent=descent_summary(plan(used=6000.0)),
        ),
    ]


def test_ranking_with_fuel_weight():
    weights = Weights(energy=1, darkness=1, communication=1, terrain_safety=1, fuel=4)
    result = rank(evaluations_with_fuel(), weights)
    assert set(result["weights"]) == set(CRITERIA) | {"fuel"}
    assert result["weights"]["fuel"] == pytest.approx(0.5)
    assert "fuel" not in result["not_modeled"] and "science" in result["not_modeled"]
    assert [r["candidate_id"] for r in result["ranked"]] == ["c2", "c1"]
    for row in result["ranked"]:
        assert sum(row["contributions"].values()) == pytest.approx(
            row["total_score"], abs=1e-12
        )
        assert "fuel" in row["contributions"]
    # Without a fuel weight the same evaluations rank on the R3 criteria only.
    plain = rank(evaluations_with_fuel(), Weights())
    assert plain["weights"]["fuel"] == 0
    assert [r["candidate_id"] for r in plain["ranked"]] == ["c1", "c2"]


def test_rescore_explains_fuel_driven_change():
    result = rescore(evaluations_with_fuel(), Weights(), Weights(fuel=10))
    changes = {c["candidate_id"]: c for c in result["changes"]}
    assert changes["c2"]["previous_rank"] == 2 and changes["c2"]["new_rank"] == 1
    assert changes["c2"]["reason"] == "fuel"


def test_r3_output_unchanged_without_descent():
    result = rank(
        [evaluate("c1", analysis(), communication(), safety(), Constraints())],
        Weights(),
    )
    assert set(result["weights"]) == set(CRITERIA)
    assert result["not_modeled"] == NOT_MODELED


def test_store_plans_descent_per_candidate(tmp_path, monkeypatch):
    jobs = JobStore(tmp_path / "jobs.sqlite")
    comms = CommunicationStore(jobs)
    store = RecommendationStore(jobs, comms)
    calls = []

    def fake_descent(data_dir, latitude, longitude, profile_id):
        calls.append((latitude, longitude, profile_id))
        return plan(feasible=latitude != -84.6)

    monkeypatch.setattr(module, "descent_plan", fake_descent)
    body = request_body(include_descent=True, weights={"fuel": 2})
    rec = store.create(RecommendationRequest.model_validate(body))
    rec = advance(jobs, comms, store, tmp_path, rec["id"], monkeypatch)
    assert sorted(calls) == sorted(
        [(-85.9948, 359.76422, "apollo-lm-11"), (-84.6, 31.0, "apollo-lm-11")]
    )
    assert rec["status"] == "ready"
    assert [r["candidate_id"] for r in rec["ranking"]["rejected"]] == ["c2"]
    assert rec["ranking"]["rejected"][0]["failures"][-1]["constraint"] == "descent"
    ranked = rec["ranking"]["ranked"][0]
    assert ranked["candidate_id"] == "c1" and "fuel" in ranked["contributions"]
    assert rec["candidates"][0]["descent"]["feasible"] is True


def test_store_without_descent_never_plans(tmp_path, monkeypatch):
    jobs = JobStore(tmp_path / "jobs.sqlite")
    comms = CommunicationStore(jobs)
    store = RecommendationStore(jobs, comms)
    monkeypatch.setattr(
        module, "descent_plan", lambda *a: pytest.fail("descent must be opt-in")
    )
    rec = store.create(RecommendationRequest.model_validate(request_body()))
    rec = advance(jobs, comms, store, tmp_path, rec["id"], monkeypatch)
    assert rec["status"] == "ready"
    assert "fuel" not in rec["ranking"]["weights"]
