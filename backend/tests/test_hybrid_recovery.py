"""Frozen H6 integration, asynchronous deadline and stale-result acceptance tests."""

from copy import deepcopy
from datetime import timedelta
from functools import lru_cache
import json
from pathlib import Path
from threading import Event

import pytest

from app.hybrid_planning import solve_exact, validate_plan
from app.hybrid_quantum import run_qaoa
from app.hybrid_recovery import (
    HybridRecoveryRequest,
    QuantumBudget,
    RecoveryCoordinator,
    checkpoint_from_decision,
    prepare_problem,
    replay_hybrid,
)
from app.self_healing import RecoveryScenario, replay_recovery
from shared.hybrid import Plan, Scenario

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def fixture_raw(after_fault=True):
    recovery = json.loads(
        (ROOT / "shared/examples/recovery/combined_recovery.json").read_text()
    )
    recovery["uncertainty"]["samples"] = 2
    recovery["targets"].append(
        {
            "target_id": "remote",
            "downrange_m": 80.0,
            "touchdown_deadline_utc": "2030-01-01T00:00:35Z",
        }
    )
    source = {
        "source_id": "h6-test",
        "kind": "synthetic",
        "description": "Declared sites and campaign slots; not a planar-to-MOON_ME conversion.",
    }

    def window(start, end):
        return {
            "start_utc": f"2030-01-01T00:{start // 60:02d}:{start % 60:02d}Z",
            "end_utc": f"2030-01-01T00:{end // 60:02d}:{end % 60:02d}Z",
        }

    def candidate(oid, mission, start, end, utility, lon):
        return {
            "option_id": oid,
            "mission_id": mission,
            "site": {"site_id": oid, "latitude_deg": -86.0, "longitude_deg_east": lon},
            "window": window(start, end),
            "metrics": {"utility": float(utility)},
            "demands": [
                {
                    "resource_id": "antenna",
                    "amount": 1.0,
                    "unit": "channel",
                    "window": window(start, end),
                }
            ],
            "source_ids": ["h6-test"],
        }

    campaign = {
        "scenario_id": "h6-campaign",
        "revision": 0,
        "description": "Synthetic two-mission campaign",
        "seed": 17,
        "window": window(0, 120),
        "sources": [source],
        "missions": [
            {
                "mission_id": "landing",
                "vehicle_id": "demo-lander",
                "deadline_utc": window(0, 120)["end_utc"],
                "source_ids": ["h6-test"],
            },
            {
                "mission_id": "follow",
                "vehicle_id": "rover",
                "deadline_utc": window(0, 120)["end_utc"],
                "source_ids": ["h6-test"],
            },
        ],
        "resources": [
            {
                "resource_id": "antenna",
                "mode": "concurrent",
                "unit": "channel",
                "capacity": 1.0,
                "availability": [window(0, 120)],
                "source_ids": ["h6-test"],
            }
        ],
        "objectives": [
            {
                "metric": "utility",
                "unit": "score",
                "direction": "maximize",
                "weight": 1.0,
                "scale": 1.0,
            }
        ],
        "candidates": [
            candidate("land-primary", "landing", 30, 32, 200, 0.0),
            candidate("land-backup", "landing", 30, 32, 10, 1.0),
            candidate("land-remote", "landing", 35, 37, 100, 2.0),
            candidate("follow-early", "follow", 33, 34, 40, 3.0),
            candidate("follow-late", "follow", 40, 41, 10, 3.0),
        ],
    }
    r = RecoveryScenario.model_validate(recovery)
    decisions = replay_recovery(r)["decisions"]
    decision = decisions[-1] if after_fault else decisions[0]
    checkpoint = {
        key: decision[key]
        for key in (
            "estimated_state",
            "available_thrust_n",
            "landing_hazards",
            "outage_end_utc",
            "uncertainty",
            "previous_target_id",
        )
    }
    return {
        "recovery": recovery,
        "checkpoint": checkpoint,
        "campaign": campaign,
        "landing_mission_id": "landing",
        "bindings": [
            {"target_id": t, "option_id": "land-" + t}
            for t in ("primary", "backup", "remote")
        ],
        "dependencies": [
            {"follower_option_id": "follow-early"},
            {"follower_option_id": "follow-late"},
        ],
    }


def job(after_fault=True):
    return HybridRecoveryRequest.model_validate(deepcopy(fixture_raw(after_fault)))


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def workers():
    coordinators, gates = [], []

    def factory(**kwargs):
        coord = RecoveryCoordinator(**kwargs)
        coordinators.append(coord)
        return coord

    yield factory, gates
    for gate in gates:
        gate.set()
    for coord in coordinators:
        coord.close()


