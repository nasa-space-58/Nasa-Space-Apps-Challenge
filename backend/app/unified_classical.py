"""Bounded classical completion and revalidation of synthetic R11/H6 snapshots."""

import hashlib
import json
import math
from datetime import datetime, timedelta
from fractions import Fraction
from time import perf_counter
from typing import Annotated, Literal

from pydantic import Field

from shared.campaign import CampaignProblem
from shared.hybrid import Contract, Digest, Identifier, Plan, fingerprint

from .campaign_planning import (
    CampaignPlan,
    CampaignValidation,
    LinkAllocation,
    generate_candidates,
    solve_joint,
    validate_campaign_plan,
)
from .hybrid_planning import validate_plan
from .hybrid_recovery import prepare_problem
from .terminal_descent import TerminalRequest, propagate_terminal
from .unified_planning import (
    AdapterError,
    PlanningSnapshot,
    UnifiedPlanningEnvelope,
    to_r11_problem,
    to_h6_request,
)


class CompletedMission(Contract):
    """Caller-supplied executed-action ledger; retained exactly or rejected."""

    mission_id: Identifier
    option_id: Identifier
    allocations: tuple[LinkAllocation, ...] = ()


class LandingExclusion(Contract):
    event_id: Identifier
    epoch_utc: datetime
    start_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    end_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]


class ClassicalCandidate(Contract):
    target_id: Identifier
    status: Literal["feasible", "rejected", "infeasible", "limit_reached"]
    reasons: tuple[str, ...] = ()
    physical: dict
    problem: CampaignProblem | None = None
    plan: CampaignPlan | None = None
    validation: CampaignValidation | None = None
    solver_status: str | None = None
    comparison_score_exact: str | None = None


class ClassicalDecision(Contract):
    schema_version: Literal[1] = 1
    snapshot_sha256: Digest
    completed: tuple[CompletedMission, ...] = ()
    candidates: tuple[ClassicalCandidate, ...]
    selected_target_id: Identifier | None
    status: Literal["feasible", "no_feasible_continuation", "limit_reached"]
    optimum_proven_over_screened_targets: bool
    elapsed_s: float
    simulated_clock_paused: Literal[True] = True
    execution_mode: Literal["classical"] = "classical"
    decision_sha256: Digest


def plan_classical(
    snapshot: PlanningSnapshot,
    *,
    current: UnifiedPlanningEnvelope,
    now_utc: datetime,
    completed: tuple[CompletedMission, ...] = (),
    time_limit_s: float = 5.0,
) -> ClassicalDecision:
    """Screen physical continuations, derive exact arrival evidence and solve full R11."""
    started = perf_counter()
    if (
        type(time_limit_s) not in (int, float)
        or not math.isfinite(time_limit_s)
        or not 0 < time_limit_s <= 30
    ):
        raise ValueError(
            "Per-target solver budget must be finite and in (0, 30] seconds"
        )
    campaign = to_r11_problem(snapshot, current=current, now_utc=now_utc)
    job = to_h6_request(snapshot, current=current, now_utc=now_utc)
    _supported(job, campaign)
    ledger = _ledger(completed, campaign, job, now_utc)
    prepared = prepare_problem(job)
    rows = []
    for physical in prepared["physical_candidates"]:
        target_id = physical["target_id"]
        common = dict(target_id=target_id, physical=physical)
        if not physical["accepted"]:
            rows.append(
                ClassicalCandidate(
                    status="rejected", reasons=tuple(physical["reasons"]), **common
                )
            )
            continue
        problem, option_id, rejection = _derive(snapshot, prepared, physical)
        if rejection:
            rows.append(
                ClassicalCandidate(status="rejected", reasons=(rejection,), **common)
            )
            continue
        excluded = _past_options(problem, ledger, now_utc)
        outcome = solve_joint(
            problem,
            forced_option_ids=(option_id,) + tuple(c.option_id for c in ledger),
            excluded_option_ids=excluded,
            time_limit_s=time_limit_s,
        )
        if outcome.plan is None:
            rows.append(
                ClassicalCandidate(
                    status="limit_reached"
                    if outcome.status == "limit_reached"
                    else "infeasible",
                    reasons=(outcome.status,),
                    problem=problem,
                    solver_status=outcome.status,
                    **common,
                )
            )
            continue
        validation = validate_campaign_plan(outcome.plan, problem)
        if validation.status != "feasible":
            raise RuntimeError(
                "Independent R11 validator rejected classical completion"
            )
        if not _preserves(outcome.plan, ledger):
            # The native solver cannot force exact link allocations. Do not
            # rewrite executed history or pretend that this proves infeasibility.
            rows.append(
                ClassicalCandidate(
                    status="limit_reached",
                    reasons=("completed_allocation_lock_unsupported",),
                    problem=problem,
                    solver_status=outcome.status,
                    **common,
                )
            )
            continue
        rows.append(
            ClassicalCandidate(
                status="feasible",
                problem=problem,
                plan=outcome.plan,
                validation=validation,
                solver_status=outcome.status,
                comparison_score_exact=_score(campaign, job, validation, physical),
                **common,
            )
        )
    feasible = [row for row in rows if row.status == "feasible"]
    selected = min(
        feasible,
        key=lambda row: (-Fraction(row.comparison_score_exact), row.target_id),
        default=None,
    )
    limited = any(
        row.status == "limit_reached" or row.solver_status == "limit_reached"
        for row in rows
    )
    decision = ClassicalDecision(
        snapshot_sha256=snapshot.snapshot_sha256,
        completed=ledger,
        candidates=tuple(rows),
        selected_target_id=selected.target_id if selected else None,
        status="feasible"
        if selected
        else "limit_reached"
        if limited
        else "no_feasible_continuation",
        optimum_proven_over_screened_targets=bool(selected) and not limited,
        elapsed_s=perf_counter() - started,
        decision_sha256="0" * 64,
    )
    return decision.model_copy(update={"decision_sha256": _decision_hash(decision)})


