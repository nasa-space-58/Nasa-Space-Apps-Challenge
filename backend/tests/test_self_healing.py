"""Frozen H5 event, uncertainty and physical replay acceptance cases."""

from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from app.self_healing import RecoveryScenario, compare_recovery, replay_recovery
from app.terminal_descent import TerminalRequest, propagate_terminal, simulate_terminal

ROOT = Path(__file__).resolve().parents[2]
SOURCE = {
    "source_id": "fault-test",
    "kind": "synthetic",
    "description": "Explicit synthetic fault assumptions",
}


def data():
    job = json.loads(
        (ROOT / "shared/examples/descent/terminal_nominal.json").read_text()
    )
    return {
        "scenario_id": "recovery-test",
        "seed": 17,
        "initial_request": job,
        "nominal_target_id": "primary",
        "targets": [
            {
                "target_id": "primary",
                "downrange_m": 0.0,
                "touchdown_deadline_utc": "2030-01-01T00:00:30Z",
            },
            {
                "target_id": "backup",
                "downrange_m": 40.0,
                "touchdown_deadline_utc": "2030-01-01T00:00:30Z",
            },
        ],
        "events": [],
        "uncertainty": {"samples": 2, "source": SOURCE},
    }


def event(kind, **payload):
    return {
        "event_id": kind,
        "vehicle_id": "demo-lander",
        "epoch_utc": "2030-01-01T00:00:05Z",
        "sequence": 0,
        "payload": {"kind": kind, **payload},
        "source": SOURCE,
    }


def scenario(*events, **updates):
    raw = data()
    raw.update(updates)
    raw["events"] = list(events)
    return RecoveryScenario.model_validate(raw)


def hazard():
    return event("landing_hazard", start_downrange_m=-5.0, end_downrange_m=5.0)


def test_pause_keeps_original_guidance_deadline():
    job = TerminalRequest.model_validate(data()["initial_request"])
    full = simulate_terminal(job)
    paused = propagate_terminal(job, stop_after_s=5.0)
    assert paused["termination"] == "paused"
    assert not paused["feasible"]
    assert paused["trace"][-1]["t_s"] == pytest.approx(5.0)
    assert paused["trace"][-1]["state"]["position_m"] == pytest.approx(
        full["trace"][50]["state"]["position_m"], abs=1e-6
    )
    assert paused["trace"][-1]["state"]["propellant_kg"] == pytest.approx(
        full["trace"][50]["state"]["propellant_kg"], abs=1e-8
    )


def test_fractional_pause_lands_exactly_on_event_time():
    job = TerminalRequest.model_validate(data()["initial_request"])
    paused = propagate_terminal(job, stop_after_s=5.037)
    assert paused["trace"][-1]["t_s"] == pytest.approx(5.037, abs=1e-10)
    assert paused["trace"][-1]["state"]["epoch_utc"] == "2030-01-01T00:00:05.037000Z"


def test_zero_thrust_cap_is_ballistic_not_tiny_fake_thrust():
    job = TerminalRequest.model_validate(data()["initial_request"])
    run = propagate_terminal(job, thrust_limit_n=0.0)
    assert not run["feasible"]
    assert run["propellant_used_kg"] == 0
    assert all(row["thrust_n"] == 0 for row in run["trace"])
    assert "vertical_speed" in run["reasons"]


def test_navigation_bias_changes_control_but_not_initial_truth():
    job = TerminalRequest.model_validate(data()["initial_request"])
    run = propagate_terminal(job, position_bias_m=(20.0, 0.0))
    assert run["trace"][0]["state"] == job.initial_state.model_dump(mode="json")
    assert run["trace"][-1]["downrange_m"] < -10
    assert not run["feasible"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stop_after_s": 0},
        {"stop_after_s": 31},
        {"thrust_limit_n": -1},
        {"thrust_limit_n": 10001},
        {"position_bias_m": (float("nan"), 0)},
        {"velocity_bias_mps": (0, 0, 0)},
    ],
)
def test_invalid_propagation_controls(kwargs):
    with pytest.raises(ValueError):
        propagate_terminal(
            TerminalRequest.model_validate(data()["initial_request"]), **kwargs
        )


