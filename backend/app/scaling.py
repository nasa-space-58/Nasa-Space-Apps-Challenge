"""H10 reproducible slot-assignment scaling, optional SciPy MILP, no QPU calls."""

import hashlib
import json
import math
import platform
import sys
from itertools import product
from statistics import median
from time import perf_counter

import numpy as np
from typing import Annotated
from pydantic import Field
from shared.hybrid import Contract


class ScalingCase(Contract):
    missions: Annotated[int, Field(strict=True, ge=1, le=32)]
    options: Annotated[int, Field(strict=True, ge=2, le=8)]
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17


def make_problem(case: ScalingCase) -> dict:
    """Build seeded integer preferences and slot capacities; synthetic assumptions."""
    case = ScalingCase.model_validate(case.model_dump())
    rng = np.random.default_rng(case.seed)
    body = dict(
        missions=case.missions,
        options=case.options,
        seed=case.seed,
        utilities=rng.integers(1, 101, size=(case.missions, case.options)).tolist(),
        capacities=[math.ceil(case.missions / case.options)] * case.options,
    )
    body["fingerprint"] = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()
    ).hexdigest()
    return {
        **body,
        "combinations": case.options**case.missions,
        "logical_qubits": case.missions * (case.options - 1).bit_length(),
        "one_hot_variables": case.missions * case.options,
    }


def check_assignment(problem: dict, selection: tuple[int, ...]) -> dict:
    """Independently verify one option per mission and simultaneous slot capacity."""
    m, n = problem["missions"], problem["options"]
    if len(selection) != m or any(
        type(x) is not int or not 0 <= x < n for x in selection
    ):
        raise ValueError("One integer option index is required for each mission")
    counts = [selection.count(i) for i in range(n)]
    return {
        "feasible": all(v <= c for v, c in zip(counts, problem["capacities"])),
        "score": sum(problem["utilities"][i][o] for i, o in enumerate(selection)),
        "occupancy": counts,
    }


def enumerate_problem(
    problem: dict, *, max_evaluations=65536, time_limit_s=2.0
) -> dict:
    """Bound exhaustive search; never label an incomplete incumbent optimal."""
    _budget(time_limit_s)
    if type(max_evaluations) is not int or not 1 <= max_evaluations <= 1_000_000:
        raise ValueError("max_evaluations must be an integer in 1..1000000")
    started = perf_counter()
    evaluated = feasible = 0
    best = score = None
    for choice in product(range(problem["options"]), repeat=problem["missions"]):
        if evaluated >= max_evaluations or perf_counter() - started >= time_limit_s:
            break
        report = check_assignment(problem, choice)
        evaluated += 1
        if report["feasible"]:
            feasible += 1
            if score is None or report["score"] > score:
                best, score = choice, report["score"]
    complete = evaluated == problem["combinations"]
    return dict(
        status=("optimal" if best is not None else "infeasible")
        if complete
        else "limit_reached",
        complete=complete,
        evaluated=evaluated,
        feasible_count=feasible,
        selection=best,
        score=score,
        elapsed_s=perf_counter() - started,
    )


