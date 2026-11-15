"""Exact finite-scenario plan selection with demand-driven R6 evaluation."""

import math
from fractions import Fraction
from itertools import product
from time import perf_counter

from .application_robust import RobustPlan


def select_lazy(sizes, *, runs, cost_limit_m, load_plan, max_batches=None):
    """Select two plans using safe optimistic bounds, never inferred safety.

    load_plan(lander_index, option_index) must return a complete RobustPlan.
    The objective is the default R13 joint success count minus worst combined
    miss in metres / 1000. Only proven upper bounds prune unobserved plans.
    Finite Monte Carlo optimality is not a flight-safety certificate.
    """
    if (
        len(sizes) != 2
        or any(type(n) is not int or n < 1 for n in sizes)
        or type(runs) is not int
        or runs < 1
        or not math.isfinite(cost_limit_m)
        or cost_limit_m <= 0
    ):
        raise ValueError("Two positive sizes, positive runs and finite limit required")
    if max_batches is None:
        max_batches = sum(sizes)
    if type(max_batches) is not int or max_batches < 0:
        raise ValueError("max_batches must be a nonnegative integer")
    started = perf_counter()
    known, checked = {}, set()
    remaining = set(product(range(sizes[0]), range(sizes[1])))
    best, selected = None, None
    oracle_s, bound_calls = 0.0, 0
    limit = Fraction(str(cost_limit_m))
    exhausted = False
    while remaining:
        bounds = []
        for pair in sorted(remaining):
            bound_calls += 1
            plans = [
                known[(side, index)]
                for side, index in enumerate(pair)
                if (side, index) in known
            ]
            success_upper = min((sum(p.success) for p in plans), default=runs)
            miss_lower = max((max(p.miss_m) for p in plans), default=0.0)
            upper = Fraction(success_upper) - Fraction(str(miss_lower)) / 1000
            if Fraction(str(miss_lower)) > limit or (
                best is not None and upper <= best
            ):
                continue
            bounds.append((upper, pair))
        if not bounds:
            break
        # Largest optimistic objective; stable lexicographic tie-break.
        _, pair = min(bounds, key=lambda item: (-item[0], item[1]))
        for side, index in enumerate(pair):
            key = (side, index)
            if key in known:
                continue
            if len(known) >= max_batches:
                exhausted = True
                break
            mark = perf_counter()
            plan = RobustPlan.model_validate(load_plan(side, index))
            oracle_s += perf_counter() - mark
            if len(plan.success) != runs:
                raise ValueError("Oracle returned the wrong number of scenarios")
            known[key] = plan
        if exhausted:
            break
        a, b = (known[(side, index)] for side, index in enumerate(pair))
        worst = max(x + y for x, y in zip(a.miss_m, b.miss_m))
        both = sum(x and y for x, y in zip(a.success, b.success))
        score = Fraction(both) - Fraction(str(worst)) / 1000
        if Fraction(str(worst)) <= limit and (best is None or score > best):
            best, selected = score, pair
        checked.add(pair)
        remaining.remove(pair)
    total = perf_counter() - started
    return dict(
        status="budget_exhausted"
        if exhausted
        else ("optimal" if best is not None else "infeasible"),
        objective=None if best is None else str(best),
        selected=None if selected is None else list(selected),
        physics_batches=len(known),
        validated_pairs=len(checked),
        bound_calls=bound_calls,
        oracle_s=oracle_s,
        selection_s=total - oracle_s,
        total_s=total,
    )
