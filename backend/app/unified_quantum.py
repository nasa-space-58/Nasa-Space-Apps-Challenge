"""Opt-in local quantum proposals with full classical completion and admission."""

import hashlib
import json
from datetime import datetime
from fractions import Fraction
from time import perf_counter

import numpy as np
from typing import Annotated, Literal

from pydantic import Field

from shared.hybrid import Contract, Digest, Plan, fingerprint

from .hybrid_quantum import QaoaResult, compile_qubo, compact_model, run_qaoa
from .hybrid_planning import validate_plan
from .hybrid_recovery import prepare_problem
from .campaign_planning import generate_candidates, solve_joint, validate_campaign_plan
from .unified_classical import (
    ClassicalCandidate,
    ClassicalDecision,
    CompletedMission,
    plan_classical,
    accept_classical,
    _derive,
    _ledger,
    _past_options,
    _preserves,
    _score,
    _decision_hash,
)
from .unified_planning import (
    AdapterError,
    PlanningSnapshot,
    UnifiedPlanningEnvelope,
    to_r11_problem,
    to_h6_request,
)


class ProposalBudget(Contract):
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    evaluations: Annotated[int, Field(strict=True, ge=1, le=128)] = 32
    shots: Annotated[int, Field(strict=True, ge=1, le=1024)] = 64
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17
    max_qubits: Annotated[int, Field(strict=True, ge=1, le=6)] = 4
    max_completions: Annotated[int, Field(strict=True, ge=1, le=8)] = 2
    completion_limit_s: Annotated[float, Field(strict=True, gt=0, le=30)] = 5.0


class ProposalAttempt(Contract):
    option_ids: tuple[str, ...]
    sample_count: int
    status: Literal["accepted", "rejected", "limit_reached"]
    reason: str | None = None
    candidate: ClassicalCandidate | None = None
    completion_s: float
    admission_s: float


class ProposalMethod(Contract):
    method: Literal["qaoa", "random", "greedy"]
    raw_counts: dict[str, int]
    raw_local_feasible_count: int
    attempts: tuple[ProposalAttempt, ...]
    skipped_by_completion_cap: tuple[str, ...] = ()
    best_decision: ClassicalDecision | None = None
    proposal_s: float


class HybridExperiment(Contract):
    schema_version: Literal[1] = 1
    snapshot_sha256: Digest
    mode: Literal["classical", "local_experiment"]
    budget: ProposalBudget
    classical_baseline: ClassicalDecision
    final_decision: ClassicalDecision
    selected_source: Literal["classical", "qaoa_proposal_classical_completion"]
    selection_reason: str
    unsupported_reason: str | None = None
    quantum: QaoaResult | None = None
    methods: tuple[ProposalMethod, ...] = ()
    option_to_target: dict[str, str] = {}
    frozen_outside_selections: dict[str, str | None] = {}
    elapsed_s: float
    preparation_s: float = 0.0
    hardware_jobs: Literal[0] = 0
    production_policy: Literal["classical_no_go_unchanged"] = (
        "classical_no_go_unchanged"
    )
    result_sha256: Digest


