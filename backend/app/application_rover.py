"""R13 application 4: rover target and route selection after R8.

Route candidates come from R8 `plan_routes` at two mobility slope limits.
Excursions are out-and-back from the lander; energy is a stated planning
assumption (drive power and climb work), and the communication gap is the
sampled no-Earth share of the route duration. Synthetic terrain and sky; not a
certified traverse plan. See docs/quantum-applications.md.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Annotated, Literal

import numpy as np
from pydantic import Field, model_validator
from rasterio.transform import from_origin
from shared.constants import MOON_GM_M3_S2, MOON_REFERENCE_RADIUS_M
from shared.hybrid import Identifier, NonNegative, Positive

from .alternatives import LocalTerrain
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
from .surface_routes import RouteRequest, plan_routes

APPLICATION = "rover_routes"
VARIANT_SLOPE_LIMITS_DEG = {"conservative": 12.0, "direct": 20.0}
LUNAR_GRAVITY_MPS2 = MOON_GM_M3_S2 / MOON_REFERENCE_RADIUS_M**2
STEREO_SOUTH = f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={MOON_REFERENCE_RADIUS_M} +units=m +no_defs"
RejectionCode = Literal[
    "outside_terrain", "target_impassable", "no_path", "comm_gap_exceeds_limit"
]


class RoverEnergyModel(InputContract):
    """Planning assumption, not a measured rover model."""

    drive_power_w: Positive = 100.0
    rover_mass_kg: Positive = 300.0
    climb_efficiency: Annotated[float, Field(strict=True, gt=0, le=1)] = 0.5


class RoverCandidate(InputContract):
    target_id: Identifier
    variant_id: Identifier
    slope_limit_deg: Positive
    length_m: Positive
    duration_s: Positive
    elevation_gain_m: NonNegative
    max_slope_deg: NonNegative
    energy_j: NonNegative
    comm_gap_s: NonNegative
    shadowed_fraction: Annotated[float, Field(ge=0, le=1)]
    route_result_id: str


class RouteRejection(InputContract):
    target_id: Identifier
    variant_id: Identifier
    code: RejectionCode
    detail: str


class RoverProblem(InputContract):
    problem_id: Identifier
    site_id: Identifier
    science_values: dict[Identifier, Positive]
    candidates: tuple[RoverCandidate, ...]
    rejections: tuple[RouteRejection, ...] = ()
    time_budget_s: Positive
    energy_budget_j: Positive
    max_comm_gap_s: NonNegative
    energy_weight: NonNegative = 1.0
    energy_scale_j: Positive = 1e7
    gap_weight: NonNegative = 1.0
    gap_scale_s: Positive = 36000.0
    source_note: str = (
        "R8 routes on synthetic terrain; out-and-back excursions; not a traverse plan"
    )

    @model_validator(mode="after")
    def consistent(self):
        keys = [(c.target_id, c.variant_id) for c in self.candidates]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate target/variant candidate")
        missing = {c.target_id for c in self.candidates} - set(self.science_values)
        if missing:
            raise ValueError(f"No science value for: {', '.join(sorted(missing))}")
        return self


def route_energy_j(
    duration_s: float, elevation_gain_m: float, model: RoverEnergyModel
) -> float:
    """Out-and-back energy: 2 * (P * duration + m * g_moon * climb / efficiency)."""
    if duration_s < 0 or elevation_gain_m < 0:
        raise ValueError("Duration and elevation gain must be nonnegative")
    climb = model.rover_mass_kg * LUNAR_GRAVITY_MPS2 * elevation_gain_m
    return 2 * (model.drive_power_w * duration_s + climb / model.climb_efficiency)


def route_comm_gap_s(route: Mapping) -> float:
    """duration * blocked-Earth waypoints / waypoints of one R8 route."""
    blocked = route.get("no_earth_waypoints")
    points = route.get("waypoints") or []
    if blocked is None or not points:
        raise ValueError("Route sky states were not evaluated")
    return float(route["duration_s"]) * blocked / len(points)


def rover_problem_from_routes(
    problem_id: str,
    route_results: Mapping[str, dict],
    science_values: Mapping[str, float],
    *,
    time_budget_s: float,
    energy_budget_j: float,
    max_comm_gap_s: float,
    energy_model: RoverEnergyModel = RoverEnergyModel(),
) -> RoverProblem:
    """Candidates and coded rejections from R8 outputs keyed by variant id."""
    candidates, rejections = [], []
    for variant, result in route_results.items():
        slope = float(result["settings"]["slope_limit_deg"])
        for route in result["routes"]:
            target = route["target_id"]
            if not route["reachable"]:
                rejections.append(
                    RouteRejection(
                        target_id=target,
                        variant_id=variant,
                        code=route["reason"],
                        detail=route.get("detail") or route["reason"],
                    )
                )
                continue
            gap = route_comm_gap_s(route)
            if gap > max_comm_gap_s:
                rejections.append(
                    RouteRejection(
                        target_id=target,
                        variant_id=variant,
                        code="comm_gap_exceeds_limit",
                        detail=f"Sampled no-Earth time {gap:.0f} s exceeds "
                        f"{max_comm_gap_s:.0f} s.",
                    )
                )
                continue
            points = route["waypoints"]
            shadowed = route.get("shadowed_waypoints") or 0
            candidates.append(
                RoverCandidate(
                    target_id=target,
                    variant_id=variant,
                    slope_limit_deg=slope,
                    length_m=float(route["length_m"]),
                    duration_s=float(route["duration_s"]),
                    elevation_gain_m=float(route["elevation_gain_m"]),
                    max_slope_deg=float(route["max_slope_deg"]),
                    energy_j=route_energy_j(
                        float(route["duration_s"]),
                        float(route["elevation_gain_m"]),
                        energy_model,
                    ),
                    comm_gap_s=gap,
                    shadowed_fraction=shadowed / len(points),
                    route_result_id=result["result_id"],
                )
            )
    candidates.sort(key=lambda c: (c.target_id, c.variant_id))
    rejections.sort(key=lambda r: (r.target_id, r.variant_id))
    return RoverProblem(
        problem_id=problem_id,
        site_id=next(iter(route_results.values()))
        .get("site", {})
        .get("site_id", "site")
        if route_results
        else "site",
        science_values=dict(science_values),
        candidates=tuple(candidates),
        rejections=tuple(rejections),
        time_budget_s=time_budget_s,
        energy_budget_j=energy_budget_j,
        max_comm_gap_s=max_comm_gap_s,
    )


def _synthetic_terrain(rng: random.Random, size: int = 121, pixel_m: float = 80.0):
    half = size * pixel_m / 2
    centres = (np.arange(size) + 0.5) * pixel_m - half
    x, y = np.meshgrid(centres, -centres)
    heights = np.zeros((size, size))
    for _ in range(14):
        cx, cy = rng.uniform(-half, half), rng.uniform(-half, half)
        sigma = rng.uniform(250, 700)
        amplitude = rng.uniform(-220, 220)
        heights += amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma**2))
    # Keep the landing area gentle so the lander cell is traversable.
    heights *= 1 - np.exp(-(x**2 + y**2) / (2 * 400.0**2))
    return LocalTerrain(
        heights_m=heights,
        transform=from_origin(-half, half, pixel_m, pixel_m),
        site_xy_m=(0.0, 0.0),
        north_unit_xy=(0.0, 1.0),
        east_unit_xy=(1.0, 0.0),
        pixel_m=pixel_m,
        crs=STEREO_SOUTH,
    )


def _synthetic_sky(rng: random.Random, hours: int = 96):
    start = datetime(2026, 11, 4, tzinfo=timezone.utc)
    phase = rng.uniform(0, 2 * math.pi)
    frames = []
    for h in range(hours + 1):
        moment = start + timedelta(hours=h)
        frames.append(
            {
                "utc": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sun": {"azimuth_deg": (15.0 * h) % 360, "elevation_deg": 1.5},
                "earth": {
                    "azimuth_deg": 0.0,
                    "elevation_deg": 4.0 + 2.0 * math.sin(2 * math.pi * h / 27 + phase),
                },
            }
        )
    lobes = rng.randint(2, 4)
    shift = rng.uniform(0, 360)

    def horizon_at(latitude_deg, longitude_deg_east, altitude_m):
        level = 3.5 + 2.5 * math.sin(math.radians(lobes * longitude_deg_east + shift))
        return {
            "directions": [
                {"azimuth_deg": float(a), "elevation_deg": level} for a in range(360)
            ]
        }

    return frames, horizon_at


def generate_rover(seed: int, *, targets: int = 5) -> RoverProblem:
    """Seeded synthetic DEM and sky, routed by R8 for both variants."""
    rng = random.Random(seed)
    for _ in range(20):
        terrain = _synthetic_terrain(rng)
        frames, horizon = _synthetic_sky(rng)
        items = [
            {
                "target_id": f"T{i + 1}",
                "bearing_deg": round(rng.uniform(0, 360), 3) % 360,
                "distance_m": round(rng.uniform(1500, 4000), 1),
            }
            for i in range(targets)
        ]
        values = {item["target_id"]: float(rng.randint(1, 10)) for item in items}
        results = {}
        for variant, slope in VARIANT_SLOPE_LIMITS_DEG.items():
            request = RouteRequest(
                site_id=f"synthetic-rover-{seed}",
                latitude_deg=-90.0,
                longitude_deg_east=0.0,
                targets=items,
                slope_limit_deg=slope,
            )
            try:
                results[variant] = plan_routes(request, terrain, frames, horizon)
            except ValueError:
                results = None
                break
        if results is None:
            continue
        reachable = [
            r for res in results.values() for r in res["routes"] if r["reachable"]
        ]
        if len(reachable) < 2:
            continue
        model = RoverEnergyModel()
        best_time: dict[str, float] = {}
        best_energy: dict[str, float] = {}
        for r in reachable:
            t = 2 * r["duration_s"]
            e = route_energy_j(r["duration_s"], r["elevation_gain_m"], model)
            best_time[r["target_id"]] = min(best_time.get(r["target_id"], math.inf), t)
            best_energy[r["target_id"]] = min(
                best_energy.get(r["target_id"], math.inf), e
            )
        return rover_problem_from_routes(
            f"rover-{seed}",
            results,
            values,
            time_budget_s=round(0.5 * sum(best_time.values()), 1),
            energy_budget_j=round(0.55 * sum(best_energy.values()), 1),
            max_comm_gap_s=4 * 3600.0,
        )
    raise RuntimeError("Could not generate a rover instance with two reachable routes")


def build_rover(problem: RoverProblem) -> ApplicationInstance:
    """Variables, validator, MILP and QUBO for one problem."""
    problem = RoverProblem.model_validate(problem.model_dump())
    candidates = problem.candidates
    if not candidates:
        raise ValueError("No route candidate passed the R8 and communication checks")
    n = len(candidates)
    ew, es = rational(problem.energy_weight), rational(problem.energy_scale_j)
    gw, gs = rational(problem.gap_weight), rational(problem.gap_scale_s)
    coefficients = [
        rational(problem.science_values[c.target_id])
        - ew * rational(c.energy_j) / es
        - gw * rational(c.comm_gap_s) / gs
        for c in candidates
    ]
    times = [2 * rational(c.duration_s) for c in candidates]
    energies = [rational(c.energy_j) for c in candidates]
    time_budget = rational(problem.time_budget_s)
    energy_budget = rational(problem.energy_budget_j)
    by_target = defaultdict(list)
    for i, c in enumerate(candidates):
        by_target[c.target_id].append(i)

    def evaluate(bits):
        chosen = [i for i, b in enumerate(bits) if b]
        issues, involved = [], set()
        violation = Fraction(0)
        for target, members in by_target.items():
            on = [i for i in members if bits[i]]
            if len(on) > 1:
                violation += len(on) - 1
                issues.append(f"target_multiple_routes:{target}:{len(on)}")
                involved.update(on)
        time = sum((times[i] for i in chosen), Fraction(0))
        energy = sum((energies[i] for i in chosen), Fraction(0))
        if time > time_budget:
            violation += (time - time_budget) / time_budget
            issues.append(f"time_budget:{float(time):.0f}s>{float(time_budget):.0f}s")
            involved.update(chosen)
        if energy > energy_budget:
            violation += (energy - energy_budget) / energy_budget
            issues.append(
                f"energy_budget:{float(energy):.0f}J>{float(energy_budget):.0f}J"
            )
            involved.update(chosen)
        gaps = [candidates[i].comm_gap_s for i in chosen]
        visited = {candidates[i].target_id for i in chosen}
        return Evaluation(
            feasible=violation == 0,
            violation=violation,
            objective=sum((coefficients[i] for i in chosen), Fraction(0)),
            metrics={
                "science_return": float(
                    sum(rational(problem.science_values[t]) for t in visited)
                ),
                "energy_used_j": float(energy),
                "comm_gap_total_s": float(sum(gaps)) if gaps else 0,
                "comm_gap_max_s": float(max(gaps)) if gaps else 0,
                "traverse_time_s": float(time),
                "targets_visited": len(visited),
            },
            issues=tuple(issues),
            violating_variables=tuple(sorted(involved)),
        )

    strength = penalty_strength(coefficients)
    qubo = QuboBuilder(n)
    for i, c in enumerate(coefficients):
        qubo.add_linear(i, -float(c))
    for members in by_target.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                qubo.add_pair(members[a], members[b], strength)
    exact = True
    rows = [
        ({i: 1.0 for i in m}, -math.inf, 1.0) for m in by_target.values() if len(m) > 1
    ]
    for weights, budget in ((times, time_budget), (energies, energy_budget)):
        if sum(weights) <= budget:
            continue
        exact = False
        qubo.add_unbalanced(
            {i: float(w) for i, w in enumerate(weights)}, float(budget), strength
        )
        rows.append(
            ({i: float(w) for i, w in enumerate(weights)}, -math.inf, float(budget))
        )
    encoding = (
        "exact pairwise penalties; budgets non-binding"
        if exact
        else "pairwise penalties plus unbalanced penalties for binding budgets (heuristic)"
    )
    return ApplicationInstance(
        application=APPLICATION,
        instance_id=problem.problem_id,
        variable_labels=tuple(f"{c.target_id}@{c.variant_id}" for c in candidates),
        evaluate=evaluate,
        qubo=qubo.build(exact=exact, encoding=encoding),
        milp=milp_model([float(c) for c in coefficients], rows, decision_count=n),
        input_fingerprint=model_fingerprint(problem),
        source_note=problem.source_note,
        rejections=tuple(
            f"{r.target_id}@{r.variant_id}:{r.code}" for r in problem.rejections
        ),
    )
