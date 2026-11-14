"""R13 application 1: communication-slot allocation on shared stations.

Synthetic planning benchmark shaped like R2/R11 inputs: station availability,
mission-station visibility and link rates are separate inputs. Not a DSN
booking. See docs/quantum-applications.md.
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

Slot = Annotated[int, Field(strict=True, ge=0, le=47)]
APPLICATION = "comm_slots"


class CommStation(InputContract):
    station_id: Identifier
    capacity_links: Annotated[int, Field(strict=True, ge=1, le=4)]
    available_slots: tuple[Slot, ...]


class CommVisibility(InputContract):
    """Geometry evidence only; availability and capacity stay separate."""

    mission_id: Identifier
    station_id: Identifier
    visible_slots: tuple[Slot, ...]


class CommRate(InputContract):
    mission_id: Identifier
    station_id: Identifier
    rate_bps: Positive


class CommRequest(InputContract):
    request_id: Identifier
    mission_id: Identifier
    volume_bits: Positive
    ready_slot: Slot
    deadline_slot: Slot


class CommSlotProblem(InputContract):
    problem_id: Identifier
    start_utc: AwareDatetime
    slot_s: Annotated[int, Field(strict=True, ge=60, le=86400)]
    slot_count: Annotated[int, Field(strict=True, ge=1, le=48)]
    stations: tuple[CommStation, ...] = Field(min_length=1)
    visibility: tuple[CommVisibility, ...]
    rates: tuple[CommRate, ...]
    requests: tuple[CommRequest, ...] = Field(min_length=1)
    bit_scale: Positive = 1e8
    latency_weight_per_slot: NonNegative = 0.05
    source_note: str = "synthetic R13 planning benchmark; not a DSN booking"

    @model_validator(mode="after")
    def consistent(self):
        station_ids = [s.station_id for s in self.stations]
        request_ids = [r.request_id for r in self.requests]
        if len(set(station_ids)) != len(station_ids):
            raise ValueError("Station IDs must be unique")
        if len(set(request_ids)) != len(request_ids):
            raise ValueError("Request IDs must be unique")
        missions = {r.mission_id for r in self.requests}
        stations = set(station_ids)
        for request in self.requests:
            if request.ready_slot > request.deadline_slot:
                raise ValueError(f"{request.request_id}: ready slot after deadline")
            if request.deadline_slot >= self.slot_count:
                raise ValueError(f"{request.request_id}: deadline outside the horizon")
        for station in self.stations:
            if any(t >= self.slot_count for t in station.available_slots):
                raise ValueError(f"{station.station_id}: slot outside the horizon")
        for label, items in (("visibility", self.visibility), ("rate", self.rates)):
            pairs = [(i.mission_id, i.station_id) for i in items]
            if len(set(pairs)) != len(pairs):
                raise ValueError(f"Duplicate {label} mission/station pair")
            for mission, station in pairs:
                if mission not in missions or station not in stations:
                    raise ValueError(f"Unknown {label} reference {mission}/{station}")
        for item in self.visibility:
            if any(t >= self.slot_count for t in item.visible_slots):
                raise ValueError("Visibility slot outside the horizon")
        return self


def generate_comm_slots(
    seed: int,
    *,
    missions: int = 3,
    requests_per_mission: int = 2,
    stations: int = 2,
    slots: int = 6,
    capacity_links: int = 1,
    start_utc: datetime | None = None,
) -> CommSlotProblem:
    """Seeded synthetic instance (random.Random)."""
    rng = random.Random(seed)
    station_ids = [f"ST{k + 1}" for k in range(stations)]
    mission_ids = [f"M{m + 1}" for m in range(missions)]
    station_models = []
    for sid in station_ids:
        available = tuple(t for t in range(slots) if rng.random() < 0.8)
        station_models.append(
            CommStation(
                station_id=sid,
                capacity_links=capacity_links,
                available_slots=available or (rng.randrange(slots),),
            )
        )
    visibility, rates, requests = [], [], []
    for mid in mission_ids:
        for sid in station_ids:
            visible = tuple(t for t in range(slots) if rng.random() < 0.6)
            visibility.append(
                CommVisibility(mission_id=mid, station_id=sid, visible_slots=visible)
            )
            rates.append(
                CommRate(
                    mission_id=mid,
                    station_id=sid,
                    rate_bps=rng.choice((5e4, 1e5, 2e5)),
                )
            )
        for r in range(requests_per_mission):
            ready = rng.randrange(max(1, slots - 1))
            deadline = rng.randrange(ready + 1, slots) if ready + 1 < slots else ready
            requests.append(
                CommRequest(
                    request_id=f"{mid}-R{r + 1}",
                    mission_id=mid,
                    volume_bits=float(rng.choice((1e8, 2e8, 3e8, 5e8))),
                    ready_slot=ready,
                    deadline_slot=deadline,
                )
            )
    # Guarantee at least one link: first request's mission sees an available slot.
    first = requests[0]
    station = station_models[0]
    slot = next(
        (
            t
            for t in station.available_slots
            if first.ready_slot <= t <= first.deadline_slot
        ),
        None,
    )
    if slot is None:
        slot = first.ready_slot
        station_models[0] = station.model_copy(
            update={"available_slots": tuple(sorted({*station.available_slots, slot}))}
        )
    index = next(
        i
        for i, v in enumerate(visibility)
        if v.mission_id == first.mission_id and v.station_id == station.station_id
    )
    visibility[index] = visibility[index].model_copy(
        update={
            "visible_slots": tuple(sorted({*visibility[index].visible_slots, slot}))
        }
    )
    return CommSlotProblem(
        problem_id=f"comm-slots-{seed}",
        start_utc=start_utc or datetime(2026, 11, 20, tzinfo=timezone.utc),
        slot_s=3600,
        slot_count=slots,
        stations=tuple(station_models),
        visibility=tuple(visibility),
        rates=tuple(rates),
        requests=tuple(requests),
    )


def build_comm_slots(problem: CommSlotProblem) -> ApplicationInstance:
    """Variables, validator, MILP and QUBO for one problem."""
    problem = CommSlotProblem.model_validate(problem.model_dump())
    stations = {s.station_id: s for s in problem.stations}
    visible = {
        (v.mission_id, v.station_id): set(v.visible_slots) for v in problem.visibility
    }
    rate = {(r.mission_id, r.station_id): r.rate_bps for r in problem.rates}
    variables = []  # (request, station_id, slot)
    for request in problem.requests:
        for station in problem.stations:
            key = (request.mission_id, station.station_id)
            if key not in rate:
                continue
            for t in sorted(set(station.available_slots) & visible.get(key, set())):
                if request.ready_slot <= t <= request.deadline_slot:
                    variables.append((request, station.station_id, t))
    if not variables:
        raise ValueError("No request has a visible, available station slot")
    bit_scale = rational(problem.bit_scale)
    latency_weight = rational(problem.latency_weight_per_slot)
    delivered = [
        min(rational(r.volume_bits), rational(rate[(r.mission_id, k)]) * problem.slot_s)
        for r, k, _ in variables
    ]
    coefficients = [
        d / bit_scale - latency_weight * (t - r.ready_slot)
        for d, (r, _, t) in zip(delivered, variables)
    ]
    by_request, by_station_slot, by_mission_slot = (
        defaultdict(list),
        defaultdict(list),
        defaultdict(list),
    )
    for i, (r, k, t) in enumerate(variables):
        by_request[r.request_id].append(i)
        by_station_slot[(k, t)].append(i)
        by_mission_slot[(r.mission_id, t)].append(i)
    total_volume = sum(rational(r.volume_bits) for r in problem.requests)

    def evaluate(bits):
        chosen = [i for i, b in enumerate(bits) if b]
        issues, involved = [], set()
        violation = Fraction(0)
        conflicts = 0
        for rid, members in by_request.items():
            on = [i for i in members if bits[i]]
            if len(on) > 1:
                violation += len(on) - 1
                issues.append(f"request_multiple_passes:{rid}:{len(on)}")
                involved.update(on)
        for (k, t), members in by_station_slot.items():
            on = [i for i in members if bits[i]]
            excess = len(on) - stations[k].capacity_links
            if excess > 0:
                conflicts += excess
                issues.append(f"station_capacity:{k}:slot{t}:{len(on)}")
                involved.update(on)
        for (m, t), members in by_mission_slot.items():
            on = [i for i in members if bits[i]]
            if len(on) > 1:
                conflicts += len(on) - 1
                issues.append(f"mission_transmitter:{m}:slot{t}:{len(on)}")
                involved.update(on)
        violation += conflicts
        served = {}
        for i in chosen:
            rid = variables[i][0].request_id
            if rid not in served:  # one pass counts towards delivery
                served[rid] = i
        delivered_bits = sum((delivered[i] for i in served.values()), Fraction(0))
        latencies = [
            (variables[i][2] + 1 - variables[i][0].ready_slot) * problem.slot_s
            for i in served.values()
        ]
        objective = sum((coefficients[i] for i in chosen), Fraction(0))
        return Evaluation(
            feasible=violation == 0,
            violation=violation,
            objective=objective,
            metrics={
                "conflicts": conflicts,
                "unmet_demand_bits": float(total_volume - delivered_bits),
                "delivered_bits": float(delivered_bits),
                "served_requests": len(served),
                "mean_latency_s": float(sum(latencies) / len(latencies))
                if latencies
                else None,
                "max_latency_s": float(max(latencies)) if latencies else None,
            },
            issues=tuple(issues),
            violating_variables=tuple(sorted(involved)),
        )

    n = len(variables)
    strength = penalty_strength(coefficients)
    qubo = QuboBuilder(n)
    for i, c in enumerate(coefficients):
        qubo.add_linear(i, -float(c))
    exact = True
    for members in list(by_request.values()) + list(by_mission_slot.values()):
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                qubo.add_pair(members[a], members[b], strength)
    for (k, _), members in by_station_slot.items():
        capacity = stations[k].capacity_links
        if len(members) <= capacity:
            continue
        if capacity == 1:
            for a in range(len(members)):
                for b in range(a + 1, len(members)):
                    qubo.add_pair(members[a], members[b], strength)
        else:
            exact = False
            qubo.add_unbalanced({i: 1.0 for i in members}, float(capacity), strength)
    encoding = (
        "exact pairwise penalties (A = 1 + 2 sum|c|)"
        if exact
        else "pairwise penalties plus unbalanced penalties for capacity > 1 (heuristic)"
    )
    rows = []
    for members in by_request.values():
        rows.append(({i: 1.0 for i in members}, -math.inf, 1.0))
    for (k, _), members in by_station_slot.items():
        if len(members) > stations[k].capacity_links:
            rows.append(
                (
                    {i: 1.0 for i in members},
                    -math.inf,
                    float(stations[k].capacity_links),
                )
            )
    for members in by_mission_slot.values():
        if len(members) > 1:
            rows.append(({i: 1.0 for i in members}, -math.inf, 1.0))
    return ApplicationInstance(
        application=APPLICATION,
        instance_id=problem.problem_id,
        variable_labels=tuple(f"{r.request_id}@{k}#{t}" for r, k, t in variables),
        evaluate=evaluate,
        qubo=qubo.build(exact=exact, encoding=encoding),
        milp=milp_model([float(c) for c in coefficients], rows, decision_count=n),
        input_fingerprint=model_fingerprint(problem),
        source_note=problem.source_note,
    )
