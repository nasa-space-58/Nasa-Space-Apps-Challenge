"""H8 bounded comparison; local simulation and explicitly archived QPU evidence."""

import json
from fractions import Fraction
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Annotated, Literal

from pydantic import Field

from shared.hybrid import Contract, Plan, Scenario, fingerprint, load_scenario
from .hybrid_planning import solve_exact, validate_plan
from .hybrid_quantum import compact_model, compile_qubo, run_qaoa


class ArenaRequest(Contract):
    preset_id: Literal[
        "recovery", "shared-conflict", "independent", "infeasible", "missing-capacity"
    ] = "recovery"
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 2
    evaluations: Annotated[int, Field(strict=True, ge=1, le=256)] = 80
    shots: Annotated[int, Field(strict=True, ge=1, le=8192)] = 512
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17


_EXAMPLES = Path(__file__).resolve().parents[2] / "shared" / "examples"
_PRESETS = (
    "recovery",
    "shared-conflict",
    "independent",
    "infeasible",
    "missing-capacity",
)
_RUN_LOCK = Lock()


def load_arena_scenario(preset_id: str) -> Scenario:
    """Load only bundled synthetic cases; recovery preserves H7's original inputs."""
    if preset_id not in _PRESETS:
        raise ValueError("Unknown arena preset")
    if preset_id != "recovery":
        return load_scenario(_EXAMPLES / "hybrid" / (preset_id + ".json"))
    # Preserve the exact input sent to H7 hardware. Reintegrating its physics
    # can change last-bit floats across platforms and invalidate the fingerprint.
    return load_scenario(_EXAMPLES / "arena" / "recovery-h7.json")


def compare_solvers(request: ArenaRequest) -> dict:
    """Run both solvers on one snapshot; report actual traces and inclusive local timings.

    Only the fixed small presets are accepted. Unsupported or infeasible inputs
    retain classical explanations without pretending a quantum run completed.
    No network, SDK, paid execution or repair is used.
    """
    return compare_scenario(load_arena_scenario(request.preset_id), request)


def compare_scenario(scenario: Scenario, request: ArenaRequest) -> dict:
    """Compare one explicit snapshot with H8 observers and independent validation."""
    started = perf_counter()
    request = ArenaRequest.model_validate(request.model_dump())
    scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
    digest = fingerprint(scenario)
    preparation_s = perf_counter() - started
    classical_trace = []
    classical_started = perf_counter()
    exact = solve_exact(
        scenario, max_evaluations=4096, on_assignment=classical_trace.append
    )
    classical_elapsed_s = perf_counter() - classical_started
    quantum = {
        "status": "not_run",
        "reason": "classical_" + exact.status,
        "trace": [],
        "result": None,
        "validation": None,
        "score": None,
        "optimum_gap": None,
        "elapsed_s": 0.0,
    }
    if exact.status == "optimal":
        quantum_started = perf_counter()
        try:
            result = run_qaoa(
                scenario,
                depth=request.depth,
                evaluations=request.evaluations,
                shots=request.shots,
                seed=request.seed,
                max_qubits=6,
                on_iteration=quantum["trace"].append,
            )
        except ValueError as exc:
            quantum.update(reason=str(exc), trace=[])
        else:
            validation = (
                validate_plan(result.best_sampled_plan, scenario)
                if result.best_sampled_plan
                else None
            )
            gap = None
            if validation is not None:
                if validation.status != "feasible":
                    raise ValueError(
                        "Observed quantum plan failed independent validation"
                    )
                gap = float(
                    Fraction(exact.validation.exact_score)
                    - Fraction(validation.exact_score)
                )
            quantum.update(
                status="completed",
                reason=None,
                result=result.model_dump(mode="json"),
                validation=validation.model_dump(mode="json") if validation else None,
                score=validation.score if validation else None,
                optimum_gap=gap,
            )
        quantum["elapsed_s"] = perf_counter() - quantum_started
    return {
        "version": "h8-arena-v1",
        "input_fingerprint": digest,
        "request": request.model_dump(mode="json"),
        "scenario": scenario.model_dump(mode="json"),
        "execution_mode": "noiseless_simulator",
        "hardware_jobs": 0,
        "credits_spent": 0,
        "preparation_s": preparation_s,
        "total_elapsed_s": perf_counter() - started,
        "classical": {
            "result": exact.model_dump(mode="json"),
            "trace": classical_trace,
            "elapsed_s": classical_elapsed_s,
            "score": exact.validation.score if exact.validation else None,
            "optimum_gap": 0.0 if exact.status == "optimal" else None,
            "evaluated_feasible_fraction": exact.feasible_count
            / exact.evaluated_combinations
            if exact.evaluated_combinations
            else None,
        },
        "quantum": quantum,
    }