def accept_classical(
    decision: ClassicalDecision,
    snapshot: PlanningSnapshot,
    *,
    current: UnifiedPlanningEnvelope,
    now_utc: datetime,
    completed: tuple[CompletedMission, ...] = (),
) -> ClassicalCandidate:
    """Recheck identity, full allocations and physical continuation before acceptance."""
    campaign = to_r11_problem(snapshot, current=current, now_utc=now_utc)
    job = to_h6_request(snapshot, current=current, now_utc=now_utc)
    checked = ClassicalDecision.model_validate(decision.model_dump(mode="python"))
    if (
        checked.decision_sha256 != _decision_hash(checked)
        or checked.snapshot_sha256 != snapshot.snapshot_sha256
    ):
        raise AdapterError(
            "decision_tampered", "Decision does not match its sealed inputs or payload"
        )
    _supported(job, campaign)
    ledger = _ledger(completed, campaign, job, now_utc)
    if ledger != checked.completed:
        raise AdapterError("stale_inputs", "Completed-action ledger changed")
    selected = next(
        (
            row
            for row in checked.candidates
            if row.target_id == checked.selected_target_id
        ),
        None,
    )
    if (
        checked.status != "feasible"
        or selected is None
        or selected.status != "feasible"
    ):
        raise AdapterError(
            "no_accepted_continuation", "No complete feasible continuation is available"
        )
    # A checksum is not a safety certificate: repeat both physical screening and
    # native constraint checks, rather than trusting stored accepted flags.
    prepared = prepare_problem(job)
    physical = next(
        (
            row
            for row in prepared["physical_candidates"]
            if row["target_id"] == selected.target_id
        ),
        None,
    )
    if physical is None or not physical["accepted"] or physical != selected.physical:
        raise AdapterError(
            "physical_revalidation_failed",
            "Current screening disagrees with decision evidence",
        )
    problem, option_id, rejection = _derive(snapshot, prepared, physical)
    if rejection or problem != selected.problem or selected.plan is None:
        raise AdapterError(
            "decision_tampered", "Derived full campaign does not match current evidence"
        )
    report = validate_campaign_plan(selected.plan, problem)
    if (
        report.status != "feasible"
        or selected.plan.selections.get(job.landing_mission_id) != option_id
        or set(selected.plan.selections.values())
        & set(_past_options(problem, ledger, now_utc))
        or not _preserves(selected.plan, ledger)
    ):
        raise AdapterError(
            "campaign_revalidation_failed",
            "Completed plan violates current campaign or action locks",
        )
    return selected.model_copy(update={"validation": report})


