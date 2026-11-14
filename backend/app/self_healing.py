"""H5 deterministic planar fault replay and classical candidate replanning."""

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from itertools import groupby

from typing import Annotated, Literal

import numpy as np

from pydantic import Field, ValidationError, model_validator

from shared.hybrid import Contract, Identifier, NonNegative, Source
from .terminal_descent import TerminalRequest, TerminalState, propagate_terminal

Sigma2 = tuple[
    Annotated[float, Field(strict=True, ge=0, le=100)],
    Annotated[float, Field(strict=True, ge=0, le=100)],
]


class NavigationUncertainty(Contract):
    position_std_m: Sigma2 = (0.0, 0.0)
    velocity_std_mps: Sigma2 = (0.0, 0.0)
    thrust_loss_fraction_bound: Annotated[float, Field(strict=True, ge=0, le=1)] = 0.0
    fuel_shortfall_bound_kg: Annotated[float, Field(strict=True, ge=0, le=1000)] = 0.0
    samples: Annotated[int, Field(strict=True, ge=1, le=32)] = 8
    source: Source


class ThrustFault(Contract):
    kind: Literal["thrust_loss"]
    available_thrust_n: NonNegative


class FuelFault(Contract):
    kind: Literal["propellant_loss"]
    amount_kg: NonNegative


class SensorFault(Contract):
    kind: Literal["sensor_degradation"]
    sensor_id: Identifier
    position_std_m: Sigma2
    velocity_std_mps: Sigma2 = (0.0, 0.0)


class LandingHazard(Contract):
    kind: Literal["landing_hazard"]
    start_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    end_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]

    @model_validator(mode="after")
    def ordered_interval(self):
        if self.start_downrange_m >= self.end_downrange_m:
            raise ValueError("Landing hazard must have positive extent")
        return self


class CommunicationFault(Contract):
    kind: Literal["communication_outage"]
    end_utc: datetime


class RecoveryEvent(Contract):
    event_id: Identifier
    vehicle_id: Identifier
    epoch_utc: datetime
    sequence: Annotated[int, Field(strict=True, ge=0)]
    payload: Annotated[
        ThrustFault | FuelFault | SensorFault | LandingHazard | CommunicationFault,
        Field(discriminator="kind"),
    ]
    source: Source


class RecoveryTarget(Contract):
    target_id: Identifier
    downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    touchdown_deadline_utc: datetime


class RecoveryScenario(Contract):
    scenario_id: Identifier
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17
    initial_request: TerminalRequest
    nominal_target_id: Identifier
    targets: Annotated[tuple[RecoveryTarget, ...], Field(min_length=1, max_length=8)]
    events: Annotated[tuple[RecoveryEvent, ...], Field(max_length=8)] = ()
    uncertainty: NavigationUncertainty
    require_link_for_retarget: Annotated[bool, Field(strict=True)] = True

    @model_validator(mode="after")
    def validate_replay(self):
        start = self.initial_request.initial_state.epoch_utc
        targets = {t.target_id: t for t in self.targets}
        if len(targets) != len(self.targets) or self.nominal_target_id not in targets:
            raise ValueError("Targets must be unique and include the nominal target")
        nominal = targets[self.nominal_target_id]
        if (
            nominal.downrange_m != self.initial_request.target_downrange_m
            or nominal.touchdown_deadline_utc
            != start + timedelta(seconds=self.initial_request.duration_s)
        ):
            raise ValueError(
                "Nominal target must match the initial request and deadline"
            )
        if any(
            not 5 <= (t.touchdown_deadline_utc - start).total_seconds() <= 300
            for t in self.targets
        ):
            raise ValueError(
                "Target deadlines must be 5 to 300 seconds from the initial epoch"
            )
        end = max(t.touchdown_deadline_utc for t in self.targets)
        if len({e.event_id for e in self.events}) != len(self.events):
            raise ValueError("Duplicate event identifier")
        if len({(e.epoch_utc, e.sequence) for e in self.events}) != len(self.events):
            raise ValueError("Simultaneous events require distinct sequence numbers")
        ordered = tuple(sorted(self.events, key=lambda e: (e.epoch_utc, e.sequence)))
        available = self.initial_request.vehicle.max_thrust_n
        for event in ordered:
            if not start <= event.epoch_utc < end:
                raise ValueError("Event lies outside the simulation horizon")
            if event.vehicle_id != self.initial_request.initial_state.vehicle_id:
                raise ValueError("Event vehicle does not match the simulated vehicle")
            fault = event.payload
            if (
                isinstance(fault, CommunicationFault)
                and fault.end_utc <= event.epoch_utc
            ):
                raise ValueError("Communication outage must end after it starts")
            if isinstance(fault, ThrustFault):
                if fault.available_thrust_n > available:
                    raise ValueError("Thrust loss cannot increase available thrust")
                available = fault.available_thrust_n
        object.__setattr__(self, "events", ordered)
        object.__setattr__(
            self, "targets", tuple(sorted(self.targets, key=lambda t: t.target_id))
        )
        return self


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _summary(run):
    last = run["trace"][-1]
    return {
        "termination": run["termination"],
        "feasible": run["feasible"],
        "reasons": list(run["reasons"]),
        "state": last["state"],
        "t_s": last["t_s"],
        "downrange_m": last["downrange_m"],
        "vertical_speed_mps": last["vertical_speed_mps"],
        "horizontal_speed_mps": last["horizontal_speed_mps"],
        "propellant_used_kg": run["propellant_used_kg"],
        "request_sha256": run["request_sha256"],
    }


