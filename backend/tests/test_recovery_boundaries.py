"""Additional H5 replay boundary evidence, frozen before refinements."""

import pytest

from app.self_healing import RecoveryScenario, compare_recovery, replay_recovery
from app.terminal_descent import TerminalRequest, propagate_terminal
from tests.test_self_healing import data, event, hazard, scenario


def test_combined_faults_preserve_a_reachable_backup():
    loss = event("propellant_loss", amount_kg=3.0)
    thrust = event("thrust_loss", available_thrust_n=3000.0)
    thrust["sequence"] = 1
    obstacle = hazard()
    obstacle["sequence"] = 2
    run = replay_recovery(scenario(loss, thrust, obstacle))
    assert run["status"] == "landed"
    assert run["selected_target_id"] == "backup"
    assert run["final_state"]["propellant_kg"] >= 10
    assert run["final_state"]["revision"] == 3


def test_outage_is_half_open_and_retarget_is_allowed_at_its_end():
    outage = event("communication_outage", end_utc="2030-01-01T00:00:10Z")
    obstacle = hazard()
    obstacle["epoch_utc"] = "2030-01-01T00:00:10Z"
    run = replay_recovery(scenario(outage, obstacle))
    assert run["status"] == "landed"
    assert run["selected_target_id"] == "backup"


def test_scenario_cannot_increase_thrust_after_degradation():
    a = event("thrust_loss", available_thrust_n=3000.0)
    b = event("thrust_loss", available_thrust_n=4000.0)
    b.update(event_id="second", sequence=1)
    with pytest.raises(ValueError, match="increase"):
        scenario(a, b)


def test_hazard_verdict_is_consistent_in_terminal_summary():
    run = compare_recovery(scenario(hazard()))["baseline"]
    assert run["status"] == "failed"
    assert not run["terminal_summary"]["feasible"]
    assert "landing_hazard" in run["terminal_summary"]["reasons"]


def test_controls_and_pause_are_included_in_result_identity():
    job = TerminalRequest.model_validate(data()["initial_request"])
    plain = propagate_terminal(job)
    biased = propagate_terminal(job, position_bias_m=(1.0, 0.0))
    capped = propagate_terminal(job, thrust_limit_n=3000.0)
    paused = propagate_terminal(job, stop_after_s=5.0)
    assert len({r["request_sha256"] for r in (plain, biased, capped, paused)}) == 4


def test_non_grid_fault_time_is_replayed_without_rounding_to_a_sample():
    fault = event("propellant_loss", amount_kg=1.0)
    fault["epoch_utc"] = "2030-01-01T00:00:05.037Z"
    run = replay_recovery(scenario(fault))
    assert run["event_log"][0]["before"]["epoch_utc"] == "2030-01-01T00:00:05.037000Z"
    assert run["event_log"][0]["after"]["propellant_kg"] == pytest.approx(
        run["event_log"][0]["before"]["propellant_kg"] - 1
    )


def test_late_fault_does_not_extend_existing_deadlines():
    fault = hazard()
    fault["epoch_utc"] = "2030-01-01T00:00:27Z"
    run = replay_recovery(scenario(fault))
    assert run["status"] == "infeasible"
    assert all(
        "insufficient_time" in c["reasons"] for c in run["decisions"][-1]["candidates"]
    )


def test_unbounded_horizon_is_rejected():
    raw = data()
    raw["targets"][1]["touchdown_deadline_utc"] = "2030-01-01T00:10:00Z"
    with pytest.raises(ValueError):
        RecoveryScenario.model_validate(raw)
