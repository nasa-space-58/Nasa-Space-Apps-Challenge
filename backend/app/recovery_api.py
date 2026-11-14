"""R12 event-panel view over H5 fault replay and H6 guarded hybrid recovery.

This module only reshapes existing H5/H6 evidence for display: event list,
original/classical/hybrid trajectories, decision explanations, sampled landing
dispersion and solver timing. It adds no new physics, estimator or solver.
"""

import json
from datetime import datetime
from pathlib import Path
from time import perf_counter

import numpy as np
from fastapi import HTTPException

import shared.hybrid as hybrid_contracts
from shared.hybrid import Contract, Identifier, Scenario

from .hybrid_recovery import (
    LandingBinding,
    LandingDependency,
    QuantumBudget,
    replay_hybrid,
)
from .self_healing import (
    CommunicationFault,
    FuelFault,
    LandingHazard,
    RecoveryScenario,
    SensorFault,
    ThrustFault,
    replay_recovery,
)

EXAMPLES_DIR = Path(hybrid_contracts.__file__).parent / "examples" / "recovery"
VIEW_VERSION = "r12-replay-view-v1"
MAX_TRAJECTORY_POINTS = 400


class HybridReplayInputs(Contract):
    campaign: Scenario
    landing_mission_id: Identifier
    bindings: tuple[LandingBinding, ...]
    dependencies: tuple[LandingDependency, ...] = ()
    quantum_budget: QuantumBudget = QuantumBudget()


class RecoveryReplayRequest(Contract):
    recovery: RecoveryScenario
    hybrid: HybridReplayInputs | None = None


def example_requests() -> dict[str, dict]:
    """Return every recovery example as a ready RecoveryReplayRequest body."""
    bodies = {}
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "recovery" in data:
            recovery = data.pop("recovery")
            bodies[path.stem] = {"recovery": recovery, "hybrid": data}
        else:
            bodies[path.stem] = {"recovery": data, "hybrid": None}
    return bodies


LIMITATIONS = (
    "All faults, targets, vehicle and terrain inputs are synthetic declarations; "
    "no live telemetry, hardware or real landing site is involved.",
    "Physics is H4's explicit nonrotating planar model with sampled terrain; "
    "no 3D MOON_ME transformation, attitude model or continuous collision proof.",
    "Sample pass counts are finite empirical checks, not calibrated success "
    "probabilities or coverage guarantees.",
    "An infeasible result means no validated continuation in the declared "
    "candidate set; it is not a safe abort or a physical vehicle stop.",
    "Hybrid planning uses a noiseless local simulator with the simulated flight "
    "clock paused; it is not live control, hardware execution or quantum advantage.",
    "Wall-clock timings depend on this machine and load; they are not "
    "flight-computer performance qualification.",
)


def _elapsed(epoch: str | datetime, start: datetime) -> float:
    if isinstance(epoch, str):
        epoch = datetime.fromisoformat(epoch.replace("Z", "+00:00"))
    return (epoch - start).total_seconds()


def _event_summary(payload) -> str:
    if isinstance(payload, ThrustFault):
        return f"Available thrust reduced to {payload.available_thrust_n:g} N"
    if isinstance(payload, FuelFault):
        return f"Propellant loss of {payload.amount_kg:g} kg declared"
    if isinstance(payload, SensorFault):
        p = ", ".join(f"{v:g}" for v in payload.position_std_m)
        v = ", ".join(f"{x:g}" for x in payload.velocity_std_mps)
        return (
            f"Sensor {payload.sensor_id} degraded: position sigma ({p}) m, "
            f"velocity sigma ({v}) m/s"
        )
    if isinstance(payload, LandingHazard):
        return (
            f"Landing exclusion {payload.start_downrange_m:g} to "
            f"{payload.end_downrange_m:g} m downrange"
        )
    if isinstance(payload, CommunicationFault):
        return f"Communication outage until {payload.end_utc.isoformat()}"
    raise TypeError(f"Unsupported fault payload {type(payload).__name__}")


