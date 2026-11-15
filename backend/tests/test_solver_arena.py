"""H8 acceptance: same inputs, authentic traces, bounded API, archived QPU evidence."""

from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
from math import isclose

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis_api import create_app
from app import solver_arena as arena
from app.hybrid_planning import solve_exact, validate_plan
from app.hybrid_quantum import compact_model, compile_qubo, qaoa_statevector, run_qaoa
from shared.hybrid import Plan, fingerprint


@lru_cache
def problem():
    return arena.load_arena_scenario("recovery")


def test_classical_trace_reports_actual_assignments_and_incumbent():
    history = []
    result = solve_exact(problem(), on_assignment=history.append)
    assert len(history) == result.total_combinations == 4
    assert [row["evaluated"] for row in history] == [1, 2, 3, 4]
    assert history[-1]["feasible_count"] == result.feasible_count
    assert history[-1]["best_option_ids"] == result.plan.selected_option_ids
    assert history[-1]["best_exact_score"] == result.validation.exact_score
    assert sum(row["feasible"] for row in history) == result.feasible_count
    assert all(row["elapsed_s"] >= 0 for row in history)
    assert [row["elapsed_s"] for row in history] == sorted(
        row["elapsed_s"] for row in history
    )


@pytest.mark.parametrize("limit", [1, 2, 4])
def test_classical_observer_preserves_limits(limit):
    history = []
    result = solve_exact(problem(), max_evaluations=limit, on_assignment=history.append)
    assert len(history) == result.evaluated_combinations == limit
    assert result.search_complete == (limit == 4)


def test_classical_observer_cannot_mutate_solver_state():
    baseline = solve_exact(problem())
    result = solve_exact(
        problem(),
        on_assignment=lambda row: row.update(
            best_exact_score="-999", best_option_ids=()
        ),
    )
    assert result.plan == baseline.plan
    assert result.validation == baseline.validation


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_qaoa_trace_is_actual_unitary_and_final_incumbent(depth):
    history = []
    result = run_qaoa(
        problem(),
        depth=depth,
        evaluations=12,
        shots=64,
        seed=17,
        on_iteration=history.append,
    )
    assert len(history) == 12
    energies = compact_model(compile_qubo(problem())).normalized_energies
    for i, row in enumerate(history):
        assert row["iteration"] == i + 1
        candidate = np.abs(qaoa_statevector(energies, row["gammas"], row["betas"])) ** 2
        incumbent = (
            np.abs(qaoa_statevector(energies, row["best_gammas"], row["best_betas"]))
            ** 2
        )
        np.testing.assert_allclose(row["probabilities"], candidate, atol=1e-12)
        np.testing.assert_allclose(row["best_probabilities"], incumbent, atol=1e-12)
        assert isclose(row["energy"], candidate @ energies, abs_tol=1e-12)
    assert all(
        b["best_energy"] <= a["best_energy"] for a, b in zip(history, history[1:])
    )
    assert history[-1]["best_gammas"] == result.gammas
    assert history[-1]["best_betas"] == result.betas
    np.testing.assert_allclose(
        history[-1]["best_probabilities"], [s.probability for s in result.samples]
    )
    assert sum(s.count for s in result.samples) == 64


def test_qaoa_observer_does_not_change_seeded_result():
    baseline = run_qaoa(problem(), evaluations=12, shots=64)
    observed = run_qaoa(
        problem(), evaluations=12, shots=64, on_iteration=lambda row: row.clear()
    )
    assert observed.model_dump(exclude={"elapsed_s"}) == baseline.model_dump(
        exclude={"elapsed_s"}
    )


@pytest.mark.parametrize(
    "solver,keyword", [(solve_exact, "on_assignment"), (run_qaoa, "on_iteration")]
)
def test_observer_failures_are_not_hidden(solver, keyword):
    def fail(row):
        raise RuntimeError("observer failed")

    with pytest.raises(RuntimeError, match="observer failed"):
        solver(problem(), **{keyword: fail})


@pytest.mark.parametrize(
    "preset,qubits", [("recovery", 2), ("shared-conflict", 6), ("independent", 6)]
)
def test_comparison_preserves_input_and_validates_observed_plan(preset, qubits):
    request = arena.ArenaRequest(preset_id=preset, evaluations=8, shots=64)
    result = arena.compare_solvers(request)
    scenario = arena.load_arena_scenario(preset)
    assert result["input_fingerprint"] == fingerprint(scenario)
    assert result["scenario"] == scenario.model_dump(mode="json")
    assert result["request"] == request.model_dump(mode="json")
    assert result["execution_mode"] == "noiseless_simulator"
    assert result["hardware_jobs"] == result["credits_spent"] == 0
    classical, quantum = result["classical"], result["quantum"]
    assert classical["result"]["input_fingerprint"] == result["input_fingerprint"]
    assert classical["result"]["status"] == "optimal"
    assert quantum["status"] == "completed"
    assert quantum["result"]["input_fingerprint"] == result["input_fingerprint"]
    assert quantum["result"]["logical_qubits"] == qubits
    assert len(quantum["trace"]) == request.evaluations
    assert quantum["elapsed_s"] >= quantum["result"]["elapsed_s"]
    assert (
        result["total_elapsed_s"]
        >= result["preparation_s"] + classical["elapsed_s"] + quantum["elapsed_s"]
    )
    assert sum(s["count"] for s in quantum["result"]["samples"]) == 64
    if quantum["result"]["best_sampled_plan"] is not None:
        checked = validate_plan(
            Plan.model_validate(quantum["result"]["best_sampled_plan"]), scenario
        )
        assert quantum["validation"] == checked.model_dump(mode="json")
        expected = Fraction(
            classical["result"]["validation"]["exact_score"]
        ) - Fraction(checked.exact_score)
        assert quantum["optimum_gap"] == float(expected) >= 0
    else:
        assert quantum["optimum_gap"] is None
        assert quantum["validation"] is None