def solve_milp(problem: dict, *, time_limit_s=2.0) -> dict:
    """Solve the identical problem with optional SciPy/HiGHS and validate output."""
    _budget(time_limit_s)
    # SciPy is already pinned transitively by the optional Qiskit environment.
    from scipy.optimize import Bounds, LinearConstraint, milp

    started = perf_counter()
    m, n = problem["missions"], problem["options"]
    a = np.zeros((m + n, m * n))
    for i in range(m):
        a[i, i * n : (i + 1) * n] = 1
    for j in range(n):
        a[m + j, j::n] = 1
    result = milp(
        -np.asarray(problem["utilities"], dtype=float).ravel(),
        integrality=np.ones(m * n),
        bounds=Bounds(0, 1),
        constraints=LinearConstraint(
            a, [1] * m + [0] * n, [1] * m + problem["capacities"]
        ),
        options={"time_limit": time_limit_s, "mip_rel_gap": 0.0},
    )
    selection = report = None
    if result.x is not None:
        values = result.x.reshape(m, n)
        if np.max(np.abs(values - np.rint(values))) > 1e-6:
            raise ValueError("MILP returned a nonintegral incumbent")
        if not np.all(np.sum(np.rint(values), axis=1) == 1):
            raise ValueError("MILP incumbent violates one-choice constraints")
        selection = tuple(int(x) for x in np.argmax(values, axis=1))
        report = check_assignment(problem, selection)
        if not report["feasible"]:
            raise ValueError("MILP incumbent failed independent validation")
    status = {0: "optimal", 1: "limit_reached", 2: "infeasible"}.get(
        result.status, "solver_error"
    )
    if status == "optimal" and report is None:
        raise ValueError("MILP optimal status without a validated incumbent")
    gap = getattr(result, "mip_gap", None)
    return dict(
        status=status,
        selection=selection,
        score=report["score"] if report else None,
        validation=report,
        mip_gap=float(gap) if gap is not None and math.isfinite(gap) else None,
        elapsed_s=perf_counter() - started,
        message=result.message,
    )


def benchmark(
    cases: tuple[ScalingCase, ...],
    *,
    repeats=3,
    max_evaluations=65536,
    time_limit_s=2.0,
) -> dict:
    """Measure both methods, preserve every repeat and label extrapolated trends."""
    import scipy

    if type(repeats) is not int or not 1 <= repeats <= 5 or not 1 <= len(cases) <= 16:
        raise ValueError("Use 1..5 repeats and 1..16 cases")
    _budget(time_limit_s)
    points = []
    for case in cases:
        problem = make_problem(case)
        exact, milp = [], []
        for repeat in range(repeats):
            # Alternate order to reduce systematic warm-up/order effects.
            methods = ("exact", "milp") if repeat % 2 == 0 else ("milp", "exact")
            for method in methods:
                if method == "exact":
                    exact.append(
                        enumerate_problem(
                            problem,
                            max_evaluations=max_evaluations,
                            time_limit_s=time_limit_s,
                        )
                    )
                else:
                    milp.append(solve_milp(problem, time_limit_s=time_limit_s))
        if any(
            a["score"] != b["score"]
            for a, b in zip(exact, milp)
            if a["status"] == b["status"] == "optimal"
        ):
            raise ValueError("Independent exact and MILP references disagree")
        points.append(
            {
                **problem,
                "combinations": str(problem["combinations"]),
                "measurement_kind": "measured",
                "exact_runs": exact,
                "milp_runs": milp,
                "exact_median_s": median(x["elapsed_s"] for x in exact),
                "milp_median_s": median(x["elapsed_s"] for x in milp),
            }
        )
    anchor = next(
        (p for p in points if all(r["complete"] for r in p["exact_runs"])), None
    )
    projections = []
    if anchor:
        per_assignment_s = anchor["exact_median_s"] / int(anchor["combinations"])
        for m in (8, 12, 16, 24, 32):
            count = anchor["options"] ** m
            projections.append(
                dict(
                    missions=m,
                    options=anchor["options"],
                    combinations=str(count),
                    estimated_exact_s=per_assignment_s * count,
                    measurement_kind="extrapolated",
                    source_fingerprint=anchor["fingerprint"],
                )
            )
    return dict(
        version="h10-scaling-v1",
        hardware_jobs=0,
        credits_spent=0,
        environment=dict(
            python=sys.version.split()[0],
            numpy=np.__version__,
            scipy=scipy.__version__,
            platform=platform.platform(),
            machine=platform.machine(),
            processor=platform.processor(),
        ),
        budget=dict(
            repeats=repeats, max_evaluations=max_evaluations, time_limit_s=time_limit_s
        ),
        points=points,
        projections=projections,
        scope="Synthetic capacitated slot assignment; MILP only, CP-SAT not measured. Encoding-size estimates are not QPU runs or speedup evidence.",
    )


def _budget(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 < value <= 30
    ):
        raise ValueError("time_limit_s must be finite and in (0,30]")