def _trajectory(trace: list[dict]) -> list[dict]:
    if len(trace) > MAX_TRAJECTORY_POINTS:
        keep = np.unique(
            np.linspace(0, len(trace) - 1, MAX_TRAJECTORY_POINTS).round().astype(int)
        )
        trace = [trace[i] for i in keep]
    return [
        dict(
            elapsed_s=row["elapsed_s"],
            downrange_m=row["downrange_m"],
            altitude_m=row["altitude_m"],
            vertical_speed_mps=row["vertical_speed_mps"],
            horizontal_speed_mps=row["horizontal_speed_mps"],
            propellant_kg=row["state"]["propellant_kg"],
            target_id=row["target_id"],
        )
        for row in trace
    ]


def _dispersion(outcomes: list[dict]) -> dict | None:
    rows = [o for o in outcomes if "downrange_m" in o]
    if not rows:
        return None
    downrange = np.array([o["downrange_m"] for o in rows])
    fuel = np.array([o["propellant_kg"] for o in rows])
    return dict(
        count=len(rows),
        min_downrange_m=float(downrange.min()),
        max_downrange_m=float(downrange.max()),
        mean_downrange_m=float(downrange.mean()),
        std_downrange_m=float(downrange.std()),
        min_propellant_kg=float(fuel.min()),
        max_propellant_kg=float(fuel.max()),
        samples=[
            dict(
                sample=o["sample"],
                accepted=o["accepted"],
                downrange_m=o["downrange_m"],
                propellant_kg=o["propellant_kg"],
                reasons=list(o["reasons"]),
            )
            for o in rows
        ],
    )


def _candidate(row: dict) -> dict:
    fine = row.get("fine")
    u = row["uncertainty"]
    return dict(
        target_id=row["target_id"],
        accepted=row["accepted"],
        reasons=list(row["reasons"]),
        coarse_feasible=row["coarse_feasible"],
        fine_feasible=row["fine_feasible"],
        numerically_consistent=row["numerically_consistent"],
        predicted_propellant_kg=None if fine is None else fine["propellant_used_kg"],
        predicted_touchdown_downrange_m=None if fine is None else fine["downrange_m"],
        sample_count=u["sample_count"],
        pass_count=u["pass_count"],
        failure_counts=dict(u["failure_counts"]),
        dispersion=_dispersion(u["outcomes"]),
    )


def _reasons_text(candidate: dict) -> str:
    return ", ".join(candidate["reasons"]) or "no recorded reason"


def _explain(decision: dict, candidates: list[dict], hybrid: dict | None) -> str:
    by_id = {c["target_id"]: c for c in candidates}
    accepted = [c["target_id"] for c in candidates if c["accepted"]]
    previous, selected = decision["previous_target_id"], decision["selected_target_id"]
    if decision["action"] == "no_feasible_continuation":
        rejected = "; ".join(
            f"{c['target_id']}: {_reasons_text(c)}" for c in candidates
        )
        if hybrid is not None and accepted:
            return (
                f"No validated joint landing/schedule plan ({hybrid['reason']}); "
                f"physically screened targets: {', '.join(accepted)}. "
                "This is not a safe abort."
            )
        return (
            f"No validated continuation: every candidate was rejected ({rejected}). "
            "This is not a safe abort."
        )
    if hybrid is not None:
        why = (
            f"Joint landing/schedule plan {', '.join(hybrid['selected_option_ids'])} "
            f"from {hybrid['source']} ({hybrid['reason']})"
        )
    else:
        why = None
    if decision["action"] == "continue":
        chosen = by_id[selected]
        text = (
            f"Kept {selected}: it passes the nominal, two-resolution and "
            f"{chosen['pass_count']}/{chosen['sample_count']} sampled checks."
        )
        return text if why is None else f"{text} {why}."
    old = by_id.get(previous)
    if old is not None and not old["accepted"]:
        cause = f"{previous} was rejected ({_reasons_text(old)})"
    else:
        cause = f"{previous} still passed"
    if why is None:
        why = (
            f"{selected} has the least predicted propellant among "
            f"{len(accepted)} accepted candidate(s)"
        )
    return f"Retargeted from {previous} to {selected}: {cause}; {why}."