def test_build_filters_failed_targets_and_keeps_all_following_options():
    request = job()
    prepared = prepare_problem(request)
    ids = {c.option_id for c in prepared["scenario"].candidates}
    assert ids == {"land-backup", "land-remote", "follow-early", "follow-late"}
    assert not next(
        c for c in prepared["physical_candidates"] if c["target_id"] == "primary"
    )["accepted"]
    assert prepared["request_sha256"]
    assert request.campaign.model_dump() == job().campaign.model_dump()


def test_binding_keeps_explicit_sites_and_moves_services_with_touchdown():
    prepared = prepare_problem(job())
    remote = next(
        c for c in prepared["scenario"].candidates if c.option_id == "land-remote"
    )
    assert remote.site.longitude_deg_east == 2
    assert remote.window.start_utc.second == 34
    assert remote.window.end_utc - remote.window.start_utc == timedelta(seconds=2)
    assert remote.demands[0].window == remote.window
    assert "recovery_propellant_kg" in remote.metrics


def test_landing_dependency_changes_downstream_schedule():
    prepared = prepare_problem(job())
    exact = solve_exact(prepared["scenario"])
    assert exact.status == "optimal"
    assert set(exact.plan.selected_option_ids) == {"land-remote", "follow-late"}
    assert validate_plan(exact.plan, prepared["scenario"]).status == "feasible"
    assert any(
        set(rule.option_ids) == {"land-remote", "follow-early"}
        for rule in prepared["scenario"].incompatibilities
    )


def test_h5_selector_executes_only_physically_accepted_options():
    recovery = job().recovery

    def choose(decision):
        if decision["revision"] == 0:
            return "primary"
        return "remote"

    result = replay_recovery(recovery, target_selector=choose)
    assert result["status"] == "landed" and result["selected_target_id"] == "remote"
    assert result["final_state"]["epoch_utc"].startswith("2030-01-01T00:00:34.")
    with pytest.raises(ValueError):
        replay_recovery(recovery, target_selector=lambda d: "not-a-target")
    with pytest.raises(ValueError):
        replay_recovery(recovery, target_selector=lambda d: "primary")


def test_selector_can_report_joint_infeasibility_without_flying_a_plan():
    result = replay_recovery(job().recovery, target_selector=lambda d: None)
    assert result["status"] == "infeasible"
    assert result["selected_target_id"] is None
    assert not result["trace"]


def test_actual_qaoa_is_small_and_independently_validated(workers):
    factory, _ = workers
    coordinator = factory()
    request = job()
    budget = QuantumBudget(evaluations=32, shots=128, deadline_s=10.0)
    ticket = coordinator.submit(request, budget)
    assert ticket.fallback.plan is not None
    result = coordinator.wait(ticket, request)
    assert result["source"] == "quantum"
    assert result["reason"] == "quantum_accepted"
    assert result["quantum"]["logical_qubits"] == 2
    assert result["quantum"]["hardware_jobs"] == 0
    assert result["selected_target_id"] == "remote"
    assert set(result["plan"]["selected_option_ids"]) == {"land-remote", "follow-late"}
    assert (
        validate_plan(
            Plan.model_validate(result["plan"]),
            Scenario.model_validate(result["campaign"]),
        ).status
        == "feasible"
    )


def test_pending_worker_returns_valid_classical_fallback_without_waiting(workers):
    factory, gates = workers
    gate = Event()
    gates.append(gate)

    def slow(scenario, **kwargs):
        gate.wait(5)
        return run_qaoa(scenario, **kwargs)

    coordinator = factory(runner=slow)
    request = job()
    ticket = coordinator.submit(request, QuantumBudget(evaluations=1, shots=16))
    result = coordinator.resolve(ticket, request)
    assert result["source"] == "classical_fallback" and result["reason"] == "pending"
    assert result["selected_target_id"] == "remote"
    assert not ticket.future.done()


def test_deadline_expiry_rejects_even_a_completed_result(workers):
    factory, _ = workers
    clock = Clock()
    coordinator = factory(clock=clock)
    request = job()
    ticket = coordinator.submit(
        request, QuantumBudget(deadline_s=1.0, evaluations=1, shots=32)
    )
    ticket.future.result(timeout=5)
    clock.now += 1
    result = coordinator.resolve(ticket, request)
    assert result["reason"] == "deadline"
    assert result["source"] == "classical_fallback"