def compare_proposals(
    snapshot: PlanningSnapshot,
    *,
    current: UnifiedPlanningEnvelope,
    now_utc: datetime,
    mode: Literal["classical", "local_experiment"] = "classical",
    budget: ProposalBudget | None = None,
    completed: tuple[CompletedMission, ...] = (),
) -> HybridExperiment:
    """Run a classical baseline; only explicit local experiments sample QAOA."""
    started = perf_counter()
    if mode not in ("classical", "local_experiment"):
        raise ValueError(
            "Only classical or explicitly local_experiment modes are supported"
        )
    budget = ProposalBudget.model_validate((budget or ProposalBudget()).model_dump())
    baseline = plan_classical(
        snapshot,
        current=current,
        now_utc=now_utc,
        completed=completed,
        time_limit_s=budget.completion_limit_s,
    )
    common = dict(
        snapshot_sha256=snapshot.snapshot_sha256,
        mode=mode,
        budget=budget,
        classical_baseline=baseline,
        final_decision=baseline,
        selected_source="classical",
        selection_reason="classical_policy",
    )
    if mode == "classical":
        return _finish(common, started)
    if baseline.status != "feasible":
        common.update(
            unsupported_reason="No complete incumbent to freeze outside choices",
            selection_reason="classical_no_continuation_or_limit",
        )
        return _finish(common, started)
    mark = perf_counter()
    job = to_h6_request(snapshot, current=current, now_utc=now_utc)
    campaign = to_r11_problem(snapshot, current=current, now_utc=now_utc)
    ledger = _ledger(completed, campaign, job, now_utc)
    prepared = prepare_problem(job)
    scenario = prepared["scenario"]
    outside = {
        mid: oid
        for mid, oid in _selected(baseline).plan.selections.items()
        if mid != job.landing_mission_id
    }
    common["frozen_outside_selections"] = outside
    if scenario is None:
        common.update(
            unsupported_reason="No prepared H0 proposal domain",
            selection_reason="unsupported_projection",
            preparation_s=perf_counter() - mark,
        )
        return _finish(common, started)
    common["option_to_target"] = {
        oid: target
        for oid, target in prepared["option_to_target"].items()
        if oid in {c.option_id for c in scenario.candidates}
    }
    try:
        # This audits only the H0 proposal domain. R11 constraints stay intact in
        # completion; they are never advertised as encoded by this Hamiltonian.
        compact = compact_model(compile_qubo(scenario), max_qubits=budget.max_qubits)
    except ValueError as error:
        common.update(
            unsupported_reason=str(error),
            selection_reason="unsupported_projection",
            preparation_s=perf_counter() - mark,
        )
        return _finish(common, started)
    common["preparation_s"] = perf_counter() - mark
    mark = perf_counter()
    quantum = run_qaoa(
        scenario,
        depth=budget.depth,
        evaluations=budget.evaluations,
        shots=budget.shots,
        seed=budget.seed,
        max_qubits=budget.max_qubits,
    )
    quantum = QaoaResult.model_validate(quantum.model_dump())
    if (
        quantum.input_fingerprint != fingerprint(scenario)
        or quantum.shots != budget.shots
        or quantum.logical_qubits != compact.logical_qubits
    ):
        raise ValueError("Quantum result does not match the bounded proposal request")
    q_time = perf_counter() - mark
    q_counts = {}
    selections = {}
    for sample in quantum.samples:
        if type(sample.count) is not int or sample.count < 0:
            raise ValueError("Quantum sample counts must be nonnegative integers")
        # The native probability report includes unsampled basis states. They
        # are not proposals and must not consume a classical completion slot.
        if sample.count == 0:
            continue
        key = _key(sample.selected_option_ids)
        q_counts[key] = q_counts.get(key, 0) + sample.count
        selections[key] = sample.selected_option_ids
    if sum(q_counts.values()) != budget.shots:
        raise ValueError("Quantum sample counts do not sum to the requested shots")
    choices = [
        tuple(
            compact.qubo.option_ids[i]
            for i in range(len(compact.qubo.option_ids))
            if mask & (1 << i)
        )
        for mask in compact.one_hot_masks
    ]
    for ids in choices:
        selections[_key(ids)] = ids
    mark = perf_counter()
    draws = np.random.default_rng(
        np.random.SeedSequence([budget.seed, 401])
    ).multinomial(budget.shots, np.full(len(choices), 1 / len(choices)))
    random_counts = {
        _key(ids): int(count) for ids, count in zip(choices, draws) if count
    }
    random_time = perf_counter() - mark
    mark = perf_counter()
    ranked = sorted(
        range(len(choices)), key=lambda i: (compact.normalized_energies[i], choices[i])
    )
    top = ranked[: min(budget.max_completions, budget.shots, len(ranked))]
    greedy_counts = {
        _key(choices[i]): budget.shots // len(top) + (rank < budget.shots % len(top))
        for rank, i in enumerate(top)
    }
    greedy_time = perf_counter() - mark
    methods = []
    for name, counts, proposal_time in (
        ("qaoa", q_counts, q_time),
        ("random", random_counts, random_time),
        ("greedy", greedy_counts, greedy_time),
    ):
        order = sorted(counts, key=lambda key: (-counts[key], key))
        attempts, decisions = [], []
        feasible_count = sum(
            count
            for key, count in counts.items()
            if _local_feasible(selections[key], scenario)
        )
        for key in order[: budget.max_completions]:
            attempt, decision = _complete(
                snapshot,
                current,
                now_utc,
                prepared,
                selections[key],
                counts[key],
                outside,
                ledger,
                budget,
            )
            attempts.append(attempt)
            if decision is not None:
                decisions.append(decision)
        best = min(
            decisions,
            key=lambda d: (
                -Fraction(_selected(d).comparison_score_exact),
                d.selected_target_id,
            ),
            default=None,
        )
        methods.append(
            ProposalMethod(
                method=name,
                raw_counts=counts,
                raw_local_feasible_count=feasible_count,
                attempts=tuple(attempts),
                skipped_by_completion_cap=tuple(order[budget.max_completions :]),
                best_decision=best,
                proposal_s=proposal_time,
            )
        )
    # Classical/random comparisons use exactly the same admission/completion
    # caps. No repair budget, allocations or cached completion is gifted to QAOA.
    references = [baseline] + [
        m.best_decision
        for m in methods
        if m.method != "qaoa" and m.best_decision is not None
    ]
    reference = min(
        references,
        key=lambda d: (
            -Fraction(_selected(d).comparison_score_exact),
            d.selected_target_id,
        ),
    )
    q_best = methods[0].best_decision
    common.update(
        quantum=quantum,
        methods=tuple(methods),
        final_decision=reference,
        selection_reason="no_strict_full_plan_improvement",
    )
    if q_best is not None and Fraction(
        _selected(q_best).comparison_score_exact
    ) > Fraction(_selected(reference).comparison_score_exact):
        common.update(
            final_decision=q_best,
            selected_source="qaoa_proposal_classical_completion",
            selection_reason="strict_validated_full_plan_improvement",
        )
    return _finish(common, started)