def _hybrid_info(result: dict, budget: QuantumBudget) -> dict:
    plan, quantum, validation = result["plan"], result["quantum"], result["validation"]
    return dict(
        status=result["status"],
        source=result["source"],
        reason=result["reason"],
        classical_status=result["classical_status"],
        selected_option_ids=[] if plan is None else list(plan["selected_option_ids"]),
        objective_exact_score=None
        if validation is None
        else str(validation["exact_score"]),
        preparation_s=result["preparation_s"],
        revalidation_s=result["revalidation_s"],
        elapsed_since_submission_s=result["elapsed_since_submission_s"],
        quantum_elapsed_s=None if quantum is None else quantum["elapsed_s"],
        logical_qubits=None if quantum is None else quantum["logical_qubits"],
        deadline_s=budget.deadline_s,
        schedule_rejections=result["schedule_rejections"],
    )


def _branch(
    name: str,
    replay: dict,
    *,
    start: datetime,
    nominal_deadline_s: float,
    initial_kg: float,
    reserve_kg: float,
    wall_s: float,
    timings: list[float],
    hybrid_results: list[dict] | None = None,
    budget: QuantumBudget | None = None,
) -> dict:
    decisions = []
    for index, decision in enumerate(replay["decisions"]):
        candidates = [_candidate(row) for row in decision["candidates"]]
        info = (
            None
            if hybrid_results is None
            else _hybrid_info(hybrid_results[index], budget)
        )
        decisions.append(
            dict(
                index=index,
                elapsed_s=_elapsed(decision["estimated_state"]["epoch_utc"], start),
                revision=decision["revision"],
                input_sha256=decision["input_sha256"],
                action=decision["action"],
                previous_target_id=decision["previous_target_id"],
                selected_target_id=decision["selected_target_id"],
                available_thrust_n=decision["available_thrust_n"],
                active_hazards=[list(h) for h in decision["landing_hazards"]],
                outage_end_utc=decision["outage_end_utc"],
                sample_count=len(decision["uncertainty_draws"]),
                candidates=candidates,
                explanation=_explain(decision, candidates, info),
                planning_wall_s=timings[index],
                hybrid=info,
            )
        )
    trajectory = _trajectory(replay["trace"])
    final = replay["final_state"]
    final_elapsed = _elapsed(final["epoch_utc"], start)
    remaining = final["propellant_kg"]
    applied = [entry["event_id"] for entry in replay["event_log"]]
    return dict(
        branch=name,
        mode=replay["mode"],
        status=replay["status"],
        selected_target_id=replay["selected_target_id"],
        final_reasons=list(replay["final_reasons"]),
        final_elapsed_s=final_elapsed,
        delay_vs_nominal_s=final_elapsed - nominal_deadline_s,
        touchdown_downrange_m=trajectory[-1]["downrange_m"] if trajectory else None,
        propellant_burned_kg=replay["propellant_burned_kg"],
        propellant_lost_kg=replay["propellant_lost_kg"],
        propellant_remaining_kg=remaining,
        reserve_margin_kg=remaining - reserve_kg,
        fuel_accounting_error_kg=initial_kg
        - replay["propellant_burned_kg"]
        - replay["propellant_lost_kg"]
        - remaining,
        terminal_summary=replay["terminal_summary"],
        applied_event_ids=applied,
        unapplied_event_ids=list(replay["unapplied_event_ids"]),
        trajectory=trajectory,
        decisions=decisions,
        replay_wall_s=wall_s,
    )