def test_state_revision_change_rejects_old_quantum_but_revalidates_fallback(workers):
    factory, _ = workers
    coordinator = factory()
    request = job()
    ticket = coordinator.submit(
        request, QuantumBudget(evaluations=1, shots=32, deadline_s=10.0)
    )
    ticket.future.result(timeout=5)
    raw = request.model_dump()
    raw["checkpoint"]["estimated_state"]["revision"] += 1
    current = HybridRecoveryRequest.model_validate(raw)
    result = coordinator.resolve(ticket, current)
    assert result["reason"] == "stale_snapshot"
    assert result["request_sha256"] != ticket.request_sha256
    assert result["source"] == "classical_fallback"


def test_resource_change_never_reuses_a_now_invalid_fallback(workers):
    factory, _ = workers
    coordinator = factory()
    request = job()
    ticket = coordinator.submit(
        request, QuantumBudget(evaluations=1, shots=32, deadline_s=10.0)
    )
    ticket.future.result(timeout=5)
    raw = request.model_dump()
    raw["campaign"]["resources"][0]["capacity"] = 0.0
    result = coordinator.resolve(ticket, HybridRecoveryRequest.model_validate(raw))
    assert result["reason"] == "stale_snapshot"
    assert result["status"] == "infeasible" and result["plan"] is None


def test_fuel_change_rechecks_physics_instead_of_using_cached_acceptance(workers):
    factory, _ = workers
    coordinator = factory()
    request = job()
    ticket = coordinator.submit(
        request, QuantumBudget(evaluations=1, shots=16, deadline_s=10.0)
    )
    raw = request.model_dump()
    raw["checkpoint"]["estimated_state"]["propellant_kg"] = 0.0
    result = coordinator.resolve(ticket, HybridRecoveryRequest.model_validate(raw))
    assert result["status"] == "infeasible" and result["selected_target_id"] is None
    assert result["reason"] == "stale_snapshot"


def test_unsupported_non_power_of_two_encoding_retains_classical_answer(workers):
    factory, _ = workers
    coordinator = factory()
    request = job(False)
    ticket = coordinator.submit(
        request, QuantumBudget(evaluations=1, shots=16, deadline_s=10.0)
    )
    result = coordinator.wait(ticket, request)
    assert result["source"] == "classical_fallback"
    assert result["reason"] == "unsupported"
    assert result["selected_target_id"] == "primary"


def test_worker_failure_has_explicit_fallback(workers):
    factory, _ = workers

    def broken(*args, **kwargs):
        raise RuntimeError("test worker failure")

    coordinator = factory(runner=broken)
    request = job()
    result = coordinator.wait(coordinator.submit(request), request)
    assert (
        result["source"] == "classical_fallback" and result["reason"] == "worker_error"
    )


def test_wrong_quantum_identity_is_rejected(workers):
    factory, _ = workers

    def wrong(scenario, **kwargs):
        return run_qaoa(scenario, **kwargs).model_copy(
            update={"input_fingerprint": "0" * 64}
        )

    coordinator = factory(runner=wrong)
    request = job()
    result = coordinator.wait(
        coordinator.submit(request, QuantumBudget(evaluations=1, shots=32)), request
    )
    assert result["reason"] == "invalid_quantum"
    assert result["source"] == "classical_fallback"


def test_unobserved_best_plan_cannot_be_accepted(workers):
    factory, _ = workers

    def wrong(scenario, **kwargs):
        result = run_qaoa(scenario, **kwargs)
        samples = tuple(s.model_copy(update={"count": 0}) for s in result.samples)
        return result.model_copy(update={"samples": samples})

    coordinator = factory(runner=wrong)
    request = job()
    result = coordinator.wait(
        coordinator.submit(request, QuantumBudget(evaluations=1, shots=32)), request
    )
    assert result["reason"] == "invalid_quantum"


def test_one_bounded_worker_does_not_queue_unlimited_jobs(workers):
    factory, gates = workers
    gate = Event()
    gates.append(gate)

    def slow(scenario, **kwargs):
        gate.wait(5)
        return run_qaoa(scenario, **kwargs)

    coordinator = factory(runner=slow)
    request = job()
    first = coordinator.submit(request, QuantumBudget(evaluations=1, shots=16))
    second = coordinator.submit(request, QuantumBudget(evaluations=1, shots=16))
    assert first.future is not None and second.future is None
    assert coordinator.resolve(second, request)["reason"] == "worker_busy"


def test_no_physical_candidate_does_not_launch_quantum(workers):
    factory, _ = workers

    def forbidden(*args, **kwargs):
        raise AssertionError("must not launch")

    coordinator = factory(runner=forbidden)
    raw = job().model_dump()
    raw["checkpoint"]["available_thrust_n"] = 0.0
    request = HybridRecoveryRequest.model_validate(raw)
    ticket = coordinator.submit(request)
    assert ticket.future is None
    result = coordinator.resolve(ticket, request)
    assert result["status"] == "infeasible" and result["plan"] is None


