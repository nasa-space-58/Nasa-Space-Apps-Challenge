"""H6 bounded asynchronous QAOA selection with fresh classical recovery fallback."""

import hashlib
import json
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeout,
    CancelledError as FutureCancelled,
)
from dataclasses import dataclass
from fractions import Fraction
from time import monotonic, perf_counter
from datetime import datetime, timedelta
from typing import Annotated

from pydantic import Field, model_validator

from shared.hybrid import Contract, Identifier, NonNegative, Plan, Scenario, fingerprint
from .self_healing import (
    NavigationUncertainty,
    RecoveryScenario,
    _draws,
    _screen,
    replay_recovery,
)
from .hybrid_planning import solve_exact, validate_plan
from .hybrid_quantum import QaoaResult, run_qaoa
from .terminal_descent import TerminalState


class RecoveryCheckpoint(Contract):
    estimated_state: TerminalState
    available_thrust_n: NonNegative
    landing_hazards: tuple[tuple[float, float], ...] = ()
    outage_end_utc: datetime
    uncertainty: NavigationUncertainty
    previous_target_id: Identifier


class LandingBinding(Contract):
    target_id: Identifier
    option_id: Identifier


class LandingDependency(Contract):
    follower_option_id: Identifier
    minimum_delay_s: Annotated[float, Field(strict=True, ge=0, le=3600)] = 0.0