def test_no_fault_preserves_nominal_landing():
    result = compare_recovery(scenario())
    assert result["baseline"]["status"] == result["recovery"]["status"] == "landed"
    assert result["recovery"]["selected_target_id"] == "primary"
    assert result["recovery"]["decisions"][0]["action"] == "continue"
    assert result["baseline"]["final_state"] == result["recovery"]["final_state"]
    assert (
        result["baseline"]["scenario_sha256"] == result["recovery"]["scenario_sha256"]
    )


def test_hazard_forces_retarget_and_original_plan_fails():
    result = compare_recovery(scenario(hazard()))
    recovery, baseline = result["recovery"], result["baseline"]
    assert recovery["status"] == "landed"
    assert recovery["selected_target_id"] == "backup"
    assert recovery["decisions"][-1]["action"] == "retarget"
    assert baseline["status"] == "failed"
    assert "landing_hazard" in baseline["final_reasons"]
    assert recovery["final_state"]["propellant_kg"] >= 10
    assert abs(recovery["terminal_summary"]["downrange_m"] - 40) < 5
    assert recovery["decisions"][-1]["revision"] == 1
    assert recovery["final_state"]["revision"] == 1
    assert recovery["event_log"][0]["before"]["epoch_utc"] == "2030-01-01T00:00:05Z"


def test_no_backup_is_reported_without_claiming_safe_abort():
    raw = data()
    raw["targets"] = raw["targets"][:1]
    raw["events"] = [hazard()]
    result = replay_recovery(RecoveryScenario.model_validate(raw))
    assert result["status"] == "infeasible"
    assert result["selected_target_id"] is None
    assert result["decisions"][-1]["action"] == "no_feasible_continuation"
    assert result["final_state"]["epoch_utc"] == "2030-01-01T00:00:05Z"


def test_total_thrust_loss_is_applied_and_unrecoverable():
    result = compare_recovery(scenario(event("thrust_loss", available_thrust_n=0.0)))
    assert result["recovery"]["status"] == "infeasible"
    assert result["recovery"]["event_log"][0]["available_thrust_n"] == 0
    assert result["baseline"]["status"] == "failed"
    assert "vertical_speed" in result["baseline"]["final_reasons"]


def test_partial_thrust_loss_is_not_just_a_label():
    result = replay_recovery(scenario(event("thrust_loss", available_thrust_n=3000.0)))
    assert result["status"] == "landed"
    assert result["event_log"][0]["available_thrust_n"] == 3000
    assert all(
        row["thrust_n"] <= 3000
        for row in result["trace"]
        if row["state"]["epoch_utc"] > "2030-01-01T00:00:05Z"
    )


def test_fuel_loss_is_bounded_by_remaining_fuel_and_not_refilled():
    result = compare_recovery(scenario(event("propellant_loss", amount_kg=1000.0)))
    log = result["recovery"]["event_log"][0]
    assert log["after"]["propellant_kg"] == 0
    assert log["lost_propellant_kg"] == pytest.approx(log["before"]["propellant_kg"])
    assert result["recovery"]["status"] == "infeasible"
    assert result["baseline"]["final_state"]["propellant_kg"] == 0


def test_sensor_fault_separates_truth_observation_and_estimate():
    result = replay_recovery(
        scenario(
            event("sensor_degradation", sensor_id="nav", position_std_m=(30.0, 0.0))
        )
    )
    log = result["event_log"][0]
    assert log["before"]["position_m"] == log["after"]["position_m"]
    assert log["estimated_after"]["position_m"] != log["after"]["position_m"]
    assert log["observation"]["sensor_id"] == "nav"
    assert log["observation"]["kind"] == "synthetic_observation"
    assert log["observation"]["position_m"] == log["estimated_after"]["position_m"]
    assert result["status"] == "infeasible"
    assert any(
        "uncertainty" in c["reasons"] for c in result["decisions"][-1]["candidates"]
    )