def test_end_to_end_replay_changes_landing_and_following_schedule():
    request = job()
    result = replay_hybrid(
        request.recovery,
        request.campaign,
        request.landing_mission_id,
        request.bindings,
        request.dependencies,
        QuantumBudget(evaluations=32, shots=128, deadline_s=10.0),
    )
    assert result["replay"]["status"] == "landed"
    assert result["replay"]["selected_target_id"] == "remote"
    assert set(result["decisions"][-1]["plan"]["selected_option_ids"]) == {
        "land-remote",
        "follow-late",
    }
    assert result["decisions"][-1]["source"] == "quantum"


@pytest.mark.parametrize(
    "field,value",
    [
        ("deadline_s", 0),
        ("max_qubits", 7),
        ("shots", 0),
        ("evaluations", 513),
        ("seed", -1),
    ],
)
def test_invalid_quantum_budgets(field, value):
    with pytest.raises(ValueError):
        QuantumBudget(**{field: value})


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_binding",
        "duplicate_option",
        "wrong_mission",
        "missing_dependency",
        "bad_hazard",
        "excess_thrust",
        "wrong_vehicle",
        "past_checkpoint",
    ],
)
def test_bad_linkages_and_checkpoint_inputs_rejected(mutation):
    raw = deepcopy(fixture_raw())
    if mutation == "missing_binding":
        raw["bindings"] = raw["bindings"][:-1]
    elif mutation == "duplicate_option":
        raw["bindings"][1]["option_id"] = raw["bindings"][0]["option_id"]
    elif mutation == "wrong_mission":
        raw["landing_mission_id"] = "follow"
    elif mutation == "missing_dependency":
        raw["dependencies"][0]["follower_option_id"] = "missing"
    elif mutation == "bad_hazard":
        raw["checkpoint"]["landing_hazards"] = [(5.0, -5.0)]
    elif mutation == "excess_thrust":
        raw["checkpoint"]["available_thrust_n"] = 10001.0
    elif mutation == "wrong_vehicle":
        raw["checkpoint"]["estimated_state"]["vehicle_id"] = "other"
    elif mutation == "past_checkpoint":
        raw["checkpoint"]["estimated_state"]["epoch_utc"] = "2029-12-31T23:59:59Z"
    with pytest.raises(ValueError):
        HybridRecoveryRequest.model_validate(raw)


def test_checkpoint_adapter_ignores_untrusted_acceptance_claims():
    r = job().recovery
    decision = replay_recovery(r)["decisions"][-1]
    for row in decision["candidates"]:
        row["accepted"] = True
    checkpoint = checkpoint_from_decision(decision)
    request = job().model_copy(update={"checkpoint": checkpoint})
    prepared = prepare_problem(request)
    assert "land-primary" not in {c.option_id for c in prepared["scenario"].candidates}


def test_valid_but_inferior_quantum_plan_cannot_replace_classical(workers):
    factory, _ = workers

    def inferior(scenario, **kwargs):
        result = run_qaoa(scenario, **kwargs)
        selected = {"land-backup", "follow-early"}
        plan = result.best_sampled_plan.model_copy(
            update={"selected_option_ids": tuple(sorted(selected))}
        )
        samples = tuple(
            s.model_copy(
                update={
                    "count": result.shots
                    if set(s.selected_option_ids) == selected
                    else 0
                }
            )
            for s in result.samples
        )
        return result.model_copy(update={"best_sampled_plan": plan, "samples": samples})

    coordinator = factory(runner=inferior)
    request = job()
    result = coordinator.wait(
        coordinator.submit(request, QuantumBudget(evaluations=1, shots=32)), request
    )
    assert result["reason"] == "inferior_quantum"
    assert result["source"] == "classical_fallback"
    assert result["selected_target_id"] == "remote"


def test_deadline_is_checked_after_fresh_physics_validation(workers, monkeypatch):
    from app import hybrid_recovery as module

    factory, _ = workers
    clock = Clock()
    coordinator = factory(clock=clock)
    request = job()
    ticket = coordinator.submit(
        request, QuantumBudget(deadline_s=1.0, evaluations=1, shots=32)
    )
    ticket.future.result(timeout=5)
    original = module.prepare_problem

    def delayed(current):
        result = original(current)
        clock.now += 2
        return result

    monkeypatch.setattr(module, "prepare_problem", delayed)
    result = coordinator.resolve(ticket, request)
    assert result["reason"] == "deadline"
    assert result["source"] == "classical_fallback"
