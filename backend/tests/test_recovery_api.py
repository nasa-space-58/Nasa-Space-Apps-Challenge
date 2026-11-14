"""R12 event panel: replay view over H5/H6 evidence and its HTTP routes."""

import json

import pytest
from fastapi.testclient import TestClient

from app.analysis_api import create_app
from app.hybrid_recovery import QuantumBudget, replay_hybrid
from app.recovery_api import (
    EXAMPLES_DIR,
    MAX_TRAJECTORY_POINTS,
    VIEW_VERSION,
    RecoveryReplayRequest,
    build_replay_view,
    example_requests,
)
from app.self_healing import RecoveryScenario, compare_recovery, replay_recovery

FAST_BUDGET = dict(
    depth=1, evaluations=32, shots=128, seed=17, max_qubits=4, deadline_s=10.0
)


def raw(name):
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def classical_request(name="combined_recovery"):
    return RecoveryReplayRequest.model_validate({"recovery": raw(name)})


def hybrid_request(**budget):
    data = raw("hybrid_campaign")
    data["quantum_budget"] = {**FAST_BUDGET, **budget}
    recovery = data.pop("recovery")
    return RecoveryReplayRequest.model_validate({"recovery": recovery, "hybrid": data})


@pytest.fixture(scope="module")
def combined():
    return build_replay_view(classical_request())


@pytest.fixture(scope="module")
def engine_out():
    return build_replay_view(classical_request("engine_out"))


@pytest.fixture(scope="module")
def hybrid():
    return build_replay_view(hybrid_request())


def branch(view, name):
    return next(b for b in view["branches"] if b["branch"] == name)


# ----------------------------------------------------------- examples


def test_examples_are_ready_request_bodies():
    bodies = example_requests()
    assert set(bodies) == {"combined_recovery", "engine_out", "hybrid_campaign"}
    for body in bodies.values():
        RecoveryReplayRequest.model_validate(body)
    assert bodies["combined_recovery"]["hybrid"] is None
    assert bodies["engine_out"]["hybrid"] is None
    hybrid_body = bodies["hybrid_campaign"]["hybrid"]
    assert (
        hybrid_body["landing_mission_id"]
        == raw("hybrid_campaign")["landing_mission_id"]
    )
    assert bodies["hybrid_campaign"]["recovery"] == raw("hybrid_campaign")["recovery"]


# ----------------------------------------------------------- structure


def test_view_identifies_inputs_and_version(combined):
    scenario = RecoveryScenario.model_validate(raw("combined_recovery"))
    reference = compare_recovery(scenario)
    assert combined["view_version"] == VIEW_VERSION
    assert combined["scenario_id"] == "h5-combined-recovery"
    assert combined["scenario_sha256"] == reference["recovery"]["scenario_sha256"]
    assert combined["nominal_target_id"] == "primary"
    assert combined["initial_epoch_utc"].startswith("2030-01-01T00:00:00")
    assert combined["reserve_kg"] == 10.0
    assert combined["initial_propellant_kg"] == 100.0
    assert combined["hybrid"] is None
    assert [b["branch"] for b in combined["branches"]] == ["original", "classical"]


def test_targets_report_downrange_and_relative_deadlines(combined):
    targets = {t["target_id"]: t for t in combined["targets"]}
    assert set(targets) == {"primary", "backup"}
    assert targets["primary"]["downrange_m"] == 0.0
    assert targets["backup"]["downrange_m"] == 40.0
    assert targets["primary"]["deadline_elapsed_s"] == pytest.approx(30.0)
    assert targets["primary"]["nominal"] is True
    assert targets["backup"]["nominal"] is False


def test_events_are_ordered_with_elapsed_time_and_payload(combined):
    events = combined["events"]
    assert [e["event_id"] for e in events] == [
        e.event_id
        for e in RecoveryScenario.model_validate(raw("combined_recovery")).events
    ]
    kinds = {e["event_id"]: e["kind"] for e in events}
    assert kinds == {
        "fuel-loss": "propellant_loss",
        "thrust-loss": "thrust_loss",
        "primary-blocked": "landing_hazard",
    }
    for event in events:
        assert event["elapsed_s"] == pytest.approx(5.0)
        assert event["payload"]["kind"] == event["kind"]
        assert event["source_kind"] == "synthetic"
        assert isinstance(event["summary"], str) and event["summary"]
    elapsed = [(e["elapsed_s"], e["sequence"]) for e in events]
    assert elapsed == sorted(elapsed)