@pytest.mark.parametrize(
    "preset,status", [("infeasible", "infeasible"), ("missing-capacity", "incomplete")]
)
def test_blocked_inputs_explain_why_quantum_did_not_run(preset, status):
    result = arena.compare_solvers(arena.ArenaRequest(preset_id=preset, evaluations=4))
    assert result["classical"]["result"]["status"] == status
    assert result["quantum"]["status"] == "not_run"
    assert result["quantum"]["reason"]
    assert result["quantum"]["trace"] == []
    assert result["quantum"]["result"] is None
    assert result["quantum"]["optimum_gap"] is None


def test_comparison_does_not_modify_scenario(monkeypatch):
    source = problem()
    before = deepcopy(source.model_dump())
    monkeypatch.setattr(arena, "load_arena_scenario", lambda _: source)
    arena.compare_solvers(arena.ArenaRequest(evaluations=4))
    assert source.model_dump() == before


@pytest.mark.parametrize(
    "patch",
    [
        {"preset_id": "../../secret"},
        {"seed": True},
        {"seed": -1},
        {"seed": 2**32},
        {"shots": 0},
        {"shots": 8193},
        {"shots": 1.5},
        {"shots": "20"},
        {"depth": 0},
        {"depth": 4},
        {"evaluations": 0},
        {"evaluations": 257},
        {"execution_mode": "hardware"},
        {"secret": "forbidden"},
    ],
)
def test_budgets_and_payload_are_strict(patch):
    with pytest.raises(ValidationError):
        arena.ArenaRequest.model_validate(patch)


def test_unknown_preset_rejected_before_file_read():
    with pytest.raises(ValueError):
        arena.load_arena_scenario("../../secret")


def test_archived_hardware_is_identified_and_checked_against_original_case():
    record = arena.recorded_hardware()
    assert record["kind"] == "recorded_hardware"
    assert record["execution_mode"] == "hardware"
    assert record["job_id"] == "4c0adc79-6bd3-4578-9147-bbf8ab7b2209"
    assert record["input_fingerprint"] == fingerprint(problem())
    assert record["shots"] == sum(record["counts"].values()) == 256
    assert record["sampled_optimal_rate"] == 200 / 256
    assert record["sampled_feasible_rate"] == 247 / 256
    assert record["optimum_gap"] == 0
    assert record["credits_spent"] == 1
    assert record["new_jobs_submitted"] == 0
    assert record["validation"]["status"] == "feasible"


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(
            state_dir=tmp_path / "state", data_dir=tmp_path / "data", start_worker=False
        )
    ) as client:
        yield client


def test_catalog_and_completed_api_run(client):
    response = client.get("/arena/presets")
    assert response.status_code == 200
    assert {p["id"] for p in response.json()["presets"]} == {
        "recovery",
        "shared-conflict",
        "independent",
        "infeasible",
        "missing-capacity",
    }
    response = client.post("/arena/compare", json={"evaluations": 4, "shots": 16})
    assert response.status_code == 200
    assert len(response.json()["quantum"]["trace"]) == 4
    assert response.json()["hardware_jobs"] == 0
    assert client.get("/arena/hardware").json()["kind"] == "recorded_hardware"


@pytest.mark.parametrize(
    "payload",
    [
        {"shots": 999999},
        {"seed": True},
        {"preset_id": "unknown"},
        {"execution_mode": "hardware"},
    ],
)
def test_api_rejects_unbounded_or_paid_execution(client, payload):
    assert client.post("/arena/compare", json=payload).status_code == 422


def test_api_rejects_overlapping_run_without_blocking(client):
    assert arena._RUN_LOCK.acquire(blocking=False)
    try:
        response = client.post("/arena/compare", json={"evaluations": 4})
        assert response.status_code == 429
    finally:
        arena._RUN_LOCK.release()
    assert client.post("/arena/compare", json={"evaluations": 1}).status_code == 200


def test_api_releases_slot_after_failure(client, monkeypatch):
    def fail(request):
        raise ValueError("bounded failure")

    monkeypatch.setattr(arena, "compare_solvers", fail)
    assert client.post("/arena/compare", json={}).status_code == 400
    assert arena._RUN_LOCK.acquire(blocking=False)
    arena._RUN_LOCK.release()
