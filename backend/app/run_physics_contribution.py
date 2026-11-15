"""C2 reproducible, sequential physics accounting; no provider/network access."""

import argparse
import hashlib
import json
import math
from fractions import Fraction
from itertools import product
from pathlib import Path
from time import perf_counter

from .application_robust import (
    PLAN_OPTIONS,
    LanderPlans,
    LanderSpec,
    RobustPlan,
    RobustProblem,
    build_robust,
    synthetic_slope_map,
)
from .descent import OrbitSettings
from .lazy_physics import select_lazy
from .physics_cache import PhysicsCache
from .quantum_applications import solve_milp
from .risk import RiskRequest, run_monte_carlo

SEEDS = (811, 812, 813, 814)
LIMITS_M = (500.0, 1500.0)
RUNS = 24


def _specs(seed):
    return tuple(
        LanderSpec(
            lander_id=f"L{side}",
            site_id=f"c2-synthetic-{side}",
            latitude_deg=(-85.9948, -88.5)[side],
            longitude_deg_east=(359.76422, 120.0)[side],
            terrain_height_m=0.0,
            slope_map=synthetic_slope_map(2 * seed + side + 1),
            plans=PLAN_OPTIONS,
        )
        for side in range(2)
    )


class _Oracle:
    def __init__(self, specs, seed, cache):
        self.specs, self.seed, self.cache = specs, seed, cache
        self.plans = {}
        self.elapsed_s = 0.0

    def load(self, side, index):
        key = (side, index)
        if key in self.plans:
            raise RuntimeError("The selector requested a duplicate plan batch")
        spec, option = self.specs[side], self.specs[side].plans[index]
        request = RiskRequest(
            site_id=spec.site_id,
            latitude_deg=spec.latitude_deg,
            longitude_deg_east=spec.longitude_deg_east,
            orbit=OrbitSettings(altitude_km=option.orbit_altitude_km),
            approach_azimuth_deg=option.approach_azimuth_deg,
            runs=RUNS,
            seed=self.seed,
        )
        started = perf_counter()
        result = run_monte_carlo(
            request, spec.terrain_height_m, spec.slope_map, physics_cache=self.cache
        )
        points = result["landing"]["points"]
        plan = RobustPlan(
            plan_id=option.plan_id,
            orbit_altitude_km=option.orbit_altitude_km,
            approach_azimuth_deg=option.approach_azimuth_deg,
            success=tuple(p["success"] for p in points),
            miss_m=tuple(math.hypot(p["down_m"], p["cross_m"]) for p in points),
            r6_result_id=result["result_id"],
        )
        self.plans[key] = plan
        self.elapsed_s += perf_counter() - started
        return plan


def _native(specs, plans, seed, limit):
    return RobustProblem(
        problem_id=f"c2-{seed}",
        runs=RUNS,
        seed=seed,
        cost_limit_m=limit,
        landers=tuple(
            LanderPlans(
                lander_id=spec.lander_id,
                site_id=spec.site_id,
                plans=tuple(plans[(side, i)] for i in range(4)),
            )
            for side, spec in enumerate(specs)
        ),
    )


def _run(specs, seed, limit, method, cache, input_s):
    before = cache.snapshot()
    started = perf_counter()
    oracle = _Oracle(specs, seed, cache)
    if method == "lazy":
        result = select_lazy(
            (4, 4), runs=RUNS, cost_limit_m=limit, load_plan=oracle.load, max_batches=8
        )
    else:
        for side, index in product(range(2), range(4)):
            oracle.load(side, index)
        mark = perf_counter()
        instance = build_robust(_native(specs, oracle.plans, seed, limit))
        reference = solve_milp(instance)
        result = dict(
            status=reference.status,
            objective=reference.objective,
            selection_s=perf_counter() - mark,
            native_milp=reference.model_dump(mode="json"),
        )
    total_s = perf_counter() - started
    after = cache.snapshot()
    delta = {
        kind: {k: after[kind][k] - before[kind][k] for k in after[kind]}
        for kind in ("nominal", "dynamics")
    }
    accounted = sum(row["compute_s"] + row["overhead_s"] for row in delta.values())
    result.update(
        input_preparation_s=input_s,
        total_s=total_s + input_s,
        r6_s=oracle.elapsed_s,
        risk_other_s=oracle.elapsed_s - accounted,
        cache=delta,
        peak_entry_limit=cache.max_entries,
        physics_batches=len(oracle.plans),
        outcome_records={
            f"{side}:{index}": p.model_dump(mode="json")
            for (side, index), p in sorted(oracle.plans.items())
        },
    )
    return result


