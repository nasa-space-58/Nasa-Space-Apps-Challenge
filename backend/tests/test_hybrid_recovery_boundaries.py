"""H6 history integrity, cancelled jobs and malformed quantum metadata."""

from concurrent.futures import Future
from dataclasses import replace

from app import self_healing
from app.hybrid_quantum import run_qaoa
from app.hybrid_recovery import QuantumBudget, RecoveryCoordinator
from tests.test_hybrid_recovery import job


def test_h5_historical_decision_is_not_changed_by_a_later_hazard():
    replay = self_healing.replay_recovery(job().recovery)
    initial, later = replay["decisions"]
    assert initial["landing_hazards"] == []
    assert later["landing_hazards"] == [(-5.0, 5.0)]
    keys = (
        "scenario_sha256",
        "revision",
        "estimated_state",
        "available_thrust_n",
        "landing_hazards",
        "outage_end_utc",
        "uncertainty",
        "previous_target_id",
    )
    assert initial["input_sha256"] == self_healing._digest(
        {k: initial[k] for k in keys}
    )


def test_cancelled_job_returns_a_current_validated_fallback():
    coordinator = RecoveryCoordinator()
    request = job()
    try:
        ticket = coordinator.submit(
            request, QuantumBudget(evaluations=1, shots=32, deadline_s=10.0)
        )
        ticket.future.result(timeout=5)
        cancelled = Future()
        cancelled.cancel()
        ticket = replace(ticket, future=cancelled)
        result = coordinator.wait(ticket, request)
        assert result["reason"] == "cancelled"
        assert result["source"] == "classical_fallback"
        assert result["validation"]["status"] == "feasible"
    finally:
        coordinator.close()


def test_impossible_qubit_metadata_is_rejected():
    def corrupt(scenario, **kwargs):
        return run_qaoa(scenario, **kwargs).model_copy(update={"logical_qubits": 0})

    coordinator = RecoveryCoordinator(runner=corrupt)
    request = job()
    try:
        result = coordinator.wait(
            coordinator.submit(request, QuantumBudget(evaluations=1, shots=32)), request
        )
        assert result["source"] == "classical_fallback"
        assert result["reason"] == "invalid_quantum"
    finally:
        coordinator.close()