def test_event_summaries_state_the_applied_quantity(combined):
    summary = {e["event_id"]: e["summary"] for e in combined["events"]}
    assert "3" in summary["fuel-loss"] and "kg" in summary["fuel-loss"]
    assert "3000" in summary["thrust-loss"] and "N" in summary["thrust-loss"]
    assert "-5" in summary["primary-blocked"] and "5" in summary["primary-blocked"]


def test_initial_uncertainty_is_exposed(combined):
    spec = raw("combined_recovery")["uncertainty"]
    assert combined["uncertainty"]["samples"] == spec["samples"]
    assert combined["uncertainty"]["source"]["kind"] == spec["source"]["kind"]


# ----------------------------------------------------------- branch outcomes


def test_original_branch_matches_fixed_target_replay(combined):
    scenario = RecoveryScenario.model_validate(raw("combined_recovery"))
    reference = replay_recovery(scenario, replan=False)
    original = branch(combined, "original")
    assert original["mode"] == "fixed_original_target"
    assert original["status"] == reference["status"]
    assert original["status"] != "landed"
    assert original["final_reasons"] == reference["final_reasons"]
    assert "landing_hazard" in original["final_reasons"]
    assert original["selected_target_id"] == "primary"
    assert original["decisions"] == []


def test_classical_branch_retargets_to_backup(combined):
    classical = branch(combined, "classical")
    assert classical["mode"] == "classical_replanning"
    assert classical["status"] == "landed"
    assert classical["selected_target_id"] == "backup"
    assert classical["propellant_burned_kg"] == pytest.approx(17.333022, abs=1e-5)
    assert classical["propellant_lost_kg"] == pytest.approx(3.0)
    assert classical["propellant_remaining_kg"] == pytest.approx(79.666978, abs=1e-5)
    assert classical["reserve_margin_kg"] == pytest.approx(69.666978, abs=1e-5)
    assert classical["touchdown_downrange_m"] == pytest.approx(40.0, abs=5.0)
    assert classical["final_elapsed_s"] == pytest.approx(30.0, abs=0.01)


def test_fuel_accounting_closes_for_every_branch(combined, hybrid):
    for view in (combined, hybrid):
        for b in view["branches"]:
            assert b["propellant_burned_kg"] + b["propellant_lost_kg"] + b[
                "propellant_remaining_kg"
            ] == pytest.approx(view["initial_propellant_kg"], abs=1e-6)
            assert b["reserve_margin_kg"] == pytest.approx(
                b["propellant_remaining_kg"] - view["reserve_kg"]
            )


def test_delay_is_relative_to_the_nominal_deadline(combined):
    for b in combined["branches"]:
        assert b["delay_vs_nominal_s"] == pytest.approx(b["final_elapsed_s"] - 30.0)


def test_applied_and_unapplied_events_partition_declared_events(combined):
    ids = {e["event_id"] for e in combined["events"]}
    for b in combined["branches"]:
        assert set(b["applied_event_ids"]) | set(b["unapplied_event_ids"]) == ids
        assert not set(b["applied_event_ids"]) & set(b["unapplied_event_ids"])


# ----------------------------------------------------------- trajectories


def test_trajectories_are_bounded_monotone_and_end_at_final_state(combined, hybrid):
    for view in (combined, hybrid):
        for b in view["branches"]:
            points = b["trajectory"]
            assert 2 <= len(points) <= MAX_TRAJECTORY_POINTS
            times = [p["elapsed_s"] for p in points]
            assert times == sorted(times)
            assert times[0] == pytest.approx(0.0)
            assert times[-1] == pytest.approx(b["final_elapsed_s"])
            assert points[-1]["downrange_m"] == pytest.approx(
                b["touchdown_downrange_m"]
            )
            for p in points:
                assert set(p) >= {"elapsed_s", "downrange_m", "altitude_m", "target_id"}


def test_trajectory_records_target_switch(combined):
    points = branch(combined, "classical")["trajectory"]
    assert points[0]["target_id"] == "primary"
    assert points[-1]["target_id"] == "backup"
    switch = next(p["elapsed_s"] for p in points if p["target_id"] == "backup")
    assert switch >= 5.0 - 1e-9


def test_original_and_classical_paths_diverge_after_fault(combined):
    original = branch(combined, "original")["trajectory"]
    classical = branch(combined, "classical")["trajectory"]
    assert abs(original[-1]["downrange_m"] - classical[-1]["downrange_m"]) > 20


# ----------------------------------------------------------- decisions