def apply_landing_exclusion(
    snapshot: PlanningSnapshot,
    event: LandingExclusion,
) -> UnifiedPlanningEnvelope:
    """Advance a zero-uncertainty synthetic estimate to the event; invalidate old plans."""
    event = LandingExclusion.model_validate(event.model_dump(mode="python"))
    if event.start_downrange_m >= event.end_downrange_m:
        raise ValueError("Landing exclusion must have positive extent")
    campaign = to_r11_problem(
        snapshot, current=snapshot.envelope, now_utc=event.epoch_utc
    )
    job = to_h6_request(snapshot, current=snapshot.envelope, now_utc=event.epoch_utc)
    _supported(job, campaign)
    checkpoint = job.checkpoint
    spec = checkpoint.uncertainty
    if (
        any(spec.position_std_m)
        or any(spec.velocity_std_mps)
        or spec.thrust_loss_fraction_bound
        or spec.fuel_shortfall_bound_kg
    ):
        raise ValueError(
            "Synthetic checkpoint advancement requires zero navigation uncertainty"
        )
    source_id = "event-" + event.event_id
    if any(
        source.source_id == source_id for source in snapshot.envelope.context.sources
    ):
        raise ValueError("Duplicate event identity")
    state = checkpoint.estimated_state
    seconds = (event.epoch_utc - state.epoch_utc).total_seconds()
    if seconds <= 0:
        raise ValueError("Event must advance the checkpoint time")
    target = next(
        t for t in job.recovery.targets if t.target_id == checkpoint.previous_target_id
    )
    raw = job.recovery.initial_request.model_dump(mode="python")
    raw.update(
        initial_state=state.model_dump(),
        target_downrange_m=target.downrange_m,
        duration_s=(target.touchdown_deadline_utc - state.epoch_utc).total_seconds(),
    )
    propagation = propagate_terminal(
        TerminalRequest.model_validate(raw),
        stop_after_s=seconds,
        thrust_limit_n=checkpoint.available_thrust_n,
    )
    final = propagation["trace"][-1]["state"]
    if (
        datetime.fromisoformat(final["epoch_utc"].replace("Z", "+00:00"))
        != event.epoch_utc
        or propagation["termination"] == "contact"
    ):
        raise ValueError("Vehicle terminated before the requested airborne event")
    final["revision"] = state.revision + 1
    data = snapshot.envelope.model_dump(mode="python")
    data["recovery"]["checkpoint"]["estimated_state"] = final
    data["recovery"]["checkpoint"]["landing_hazards"] = (
        *checkpoint.landing_hazards,
        (event.start_downrange_m, event.end_downrange_m),
    )
    data["context"]["created_utc"] = event.epoch_utc
    data["context"]["revision"] += 1
    data["campaign"]["revision"] += 1
    data["context"]["sources"] = (
        *data["context"]["sources"],
        dict(
            source_id=source_id,
            kind="derived",
            description="Synthetic exact-time landing-exclusion event; estimated state propagation, no telemetry or coordinate transform.",
            sha256=_hash(event.model_dump(mode="json")),
            version="unified-exclusion-v1",
            parent_source_ids=tuple(
                s.source_id for s in snapshot.envelope.context.sources
            ),
        ),
    )
    return UnifiedPlanningEnvelope.model_validate(data)


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _decision_hash(decision):
    return _hash(decision.model_dump(mode="json", exclude={"decision_sha256"}))


def _supported(job, campaign):
    if {m.mission_id for m in job.campaign.missions} != {job.landing_mission_id}:
        raise ValueError(
            "Stage 3 supports one active H6 lander; other missions remain in full R11"
        )
    if any(
        e.epoch_utc > job.checkpoint.estimated_state.epoch_utc
        for e in job.recovery.events
    ):
        raise ValueError(
            "Unapplied future faults require event replay, not a static continuation"
        )
    initial = job.recovery.initial_request.initial_state.propellant_kg
    vehicle = next(
        v
        for v in campaign.vehicles
        if v.vehicle_id == job.checkpoint.estimated_state.vehicle_id
    )
    if not math.isclose(vehicle.usable_propellant_kg, initial, rel_tol=0, abs_tol=1e-9):
        raise ValueError(
            "R11 starting usable fuel must equal the H6 initial propellant"
        )
    if job.checkpoint.estimated_state.propellant_kg > initial:
        raise ValueError(
            "Checkpoint fuel cannot exceed initial fuel without modeled refueling"
        )


def _ledger(completed, campaign, job, now):
    checked = tuple(CompletedMission.model_validate(c.model_dump()) for c in completed)
    if len({c.mission_id for c in checked}) != len(checked):
        raise ValueError("Duplicate completed mission IDs")
    candidates = {c.option_id: c for c in generate_candidates(campaign).candidates}
    for c in checked:
        native = candidates.get(c.option_id)
        if (
            native is None
            or native.mission_id != c.mission_id
            or c.mission_id == job.landing_mission_id
        ):
            raise ValueError("Completed ledger has an unknown or active mission choice")
        if native.surface_window.end_utc > now:
            raise ValueError(
                "A completed mission must have finished its modeled surface operation"
            )
        if any(
            a.mission_id != c.mission_id or a.window.end_utc > now
            for a in c.allocations
        ):
            raise ValueError(
                "Executed allocations must belong to the completed mission and be in the past"
            )
    return tuple(sorted(checked, key=lambda c: c.mission_id))


def _past_options(problem, ledger, now):
    fixed = {c.option_id for c in ledger}
    return tuple(
        c.option_id
        for c in generate_candidates(problem).candidates
        if c.landing_utc < now and c.option_id not in fixed
    )