def _hazard_reasons(run, hazards):
    if run["termination"] == "contact" and any(
        a <= run["trace"][-1]["downrange_m"] <= b for a, b in hazards
    ):
        return ["landing_hazard"]
    return []


def _request(base, state, target):
    raw = base.model_dump()
    raw.update(
        initial_state=state.model_dump(),
        target_downrange_m=target.downrange_m,
        duration_s=(target.touchdown_deadline_utc - state.epoch_utc).total_seconds(),
    )
    return TerminalRequest.model_validate(raw)


def _draws(spec, seed, revision):
    rng = np.random.default_rng(np.random.SeedSequence([seed, revision, 1]))
    return [
        dict(
            position_offset_m=rng.normal(0, spec.position_std_m).tolist(),
            velocity_offset_mps=rng.normal(0, spec.velocity_std_mps).tolist(),
            thrust_fraction=float(rng.uniform(1 - spec.thrust_loss_fraction_bound, 1)),
            fuel_shortfall_kg=float(rng.uniform(0, spec.fuel_shortfall_bound_kg)),
        )
        for _ in range(spec.samples)
    ]


def _screen(base, estimate, target, available, hazards, draws, policy_reason):
    record = dict(
        target_id=target.target_id,
        accepted=False,
        reasons=[],
        coarse_feasible=False,
        fine_feasible=False,
        numerically_consistent=False,
        uncertainty=dict(sample_count=0, pass_count=0, failure_counts={}, outcomes=[]),
    )
    reasons = record["reasons"]
    if (target.touchdown_deadline_utc - estimate.epoch_utc).total_seconds() < 5:
        reasons.append("insufficient_time")
    if any(a <= target.downrange_m <= b for a, b in hazards):
        reasons.append("landing_hazard")
    if policy_reason:
        reasons.append(policy_reason)
    if reasons:
        return record
    try:
        job = _request(base, estimate, target)
    except ValidationError:
        reasons.append("invalid_estimated_state")
        return record
    coarse = propagate_terminal(job, thrust_limit_n=available)
    fine = propagate_terminal(
        job, thrust_limit_n=available, integration_dt_s=job.dt_s / 2
    )
    a, b = coarse["trace"][-1], fine["trace"][-1]
    differences = dict(
        position_m=float(
            np.linalg.norm(
                np.array(a["state"]["position_m"]) - b["state"]["position_m"]
            )
        ),
        velocity_mps=float(
            np.linalg.norm(
                np.array(a["state"]["velocity_mps"]) - b["state"]["velocity_mps"]
            )
        ),
        fuel_kg=abs(a["state"]["propellant_kg"] - b["state"]["propellant_kg"]),
        time_s=abs(a["t_s"] - b["t_s"]),
    )
    consistent = (
        coarse["termination"] == fine["termination"]
        and coarse["reasons"] == fine["reasons"]
        and differences["position_m"] <= 0.5
        and differences["velocity_mps"] <= 0.1
        and differences["fuel_kg"] <= 0.05
        and differences["time_s"] <= 0.2
    )
    record.update(
        coarse_feasible=coarse["feasible"],
        fine_feasible=fine["feasible"],
        numerically_consistent=consistent,
        numerical_differences=differences,
        coarse=_summary(coarse),
        fine=_summary(fine),
    )
    reasons.extend(
        sorted(
            set(
                coarse["reasons"]
                + fine["reasons"]
                + _hazard_reasons(coarse, hazards)
                + _hazard_reasons(fine, hazards)
            )
        )
    )
    if not consistent:
        reasons.append("numerical_disagreement")
    outcomes = []
    for index, draw in enumerate(draws):
        state = estimate.model_dump()
        state.update(
            position_m=(
                np.array(estimate.position_m) + draw["position_offset_m"]
            ).tolist(),
            velocity_mps=(
                np.array(estimate.velocity_mps) + draw["velocity_offset_mps"]
            ).tolist(),
            propellant_kg=max(0.0, estimate.propellant_kg - draw["fuel_shortfall_kg"]),
        )
        try:
            truth = TerminalState.model_validate(state)
            sampled_job = _request(base, truth, target)
        except ValidationError:
            outcomes.append(
                dict(sample=index, accepted=False, reasons=["invalid_sampled_state"])
            )
            continue
        # The latent truth differs from the estimate. The controller sees the same
        # estimated initial navigation state, not each sample's true state.
        run = propagate_terminal(
            sampled_job,
            integration_dt_s=job.dt_s / 2,
            thrust_limit_n=available * draw["thrust_fraction"],
            position_bias_m=-np.array(draw["position_offset_m"]),
            velocity_bias_mps=-np.array(draw["velocity_offset_mps"]),
        )
        failures = run["reasons"] + _hazard_reasons(run, hazards)
        last = run["trace"][-1]
        outcomes.append(
            dict(
                sample=index,
                accepted=not failures,
                reasons=failures,
                downrange_m=last["downrange_m"],
                propellant_kg=last["state"]["propellant_kg"],
            )
        )
    passed = sum(row["accepted"] for row in outcomes)
    failures = Counter(reason for row in outcomes for reason in row["reasons"])
    record["uncertainty"] = dict(
        sample_count=len(outcomes),
        pass_count=passed,
        failure_counts=dict(sorted(failures.items())),
        outcomes=outcomes,
    )
    if passed != len(outcomes):
        reasons.append("uncertainty")
    record["accepted"] = not reasons
    return record


