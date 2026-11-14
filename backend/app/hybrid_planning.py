"""H1 static mission-option planning. No physics, event replay or quantum calls."""

import hashlib
import json
import math
from collections import Counter
from fractions import Fraction
from itertools import islice, product
from time import perf_counter
from typing import Literal

from shared.hybrid import (
    Candidate,
    Contract,
    Demand,
    Identifier,
    NonNegative,
    Number,
    Plan,
    Scenario,
    Site,
    TimeWindow,
    fingerprint,
    validate_plan_references,
)


class CandidateInput(Contract):
    """Explicit site/time alternative; all metrics and demands supplied by caller."""

    mission_id: Identifier
    site: Site
    window: TimeWindow
    demands: tuple[Demand, ...] = ()
    metrics: dict[Identifier, Number | None]
    source_ids: tuple[Identifier, ...]


class Issue(Contract):
    """A constraint or unsupported-input explanation; quantities retain their unit."""

    code: str
    message: str
    option_ids: tuple[str, ...] = ()
    mission_id: str | None = None
    resource_id: str | None = None
    window: TimeWindow | None = None
    required: str | None = None
    capacity: str | None = None
    unit: str | None = None


class ValidationReport(Contract):
    """Evidence against original static rules, separate from H0's unvalidated Plan."""

    status: Literal["feasible", "infeasible", "incomplete", "unsupported"]
    input_fingerprint: str
    selected_option_ids: tuple[str, ...]
    issues: tuple[Issue, ...] = ()
    score: Number | None = None
    exact_score: str | None = None
    metric_totals: dict[str, Number] = {}


class SolverResult(Contract):
    """Counts cover only evaluated assignments; optimality requires complete search."""

    status: Literal[
        "optimal",
        "infeasible",
        "incomplete",
        "unsupported",
        "limit_reached",
        "proposal",
    ]
    solver_name: str
    input_fingerprint: str
    total_combinations: int
    evaluated_combinations: int = 0
    feasible_count: int = 0
    infeasible_count: int = 0
    search_complete: bool = False
    optimal_plan_count: int | None = None
    plan: Plan | None = None
    validation: ValidationReport | None = None
    issues: tuple[Issue, ...] = ()
    failure_counts: dict[str, int] = {}
    elapsed_s: NonNegative = 0
    max_evaluations: int | None = None


def generate_candidates(inputs: tuple[CandidateInput, ...]) -> tuple[Candidate, ...]:
    """Assign stable content IDs to explicit options; reject empty/duplicate input.

    Return H0 candidates, preserving MOON_ME sites, UTC windows, units and sources.
    Does not filter feasibility or infer metrics. Raises ValueError for invalid rows.
    """
    if not inputs:
        raise ValueError("At least one candidate input is required")
    candidates = {}
    for row in inputs:
        checked = CandidateInput.model_validate(row.model_dump(mode="python"))
        candidate = Candidate(option_id="pending", **checked.model_dump())
        raw = candidate.model_dump(mode="json", exclude={"option_id"})
        encoded = json.dumps(
            _canonical_numbers(raw), sort_keys=True, separators=(",", ":")
        )
        option_id = "option-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if option_id in candidates:
            raise ValueError("Duplicate candidate input")
        candidates[option_id] = Candidate(option_id=option_id, **checked.model_dump())
    return tuple(candidates[key] for key in sorted(candidates))


def validate_plan(plan: Plan, scenario: Scenario) -> ValidationReport:
    """Check H0 identity, selection, deadlines, resources and exclusions.

    Return static feasibility with explanations and normalized dimensionless score.
    Reject malformed or stale input with ValueError; report events as unsupported.
    Unknown selected metrics/capacities are incomplete, never assumed feasible.
    """
    scenario = _snapshot(scenario)
    validate_plan_references(plan, scenario)
    return _evaluate(plan, scenario, fingerprint(scenario))