def test_classical_decisions_explain_candidates(combined):
    decisions = branch(combined, "classical")["decisions"]
    assert len(decisions) == 2
    first, last = decisions
    assert first["action"] == "continue" and first["elapsed_s"] == pytest.approx(0.0)
    assert last["action"] == "retarget"
    assert last["previous_target_id"] == "primary"
    assert last["selected_target_id"] == "backup"
    assert last["elapsed_s"] == pytest.approx(5.0)
    assert last["revision"] > first["revision"]
    assert last["active_hazards"] == [[-5.0, 5.0]]
    candidates = {c["target_id"]: c for c in last["candidates"]}
    assert candidates["primary"]["accepted"] is False
    assert "landing_hazard" in candidates["primary"]["reasons"]
    assert candidates["backup"]["accepted"] is True
    assert candidates["backup"]["reasons"] == []
    explanation = last["explanation"]
    assert "primary" in explanation and "backup" in explanation
    assert "landing_hazard" in explanation


def test_decision_index_and_input_hash_follow_h5(combined):
    scenario = RecoveryScenario.model_validate(raw("combined_recovery"))
    reference = replay_recovery(scenario)["decisions"]
    decisions = branch(combined, "classical")["decisions"]
    assert [d["index"] for d in decisions] == list(range(len(reference)))
    assert [d["input_sha256"] for d in decisions] == [
        d["input_sha256"] for d in reference
    ]


def test_candidate_uncertainty_reports_sample_dispersion(combined):
    last = branch(combined, "classical")["decisions"][-1]
    backup = next(c for c in last["candidates"] if c["target_id"] == "backup")
    samples = raw("combined_recovery")["uncertainty"]["samples"]
    assert backup["sample_count"] == samples
    assert backup["pass_count"] == samples
    dispersion = backup["dispersion"]
    assert dispersion["count"] == samples
    assert (
        dispersion["min_downrange_m"]
        <= dispersion["mean_downrange_m"]
        <= dispersion["max_downrange_m"]
    )
    assert dispersion["std_downrange_m"] >= 0
    assert dispersion["min_propellant_kg"] <= dispersion["max_propellant_kg"]
    assert backup["predicted_propellant_kg"] > 0


def test_screened_out_candidate_has_no_dispersion(combined):
    last = branch(combined, "classical")["decisions"][-1]
    primary = next(c for c in last["candidates"] if c["target_id"] == "primary")
    assert primary["sample_count"] == 0
    assert primary["dispersion"] is None
    assert primary["predicted_propellant_kg"] is None


def test_classical_decisions_report_nonnegative_wall_time(combined):
    for decision in branch(combined, "classical")["decisions"]:
        assert decision["planning_wall_s"] >= 0
        assert decision["hybrid"] is None
    assert branch(combined, "classical")["replay_wall_s"] >= 0


# ----------------------------------------------------------- unrecoverable


def test_engine_out_reports_no_feasible_continuation(engine_out):
    classical = branch(engine_out, "classical")
    assert classical["status"] == "infeasible"
    assert classical["selected_target_id"] is None
    last = classical["decisions"][-1]
    assert last["action"] == "no_feasible_continuation"
    assert all(not c["accepted"] for c in last["candidates"])
    assert "no validated" in last["explanation"].lower()
    assert branch(engine_out, "original")["status"] != "landed"
    assert any("safe abort" in note for note in engine_out["limitations"])


# ----------------------------------------------------------- hybrid


def test_hybrid_branch_matches_h6_replay(hybrid):
    request = hybrid_request()
    h = request.hybrid
    reference = replay_hybrid(
        request.recovery,
        h.campaign,
        h.landing_mission_id,
        h.bindings,
        h.dependencies,
        QuantumBudget(**FAST_BUDGET),
    )
    assert [b["branch"] for b in hybrid["branches"]] == [
        "original",
        "classical",
        "hybrid",
    ]
    hb = branch(hybrid, "hybrid")
    assert hb["mode"] == "hybrid_replanning"
    assert hb["status"] == reference["replay"]["status"] == "landed"
    assert hb["selected_target_id"] == "remote"
    assert hb["propellant_remaining_kg"] == pytest.approx(
        reference["replay"]["final_state"]["propellant_kg"]
    )
    assert hb["propellant_remaining_kg"] == pytest.approx(76.814439, abs=1e-5)
    assert branch(hybrid, "classical")["selected_target_id"] == "backup"


