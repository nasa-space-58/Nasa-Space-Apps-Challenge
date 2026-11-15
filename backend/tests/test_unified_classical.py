"""Classical pilot acceptance, physical accounting and immutable execution boundaries."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.campaign_planning import validate_campaign_plan
from app.unified_classical import (
    CompletedMission,
    LandingExclusion,
    accept_classical,
    apply_landing_exclusion,
    plan_classical,
)
from app.unified_planning import AdapterError, UnifiedPlanningEnvelope, seal_inputs

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def raw():
    return json.loads(
        (ROOT / "shared/examples/unified/classical-pilot.json").read_text()
    )


@pytest.fixture
def envelope(raw):
    return UnifiedPlanningEnvelope.model_validate(raw)


@pytest.fixture
def fault():
    return LandingExclusion(
        event_id="obstacle-1",
        epoch_utc=NOW + timedelta(seconds=8),
        start_downrange_m=-5.0,
        end_downrange_m=5.0,
    )


def run(envelope, now=NOW, completed=()):
    snap = seal_inputs(envelope)
    decision = plan_classical(snap, current=envelope, now_utc=now, completed=completed)
    return snap, decision


def test_nominal_complete_three_mission_plan(envelope):
    snap, result = run(envelope)
    assert result.status == "feasible"
    assert result.selected_target_id == "primary"
    assert result.optimum_proven_over_screened_targets
    assert result.execution_mode == "classical"
    selected = accept_classical(result, snap, current=envelope, now_utc=NOW)
    assert set(selected.plan.selections) == {"landing", "rover", "other"}
    assert all(selected.plan.selections.values())
    assert selected.validation.status == "feasible"
    assert validate_campaign_plan(selected.plan, selected.problem).status == "feasible"
    assert selected.physical["accepted"]
    assert selected.physical["numerically_consistent"]
    assert (
        selected.physical["uncertainty"]["pass_count"]
        == selected.physical["uncertainty"]["sample_count"]
    )
    assert result.simulated_clock_paused


def test_actual_arrival_fuel_and_unchanged_constraints(envelope):
    snap, result = run(envelope)
    selected = accept_classical(result, snap, current=envelope, now_utc=NOW)
    physical = selected.physical["fine"]
    mission = next(m for m in selected.problem.missions if m.mission_id == "landing")
    assert mission.landing_window.start_utc == datetime.fromisoformat(
        physical["state"]["epoch_utc"]
    )
    assert (
        mission.landing_window.start_utc
        != envelope.campaign.missions[0].landing_window.start_utc
    )
    assert selected.problem.stations == envelope.campaign.stations
    assert selected.problem.link_rates == envelope.campaign.link_rates
    assert selected.problem.relay_geometry == envelope.campaign.relay_geometry
    assert selected.problem.preferences == envelope.campaign.preferences
    for before in envelope.campaign.missions:
        after = next(
            m for m in selected.problem.missions if m.mission_id == before.mission_id
        )
        if before.mission_id != "landing":
            assert after == before
        assert after.requirements == before.requirements
        assert after.dependencies == before.dependencies
        assert after.policy == before.policy
    assert physical["propellant_used_kg"] + physical["state"][
        "propellant_kg"
    ] == pytest.approx(100.0, abs=1e-7)
    used = next(
        d.propellant_used_kg
        for s in selected.problem.sites
        if s.site.site_id == "land-primary"
        for d in s.descents
        if d.vehicle_id == "demo-lander"
    )
    assert used == pytest.approx(physical["propellant_used_kg"], abs=1e-7)


def test_fault_replans_target_and_dependent_rover(envelope, fault):
    old_snap, original = run(envelope)
    updated = apply_landing_exclusion(old_snap, fault)
    new_snap, replanned = run(updated, fault.epoch_utc)
    assert updated.context.revision == envelope.context.revision + 1
    assert updated.recovery.checkpoint.estimated_state.epoch_utc == fault.epoch_utc
    assert (
        updated.recovery.checkpoint.estimated_state.revision
        > envelope.recovery.checkpoint.estimated_state.revision
    )
    assert replanned.status == "feasible"
    assert replanned.selected_target_id != "primary"
    blocked = next(c for c in replanned.candidates if c.target_id == "primary")
    assert blocked.status == "rejected"
    assert "landing_hazard" in blocked.reasons
    chosen = accept_classical(
        replanned, new_snap, current=updated, now_utc=fault.epoch_utc
    )
    initial = accept_classical(original, old_snap, current=envelope, now_utc=NOW)
    if chosen.target_id == "remote":
        assert chosen.plan.selections["rover"] != initial.plan.selections["rover"]
    assert validate_campaign_plan(chosen.plan, chosen.problem).status == "feasible"
    initial_fuel = (
        envelope.recovery.recovery.initial_request.initial_state.propellant_kg
    )
    checkpoint_fuel = updated.recovery.checkpoint.estimated_state.propellant_kg
    assert checkpoint_fuel < initial_fuel
    final_fuel = chosen.physical["fine"]["state"]["propellant_kg"]
    descent = next(
        d
        for s in chosen.problem.sites
        if s.site.site_id == "land-" + chosen.target_id
        for d in s.descents
        if d.vehicle_id == "demo-lander"
    )
    assert descent.propellant_used_kg + final_fuel == pytest.approx(
        initial_fuel, abs=1e-7
    )
    with pytest.raises(AdapterError, match="stale_inputs"):
        accept_classical(original, old_snap, current=updated, now_utc=fault.epoch_utc)


def test_no_physical_continuation_is_not_a_safe_abort(envelope, fault):
    all_blocked = fault.model_copy(
        update={"start_downrange_m": -50000.0, "end_downrange_m": 50000.0}
    )
    updated = apply_landing_exclusion(seal_inputs(envelope), all_blocked)
    snap, result = run(updated, fault.epoch_utc)
    assert result.status == "no_feasible_continuation"
    assert result.selected_target_id is None
    assert all(c.plan is None for c in result.candidates)
    with pytest.raises(AdapterError, match="no_accepted_continuation"):
        accept_classical(result, snap, current=updated, now_utc=fault.epoch_utc)


def test_full_campaign_infeasibility_not_hidden_by_physical_success(raw):
    raw["campaign"]["missions"][1]["requirements"]["downlink_bits"] = 1e12
    env = UnifiedPlanningEnvelope.model_validate(raw)
    _, result = run(env)
    assert result.status == "no_feasible_continuation"
    assert result.selected_target_id is None
    assert any(c.physical["accepted"] for c in result.candidates)
    assert all(c.plan is None for c in result.candidates)


def test_reserve_failure_rejects_continuation(raw):
    raw["recovery"]["recovery"]["initial_request"]["reserve_kg"] = 99.0
    _, result = run(UnifiedPlanningEnvelope.model_validate(raw))
    assert result.status == "no_feasible_continuation"


def test_repeat_choices_and_evidence_are_deterministic(envelope):
    _, a = run(envelope)
    _, b = run(envelope)
    assert a.selected_target_id == b.selected_target_id
    assert [c.physical for c in a.candidates] == [c.physical for c in b.candidates]
    assert [c.plan for c in a.candidates] == [c.plan for c in b.candidates]


@pytest.mark.parametrize("tamper", ["selection", "allocation", "physical", "hash"])
def test_tampered_decision_is_rejected(envelope, tamper):
    snap, result = run(envelope)
    raw = result.model_dump(mode="python")
    if tamper == "selection":
        raw["selected_target_id"] = "missing"
    elif tamper == "hash":
        raw["decision_sha256"] = "0" * 64
    else:
        selected = next(
            c for c in raw["candidates"] if c["target_id"] == result.selected_target_id
        )
        if tamper == "allocation":
            selected["plan"]["allocations"] = ()
        else:
            selected["physical"]["fine"]["state"]["propellant_kg"] += 1.0
    changed = type(result).model_validate(raw)
    with pytest.raises(AdapterError, match="decision_tampered"):
        accept_classical(changed, snap, current=envelope, now_utc=NOW)


def test_acceptance_repeats_physics(envelope, monkeypatch):
    import app.unified_classical as module

    snap, result = run(envelope)
    original = module.prepare_problem
    calls = []

    def observe(job):
        calls.append(job)
        return original(job)

    monkeypatch.setattr(module, "prepare_problem", observe)
    accept_classical(result, snap, current=envelope, now_utc=NOW)
    assert calls


def test_expired_decision_cannot_apply(envelope):
    snap, result = run(envelope)
    with pytest.raises(AdapterError, match="outside_decision_window"):
        accept_classical(
            result, snap, current=envelope, now_utc=NOW + timedelta(seconds=10)
        )


@pytest.mark.parametrize("limit", [0.0, -1.0, float("nan"), float("inf"), True, 31.0])
def test_invalid_solver_limits(envelope, limit):
    with pytest.raises(ValueError):
        plan_classical(
            seal_inputs(envelope), current=envelope, now_utc=NOW, time_limit_s=limit
        )


def test_solver_limit_is_not_proven_infeasibility(envelope, monkeypatch):
    import app.unified_classical as module
    from app.campaign_planning import SolveOutcome

    monkeypatch.setattr(
        module,
        "solve_joint",
        lambda *a, **k: SolveOutcome(
            status="limit_reached",
            plan=None,
            validation=None,
            variables=1,
            constraints=1,
            elapsed_s=0.1,
        ),
    )
    _, result = run(envelope)
    assert result.status == "limit_reached"
    assert result.selected_target_id is None
    assert not result.optimum_proven_over_screened_targets


def test_current_completed_ledger_must_match(envelope):
    snap, result = run(envelope)
    bogus = CompletedMission(mission_id="other", option_id="missing")
    with pytest.raises(ValueError):
        accept_classical(
            result, snap, current=envelope, now_utc=NOW, completed=(bogus,)
        )


@pytest.mark.parametrize(
    "entry",
    [
        CompletedMission(mission_id="missing", option_id="missing"),
        CompletedMission(
            mission_id="landing", option_id="landing.land-primary.20300101T000020Z"
        ),
    ],
)
def test_unknown_or_unfinished_committed_actions_reject(envelope, entry):
    with pytest.raises(ValueError):
        run(envelope, completed=(entry,))


def test_completed_past_action_is_preserved(raw, fault):
    mission = deepcopy(raw["campaign"]["missions"][1])
    mission.update(
        mission_id="done",
        vehicle_id="done-vehicle",
        dependencies=[],
        landing_window={
            "start_utc": "2030-01-01T00:00:00Z",
            "end_utc": "2030-01-01T00:00:01Z",
        },
    )
    mission["requirements"] = {
        "surface_duration_s": 1.0,
        "energy_j": 0.0,
        "downlink_bits": 0.0,
    }
    raw["campaign"]["missions"].append(mission)
    vehicle = deepcopy(raw["campaign"]["vehicles"][0])
    vehicle["vehicle_id"] = "done-vehicle"
    raw["campaign"]["vehicles"].append(vehicle)
    for site in raw["campaign"]["sites"]:
        descent = deepcopy(site["descents"][0])
        descent["vehicle_id"] = "done-vehicle"
        site["descents"].append(descent)
    env = UnifiedPlanningEnvelope.model_validate(raw)
    updated = apply_landing_exclusion(seal_inputs(env), fault)
    done = CompletedMission(
        mission_id="done", option_id="done.land-primary.20300101T000000Z"
    )
    snap, decision = run(updated, fault.epoch_utc, (done,))
    chosen = accept_classical(
        decision, snap, current=updated, now_utc=fault.epoch_utc, completed=(done,)
    )
    assert chosen.plan.selections["done"] == done.option_id
    assert not [a for a in chosen.plan.allocations if a.mission_id == "done"]
    _, without_ledger = run(updated, fault.epoch_utc)
    assert without_ledger.status == "no_feasible_continuation"


@pytest.mark.parametrize(
    "change", ["reverse", "expired", "past", "uncertainty", "future_fault"]
)
def test_unsupported_event_inputs_reject(envelope, fault, change):
    if change == "reverse":
        fault = fault.model_copy(
            update={"start_downrange_m": 10.0, "end_downrange_m": 0.0}
        )
    elif change == "expired":
        fault = fault.model_copy(update={"epoch_utc": NOW + timedelta(seconds=10)})
    elif change == "past":
        fault = fault.model_copy(update={"epoch_utc": NOW - timedelta(seconds=1)})
    elif change == "uncertainty":
        raw = envelope.model_dump(mode="python")
        raw["recovery"]["checkpoint"]["uncertainty"]["position_std_m"] = (1.0, 0.0)
        envelope = UnifiedPlanningEnvelope.model_validate(raw)
    else:
        legacy = json.loads(
            (ROOT / "shared/examples/recovery/hybrid_campaign.json").read_text()
        )
        raw = envelope.model_dump(mode="python")
        raw["recovery"]["recovery"]["events"] = legacy["recovery"]["events"]
        envelope = UnifiedPlanningEnvelope.model_validate(raw)
    with pytest.raises(ValueError):
        apply_landing_exclusion(seal_inputs(envelope), fault)


def test_no_quantum_solver_is_called(envelope, monkeypatch):
    import app.hybrid_recovery as h6

    def forbidden(*args, **kwargs):
        raise AssertionError("classical stage must not invoke QAOA")

    monkeypatch.setattr(h6, "run_qaoa", forbidden)
    _, result = run(envelope)
    assert result.status == "feasible"