def run_experiment(*, protocol_path=None, checkpoint_path=None):
    """Run the frozen protocol, then audit hidden outcomes and native quality."""
    root = Path(__file__).resolve().parents[2]
    paths = [
        "backend/app/risk.py",
        "backend/app/descent.py",
        "backend/app/descent_dynamics.py",
        "backend/app/physics_cache.py",
        "backend/app/lazy_physics.py",
        "backend/app/run_physics_contribution.py",
        "backend/app/application_robust.py",
        "docs/physics-contribution-c2-protocol.md",
    ]
    protocol_path = protocol_path or root / paths[-1]
    source_sha256 = {
        p: hashlib.sha256(
            (protocol_path if p == paths[-1] else root / p).read_bytes()
        ).hexdigest()
        for p in paths
    }
    cases = []
    for seed in SEEDS:
        for limit in LIMITS_M:
            started = perf_counter()
            specs = _specs(seed)
            input_s = perf_counter() - started
            order = ["eager_uncached", "eager_cached", "lazy_cached"]
            offset = seed % len(order)
            order = order[offset:] + order[:offset]
            rows = {}
            for method in order:
                cache = PhysicsCache(enabled=method != "eager_uncached")
                selector = "lazy" if method == "lazy_cached" else "eager"
                rows[method + "_cold"] = _run(
                    specs, seed, limit, selector, cache, input_s
                )
                if cache.enabled:
                    rows[method + "_warm"] = _run(
                        specs, seed, limit, selector, cache, input_s
                    )
            # All outcomes below are revealed only after every search is complete.
            mark = perf_counter()
            records = rows["eager_uncached_cold"]["outcome_records"]
            plans = {
                tuple(map(int, key.split(":"))): RobustPlan(**value)
                for key, value in records.items()
            }
            instance = build_robust(_native(specs, plans, seed, limit))
            exact = None
            for a, b in product(range(4), repeat=2):
                bits = tuple(int(i == a) for i in range(4)) + tuple(
                    int(i == b) for i in range(4)
                )
                evaluation = instance.evaluate(bits)
                if evaluation.feasible and (
                    exact is None or evaluation.objective > exact
                ):
                    exact = evaluation.objective
            audits = {}
            for name, row in rows.items():
                outcome_equal = all(
                    value == records[key]
                    for key, value in row["outcome_records"].items()
                )
                score = None if row["objective"] is None else Fraction(row["objective"])
                quality_equal = score == exact
                native_tolerance = (score is None and exact is None) or (
                    score is not None
                    and exact is not None
                    and abs(float(score - exact)) <= 1e-6 * (1 + abs(float(exact)))
                )
                status_equal = row["status"] == (
                    "infeasible" if exact is None else "optimal"
                )
                accepted = (
                    outcome_equal
                    and status_equal
                    and (quality_equal if name.startswith("lazy") else native_tolerance)
                )
                audits[name] = dict(
                    outcomes_identical=outcome_equal,
                    exact_score_equal=quality_equal,
                    native_tolerance_equal=native_tolerance,
                    accepted=accepted,
                )
                if not accepted:
                    raise RuntimeError(
                        f"C2 audit failed for {seed}/{limit}/{name}: {audits[name]}"
                    )
            case = dict(
                seed=seed,
                cost_limit_m=limit,
                runs=RUNS,
                methods=rows,
                audit=audits,
                audit_s=perf_counter() - mark,
                exact_objective=None if exact is None else str(exact),
                inputs=[
                    dict(
                        site_id=s.site_id,
                        latitude_deg=s.latitude_deg,
                        longitude_deg_east=s.longitude_deg_east,
                        terrain_height_m=s.terrain_height_m,
                        slope_seed=2 * seed + side + 1,
                        options=[p.model_dump(mode="json") for p in s.plans],
                    )
                    for side, s in enumerate(specs)
                ],
            )
            cases.append(case)
            if checkpoint_path is not None:
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                checkpoint_path.write_text(
                    json.dumps(
                        dict(
                            protocol="physics-c2",
                            complete=False,
                            source_sha256=source_sha256,
                            cases=cases,
                        ),
                        indent=2,
                        allow_nan=False,
                    )
                    + "\n"
                )
            print(
                json.dumps(
                    dict(
                        seed=seed,
                        limit_m=limit,
                        methods={
                            k: dict(
                                total_s=round(v["total_s"], 3),
                                batches=v["physics_batches"],
                                dynamics=v["cache"]["dynamics"]["misses"],
                            )
                            for k, v in rows.items()
                        },
                    )
                ),
                flush=True,
            )
    return dict(
        protocol="physics-c2",
        seeds=SEEDS,
        runs=RUNS,
        limits_m=LIMITS_M,
        source_sha256=source_sha256,
        cases=cases,
        hardware_jobs=0,
        spark_spent=0,
        quantum_advantage=False,
        quantum_comparison="blocked: existing QAOA requires the full hidden pair outcome table",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol-path", type=Path)
    args = parser.parse_args()
    report = run_experiment(
        protocol_path=args.protocol_path,
        checkpoint_path=args.output.with_suffix(".partial.json"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