def solve_exact(scenario: Scenario, *, max_evaluations: int = 10_000) -> SolverResult:
    """Enumerate bounded assignments; maximize H0 weighted normalized objectives.

    Return an incumbent and counts, with optimality only after full enumeration.
    Missing candidate metrics/used capacities stop search conservatively. Events are
    unsupported. Positive integer evaluation limit required; invalid input raises
    ValueError. Exact decimal-rational comparisons; ID order resolves equal scores.
    """
    started = perf_counter()
    if type(max_evaluations) is not int or max_evaluations <= 0:
        raise ValueError("max_evaluations must be a positive integer")
    scenario = _snapshot(scenario)
    digest = fingerprint(scenario)
    groups = _groups(scenario)
    total = math.prod(len(group) for group in groups)
    common = dict(
        solver_name="h1-exact-v1",
        input_fingerprint=digest,
        total_combinations=total,
        max_evaluations=max_evaluations,
    )
    blockers = _blockers(scenario)
    if blockers:
        return SolverResult(
            status=_blocked_status(blockers),
            issues=blockers,
            elapsed_s=perf_counter() - started,
            **common,
        )
    best_plan = best_report = best_score = None
    feasible = evaluated = ties = 0
    failures = Counter()
    for choice in islice(product(*groups), max_evaluations):
        plan = _make_plan(choice, scenario, digest, common["solver_name"])
        report = _evaluate(plan, scenario, digest)
        evaluated += 1
        if report.status != "feasible":
            failures.update({issue.code for issue in report.issues})
            continue
        feasible += 1
        score = Fraction(report.exact_score)
        if best_score is None or score > best_score:
            best_plan, best_report, best_score, ties = plan, report, score, 1
        elif score == best_score:
            ties += 1
            if plan.selected_option_ids < best_plan.selected_option_ids:
                best_plan, best_report = plan, report
    complete = evaluated == total
    status = ("optimal" if best_plan else "infeasible") if complete else "limit_reached"
    issues = (
        ()
        if complete
        else (
            Issue(
                code="search_limit",
                message=f"Examined {evaluated} of {total} assignments; optimum is not certified.",
            ),
        )
    )
    return SolverResult(
        status=status,
        plan=best_plan,
        validation=best_report,
        evaluated_combinations=evaluated,
        feasible_count=feasible,
        infeasible_count=evaluated - feasible,
        search_complete=complete,
        optimal_plan_count=ties if complete and best_plan else None,
        failure_counts=dict(sorted(failures.items())),
        issues=issues,
        elapsed_s=perf_counter() - started,
        **common,
    )


def select_independent(scenario: Scenario) -> SolverResult:
    """Pick each mission's highest score without joint constraints, then validate.

    Return a proposal, including conflicts; never claim global optimality.
    Missing inputs/events block selection. Invalid input raises ValueError.
    """
    started = perf_counter()
    scenario = _snapshot(scenario)
    digest = fingerprint(scenario)
    groups = _groups(scenario)
    common = dict(
        solver_name="h1-independent-v1",
        input_fingerprint=digest,
        total_combinations=math.prod(len(group) for group in groups),
    )
    blockers = _blockers(scenario)
    if blockers:
        return SolverResult(
            status=_blocked_status(blockers),
            issues=blockers,
            elapsed_s=perf_counter() - started,
            **common,
        )
    # No joint constraint filtering: this deliberately exposes independent conflicts.
    choice = tuple(
        min(group, key=lambda c: (-_candidate_score(c, scenario), c.option_id))
        for group in groups
    )
    plan = _make_plan(choice, scenario, digest, common["solver_name"])
    report = _evaluate(plan, scenario, digest)
    feasible = int(report.status == "feasible")
    return SolverResult(
        status="proposal",
        plan=plan,
        validation=report,
        evaluated_combinations=1,
        feasible_count=feasible,
        infeasible_count=1 - feasible,
        failure_counts={code: 1 for code in sorted({i.code for i in report.issues})},
        elapsed_s=perf_counter() - started,
        **common,
    )


