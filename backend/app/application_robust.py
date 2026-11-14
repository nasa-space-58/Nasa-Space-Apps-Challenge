"""R13 application 5: robust plan selection after R6.

Two landers each choose one of their candidate plans. Every plan is flown
through R6 `run_monte_carlo` with the same seed and run count, so scenario s is
the same seeded error draw for every plan and both landers (explicit
common-cause assumption). Finite Monte Carlo counts are not certified
probabilities. See docs/quantum-applications.md.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from fractions import Fraction
from itertools import product
from typing import Annotated

import numpy as np
from pydantic import Field, model_validator
from rasterio.transform import from_origin
from shared.hybrid import Identifier, NonNegative, Positive

from .descent import DEFAULT_ORBIT_ALTITUDE_KM, OrbitSettings
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
from .risk import LocalSlopeMap, RiskRequest, run_monte_carlo, wilson_interval

APPLICATION = "robust_plans"


class PlanOption(InputContract):
    plan_id: Identifier
    orbit_altitude_km: Annotated[float, Field(ge=30, le=500)]
    approach_azimuth_deg: Annotated[float, Field(ge=0, lt=360)]


class RobustPlan(InputContract):
    """Per-scenario R6 outcome of one plan."""

    plan_id: Identifier
    orbit_altitude_km: float
    approach_azimuth_deg: float
    success: tuple[bool, ...]
    miss_m: tuple[NonNegative, ...]
    r6_result_id: str

    @model_validator(mode="after")
    def matching_lengths(self):
        if len(self.success) != len(self.miss_m):
            raise ValueError(f"{self.plan_id}: success and miss counts differ")
        return self


class LanderPlans(InputContract):
    lander_id: Identifier
    site_id: Identifier
    plans: tuple[RobustPlan, ...] = Field(min_length=1)


class RobustProblem(InputContract):
    problem_id: Identifier
    runs: Annotated[int, Field(strict=True, ge=10, le=300)]
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
    landers: tuple[LanderPlans, LanderPlans]
    cost_limit_m: Positive
    cost_weight: NonNegative = 1.0
    cost_scale_m: Positive = 1000.0
    source_note: str = (
        "R6 seeded Monte Carlo on synthetic slope maps; common-cause scenarios"
    )

    @model_validator(mode="after")
    def consistent(self):
        if self.landers[0].lander_id == self.landers[1].lander_id:
            raise ValueError("Lander IDs must differ")
        for lander in self.landers:
            ids = [p.plan_id for p in lander.plans]
            if len(set(ids)) != len(ids):
                raise ValueError(f"{lander.lander_id}: plan IDs must be unique")
            for plan in lander.plans:
                if len(plan.success) != self.runs:
                    raise ValueError(f"{plan.plan_id}: expected {self.runs} scenarios")
        return self


@dataclass(frozen=True)
class LanderSpec:
    """R6 inputs for one lander; `plans` vary the orbit and approach."""

    lander_id: str
    site_id: str
    latitude_deg: float
    longitude_deg_east: float
    terrain_height_m: float
    slope_map: LocalSlopeMap | None
    plans: tuple[PlanOption, ...]


def _fly(spec: LanderSpec, option: PlanOption, runs: int, seed: int) -> RobustPlan:
    request = RiskRequest(
        site_id=spec.site_id,
        latitude_deg=spec.latitude_deg,
        longitude_deg_east=spec.longitude_deg_east,
        orbit=OrbitSettings(altitude_km=option.orbit_altitude_km),
        approach_azimuth_deg=option.approach_azimuth_deg,
        runs=runs,
        seed=seed,
    )
    result = run_monte_carlo(request, spec.terrain_height_m, spec.slope_map)
    points = result["landing"]["points"]
    if len(points) != runs:
        raise ValueError("R6 reported fewer landing points than runs")
    return RobustPlan(
        plan_id=option.plan_id,
        orbit_altitude_km=option.orbit_altitude_km,
        approach_azimuth_deg=option.approach_azimuth_deg,
        success=tuple(bool(p["success"]) for p in points),
        miss_m=tuple(math.hypot(p["down_m"], p["cross_m"]) for p in points),
        r6_result_id=result["result_id"],
    )


def robust_problem_from_r6(
    problem_id: str,
    landers: tuple[LanderSpec, LanderSpec],
    *,
    runs: int,
    seed: int,
    cost_limit_m: float,
) -> RobustProblem:
    """Fly every plan through R6 with the same seed and run count."""
    built = tuple(
        LanderPlans(
            lander_id=spec.lander_id,
            site_id=spec.site_id,
            plans=tuple(_fly(spec, option, runs, seed) for option in spec.plans),
        )
        for spec in landers
    )
    return RobustProblem(
        problem_id=problem_id,
        runs=runs,
        seed=seed,
        landers=built,
        cost_limit_m=cost_limit_m,
    )


def synthetic_slope_map(seed: int, *, size: int = 101, pixel_m: float = 80.0):
    """Seeded slope map (deg) with steep patches around a site at the origin."""
    rng = random.Random(seed)
    noise = np.random.default_rng(seed)
    half = size * pixel_m / 2
    centres = (np.arange(size) + 0.5) * pixel_m - half
    x, y = np.meshgrid(centres, -centres)
    slopes = 2.0 + 4.0 * noise.random((size, size))
    for _ in range(6):
        cx, cy = rng.uniform(-1500, 1500), rng.uniform(-1500, 1500)
        radius = rng.uniform(200, 800)
        slopes[(x - cx) ** 2 + (y - cy) ** 2 <= radius**2] = rng.uniform(16, 30)
    return LocalSlopeMap(
        slope_deg=slopes,
        transform=from_origin(-half, half, pixel_m, pixel_m),
        site_xy_m=(0.0, 0.0),
        north_unit_xy=(0.0, 1.0),
        east_unit_xy=(1.0, 0.0),
        pixel_m=pixel_m,
    )


PLAN_OPTIONS = tuple(
    PlanOption(
        plan_id=f"alt{round(altitude)}-{'s' if azimuth == 180.0 else 'n'}",
        orbit_altitude_km=altitude,
        approach_azimuth_deg=azimuth,
    )
    for altitude in (DEFAULT_ORBIT_ALTITUDE_KM, 60.0)
    for azimuth in (180.0, 0.0)
)


def _pair_table(problem: RobustProblem):
    """(plan A, plan B) -> (joint successes, worst-case cost m, mean cost m)."""
    a_plans, b_plans = problem.landers[0].plans, problem.landers[1].plans
    table = {}
    for (p, a), (q, b) in product(enumerate(a_plans), enumerate(b_plans)):
        costs = [x + y for x, y in zip(a.miss_m, b.miss_m)]
        both = sum(s and t for s, t in zip(a.success, b.success))
        table[(p, q)] = (both, max(costs), sum(costs) / len(costs))
    return table


def generate_robust(seed: int, *, runs: int = 24) -> RobustProblem:
    """Two seeded landers with four plans each, flown through R6."""
    specs = (
        LanderSpec(
            lander_id="LA",
            site_id="malapert-massif-r23",
            latitude_deg=-85.9948,
            longitude_deg_east=359.76422,
            terrain_height_m=0.0,
            slope_map=synthetic_slope_map(2 * seed + 1),
            plans=PLAN_OPTIONS,
        ),
        LanderSpec(
            lander_id="LB",
            site_id="synthetic-south-pole-b",
            latitude_deg=-88.5,
            longitude_deg_east=120.0,
            terrain_height_m=0.0,
            slope_map=synthetic_slope_map(2 * seed + 2),
            plans=PLAN_OPTIONS,
        ),
    )
    draft = robust_problem_from_r6(
        f"robust-{seed}", specs, runs=runs, seed=seed, cost_limit_m=1e12
    )
    worst = sorted(v[1] for v in _pair_table(draft).values())
    limit = round(worst[len(worst) // 2], 3) + 0.001  # Median pair stays feasible.
    return draft.model_copy(update={"cost_limit_m": limit})


def build_robust(problem: RobustProblem) -> ApplicationInstance:
    """Variables, validator, MILP and exact pairwise QUBO for one problem."""
    problem = RobustProblem.model_validate(problem.model_dump())
    a_plans, b_plans = problem.landers[0].plans, problem.landers[1].plans
    na, nb = len(a_plans), len(b_plans)
    n = na + nb
    table = _pair_table(problem)
    weight = rational(problem.cost_weight) / rational(problem.cost_scale_m)
    limit = rational(problem.cost_limit_m)
    value = {
        key: Fraction(both) - weight * rational(worst)
        for key, (both, worst, _) in table.items()
    }
    forbidden = {key for key, (_, worst, _) in table.items() if rational(worst) > limit}
    la, lb = problem.landers[0].lander_id, problem.landers[1].lander_id
    labels = tuple(f"{la}:{p.plan_id}" for p in a_plans) + tuple(
        f"{lb}:{p.plan_id}" for p in b_plans
    )

    def evaluate(bits):
        chosen_a = [p for p in range(na) if bits[p]]
        chosen_b = [q for q in range(nb) if bits[na + q]]
        issues, involved = [], set()
        violation = Fraction(0)
        for lander, chosen, offset, size in (
            (la, chosen_a, 0, na),
            (lb, chosen_b, na, nb),
        ):
            if len(chosen) != 1:
                violation += abs(len(chosen) - 1)
                issues.append(f"plan_count:{lander}:{len(chosen)}")
                involved.update(range(offset, offset + size))
        objective = sum(
            (value[(p, q)] for p in chosen_a for q in chosen_b), Fraction(0)
        )
        metrics = {
            "campaign_success_rate": None,
            "campaign_wilson95_low": None,
            "campaign_wilson95_high": None,
            f"{la}_success_rate": None,
            f"{lb}_success_rate": None,
            "worst_case_cost_m": None,
            "mean_cost_m": None,
        }
        if len(chosen_a) == 1 and len(chosen_b) == 1:
            p, q = chosen_a[0], chosen_b[0]
            both, worst, mean = table[(p, q)]
            if (p, q) in forbidden:
                violation += (rational(worst) - limit) / limit
                issues.append(
                    f"worst_case_cost:{worst:.1f}m>{problem.cost_limit_m:.1f}m"
                )
                involved.update((p, na + q))
            low, high = wilson_interval(both, problem.runs)
            metrics.update(
                {
                    "campaign_success_rate": both / problem.runs,
                    "campaign_wilson95_low": low,
                    "campaign_wilson95_high": high,
                    f"{la}_success_rate": sum(a_plans[p].success) / problem.runs,
                    f"{lb}_success_rate": sum(b_plans[q].success) / problem.runs,
                    "worst_case_cost_m": worst,
                    "mean_cost_m": mean,
                }
            )
        return Evaluation(
            feasible=violation == 0,
            violation=violation,
            objective=objective,
            metrics=metrics,
            issues=tuple(issues),
            violating_variables=tuple(sorted(involved)),
        )

    strength = penalty_strength(value.values())
    qubo = QuboBuilder(n)
    for offset, size in ((0, na), (na, nb)):
        qubo.constant += strength  # A * (sum x - 1)^2
        for i in range(offset, offset + size):
            qubo.add_linear(i, -strength)
            for j in range(i + 1, offset + size):
                qubo.add_pair(i, j, 2 * strength)
    for (p, q), v in value.items():
        qubo.add_pair(p, na + q, -float(v) + (strength if (p, q) in forbidden else 0.0))
    # MILP columns: x (n), then one pair indicator per allowed (p, q).
    pairs = [key for key in sorted(table) if key not in forbidden]
    objective = [0.0] * n + [float(value[key]) for key in pairs]
    rows = [
        ({i: 1.0 for i in range(na)}, 1.0, 1.0),
        ({na + q: 1.0 for q in range(nb)}, 1.0, 1.0),
    ]
    for c, (p, q) in enumerate(pairs, start=n):
        rows.append(({c: 1.0, p: -1.0}, -math.inf, 0.0))
        rows.append(({c: 1.0, na + q: -1.0}, -math.inf, 0.0))
        rows.append(({c: 1.0, p: -1.0, na + q: -1.0}, -1.0, math.inf))
    for p, q in sorted(forbidden):
        rows.append(({p: 1.0, na + q: 1.0}, -math.inf, 1.0))
    columns = n + len(pairs)
    return ApplicationInstance(
        application=APPLICATION,
        instance_id=problem.problem_id,
        variable_labels=labels,
        evaluate=evaluate,
        qubo=qubo.build(exact=True, encoding="exact one-hot and pair penalties"),
        milp=milp_model(
            objective,
            rows,
            decision_count=n,
            integrality=[1] * n + [0] * len(pairs),
            lower=[0.0] * columns,
            upper=[1.0] * columns,
        ),
        input_fingerprint=model_fingerprint(problem),
        source_note=problem.source_note,
    )