def _preserves(plan, ledger):
    for action in ledger:
        if plan.selections.get(action.mission_id) != action.option_id:
            return False
        actual = [
            a.model_dump(mode="json")
            for a in plan.allocations
            if a.mission_id == action.mission_id
        ]
        expected = [a.model_dump(mode="json") for a in action.allocations]
        if sorted(actual, key=lambda a: json.dumps(a, sort_keys=True)) != sorted(
            expected, key=lambda a: json.dumps(a, sort_keys=True)
        ):
            return False
    return True


def _derive(snapshot, prepared, physical):
    envelope = snapshot.envelope
    campaign, job = envelope.campaign, envelope.recovery
    target = physical["target_id"]
    binding = next(b for b in job.bindings if b.target_id == target)
    scenario = prepared["scenario"]
    if scenario is None or binding.option_id not in {
        c.option_id for c in scenario.candidates
    }:
        return None, None, "h0_preparation_rejected"
    proposal = Plan(
        plan_id="classical-continuation",
        scenario_id=scenario.scenario_id,
        scenario_revision=scenario.revision,
        input_fingerprint=fingerprint(scenario),
        state_revisions={s.state_id: s.revision for s in scenario.states},
        selected_option_ids=(binding.option_id,),
        execution_mode="classical",
        solver_name="native-h1-validator",
    )
    if validate_plan(proposal, scenario).status != "feasible":
        return None, None, "h0_constraints"
    site_id = next(
        c.site.site_id
        for c in job.campaign.candidates
        if c.option_id == binding.option_id
    )
    arrival = datetime.fromisoformat(
        physical["fine"]["state"]["epoch_utc"].replace("Z", "+00:00")
    )
    mission = next(
        m for m in campaign.missions if m.mission_id == job.landing_mission_id
    )
    if not mission.landing_window.start_utc <= arrival < mission.landing_window.end_utc:
        return None, None, "physical_arrival_outside_nominal_policy"
    reserve = next(
        v.propellant_reserve_kg
        for v in campaign.vehicles
        if v.vehicle_id == mission.vehicle_id
    )
    if any(
        sample["propellant_kg"] < reserve
        for sample in physical["uncertainty"]["outcomes"]
    ):
        return None, None, "r11_sampled_reserve"
    consumed = (
        job.recovery.initial_request.initial_state.propellant_kg
        - physical["fine"]["state"]["propellant_kg"]
    )
    data = campaign.model_dump(mode="python")
    source_id = "physical-" + _hash([snapshot.snapshot_sha256, target])[:24]
    data["sources"] = (
        *data["sources"],
        dict(
            source_id=source_id,
            kind="derived",
            description="Two-resolution H6 terminal screening; exact nominal arrival and total propellant depletion from the declared initial state. Not a MOON_ME transform.",
            sha256=_hash(physical),
            version="unified-classical-v1",
            parent_source_ids=tuple(s.source_id for s in campaign.sources),
        ),
    )
    data["revision"] += 1
    for m in data["missions"]:
        if m["mission_id"] == mission.mission_id:
            m["landing_window"] = dict(
                start_utc=arrival, end_utc=arrival + timedelta(microseconds=1)
            )
            m["source_ids"] = (*m["source_ids"], source_id)
    for site in data["sites"]:
        if site["site"]["site_id"] == site_id:
            for descent in site["descents"]:
                if descent["vehicle_id"] == mission.vehicle_id:
                    descent.update(
                        feasible=True,
                        propellant_used_kg=consumed,
                        source_ids=(*descent["source_ids"], source_id),
                    )
    derived = CampaignProblem.model_validate(data)
    selected = next(
        (
            c
            for c in generate_candidates(derived).candidates
            if c.mission_id == mission.mission_id
            and c.site_id == site_id
            and c.landing_utc == arrival
        ),
        None,
    )
    if selected is None:
        return None, None, "r11_candidate_rejected"
    return derived, selected.option_id, None


def _score(original, job, validation, physical):
    # Fixing the active arrival changes R11's window-relative wait origin.
    # Restore that constant before comparing different target branches.
    mission = next(
        m for m in original.missions if m.mission_id == job.landing_mission_id
    )
    arrival = datetime.fromisoformat(
        physical["fine"]["state"]["epoch_utc"].replace("Z", "+00:00")
    )
    delta = arrival - mission.landing_window.start_utc
    seconds = Fraction(delta.days * 86400 + delta.seconds) + Fraction(
        delta.microseconds, 1000000
    )
    penalty = (
        Fraction(str(original.preferences.wait_weight))
        * seconds
        / Fraction(str(original.preferences.wait_scale_s))
    )
    return str(Fraction(validation.exact_score) - penalty)