class HybridRecoveryRequest(Contract):
    recovery: RecoveryScenario
    checkpoint: RecoveryCheckpoint
    campaign: Scenario
    landing_mission_id: Identifier
    fuel_penalty_weight: Annotated[float, Field(strict=True, gt=0, le=1000)] = 1.0
    fuel_scale_kg: Annotated[float, Field(strict=True, gt=0, le=1e9)] = 1000.0
    bindings: Annotated[tuple[LandingBinding, ...], Field(min_length=1, max_length=8)]
    dependencies: Annotated[tuple[LandingDependency, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def validate_linkages(self):
        recovery, checkpoint, campaign = self.recovery, self.checkpoint, self.campaign
        state = checkpoint.estimated_state
        initial = recovery.initial_request.initial_state
        if state.vehicle_id != initial.vehicle_id or state.revision < initial.revision:
            raise ValueError("Checkpoint vehicle/revision does not match recovery")
        if (
            not initial.epoch_utc
            <= state.epoch_utc
            < max(t.touchdown_deadline_utc for t in recovery.targets)
        ):
            raise ValueError("Checkpoint epoch lies outside the recovery horizon")
        if (
            state.propellant_kg
            > recovery.initial_request.vehicle.propellant_capacity_kg
        ):
            raise ValueError("Checkpoint fuel exceeds tank capacity")
        if (
            checkpoint.available_thrust_n
            > recovery.initial_request.vehicle.max_thrust_n
        ):
            raise ValueError("Checkpoint thrust exceeds the vehicle envelope")
        if any(not (-50000 <= a < b <= 50000) for a, b in checkpoint.landing_hazards):
            raise ValueError("Invalid landing hazard interval")
        targets = {t.target_id for t in recovery.targets}
        if checkpoint.previous_target_id not in targets:
            raise ValueError("Unknown previous target")
        if any(o.metric == "recovery_propellant_kg" for o in campaign.objectives):
            raise ValueError(
                "Recovery fuel is a reserved derived objective; configure its weight/scale on the H6 request"
            )
        if campaign.events:
            raise ValueError(
                "Campaign must be a current static snapshot without unapplied events"
            )
        if len(campaign.candidates) > 32 or len(campaign.missions) > 8:
            raise ValueError("Campaign exceeds bounded H6 preparation size")
        mission = next(
            (m for m in campaign.missions if m.mission_id == self.landing_mission_id),
            None,
        )
        if mission is None or mission.vehicle_id != initial.vehicle_id:
            raise ValueError("Landing mission must reference the recovery vehicle")
        landing_options = {
            c.option_id
            for c in campaign.candidates
            if c.mission_id == self.landing_mission_id
        }
        if (
            len({b.target_id for b in self.bindings}) != len(self.bindings)
            or len({b.option_id for b in self.bindings}) != len(self.bindings)
            or {b.target_id for b in self.bindings} != targets
            or {b.option_id for b in self.bindings} != landing_options
        ):
            raise ValueError(
                "Provide a bijection between recovery targets and landing options"
            )
        following = {c.option_id for c in campaign.candidates} - landing_options
        if len({d.follower_option_id for d in self.dependencies}) != len(
            self.dependencies
        ) or any(d.follower_option_id not in following for d in self.dependencies):
            raise ValueError(
                "Dependencies must uniquely reference following-mission options"
            )
        return self


class QuantumBudget(Contract):
    deadline_s: Annotated[float, Field(strict=True, gt=0, le=30)] = 5.0
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    evaluations: Annotated[int, Field(strict=True, ge=1, le=512)] = 64
    shots: Annotated[int, Field(strict=True, ge=1, le=8192)] = 256
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17
    max_qubits: Annotated[int, Field(strict=True, ge=1, le=6)] = 4


@dataclass(frozen=True)
class RecoveryTicket:
    request_sha256: str
    prepared: dict
    fallback: object
    future: object
    deadline: float
    launch_reason: str
    budget: QuantumBudget
    submitted_at: float
    preparation_s: float


def checkpoint_from_decision(decision: dict) -> RecoveryCheckpoint:
    """Extract H5 estimate and active constraints; never consume accepted flags."""
    return RecoveryCheckpoint.model_validate(
        {
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
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def prepare_problem(job_request: HybridRecoveryRequest) -> dict:
    """Re-screen physical options, then build a static campaign without frame inference."""
    job = HybridRecoveryRequest.model_validate(job_request.model_dump())
    checkpoint, recovery = job.checkpoint, job.recovery
    identity = _digest(
        dict(version="h6-recovery-v1", request=job.model_dump(mode="json"))
    )
    draws = _draws(
        checkpoint.uncertainty, recovery.seed, checkpoint.estimated_state.revision
    )
    rows = [
        _screen(
            recovery.initial_request,
            checkpoint.estimated_state,
            target,
            checkpoint.available_thrust_n,
            checkpoint.landing_hazards,
            draws,
            "communication_required"
            if recovery.require_link_for_retarget
            and checkpoint.estimated_state.epoch_utc < checkpoint.outage_end_utc
            and target.target_id != checkpoint.previous_target_id
            else None,
        )
        for target in recovery.targets
    ]
    accepted = {row["target_id"]: row for row in rows if row["accepted"]}
    option_to_target = {b.option_id: b.target_id for b in job.bindings}
    prepared = dict(
        request_sha256=identity,
        scenario=None,
        physical_candidates=rows,
        option_to_target=option_to_target,
        schedule_rejections={},
        arrival_utc={},
    )
    raw = job.campaign.model_dump(mode="json")
    raw["objectives"].append(
        dict(
            metric="recovery_propellant_kg",
            unit="kg",
            direction="minimize",
            weight=job.fuel_penalty_weight,
            scale=job.fuel_scale_kg,
        )
    )
    evidence_id = "h6-" + identity[:24]
    raw["sources"].append(
        dict(
            source_id=evidence_id,
            kind="derived",
            description="H5 planar screening and explicit target/site bindings; no coordinate conversion.",
            version="h6-recovery-v1",
            sha256=identity,
            parent_source_ids=[s.source_id for s in job.campaign.sources],
        )
    )
    candidates = []
    for candidate in job.campaign.candidates:
        row = candidate.model_dump(mode="json")
        row["metrics"]["recovery_propellant_kg"] = 0.0
        if candidate.option_id in option_to_target:
            target_id = option_to_target[candidate.option_id]
            if target_id not in accepted:
                prepared["schedule_rejections"][candidate.option_id] = (
                    "physical_rejection"
                )
                continue
            checked = accepted[target_id]["fine"]
            arrival = datetime.fromisoformat(
                checked["state"]["epoch_utc"].replace("Z", "+00:00")
            )
            delta = arrival - candidate.window.start_utc
            end = candidate.window.end_utc + delta
            if (
                not job.campaign.window.start_utc
                <= arrival
                < end
                <= job.campaign.window.end_utc
            ):
                prepared["schedule_rejections"][candidate.option_id] = (
                    "outside_campaign_window"
                )
                continue
            row["window"] = dict(start_utc=arrival.isoformat(), end_utc=end.isoformat())
            for demand, original in zip(row["demands"], candidate.demands):
                demand["window"] = dict(
                    start_utc=(original.window.start_utc + delta).isoformat(),
                    end_utc=(original.window.end_utc + delta).isoformat(),
                )
            row["metrics"]["recovery_propellant_kg"] = checked["propellant_used_kg"]
            row["source_ids"].append(evidence_id)
            prepared["arrival_utc"][candidate.option_id] = arrival
        elif candidate.window.start_utc < checkpoint.estimated_state.epoch_utc:
            prepared["schedule_rejections"][candidate.option_id] = (
                "past_unexecuted_option"
            )
            continue
        candidates.append(row)
    if {row["mission_id"] for row in candidates} != {
        m.mission_id for m in job.campaign.missions
    }:
        return prepared
    raw["candidates"] = candidates
    remaining = {row["option_id"] for row in candidates}
    # Restrict existing at-most-one rules to the surviving choice domain. A
    # removed option is never selected; fewer than two survivors is a tautology.
    constraints = []
    for rule in job.campaign.incompatibilities:
        options = sorted(set(rule.option_ids) & remaining)
        if len(options) >= 2:
            constraints.append(
                dict(
                    constraint_id=rule.constraint_id, kind=rule.kind, option_ids=options
                )
            )
    originals = {c.option_id: c for c in job.campaign.candidates}
    known_ids = {c["constraint_id"] for c in constraints}
    for dependency in job.dependencies:
        if dependency.follower_option_id not in remaining:
            continue
        follower = originals[dependency.follower_option_id]
        for landing_id, arrival in prepared["arrival_utc"].items():
            if follower.window.start_utc < arrival + timedelta(
                seconds=dependency.minimum_delay_s
            ):
                cid = "h6-dependency-" + _digest(
                    [identity, landing_id, follower.option_id]
                )
                if cid in known_ids:
                    raise ValueError(
                        "Generated dependency identity conflicts with an existing rule"
                    )
                known_ids.add(cid)
                constraints.append(
                    dict(
                        constraint_id=cid,
                        kind="at_most_one",
                        option_ids=[landing_id, follower.option_id],
                    )
                )
    raw["incompatibilities"] = constraints
    prepared["scenario"] = Scenario.model_validate(raw)
    return prepared


def _classical(prepared):
    if prepared["scenario"] is None:
        return None
    return solve_exact(prepared["scenario"], max_evaluations=4096)


class RecoveryCoordinator:
    """One local worker and no unbounded queue. Caller supplies the latest snapshot."""

    def __init__(self, *, runner=None, clock=None):
        self.runner = run_qaoa if runner is None else runner
        self.clock = monotonic if clock is None else clock
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="h6-qaoa")
        self.active = None
        self.closed = False

    def submit(
        self,
        job_request: HybridRecoveryRequest,
        budget: QuantumBudget = QuantumBudget(),
    ) -> RecoveryTicket:
        if self.closed:
            raise RuntimeError("Coordinator is closed")
        started = perf_counter()
        budget = QuantumBudget.model_validate(budget.model_dump())
        prepared = prepare_problem(job_request)
        fallback = _classical(prepared)
        now = self.clock()
        future = None
        if fallback is None or fallback.plan is None:
            reason = "infeasible"
        elif self.active is not None and not self.active.done():
            reason = "worker_busy"
        else:
            reason = "submitted"
            # Isolate mutable nested metric dictionaries from the caller/ticket.
            problem = Scenario.model_validate(prepared["scenario"].model_dump())
            future = self.executor.submit(
                self.runner, problem, **budget.model_dump(exclude={"deadline_s"})
            )
            self.active = future
        return RecoveryTicket(
            prepared["request_sha256"],
            prepared,
            fallback,
            future,
            now + budget.deadline_s,
            reason,
            budget,
            now,
            perf_counter() - started,
        )

    def resolve(
        self, ticket: RecoveryTicket, current_request: HybridRecoveryRequest
    ) -> dict:
        started = perf_counter()
        # No fallback is reused blindly: changes to fuel, observations, policies,
        # campaign resources or time trigger a complete fresh physical/static check.
        prepared = prepare_problem(current_request)
        fallback = _classical(prepared)
        chosen = None if fallback is None else fallback.plan
        validation = (
            None if chosen is None else validate_plan(chosen, prepared["scenario"])
        )
        if validation is not None and validation.status != "feasible":
            chosen, validation = None, None
        source, quantum, error = "classical_fallback", None, None
        if prepared["request_sha256"] != ticket.request_sha256:
            reason = "stale_snapshot"
        elif self.clock() >= ticket.deadline:
            reason = "deadline"
            if ticket.future is not None:
                ticket.future.cancel()  # Running Python work is bounded, not forcibly killed.
        elif ticket.future is None:
            reason = ticket.launch_reason
        elif ticket.future.cancelled():
            reason = "cancelled"
        elif not ticket.future.done():
            reason = "pending"
        else:
            try:
                raw = ticket.future.result()
            except FutureCancelled:
                reason = "cancelled"
            except ValueError as exc:
                reason, error = "unsupported", str(exc)
            except RuntimeError as exc:
                reason, error = "worker_error", str(exc)
            else:
                reason = "invalid_quantum"
                try:
                    quantum = QaoaResult.model_validate(raw.model_dump())
                    problem = prepared["scenario"]
                    if problem is None or quantum.input_fingerprint != fingerprint(
                        problem
                    ):
                        raise ValueError(
                            "Quantum input fingerprint differs from the current problem"
                        )
                    budget = ticket.budget
                    sizes = [
                        sum(c.mission_id == m.mission_id for c in problem.candidates)
                        for m in problem.missions
                    ]
                    expected_qubits = sum(size.bit_length() - 1 for size in sizes)
                    if (
                        any(size < 1 or size & (size - 1) for size in sizes)
                        or not 1
                        <= quantum.logical_qubits
                        == expected_qubits
                        <= budget.max_qubits
                        or quantum.qubo_variables != len(problem.candidates)
                        or len(quantum.samples) != 2**expected_qubits
                        or {s.basis_index for s in quantum.samples}
                        != set(range(2**expected_qubits))
                        or any(not 0 <= s.probability <= 1 for s in quantum.samples)
                        or abs(sum(s.probability for s in quantum.samples) - 1) > 1e-9
                    ):
                        raise ValueError(
                            "Quantum register or probability metadata is inconsistent"
                        )
                    if (
                        quantum.depth != budget.depth
                        or quantum.shots != budget.shots
                        or quantum.seed != budget.seed
                        or quantum.objective_evaluations != budget.evaluations
                        or quantum.logical_qubits > budget.max_qubits
                        or sum(s.count for s in quantum.samples) != quantum.shots
                        or any(s.count < 0 for s in quantum.samples)
                    ):
                        raise ValueError("Quantum execution/sample budget mismatch")
                    proposal = quantum.best_sampled_plan
                    if proposal is None:
                        reason = "no_sampled_plan"
                    else:
                        proposal = Plan.model_validate(proposal.model_dump())
                        if proposal.execution_mode != "noiseless_simulator" or not any(
                            s.count > 0
                            and set(s.selected_option_ids)
                            == set(proposal.selected_option_ids)
                            for s in quantum.samples
                        ):
                            raise ValueError(
                                "Proposed plan is not an observed simulator outcome"
                            )
                        report = validate_plan(proposal, problem)
                        if report.status != "feasible":
                            raise ValueError(
                                "Quantum proposal violates original constraints"
                            )
                        if validation is not None and Fraction(
                            report.exact_score
                        ) < Fraction(validation.exact_score):
                            reason = "inferior_quantum"
                        else:
                            chosen, validation = proposal, report
                            source, reason = "quantum", "quantum_accepted"
                except (ValueError, TypeError, AttributeError) as exc:
                    reason, error = "invalid_quantum", str(exc)
        # Validation and data preparation consume time too. Never accept a result
        # just because it finished before its deadline if dispatch is already late.
        if source == "quantum" and self.clock() >= ticket.deadline:
            source, reason = "classical_fallback", "deadline"
            chosen = None if fallback is None else fallback.plan
            validation = (
                None if chosen is None else validate_plan(chosen, prepared["scenario"])
            )
        selected_target = None
        if chosen is not None:
            selected_target = next(
                prepared["option_to_target"][oid]
                for oid in chosen.selected_option_ids
                if oid in prepared["option_to_target"]
            )
        return dict(
            status="selected" if chosen is not None else "infeasible",
            source=source,
            reason=reason,
            request_sha256=prepared["request_sha256"],
            selected_target_id=selected_target,
            plan=None if chosen is None else chosen.model_dump(mode="json"),
            validation=None
            if validation is None
            else validation.model_dump(mode="json"),
            campaign=None
            if prepared["scenario"] is None
            else prepared["scenario"].model_dump(mode="json"),
            physical_candidates=prepared["physical_candidates"],
            schedule_rejections=prepared["schedule_rejections"],
            quantum=None if quantum is None else quantum.model_dump(mode="json"),
            error=error,
            classical_status=None if fallback is None else fallback.status,
            preparation_s=ticket.preparation_s,
            revalidation_s=perf_counter() - started,
            elapsed_since_submission_s=max(0.0, self.clock() - ticket.submitted_at),
        )

    def wait(
        self, ticket: RecoveryTicket, current_request: HybridRecoveryRequest
    ) -> dict:
        if ticket.future is not None and not ticket.future.cancelled():
            remaining = max(0.0, ticket.deadline - self.clock())
            try:
                ticket.future.result(timeout=remaining)
            except (FutureTimeout, FutureCancelled, ValueError, RuntimeError):
                # resolve reports these explicit worker/deadline outcomes and checks
                # current fallback validity. Unexpected programming errors propagate.
                pass
        return self.resolve(ticket, current_request)

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)


def replay_hybrid(
    recovery: RecoveryScenario,
    campaign: Scenario,
    landing_mission_id: str,
    bindings: tuple[LandingBinding, ...],
    dependencies: tuple[LandingDependency, ...],
    budget: QuantumBudget = QuantumBudget(),
    decision_timings: list | None = None,
) -> dict:
    """Offline replay: simulated flight clock is paused while selection runs.

    This demonstrates guarded integration, not a real-time flight controller.
    Live callers must advance their estimator and provide the latest request to resolve.
    """
    coordinator = RecoveryCoordinator()
    results = []

    def select(decision):
        request = HybridRecoveryRequest(
            recovery=recovery,
            checkpoint=checkpoint_from_decision(decision),
            campaign=campaign,
            landing_mission_id=landing_mission_id,
            bindings=bindings,
            dependencies=dependencies,
        )
        result = coordinator.wait(coordinator.submit(request, budget), request)
        results.append(result)
        return result["selected_target_id"]

    try:
        replay = replay_recovery(recovery, target_selector=select)
    finally:
        coordinator.close()
    return dict(
        replay=replay,
        decisions=results,
        execution_mode="noiseless_simulator",
        hardware_jobs=0,
        simulated_clock_paused_during_planning=True,
    )