def build_replay_view(request: RecoveryReplayRequest) -> dict:
    """Replay identical events under original, classical and optional hybrid plans."""
    request = RecoveryReplayRequest.model_validate(request.model_dump())
    scenario = request.recovery
    base = scenario.initial_request
    start = base.initial_state.epoch_utc
    common = dict(
        start=start,
        nominal_deadline_s=base.duration_s,
        initial_kg=base.initial_state.propellant_kg,
        reserve_kg=base.reserve_kg,
    )
    branches = []

    started = perf_counter()
    original = replay_recovery(scenario, replan=False)
    branches.append(
        _branch(
            "original",
            original,
            wall_s=perf_counter() - started,
            timings=[],
            **common,
        )
    )

    timings: list[float] = []
    started = perf_counter()
    classical = replay_recovery(scenario, decision_timings=timings)
    branches.append(
        _branch(
            "classical",
            classical,
            wall_s=perf_counter() - started,
            timings=timings,
            **common,
        )
    )

    hybrid_section = None
    if request.hybrid is not None:
        h = request.hybrid
        timings = []
        started = perf_counter()
        result = replay_hybrid(
            scenario,
            h.campaign,
            h.landing_mission_id,
            h.bindings,
            h.dependencies,
            h.quantum_budget,
            decision_timings=timings,
        )
        hybrid_branch = _branch(
            "hybrid",
            {**result["replay"], "mode": "hybrid_replanning"},
            wall_s=perf_counter() - started,
            timings=timings,
            hybrid_results=result["decisions"],
            budget=h.quantum_budget,
            **common,
        )
        branches.append(hybrid_branch)
        last = hybrid_branch["decisions"][-1]["hybrid"] if result["decisions"] else None
        hybrid_section = dict(
            execution_mode=result["execution_mode"],
            hardware_jobs=result["hardware_jobs"],
            simulated_clock_paused_during_planning=result[
                "simulated_clock_paused_during_planning"
            ],
            landing_mission_id=h.landing_mission_id,
            campaign_id=h.campaign.scenario_id,
            quantum_budget=h.quantum_budget.model_dump(mode="json"),
            final_selected_option_ids=None
            if last is None
            else last["selected_option_ids"],
        )

    return dict(
        view_version=VIEW_VERSION,
        scenario_id=scenario.scenario_id,
        scenario_sha256=classical["scenario_sha256"],
        nominal_target_id=scenario.nominal_target_id,
        initial_epoch_utc=start.isoformat(),
        initial_propellant_kg=base.initial_state.propellant_kg,
        reserve_kg=base.reserve_kg,
        require_link_for_retarget=scenario.require_link_for_retarget,
        targets=[
            dict(
                target_id=t.target_id,
                downrange_m=t.downrange_m,
                deadline_elapsed_s=_elapsed(t.touchdown_deadline_utc, start),
                nominal=t.target_id == scenario.nominal_target_id,
            )
            for t in scenario.targets
        ],
        events=[
            dict(
                event_id=e.event_id,
                kind=e.payload.kind,
                epoch_utc=e.epoch_utc.isoformat(),
                elapsed_s=_elapsed(e.epoch_utc, start),
                sequence=e.sequence,
                summary=_event_summary(e.payload),
                payload=e.payload.model_dump(mode="json"),
                source_kind=e.source.kind,
                source_description=e.source.description,
            )
            for e in scenario.events
        ],
        uncertainty=scenario.uncertainty.model_dump(mode="json"),
        branches=branches,
        hybrid=hybrid_section,
        limitations=list(LIMITATIONS),
    )


def register_recovery_routes(app) -> None:
    """GET /recovery/examples[/{name}] and POST /recovery/replays."""

    @app.get("/recovery/examples")
    def recovery_examples():
        return [
            {
                "name": name,
                "scenario_id": body["recovery"]["scenario_id"],
                "hybrid": body.get("hybrid") is not None,
            }
            for name, body in example_requests().items()
        ]

    @app.get("/recovery/examples/{name}")
    def recovery_example(name: str):
        body = example_requests().get(name)
        if body is None:
            raise HTTPException(404, "Unknown recovery example")
        return body

    @app.post("/recovery/replays")
    def recovery_replay(body: RecoveryReplayRequest):
        try:
            return build_replay_view(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
