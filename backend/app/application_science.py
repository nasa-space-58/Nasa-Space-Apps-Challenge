"""R13 application 2: science-activity selection and ordering.

One instrument, precedence rules, deadlines and an explicitly ideal battery
(unlimited, lossless storage; H3-style assumption) with per-slot solar input.
Synthetic planning benchmark. See docs/quantum-applications.md.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from typing import Annotated

from pydantic import AwareDatetime, Field, model_validator
from shared.hybrid import Identifier, NonNegative, Positive

from .quantum_applications import (
    ApplicationInstance,
    Evaluation,
    InputContract,
    QuboBuilder,
    milp_model,
    model_fingerprint,
    penalty_strength,
    rational,
)

APPLICATION = "science_activities"


class ScienceActivity(InputContract):
    """Must finish by `deadline_slot` (exclusive end slot index)."""

    activity_id: Identifier
    science_value: Positive
    duration_slots: Annotated[int, Field(strict=True, ge=1, le=48)]
    energy_j: NonNegative
    earliest_slot: Annotated[int, Field(strict=True, ge=0, le=47)]
    deadline_slot: Annotated[int, Field(strict=True, ge=1, le=48)]


class Precedence(InputContract):
    before_id: Identifier
    after_id: Identifier


class ScienceProblem(InputContract):
    problem_id: Identifier
    start_utc: AwareDatetime
    slot_s: Annotated[int, Field(strict=True, ge=60, le=86400)]
    slot_count: Annotated[int, Field(strict=True, ge=1, le=48)]
    activities: tuple[ScienceActivity, ...] = Field(min_length=1)
    precedences: tuple[Precedence, ...] = ()
    initial_energy_j: NonNegative
    reserve_energy_j: NonNegative
    solar_input_j_per_slot: tuple[NonNegative, ...]
    lateness_weight_per_slot: NonNegative = 0.05
    source_note: str = (
        "synthetic R13 planning benchmark; ideal unlimited lossless battery"
    )

    @model_validator(mode="after")
    def consistent(self):
        ids = [a.activity_id for a in self.activities]
        if len(set(ids)) != len(ids):
            raise ValueError("Activity IDs must be unique")
        if len(self.solar_input_j_per_slot) != self.slot_count:
            raise ValueError("Solar input needs one value per slot")
        if self.reserve_energy_j > self.initial_energy_j:
            raise ValueError("Reserve exceeds the initial energy")
        for a in self.activities:
            if a.earliest_slot >= a.deadline_slot:
                raise ValueError(f"{a.activity_id}: earliest slot not before deadline")
            if a.deadline_slot > self.slot_count:
                raise ValueError(f"{a.activity_id}: deadline outside the horizon")
        known = set(ids)
        successors = defaultdict(set)
        for p in self.precedences:
            if p.before_id not in known or p.after_id not in known:
                raise ValueError("Precedence references an unknown activity")
            if p.before_id == p.after_id:
                raise ValueError("An activity cannot precede itself")
            successors[p.before_id].add(p.after_id)
        state: dict[str, int] = {}

        def visit(node):
            state[node] = 1
            for nxt in successors[node]:
                if state.get(nxt) == 1:
                    raise ValueError("Precedences contain a cycle")
                if nxt not in state:
                    visit(nxt)
            state[node] = 2

        for node in ids:
            if node not in state:
                visit(node)
        return self


def generate_science(
    seed: int,
    *,
    activities: int = 4,
    slots: int = 6,
    precedence_pairs: int = 1,
    start_utc: datetime | None = None,
) -> ScienceProblem:
    """Seeded synthetic instance (random.Random)."""
    rng = random.Random(seed)
    items = []
    for a in range(activities):
        duration = rng.choice((1, 1, 2))
        earliest = rng.randrange(0, max(1, slots - duration))
        deadline = rng.randrange(earliest + duration, slots + 1)
        items.append(
            ScienceActivity(
                activity_id=f"A{a + 1}",
                science_value=float(rng.randint(1, 10)),
                duration_slots=duration,
                energy_j=float(rng.choice((50, 100, 150, 200))) * duration,
                earliest_slot=earliest,
                deadline_slot=deadline,
            )
        )
    pairs = []
    candidates = [(i, j) for i in range(activities) for j in range(i + 1, activities)]
    rng.shuffle(candidates)
    for i, j in candidates[:precedence_pairs]:
        pairs.append(
            Precedence(before_id=items[i].activity_id, after_id=items[j].activity_id)
        )
    solar = tuple(float(rng.choice((0, 50, 100))) for _ in range(slots))
    total = sum(a.energy_j for a in items)
    reserve = 50.0
    # Binding but not empty: the battery covers roughly 60 % of all activities.
    initial = reserve + max(0.0, 0.6 * total - sum(solar))
    return ScienceProblem(
        problem_id=f"science-{seed}",
        start_utc=start_utc or datetime(2026, 11, 20, tzinfo=timezone.utc),
        slot_s=3600,
        slot_count=slots,
        activities=tuple(items),
        precedences=tuple(pairs),
        initial_energy_j=initial,
        reserve_energy_j=reserve,
        solar_input_j_per_slot=solar,
    )


def build_science(problem: ScienceProblem) -> ApplicationInstance:
    """Variables, validator, MILP and QUBO for one problem."""
    problem = ScienceProblem.model_validate(problem.model_dump())
    T = problem.slot_count
    variables = []  # (activity, start)
    for a in problem.activities:
        last = min(a.deadline_slot, T) - a.duration_slots
        for t in range(a.earliest_slot, last + 1):
            variables.append((a, t))
    if not variables:
        raise ValueError("No activity can be scheduled on time")
    n = len(variables)
    weight = rational(problem.lateness_weight_per_slot)
    coefficients = [rational(a.science_value) - weight * t for a, t in variables]
    load = [rational(a.energy_j) / a.duration_slots for a, _ in variables]
    starts = defaultdict(list)
    for i, (a, _) in enumerate(variables):
        starts[a.activity_id].append(i)
    running = defaultdict(list)  # slot -> variables running in it
    for i, (a, t) in enumerate(variables):
        for u in range(t, t + a.duration_slots):
            running[u].append(i)
    available = []  # C_tau = initial + solar_<=tau - reserve
    cumulative = rational(problem.initial_energy_j) - rational(problem.reserve_energy_j)
    for tau in range(T):
        cumulative += rational(problem.solar_input_j_per_slot[tau])
        available.append(cumulative)
    used_upto = []  # per tau: {var: energy used by end of tau}
    for tau in range(T):
        row = {}
        for i, (a, t) in enumerate(variables):
            slots_used = max(0, min(tau, t + a.duration_slots - 1) - t + 1)
            if slots_used:
                row[i] = load[i] * slots_used
        used_upto.append(row)
    energy_scale = max(
        [rational(a.energy_j) for a in problem.activities] + [Fraction(1)]
    )
    precedences = [(p.before_id, p.after_id) for p in problem.precedences]
    by_id = {a.activity_id: a for a in problem.activities}

    def compatible(before_index, after_index):
        (b, tb), (_, ta) = variables[before_index], variables[after_index]
        return tb + b.duration_slots <= ta

    def evaluate(bits):
        issues, involved = [], set()
        violation = Fraction(0)
        for aid, members in starts.items():
            on = [i for i in members if bits[i]]
            if len(on) > 1:
                violation += len(on) - 1
                issues.append(f"activity_multiple_starts:{aid}:{len(on)}")
                involved.update(on)
        for u, members in sorted(running.items()):
            on = [i for i in members if bits[i]]
            if len(on) > 1:
                violation += len(on) - 1
                issues.append(f"instrument_overlap:slot{u}:{len(on)}")
                involved.update(on)
        for before, after in precedences:
            for i in starts.get(after, []):
                if bits[i] and not any(
                    bits[j] and compatible(j, i) for j in starts.get(before, [])
                ):
                    violation += 1
                    issues.append(f"precedence:{before}->{after}")
                    involved.add(i)
                    involved.update(j for j in starts.get(before, []) if bits[j])
                    involved.update(starts.get(before, []))
        margins = []
        for tau in range(T):
            used = sum((e for i, e in used_upto[tau].items() if bits[i]), Fraction(0))
            margin = available[tau] - used
            margins.append(margin)
            if margin < 0:
                violation += -margin / energy_scale
                issues.append(f"energy_reserve:slot{tau}:{float(margin):.3f}J")
                involved.update(i for i in used_upto[tau] if bits[i])
        chosen = [i for i, b in enumerate(bits) if b]
        done = {variables[i][0].activity_id for i in chosen}
        ends = [variables[i][1] + variables[i][0].duration_slots for i in chosen]
        return Evaluation(
            feasible=violation == 0,
            violation=violation,
            objective=sum((coefficients[i] for i in chosen), Fraction(0)),
            metrics={
                "completed_science_value": float(
                    sum(rational(by_id[a].science_value) for a in done)
                ),
                "completed_activities": len(done),
                "missed_deadlines": len(problem.activities) - len(done),
                "min_battery_margin_j": float(min(margins)),
                "makespan_s": float(max(ends) * problem.slot_s) if ends else 0,
            },
            issues=tuple(issues),
            violating_variables=tuple(sorted(involved)),
        )

    strength = penalty_strength(coefficients)
    qubo = QuboBuilder(n)
    for i, c in enumerate(coefficients):
        qubo.add_linear(i, -float(c))
    pairs = set()
    for members in starts.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                qubo.add_pair(members[a], members[b], 2 * strength)
                pairs.add((members[a], members[b]))
    for members in running.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                key = (min(members[a], members[b]), max(members[a], members[b]))
                same = (
                    variables[key[0]][0].activity_id == variables[key[1]][0].activity_id
                )
                if key not in pairs and not same:
                    qubo.add_pair(*key, strength)
                    pairs.add(key)
    for before, after in precedences:
        for i in starts.get(after, []):
            qubo.add_linear(i, strength)
            for j in starts.get(before, []):
                if compatible(j, i):
                    qubo.add_pair(i, j, -strength)
    exact = True
    rows = []
    for tau in range(T):
        weights = used_upto[tau]
        if not weights or sum(weights.values()) <= available[tau]:
            continue  # Non-binding energy row.
        exact = False
        if available[tau] == 0:
            for i in weights:
                qubo.add_linear(i, strength)
        else:
            qubo.add_unbalanced(
                {i: float(w) for i, w in weights.items()},
                float(available[tau]),
                strength,
            )
        rows.append(
            (
                {i: float(w) for i, w in weights.items()},
                -math.inf,
                float(available[tau]),
            )
        )
    for members in starts.values():
        rows.append(({i: 1.0 for i in members}, -math.inf, 1.0))
    for members in running.values():
        if len(members) > 1:
            rows.append(({i: 1.0 for i in members}, -math.inf, 1.0))
    for before, after in precedences:
        for i in starts.get(after, []):
            row = {i: 1.0}
            for j in starts.get(before, []):
                if compatible(j, i):
                    row[j] = -1.0
            rows.append((row, -math.inf, 0.0))
    encoding = (
        "exact pairwise penalties; energy rows non-binding"
        if exact
        else "pairwise penalties plus unbalanced penalties for binding energy rows (heuristic)"
    )
    return ApplicationInstance(
        application=APPLICATION,
        instance_id=problem.problem_id,
        variable_labels=tuple(f"{a.activity_id}@{t}" for a, t in variables),
        evaluate=evaluate,
        qubo=qubo.build(exact=exact, encoding=encoding),
        milp=milp_model([float(c) for c in coefficients], rows, decision_count=n),
        input_fingerprint=model_fingerprint(problem),
        source_note=problem.source_note,
    )