def recorded_hardware() -> dict:
    """Revalidate the archived H7 raw counts; never submit or query a cloud job."""
    record = json.loads((_EXAMPLES / "arena" / "h7-rigetti.json").read_text())
    scenario = load_arena_scenario("recovery")
    if record["input_fingerprint"] != fingerprint(scenario):
        raise ValueError(
            "Archived hardware input differs from the current recovery preset"
        )
    model = compact_model(compile_qubo(scenario), max_qubits=2)
    counts = record["counts"]
    keys = {format(i, "02b") for i in range(4)}
    if (
        not isinstance(counts, dict)
        or not set(counts) <= keys
        or any(type(v) is not int or v < 0 for v in counts.values())
        or type(record["shots"]) is not int
        or record["shots"] <= 0
        or sum(counts.values()) != record["shots"]
    ):
        raise ValueError("Invalid archived hardware counts")
    exact = solve_exact(scenario, max_evaluations=4096)
    if exact.status != "optimal":
        raise ValueError("Archived hardware requires a complete classical reference")
    feasible_count = optimal_count = 0
    best = validation = best_score = None
    samples = []
    for basis, mask in enumerate(model.one_hot_masks):
        ids = tuple(
            oid for i, oid in enumerate(model.qubo.option_ids) if mask & (1 << i)
        )
        plan = Plan(
            plan_id="h8-archive-" + str(basis),
            scenario_id=scenario.scenario_id,
            scenario_revision=scenario.revision,
            input_fingerprint=fingerprint(scenario),
            state_revisions={s.state_id: s.revision for s in scenario.states},
            selected_option_ids=ids,
            execution_mode="hardware",
            solver_name="h7-archived-qaoa",
        )
        checked = validate_plan(plan, scenario)
        feasible = checked.status == "feasible"
        score = Fraction(checked.exact_score) if feasible else None
        optimal = feasible and score == Fraction(exact.validation.exact_score)
        count = counts.get(format(basis, "02b"), 0)
        feasible_count += count if feasible else 0
        optimal_count += count if optimal else 0
        if count and feasible and (best_score is None or score > best_score):
            best, validation, best_score = plan, checked, score
        samples.append(
            {
                "basis_index": basis,
                "count": count,
                "feasible": feasible,
                "optimal": optimal,
                "selected_option_ids": ids,
            }
        )
    return {
        **record,
        "kind": "recorded_hardware",
        "new_jobs_submitted": 0,
        "samples": samples,
        "sampled_feasible_rate": feasible_count / record["shots"],
        "sampled_optimal_rate": optimal_count / record["shots"],
        "best_plan": best.model_dump(mode="json") if best else None,
        "validation": validation.model_dump(mode="json") if validation else None,
        "optimum_gap": float(Fraction(exact.validation.exact_score) - best_score)
        if best_score is not None
        else None,
    }


def register_arena_routes(app):
    """Attach bounded thread-pool routes; keep notebook compute imports framework-free."""
    from fastapi import HTTPException

    @app.get("/arena/presets", tags=["solver-arena"])
    def arena_presets():
        return {"presets": [{"id": name, "synthetic": True} for name in _PRESETS]}

    @app.post("/arena/compare", tags=["solver-arena"])
    def arena_compare(request: ArenaRequest):
        if not _RUN_LOCK.acquire(blocking=False):
            raise HTTPException(
                429, "Arena is busy; retry after the current run finishes"
            )
        try:
            return compare_solvers(request)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            _RUN_LOCK.release()

    @app.get("/arena/hardware", tags=["solver-arena"])
    def arena_hardware():
        if not _RUN_LOCK.acquire(blocking=False):
            raise HTTPException(
                429, "Arena is busy; retry after the current run finishes"
            )
        try:
            return recorded_hardware()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            _RUN_LOCK.release()