def accept_hybrid(
    result: HybridExperiment,
    snapshot: PlanningSnapshot,
    *,
    current: UnifiedPlanningEnvelope,
    now_utc: datetime,
    completed: tuple[CompletedMission, ...] = (),
) -> ClassicalCandidate:
    """Revalidate final physics, campaign and frozen choices; never dispatch hardware."""
    # Check freshness before inspecting any stored success/provenance labels.
    to_r11_problem(snapshot, current=current, now_utc=now_utc)
    checked = HybridExperiment.model_validate(result.model_dump(mode="python"))
    if (
        checked.result_sha256 != _result_hash(checked)
        or checked.snapshot_sha256 != snapshot.snapshot_sha256
    ):
        raise AdapterError(
            "experiment_tampered",
            "Experiment no longer matches its sealed payload/inputs",
        )
    baseline = accept_classical(
        checked.classical_baseline,
        snapshot,
        current=current,
        now_utc=now_utc,
        completed=completed,
    )
    final = accept_classical(
        checked.final_decision,
        snapshot,
        current=current,
        now_utc=now_utc,
        completed=completed,
    )
    if checked.quantum is not None:
        active = current.recovery.landing_mission_id
        expected = {
            mid: oid for mid, oid in baseline.plan.selections.items() if mid != active
        }
        if checked.frozen_outside_selections != expected or any(
            final.plan.selections.get(mid) != oid for mid, oid in expected.items()
        ):
            raise AdapterError(
                "outside_choices_changed", "A proposal altered frozen outside choices"
            )
    if checked.selected_source == "qaoa_proposal_classical_completion":
        q = next((m for m in checked.methods if m.method == "qaoa"), None)
        if (
            checked.mode != "local_experiment"
            or checked.quantum is None
            or q is None
            or q.best_decision != checked.final_decision
        ):
            raise AdapterError(
                "experiment_tampered",
                "Quantum attribution does not match admitted proposal",
            )

    # Stored score labels are not evidence, even when a caller rehashes them.
    def verified_score(candidate):
        return Fraction(
            _score(
                snapshot.envelope.campaign,
                snapshot.envelope.recovery,
                candidate.validation,
                candidate.physical,
            )
        )

    final_score = verified_score(final)
    reference_score = verified_score(baseline)
    if final_score < reference_score:
        raise AdapterError(
            "plan_regression", "Final plan is worse than the classical baseline"
        )
    if checked.selected_source == "qaoa_proposal_classical_completion":
        for method in checked.methods:
            if method.method != "qaoa" and method.best_decision is not None:
                candidate = accept_classical(
                    method.best_decision,
                    snapshot,
                    current=current,
                    now_utc=now_utc,
                    completed=completed,
                )
                if any(
                    candidate.plan.selections.get(mid) != oid
                    for mid, oid in expected.items()
                ):
                    raise AdapterError(
                        "outside_choices_changed",
                        "Matched reference altered fixed choices",
                    )
                reference_score = max(reference_score, verified_score(candidate))
        if final_score <= reference_score:
            raise AdapterError(
                "no_strict_improvement",
                "Quantum attribution requires a strictly better independently checked full plan",
            )
    return final.model_copy(update={"comparison_score_exact": str(final_score)})