def test_seeded_replay_is_repeatable_and_input_is_not_mutated():
    job = scenario(hazard())
    original = job.model_dump(mode="json")
    a, b = compare_recovery(job), compare_recovery(job)
    assert a == b
    assert job.model_dump(mode="json") == original
    assert len(a["recovery"]["scenario_sha256"]) == 64


def test_seed_changes_uncertainty_draws():
    spec = {"samples": 4, "position_std_m": (0.1, 0.1), "source": SOURCE}
    a = replay_recovery(scenario(uncertainty=spec, seed=1))
    b = replay_recovery(scenario(uncertainty=spec, seed=2))
    assert (
        a["decisions"][0]["uncertainty_draws"] != b["decisions"][0]["uncertainty_draws"]
    )
    assert a["scenario_sha256"] != b["scenario_sha256"]


def test_common_uncertainty_draws_and_zero_uncertainty_pass_rate():
    result = replay_recovery(scenario())
    decision = result["decisions"][0]
    assert len(decision["uncertainty_draws"]) == 2
    assert all(c["uncertainty"]["pass_count"] == 2 for c in decision["candidates"])
    assert all(c["uncertainty"]["sample_count"] == 2 for c in decision["candidates"])
    assert all(
        c["coarse_feasible"] and c["fine_feasible"] for c in decision["candidates"]
    )


def test_model_uncertainty_affects_acceptance():
    spec = {
        "samples": 8,
        "thrust_loss_fraction_bound": 1.0,
        "fuel_shortfall_bound_kg": 1000.0,
        "source": SOURCE,
    }
    result = replay_recovery(scenario(uncertainty=spec))
    assert result["status"] == "infeasible"
    assert any(
        c["uncertainty"]["pass_count"] < 8 for c in result["decisions"][0]["candidates"]
    )


def test_communication_policy_blocks_retarget_but_not_original_plan():
    outage = event("communication_outage", end_utc="2030-01-01T00:00:20Z")
    assert replay_recovery(scenario(outage))["status"] == "landed"
    blocked = hazard()
    blocked["sequence"] = 1
    result = replay_recovery(scenario(outage, blocked))
    assert result["status"] == "infeasible"
    backup = next(
        c for c in result["decisions"][-1]["candidates"] if c["target_id"] == "backup"
    )
    assert "communication_required" in backup["reasons"]
    autonomous = replay_recovery(
        scenario(outage, blocked, require_link_for_retarget=False)
    )
    assert autonomous["status"] == "landed"
    assert autonomous["selected_target_id"] == "backup"


def test_events_are_canonicalized_by_time_then_sequence():
    a = event("propellant_loss", amount_kg=1.0)
    b = hazard()
    b["sequence"] = 1
    left, right = compare_recovery(scenario(a, b)), compare_recovery(scenario(b, a))
    assert left == right
    assert [e["event_id"] for e in left["recovery"]["event_log"]] == [
        "propellant_loss",
        "landing_hazard",
    ]
    assert left["recovery"]["decisions"][-1]["revision"] == 2


def test_expired_backup_is_not_rescheduled_from_event_time():
    raw = data()
    raw["events"] = [hazard()]
    raw["targets"][1]["touchdown_deadline_utc"] = "2030-01-01T00:00:06Z"
    result = replay_recovery(RecoveryScenario.model_validate(raw))
    assert result["status"] == "infeasible"
    backup = next(
        c for c in result["decisions"][-1]["candidates"] if c["target_id"] == "backup"
    )
    assert "insufficient_time" in backup["reasons"]