def _canonical_numbers(value):
    if isinstance(value, dict):
        return {key: _canonical_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_numbers(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _snapshot(scenario):
    # H0 models are shallow-frozen; validate nested dictionaries at public boundaries.
    return Scenario.model_validate(scenario.model_dump(mode="python"))


def _groups(scenario):
    return tuple(
        tuple(
            sorted(
                (c for c in scenario.candidates if c.mission_id == m.mission_id),
                key=lambda c: c.option_id,
            )
        )
        for m in sorted(scenario.missions, key=lambda m: m.mission_id)
    )


def _make_plan(choice, scenario, digest, solver_name):
    ids = tuple(sorted(c.option_id for c in choice))
    selection_digest = hashlib.sha256(json.dumps(ids).encode()).hexdigest()
    return Plan(
        plan_id="plan-" + selection_digest,
        scenario_id=scenario.scenario_id,
        scenario_revision=scenario.revision,
        input_fingerprint=digest,
        state_revisions={s.state_id: s.revision for s in scenario.states},
        selected_option_ids=ids,
        execution_mode="classical",
        solver_name=solver_name,
    )


def _fraction(value):
    # Compare the canonical decimal spelling exactly, including fractional channels.
    return Fraction(str(value))


def _finite_float(value):
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError("Objective exceeds finite output range") from exc
    if not math.isfinite(result):
        raise ValueError("Objective exceeds finite output range")
    return result


def _candidate_score(candidate, scenario):
    return sum(
        (
            _fraction(o.weight)
            * _fraction(candidate.metrics[o.metric])
            / _fraction(o.scale)
            * (1 if o.direction == "maximize" else -1)
            for o in scenario.objectives
        ),
        Fraction(0),
    )


def _event_issues(scenario):
    if not scenario.events:
        return ()
    return (
        Issue(
            code="unapplied_events",
            message="H1 accepts static inputs only; event declarations require explicit state/resource updates before planning.",
        ),
    )


def _unknown_issues(candidates, scenario):
    issues = []
    for candidate in candidates:
        for metric, value in sorted(candidate.metrics.items()):
            if value is None:
                issues.append(
                    Issue(
                        code="unknown_metric",
                        option_ids=(candidate.option_id,),
                        mission_id=candidate.mission_id,
                        message=f"Unknown objective metric: {metric}",
                    )
                )
    used = {d.resource_id for c in candidates for d in c.demands if d.amount > 0}
    for resource in scenario.resources:
        if resource.resource_id in used and resource.capacity is None:
            issues.append(
                Issue(
                    code="unknown_capacity",
                    resource_id=resource.resource_id,
                    unit=resource.unit,
                    message="Required resource capacity is unknown.",
                )
            )
    return tuple(issues)


def _blockers(scenario):
    return _event_issues(scenario) or _unknown_issues(scenario.candidates, scenario)


def _blocked_status(issues):
    return (
        "unsupported"
        if any(i.code == "unapplied_events" for i in issues)
        else "incomplete"
    )


def _covers(window, availability):
    cursor = window.start_utc
    for available in sorted(availability, key=lambda w: w.start_utc):
        if available.end_utc <= cursor:
            continue
        if available.start_utc > cursor:
            return False
        cursor = available.end_utc
        if cursor >= window.end_utc:
            return True
    return False


def _resource_issues(selected, scenario):
    issues = []
    for resource in scenario.resources:
        demands = [
            (c.option_id, d)
            for c in selected
            for d in c.demands
            if d.resource_id == resource.resource_id and d.amount > 0
        ]
        for option_id, demand in demands:
            if not _covers(demand.window, resource.availability):
                issues.append(
                    Issue(
                        code="availability",
                        resource_id=resource.resource_id,
                        option_ids=(option_id,),
                        window=demand.window,
                        message="Demand interval is not fully covered by resource availability.",
                    )
                )
        if resource.capacity is None or not demands:
            continue
        capacity = _fraction(resource.capacity)
        if resource.mode == "budget":
            required = sum((_fraction(d.amount) for _, d in demands), Fraction(0))
            if required > capacity:
                issues.append(
                    Issue(
                        code="budget",
                        resource_id=resource.resource_id,
                        option_ids=tuple(sorted({oid for oid, _ in demands})),
                        required=str(required),
                        capacity=str(capacity),
                        unit=resource.unit,
                        message="Total consumption exceeds the cumulative energy budget.",
                    )
                )
        else:
            boundaries = sorted(
                {t for _, d in demands for t in (d.window.start_utc, d.window.end_utc)}
            )
            for start, end in zip(boundaries, boundaries[1:]):
                active = [
                    (oid, d)
                    for oid, d in demands
                    if d.window.start_utc <= start < d.window.end_utc
                ]
                required = sum((_fraction(d.amount) for _, d in active), Fraction(0))
                if required > capacity:
                    issues.append(
                        Issue(
                            code="capacity",
                            resource_id=resource.resource_id,
                            option_ids=tuple(sorted({oid for oid, _ in active})),
                            window=TimeWindow(start_utc=start, end_utc=end),
                            required=str(required),
                            capacity=str(capacity),
                            unit=resource.unit,
                            message="Simultaneous demand exceeds available channel capacity.",
                        )
                    )
    return issues


def _evaluate(plan, scenario, digest):
    # This validator reads original H0 constraints, not a solver's score or penalties.
    ids = tuple(sorted(plan.selected_option_ids))
    common = dict(input_fingerprint=digest, selected_option_ids=ids)
    event_issues = _event_issues(scenario)
    if event_issues:
        return ValidationReport(status="unsupported", issues=event_issues, **common)
    selected = tuple(c for c in scenario.candidates if c.option_id in ids)
    issues = []
    for mission in scenario.missions:
        choices = [c for c in selected if c.mission_id == mission.mission_id]
        if len(choices) != 1:
            issues.append(
                Issue(
                    code="selection_count",
                    mission_id=mission.mission_id,
                    option_ids=tuple(c.option_id for c in choices),
                    message=f"Expected exactly one option, received {len(choices)}.",
                )
            )
        for candidate in choices:
            if candidate.window.end_utc > mission.deadline_utc:
                issues.append(
                    Issue(
                        code="deadline",
                        mission_id=mission.mission_id,
                        option_ids=(candidate.option_id,),
                        window=candidate.window,
                        message=f"Option ends after deadline {mission.deadline_utc.isoformat()}.",
                    )
                )
    for constraint in scenario.incompatibilities:
        conflicting = tuple(sorted(set(ids) & set(constraint.option_ids)))
        if len(conflicting) > 1:
            issues.append(
                Issue(
                    code="incompatibility",
                    option_ids=conflicting,
                    message=f"At-most-one constraint violated: {constraint.constraint_id}",
                )
            )
    issues.extend(_resource_issues(selected, scenario))
    violated = bool(issues)
    unknown = _unknown_issues(selected, scenario)
    issues.extend(unknown)
    score = exact_score = None
    totals = {}
    if not any(i.code == "unknown_metric" for i in unknown):
        exact = sum((_candidate_score(c, scenario) for c in selected), Fraction(0))
        score, exact_score = _finite_float(exact), str(exact)
        totals = {
            o.metric: _finite_float(
                sum((_fraction(c.metrics[o.metric]) for c in selected), Fraction(0))
            )
            for o in scenario.objectives
        }
    status = "infeasible" if violated else ("incomplete" if unknown else "feasible")
    return ValidationReport(
        status=status,
        issues=tuple(issues),
        score=score,
        exact_score=exact_score,
        metric_totals=totals,
        **common,
    )