def _key(ids):
    return ids[0] if len(ids) == 1 else json.dumps(ids, separators=(",", ":"))


def _selected(decision):
    return next(
        row
        for row in decision.candidates
        if row.target_id == decision.selected_target_id
    )


def _result_hash(result):
    raw = result.model_dump(mode="json", exclude={"result_sha256"})
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _finish(common, started):
    result = HybridExperiment(
        **common, elapsed_s=perf_counter() - started, result_sha256="0" * 64
    )
    return result.model_copy(update={"result_sha256": _result_hash(result)})


def _local_feasible(ids, scenario):
    try:
        plan = Plan(
            plan_id="local-proposal",
            scenario_id=scenario.scenario_id,
            scenario_revision=scenario.revision,
            input_fingerprint=fingerprint(scenario),
            state_revisions={s.state_id: s.revision for s in scenario.states},
            selected_option_ids=ids,
            execution_mode="noiseless_simulator",
            solver_name="local-proposal-validator",
        )
        return validate_plan(plan, scenario).status == "feasible"
    except ValueError:
        return False


def _complete(snapshot, current, now, prepared, ids, count, outside, ledger, budget):
    mark = perf_counter()

    def rejected(reason, status="rejected", candidate=None):
        return ProposalAttempt(
            option_ids=ids,
            sample_count=count,
            status=status,
            reason=reason,
            candidate=candidate,
            completion_s=perf_counter() - mark,
            admission_s=0.0,
        ), None

    if len(ids) != 1 or ids[0] not in prepared["option_to_target"]:
        return rejected("unknown_proposal")
    if not _local_feasible(ids, prepared["scenario"]):
        return rejected("local_constraints")
    physical = next(
        (
            row
            for row in prepared["physical_candidates"]
            if row["target_id"] == prepared["option_to_target"][ids[0]]
        ),
        None,
    )
    if physical is None or not physical["accepted"]:
        return rejected("physical_rejection")
    problem, option_id, reason = _derive(snapshot, prepared, physical)
    if reason:
        return rejected(reason)
    candidates = generate_candidates(problem).candidates
    excluded = set(_past_options(problem, ledger, now))
    excluded.update(
        c.option_id
        for c in candidates
        if c.mission_id in outside and outside[c.mission_id] is None
    )
    forced = {
        option_id,
        *(oid for oid in outside.values() if oid is not None),
        *(c.option_id for c in ledger),
    }
    outcome = solve_joint(
        problem,
        forced_option_ids=tuple(sorted(forced)),
        excluded_option_ids=tuple(sorted(excluded)),
        time_limit_s=budget.completion_limit_s,
    )
    if outcome.plan is None:
        return rejected(
            outcome.status,
            "limit_reached" if outcome.status == "limit_reached" else "rejected",
        )
    report = validate_campaign_plan(outcome.plan, problem)
    if report.status != "feasible":
        return rejected("campaign_validation_failed")
    if any(outcome.plan.selections.get(mid) != oid for mid, oid in outside.items()):
        return rejected("outside_choices_changed")
    if not _preserves(outcome.plan, ledger):
        return rejected("completed_allocation_lock_unsupported", "limit_reached")
    job = snapshot.envelope.recovery
    candidate = ClassicalCandidate(
        target_id=physical["target_id"],
        status="feasible",
        physical=physical,
        problem=problem,
        plan=outcome.plan,
        validation=report,
        solver_status=outcome.status,
        comparison_score_exact=_score(
            snapshot.envelope.campaign, job, report, physical
        ),
    )
    decision = ClassicalDecision(
        snapshot_sha256=snapshot.snapshot_sha256,
        completed=ledger,
        candidates=(candidate,),
        selected_target_id=candidate.target_id,
        status="feasible",
        optimum_proven_over_screened_targets=False,
        elapsed_s=perf_counter() - mark,
        decision_sha256="0" * 64,
    )
    decision = decision.model_copy(update={"decision_sha256": _decision_hash(decision)})
    completion_s = perf_counter() - mark
    admission_start = perf_counter()
    accepted = accept_classical(
        decision, snapshot, current=current, now_utc=now, completed=ledger
    )
    admission_s = perf_counter() - admission_start
    return ProposalAttempt(
        option_ids=ids,
        sample_count=count,
        status="accepted",
        candidate=accepted,
        completion_s=completion_s,
        admission_s=admission_s,
    ), decision