def test_event_at_initial_time_applies_before_planning():
    e = hazard()
    e["epoch_utc"] = "2030-01-01T00:00:00Z"
    result = replay_recovery(scenario(e))
    assert result["status"] == "landed"
    assert result["selected_target_id"] == "backup"
    assert result["decisions"][0]["revision"] == 1


def test_event_changes_decision_identity():
    result = replay_recovery(scenario(event("propellant_loss", amount_kg=1.0)))
    assert (
        result["decisions"][0]["input_sha256"]
        != result["decisions"][-1]["input_sha256"]
    )
    assert result["decisions"][-1]["estimated_state"]["propellant_kg"] < 99


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_id",
        "duplicate_order",
        "wrong_vehicle",
        "past_event",
        "naive_time",
        "bad_hazard",
        "backwards_outage",
        "unknown_primary",
        "too_many_events",
        "zero_samples",
        "negative_sigma",
        "duplicate_target",
        "primary_mismatch",
    ],
)
def test_invalid_scenario_is_rejected(mutation):
    raw = data()
    raw["events"] = [hazard()]
    if mutation == "duplicate_id":
        copy = deepcopy(raw["events"][0])
        copy["sequence"] = 1
        raw["events"].append(copy)
    elif mutation == "duplicate_order":
        copy = event("propellant_loss", amount_kg=1.0)
        raw["events"].append(copy)
    elif mutation == "wrong_vehicle":
        raw["events"][0]["vehicle_id"] = "other"
    elif mutation == "past_event":
        raw["events"][0]["epoch_utc"] = "2029-12-31T23:59:59Z"
    elif mutation == "naive_time":
        raw["events"][0]["epoch_utc"] = "2030-01-01T00:00:05"
    elif mutation == "bad_hazard":
        raw["events"][0]["payload"]["start_downrange_m"] = 10
    elif mutation == "backwards_outage":
        raw["events"] = [event("communication_outage", end_utc="2030-01-01T00:00:04Z")]
    elif mutation == "unknown_primary":
        raw["nominal_target_id"] = "missing"
    elif mutation == "too_many_events":
        raw["events"] = raw["events"] * 9
    elif mutation == "zero_samples":
        raw["uncertainty"]["samples"] = 0
    elif mutation == "negative_sigma":
        raw["uncertainty"]["position_std_m"] = (-1.0, 0.0)
    elif mutation == "duplicate_target":
        raw["targets"].append(raw["targets"][0])
    elif mutation == "primary_mismatch":
        raw["targets"][0]["downrange_m"] = 10.0
    with pytest.raises(ValueError):
        RecoveryScenario.model_validate(raw)


def test_execution_revalidates_unchecked_updates():
    bad = scenario().model_copy(update={"nominal_target_id": "missing"})
    with pytest.raises(ValueError):
        replay_recovery(bad)


def test_later_events_are_not_applied_after_landing():
    raw = data()
    raw["targets"][1]["touchdown_deadline_utc"] = "2030-01-01T00:00:40Z"
    e = hazard()
    e["epoch_utc"] = "2030-01-01T00:00:35Z"
    raw["events"] = [e]
    result = replay_recovery(RecoveryScenario.model_validate(raw))
    assert result["status"] == "landed"
    assert not result["event_log"]
    assert result["unapplied_event_ids"] == ["landing_hazard"]


def test_replay_trace_clock_is_monotonic_and_fuel_loss_accounted():
    result = replay_recovery(
        scenario(
            event("propellant_loss", amount_kg=1.0), require_link_for_retarget=False
        )
    )
    times = [row["elapsed_s"] for row in result["trace"]]
    assert times == sorted(times)
    assert result["propellant_lost_kg"] == 1
    assert result["propellant_burned_kg"] + result["propellant_lost_kg"] + result[
        "final_state"
    ]["propellant_kg"] == pytest.approx(100)
    assert np.linalg.norm(result["final_state"]["position_m"]) > 1700000