def replay_recovery(
    scenario: RecoveryScenario, *, replan: bool = True, target_selector=None
) -> dict:
    """Run synthetic faults on a planar vehicle; no hardware, live telemetry or safe-abort claim."""
    scenario = RecoveryScenario.model_validate(scenario.model_dump())
    base = scenario.initial_request
    state = base.initial_state
    start = state.epoch_utc
    targets = {t.target_id: t for t in scenario.targets}
    selected = scenario.nominal_target_id
    available = base.vehicle.max_thrust_n
    uncertainty = scenario.uncertainty
    sensor_id = "initial-navigation"
    revision = state.revision
    hazards, log, decisions, trace = [], [], [], []
    outage_end = start
    burned, lost = 0.0, 0.0
    status, final_reasons, terminal_summary = "running", [], None
    scenario_hash = _digest(
        dict(model_version="h5-replay-v1", scenario=scenario.model_dump(mode="json"))
    )

    def navigation_bias():
        rng = np.random.default_rng(
            np.random.SeedSequence([scenario.seed, revision, 0])
        )
        return rng.normal(0, uncertainty.position_std_m), rng.normal(
            0, uncertainty.velocity_std_mps
        )

    position_bias, velocity_bias = navigation_bias()

    def estimated():
        # Synthetic observation is the estimate (identity estimator); no truth correction.
        return state.model_copy(
            update={
                "position_m": tuple(np.array(state.position_m) + position_bias),
                "velocity_mps": tuple(np.array(state.velocity_mps) + velocity_bias),
            }
        )

    def decide():
        nonlocal selected, status, final_reasons
        estimate = estimated()
        draws = _draws(uncertainty, scenario.seed, revision)
        rows = [
            _screen(
                base,
                estimate,
                target,
                available,
                hazards,
                draws,
                "communication_required"
                if scenario.require_link_for_retarget
                and state.epoch_utc < outage_end
                and target.target_id != selected
                else None,
            )
            for target in scenario.targets
        ]
        accepted = [row for row in rows if row["accepted"]]
        previous = selected
        keep = next((row for row in accepted if row["target_id"] == selected), None)
        choice = keep or min(
            accepted,
            key=lambda row: (row["fine"]["propellant_used_kg"], row["target_id"]),
            default=None,
        )
        selected = choice["target_id"] if choice else None
        snapshot = dict(
            scenario_sha256=scenario_hash,
            revision=revision,
            estimated_state=estimate.model_dump(mode="json"),
            available_thrust_n=available,
            landing_hazards=hazards,
            outage_end_utc=outage_end.isoformat(),
            uncertainty=uncertainty.model_dump(mode="json"),
            previous_target_id=previous,
        )
        decisions.append(
            dict(
                **snapshot,
                input_sha256=_digest(snapshot),
                action="no_feasible_continuation"
                if choice is None
                else "continue"
                if selected == previous
                else "retarget",
                selected_target_id=selected,
                candidates=rows,
                uncertainty_draws=draws,
            )
        )
        if choice is None:
            status = "infeasible"
            final_reasons = ["no_validated_candidate"]

    def fly(until=None):
        nonlocal state, status, final_reasons, terminal_summary, burned
        target = targets[selected]
        remaining = (target.touchdown_deadline_utc - state.epoch_utc).total_seconds()
        if remaining < 5:
            status, final_reasons = "infeasible", ["insufficient_time"]
            return
        job = _request(base, state, target)
        interval = (
            remaining
            if until is None
            else min(remaining, (until - state.epoch_utc).total_seconds())
        )
        if interval <= 0:
            return
        run = propagate_terminal(
            job,
            stop_after_s=interval if interval < remaining else None,
            position_bias_m=position_bias,
            velocity_bias_mps=velocity_bias,
            thrust_limit_n=available,
        )
        for row in run["trace"]:
            epoch = datetime.fromisoformat(
                row["state"]["epoch_utc"].replace("Z", "+00:00")
            )
            trace.append(
                dict(
                    **row, elapsed_s=(epoch - start).total_seconds(), target_id=selected
                )
            )
        burned += run["propellant_used_kg"]
        state = TerminalState.model_validate(run["trace"][-1]["state"])
        terminal_summary = _summary(run)
        if run["termination"] != "paused":
            final_reasons = run["reasons"] + _hazard_reasons(run, hazards)
            terminal_summary.update(
                h4_feasible=run["feasible"],
                feasible=not final_reasons,
                reasons=final_reasons,
            )
            status = "landed" if not final_reasons else "failed"

    groups = [
        (epoch, list(events))
        for epoch, events in groupby(scenario.events, key=lambda e: e.epoch_utc)
    ]
    if not groups or groups[0][0] != start:
        if replan:
            decide()
    for epoch, events in groups:
        if status != "running":
            break
        if epoch > state.epoch_utc:
            fly(epoch)
        if status != "running":
            break
        for event in events:
            before = state.model_dump(mode="json")
            fault = event.payload
            lost_here = 0.0
            if isinstance(fault, ThrustFault):
                available = fault.available_thrust_n
            elif isinstance(fault, FuelFault):
                lost_here = min(state.propellant_kg, fault.amount_kg)
                lost += lost_here
                state = state.model_copy(
                    update={"propellant_kg": state.propellant_kg - lost_here}
                )
            elif isinstance(fault, SensorFault):
                spec = uncertainty.model_dump()
                spec.update(
                    position_std_m=fault.position_std_m,
                    velocity_std_mps=fault.velocity_std_mps,
                    source=event.source.model_dump(),
                )
                uncertainty = NavigationUncertainty.model_validate(spec)
                sensor_id = fault.sensor_id
            elif isinstance(fault, LandingHazard):
                hazards.append((fault.start_downrange_m, fault.end_downrange_m))
            elif isinstance(fault, CommunicationFault):
                outage_end = max(outage_end, fault.end_utc)
            revision += 1
            state = state.model_copy(update={"revision": revision})
            if isinstance(fault, SensorFault):
                position_bias, velocity_bias = navigation_bias()
            estimate = estimated().model_dump(mode="json")
            observation = dict(
                kind="synthetic_observation",
                sensor_id=sensor_id,
                epoch_utc=estimate["epoch_utc"],
                position_m=estimate["position_m"],
                velocity_mps=estimate["velocity_mps"],
                position_std_m=uncertainty.position_std_m,
                velocity_std_mps=uncertainty.velocity_std_mps,
            )
            log.append(
                dict(
                    event_id=event.event_id,
                    event=event.model_dump(mode="json"),
                    before=before,
                    after=state.model_dump(mode="json"),
                    estimated_after=estimate,
                    observation=observation,
                    available_thrust_n=available,
                    lost_propellant_kg=lost_here,
                )
            )
        if replan:
            decide()
    if status == "running":
        fly()
    applied = {entry["event_id"] for entry in log}
    return dict(
        mode="classical_replanning" if replan else "fixed_original_target",
        status=status,
        scenario_sha256=scenario_hash,
        selected_target_id=selected,
        final_state=state.model_dump(mode="json"),
        final_reasons=final_reasons,
        propellant_burned_kg=burned,
        propellant_lost_kg=lost,
        terminal_summary=terminal_summary,
        event_log=log,
        decisions=decisions,
        trace=trace,
        unapplied_event_ids=[
            e.event_id for e in scenario.events if e.event_id not in applied
        ],
    )


def compare_recovery(scenario: RecoveryScenario) -> dict:
    """Replay identical exogenous events and sensor seeds, without hidden truth in planning."""
    validated = RecoveryScenario.model_validate(scenario.model_dump())
    baseline = replay_recovery(validated, replan=False)
    recovery = replay_recovery(validated, replan=True)
    return dict(
        baseline=baseline,
        recovery=recovery,
        comparison=dict(
            baseline_status=baseline["status"],
            recovery_status=recovery["status"],
            same_final_epoch=baseline["final_state"]["epoch_utc"]
            == recovery["final_state"]["epoch_utc"],
            note="Fuel at different stopping epochs is not an efficiency comparison; infeasible means no validated continuation, not a safe abort.",
        ),
    )
