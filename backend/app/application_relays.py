"""R13 application 3: relay-location selection after geometry/capacity checks.

Positions are in a site-centred local tangent frame (east/north/up, m) with a
spherical-surface drop d^2/(2R). Horizon masks use the Task 2 azimuth grid and
are applied conservatively at every distance. A relay is credited only with
relay equipment, range, horizon clearance, its own Earth visibility and enough
link capacity. Synthetic planning benchmark; not a validated relay site.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from fractions import Fraction
from itertools import combinations
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator
from shared.constants import MOON_REFERENCE_RADIUS_M
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

APPLICATION = "relay_selection"
FRAME = "site-centred-local-tangent-enu"
Slot = Annotated[int, Field(strict=True, ge=0, le=47)]
RejectionCode = Literal["no_relay_equipment", "no_credited_link", "capacity_exceeded"]


class HorizonMask(InputContract):
    """Horizon elevation (deg) at azimuths 0, step, 2*step, ... (clockwise from north)."""

    azimuth_step_deg: Annotated[float, Field(strict=True, gt=0, le=90)]
    elevations_deg: tuple[Annotated[float, Field(ge=-90, le=90)], ...]

    @model_validator(mode="after")
    def closed_grid(self):
        count = 360 / self.azimuth_step_deg
        if not math.isclose(count, round(count), abs_tol=1e-9):
            raise ValueError("azimuth_step_deg must divide 360 evenly")
        if len(self.elevations_deg) != round(count):
            raise ValueError("One horizon elevation per azimuth step is required")
        return self


class RelaySite(InputContract):
    site_id: Identifier
    east_m: float
    north_m: float
    up_m: float
    observer_height_m: NonNegative = 2.0
    horizon: HorizonMask
    demand_slots: tuple[Slot, ...]
    direct_earth_slots: tuple[Slot, ...] = ()


class RelayCandidate(InputContract):
    relay_id: Identifier
    east_m: float
    north_m: float
    up_m: float
    mast_height_m: NonNegative
    has_relay_equipment: bool
    capacity_links: Annotated[int, Field(strict=True, ge=1, le=8)]
    max_range_m: Positive
    earth_visible_slots: tuple[Slot, ...]


class RelayProblem(InputContract):
    problem_id: Identifier
    start_utc: AwareDatetime
    slot_s: Annotated[int, Field(strict=True, ge=60, le=86400)]
    slot_count: Annotated[int, Field(strict=True, ge=1, le=48)]
    frame: Literal["site-centred-local-tangent-enu"] = FRAME
    sites: tuple[RelaySite, ...] = Field(min_length=1)
    candidates: tuple[RelayCandidate, ...] = Field(min_length=1)
    select_count: Annotated[int, Field(strict=True, ge=1, le=8)]
    clearance_margin_deg: NonNegative = 0.5
    resilience_weight: NonNegative = 0.5
    source_note: str = (
        "synthetic R13 planning benchmark; not a validated relay site or link budget"
    )

    @model_validator(mode="after")
    def consistent(self):
        for label, ids in (
            ("Site", [s.site_id for s in self.sites]),
            ("Relay", [c.relay_id for c in self.candidates]),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{label} IDs must be unique")
        if self.select_count > len(self.candidates):
            raise ValueError("select_count exceeds the candidate count")
        slots = [t for s in self.sites for t in s.demand_slots + s.direct_earth_slots]
        slots += [t for c in self.candidates for t in c.earth_visible_slots]
        if any(t >= self.slot_count for t in slots):
            raise ValueError("Slot outside the horizon")
        return self


class RelayLink(InputContract):
    relay_id: Identifier
    site_id: Identifier
    range_m: NonNegative
    azimuth_deg: float
    elevation_deg: float
    horizon_deg: float
    line_of_sight: bool
    in_range: bool
    credited_slots: tuple[Slot, ...]


class RelayRejection(InputContract):
    relay_id: Identifier
    code: RejectionCode
    detail: str


class RelayValidation(InputContract):
    accepted_relay_ids: tuple[Identifier, ...]
    rejections: tuple[RelayRejection, ...]
    links: tuple[RelayLink, ...]
    demand_points: tuple[tuple[Identifier, int], ...]


def horizon_at(mask: HorizonMask, azimuth_deg: float) -> float:
    """Linear interpolation with azimuth wrap-around."""
    if not math.isfinite(azimuth_deg):
        raise ValueError("Azimuth must be finite")
    step = mask.azimuth_step_deg
    values = mask.elevations_deg
    position = (azimuth_deg % 360.0) / step
    low = int(math.floor(position)) % len(values)
    high = (low + 1) % len(values)
    fraction = position - math.floor(position)
    return values[low] + (values[high] - values[low]) * fraction


def link_geometry(site: RelaySite, relay: RelayCandidate) -> tuple[float, float, float]:
    """(range_m, azimuth_deg, elevation_deg) of the relay seen from the site."""
    de = relay.east_m - site.east_m
    dn = relay.north_m - site.north_m
    d = math.hypot(de, dn)
    du = (relay.up_m + relay.mast_height_m) - (site.up_m + site.observer_height_m)
    du -= d * d / (2 * MOON_REFERENCE_RADIUS_M)
    azimuth = math.degrees(math.atan2(de, dn)) % 360.0 if d > 0 else 0.0
    elevation = math.degrees(math.atan2(du, d))
    return math.hypot(d, du), azimuth, elevation


def _demand(site: RelaySite) -> tuple[int, ...]:
    return tuple(sorted(set(site.demand_slots) - set(site.direct_earth_slots)))


def validate_relay_geometry(problem: RelayProblem) -> RelayValidation:
    """Credited links and coded rejections before any selection."""
    links, rejections, accepted = [], [], []
    for relay in problem.candidates:
        credited_any = False
        per_slot: dict[int, int] = {}
        earth = set(relay.earth_visible_slots)
        for site in problem.sites:
            range_m, azimuth, elevation = link_geometry(site, relay)
            horizon = horizon_at(site.horizon, azimuth)
            sight = elevation >= horizon + problem.clearance_margin_deg
            in_range = range_m <= relay.max_range_m
            credited = ()
            if relay.has_relay_equipment and sight and in_range:
                credited = tuple(t for t in _demand(site) if t in earth)
            for t in credited:
                per_slot[t] = per_slot.get(t, 0) + 1
            credited_any = credited_any or bool(credited)
            links.append(
                RelayLink(
                    relay_id=relay.relay_id,
                    site_id=site.site_id,
                    range_m=range_m,
                    azimuth_deg=azimuth,
                    elevation_deg=elevation,
                    horizon_deg=horizon,
                    line_of_sight=sight,
                    in_range=in_range,
                    credited_slots=credited,
                )
            )
        if not relay.has_relay_equipment:
            rejections.append(
                RelayRejection(
                    relay_id=relay.relay_id,
                    code="no_relay_equipment",
                    detail="The candidate carries no relay terminal.",
                )
            )
        elif not credited_any:
            rejections.append(
                RelayRejection(
                    relay_id=relay.relay_id,
                    code="no_credited_link",
                    detail="No demand point has line of sight, range and relay Earth "
                    "visibility together.",
                )
            )
        elif max(per_slot.values()) > relay.capacity_links:
            worst = max(per_slot, key=lambda t: (per_slot[t], -t))
            rejections.append(
                RelayRejection(
                    relay_id=relay.relay_id,
                    code="capacity_exceeded",
                    detail=f"{per_slot[worst]} simultaneous links in slot {worst} "
                    f"exceed capacity {relay.capacity_links}.",
                )
            )
        else:
            accepted.append(relay.relay_id)
    points = tuple((s.site_id, t) for s in problem.sites for t in _demand(s))
    return RelayValidation(
        accepted_relay_ids=tuple(accepted),
        rejections=tuple(rejections),
        links=tuple(links),
        demand_points=points,
    )


def _coverage(validation: RelayValidation) -> dict[str, set]:
    sets: dict[str, set] = {rid: set() for rid in validation.accepted_relay_ids}
    for link in validation.links:
        if link.relay_id in sets:
            sets[link.relay_id].update((link.site_id, t) for t in link.credited_slots)
    return sets


def relay_metrics(
    problem: RelayProblem, validation: RelayValidation, selected: tuple[str, ...]
) -> dict:
    """Coverage, single-failure resilience, double coverage and longest gaps."""
    sets = _coverage(validation)
    unknown = [rid for rid in selected if rid not in sets]
    if unknown:
        raise ValueError(f"Relays not validated: {', '.join(unknown)}")
    points = validation.demand_points
    total = len(points)
    counts = {p: sum(p in sets[rid] for rid in selected) for p in points}
    covered = sum(c > 0 for c in counts.values())
    double = sum(c > 1 for c in counts.values())
    resilient = 0
    if selected:
        resilient = min(
            sum(any(p in sets[o] for o in selected if o != rid) for p in points)
            for rid in selected
        )
    longest = 0
    for site in problem.sites:
        run = best = 0
        previous = None
        for t in _demand(site):
            if counts[(site.site_id, t)]:
                run = 0
            else:
                run = (
                    run + 1 if previous is not None and t == previous + 1 and run else 1
                )
                best = max(best, run)
            previous = t
        longest = max(longest, best)
    return {
        "selected": len(selected),
        "demand_points": total,
        "covered_points": covered,
        "coverage": covered / total if total else None,
        "resilient_points": resilient,
        "resilience": resilient / total if total else None,
        "double_coverage": double / total if total else None,
        "longest_gap_s": longest * problem.slot_s,
    }


def generate_relays(
    seed: int,
    *,
    sites: int = 3,
    candidates: int = 8,
    slots: int = 8,
    select_count: int = 2,
    start_utc: datetime | None = None,
) -> RelayProblem:
    """Seeded synthetic instance (random.Random)."""
    rng = random.Random(seed)
    for _ in range(200):
        site_models = []
        for k in range(sites):
            demand = tuple(range(slots))
            direct = tuple(t for t in range(slots) if rng.random() < 0.25)
            site_models.append(
                RelaySite(
                    site_id=f"S{k + 1}",
                    east_m=rng.uniform(-3000, 3000),
                    north_m=rng.uniform(-3000, 3000),
                    up_m=rng.uniform(-50, 50),
                    horizon=HorizonMask(
                        azimuth_step_deg=30.0,
                        elevations_deg=tuple(rng.uniform(0, 4) for _ in range(12)),
                    ),
                    demand_slots=demand,
                    direct_earth_slots=direct,
                )
            )
        relays = []
        for k in range(candidates):
            relays.append(
                RelayCandidate(
                    relay_id=f"R{k + 1}",
                    east_m=rng.uniform(-5000, 5000),
                    north_m=rng.uniform(-5000, 5000),
                    up_m=rng.uniform(0, 400),
                    mast_height_m=rng.choice((5.0, 10.0, 20.0)),
                    has_relay_equipment=rng.random() < 0.9,
                    capacity_links=rng.choice((1, 2, 3)),
                    max_range_m=rng.choice((4000.0, 6000.0, 9000.0)),
                    earth_visible_slots=tuple(
                        t for t in range(slots) if rng.random() < 0.7
                    ),
                )
            )
        problem = RelayProblem(
            problem_id=f"relays-{seed}",
            start_utc=start_utc or datetime(2026, 11, 20, tzinfo=timezone.utc),
            slot_s=3600,
            slot_count=slots,
            sites=tuple(site_models),
            candidates=tuple(relays),
            select_count=select_count,
        )
        validation = validate_relay_geometry(problem)
        if (
            len(validation.accepted_relay_ids) >= select_count + 1
            and validation.demand_points
        ):
            return problem
    raise RuntimeError(
        "Could not generate a relay instance with enough valid candidates"
    )


def build_relays(problem: RelayProblem) -> ApplicationInstance:
    """Validated candidates become the variables; raises ValueError when fewer
    than `select_count` candidates are validated."""
    problem = RelayProblem.model_validate(problem.model_dump())
    validation = validate_relay_geometry(problem)
    ids = validation.accepted_relay_ids
    k = problem.select_count
    if len(ids) < k:
        raise ValueError(f"Only {len(ids)} validated relays for select_count {k}")
    if not validation.demand_points:
        raise ValueError("No demand point needs a relay")
    sets = _coverage(validation)
    points = validation.demand_points
    n = len(ids)
    weight = rational(problem.resilience_weight)

    def evaluate(bits):
        selected = tuple(rid for rid, b in zip(ids, bits) if b)
        metrics = relay_metrics(problem, validation, selected)
        count = len(selected)
        objective = metrics["covered_points"] + weight * metrics["resilient_points"]
        issues = () if count == k else (f"select_count:{count}!={k}",)
        return Evaluation(
            feasible=count == k,
            violation=Fraction(abs(count - k)),
            objective=Fraction(objective),
            metrics=metrics,
            issues=issues,
            violating_variables=() if count == k else tuple(range(n)),
        )

    a = [[int(p in sets[rid]) for p in points] for rid in ids]
    cover = [sum(row) for row in a]
    overlap = {
        (i, j): sum(x and y for x, y in zip(a[i], a[j]))
        for i, j in combinations(range(n), 2)
    }
    w = float(weight)
    coefficients = cover + [v * (1 + w) for v in overlap.values()]
    strength = penalty_strength(coefficients)
    qubo = QuboBuilder(n)
    for i in range(n):
        qubo.add_linear(i, -cover[i])
    for (i, j), v in overlap.items():
        qubo.add_pair(
            i, j, v - w * v
        )  # inclusion-exclusion; double cover as resilience proxy
    qubo.constant += strength * k * k
    for i in range(n):
        qubo.add_linear(i, strength * (1 - 2 * k))
    for i, j in combinations(range(n), 2):
        qubo.add_pair(i, j, 2 * strength)
    P = len(points)
    # Columns: x (n), y_p (P), r_{i,p} (n*P), z (1).
    columns = n + P + n * P + 1
    objective = [0.0] * n + [1.0] * P + [0.0] * (n * P) + [w]
    rows = [({i: 1.0 for i in range(n)}, float(k), float(k))]
    for p in range(P):
        row = {n + p: 1.0}
        for i in range(n):
            if a[i][p]:
                row[i] = -1.0
        rows.append((row, -math.inf, 0.0))
    for i in range(n):
        for p in range(P):
            col = n + P + i * P + p
            row = {col: 1.0}
            for j in range(n):
                if j != i and a[j][p]:
                    row[j] = -1.0
            rows.append((row, -math.inf, 0.0))
        row = {columns - 1: 1.0, i: float(P)}
        for p in range(P):
            row[n + P + i * P + p] = -1.0
        rows.append((row, -math.inf, float(P)))
    model = milp_model(
        objective,
        rows,
        decision_count=n,
        integrality=[1] * n + [0] * (columns - n),
        lower=[0.0] * columns,
        upper=[1.0] * (columns - 1) + [float(P)],
    )
    return ApplicationInstance(
        application=APPLICATION,
        instance_id=problem.problem_id,
        variable_labels=ids,
        evaluate=evaluate,
        qubo=qubo.build(
            exact=False,
            encoding="second-order inclusion-exclusion coverage, double-coverage "
            "resilience proxy and exact-k penalty (heuristic)",
        ),
        milp=model,
        input_fingerprint=model_fingerprint(problem),
        source_note=problem.source_note,
        rejections=tuple(f"{r.relay_id}:{r.code}" for r in validation.rejections),
    )