def test_hybrid_decisions_report_source_schedule_and_timing(hybrid):
    decisions = branch(hybrid, "hybrid")["decisions"]
    assert decisions
    last = decisions[-1]
    assert last["action"] == "retarget"
    info = last["hybrid"]
    assert info["source"] in {"quantum", "classical_fallback", "classical"}
    assert set(info["selected_option_ids"]) == {"land-remote", "follow-late"}
    for key in ("preparation_s", "revalidation_s", "elapsed_since_submission_s"):
        assert info[key] >= 0
    if info["source"] == "quantum":
        assert info["quantum_elapsed_s"] >= 0
    assert info["deadline_s"] == FAST_BUDGET["deadline_s"]
    assert last["planning_wall_s"] >= 0
    assert "remote" in last["explanation"]


def test_hybrid_section_is_explicit_about_execution(hybrid):
    section = hybrid["hybrid"]
    assert section["execution_mode"] == "noiseless_simulator"
    assert section["hardware_jobs"] == 0
    assert section["simulated_clock_paused_during_planning"] is True
    assert section["landing_mission_id"] == raw("hybrid_campaign")["landing_mission_id"]
    assert (
        section["final_selected_option_ids"]
        == branch(hybrid, "hybrid")["decisions"][-1]["hybrid"]["selected_option_ids"]
    )


def test_unsupported_quantum_budget_keeps_classical_answer():
    view = build_replay_view(hybrid_request(max_qubits=1))
    hb = branch(view, "hybrid")
    assert hb["status"] == "landed"
    assert all(d["hybrid"]["source"] != "quantum" for d in hb["decisions"])
    assert set(hb["decisions"][-1]["hybrid"]["selected_option_ids"]) == {
        "land-remote",
        "follow-late",
    }


# ----------------------------------------------------------- determinism


def test_identical_inputs_give_identical_evidence_apart_from_timing():
    def strip(view):
        view = json.loads(json.dumps(view))
        for b in view["branches"]:
            b.pop("replay_wall_s")
            for d in b["decisions"]:
                d.pop("planning_wall_s")
        return view

    a = build_replay_view(classical_request())
    b = build_replay_view(classical_request())
    assert strip(a) == strip(b)


def test_view_is_json_serializable(combined, hybrid):
    json.dumps(combined, allow_nan=False)
    json.dumps(hybrid, allow_nan=False)


def test_limitations_are_stated(combined):
    text = " ".join(combined["limitations"]).lower()
    assert "synthetic" in text
    assert "planar" in text
    assert "not" in text and "probabilit" in text


def test_h5_default_output_is_unchanged_by_timing_hook():
    scenario = RecoveryScenario.model_validate(raw("combined_recovery"))
    timings = []
    with_hook = replay_recovery(scenario, decision_timings=timings)
    assert with_hook == replay_recovery(scenario)
    assert len(timings) == len(with_hook["decisions"])
    assert all(t >= 0 for t in timings)


# ----------------------------------------------------------- HTTP


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as c:
        yield c


def test_example_routes(client):
    listing = client.get("/recovery/examples")
    assert listing.status_code == 200
    rows = {row["name"]: row for row in listing.json()}
    assert set(rows) == {"combined_recovery", "engine_out", "hybrid_campaign"}
    assert rows["hybrid_campaign"]["hybrid"] is True
    assert rows["engine_out"]["hybrid"] is False
    body = client.get("/recovery/examples/combined_recovery")
    assert body.status_code == 200
    assert body.json() == example_requests()["combined_recovery"]
    assert client.get("/recovery/examples/missing").status_code == 404


def test_replay_route_returns_view(client):
    body = example_requests()["combined_recovery"]
    response = client.post("/recovery/replays", json=body)
    assert response.status_code == 200
    view = response.json()
    assert view["view_version"] == VIEW_VERSION
    assert branch(view, "classical")["selected_target_id"] == "backup"


def test_replay_route_rejects_invalid_input(client):
    body = json.loads(json.dumps(example_requests()["combined_recovery"]))
    body["recovery"]["events"][0]["epoch_utc"] = "2030-01-01T00:00:05"
    assert client.post("/recovery/replays", json=body).status_code == 422
    body = json.loads(json.dumps(example_requests()["combined_recovery"]))
    body["recovery"]["nominal_target_id"] = "nowhere"
    assert client.post("/recovery/replays", json=body).status_code == 422
    body = json.loads(json.dumps(example_requests()["combined_recovery"]))
    body["unexpected"] = 1
    assert client.post("/recovery/replays", json=body).status_code == 422
