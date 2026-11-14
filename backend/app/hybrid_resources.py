"""H3 adapters for geometric contact, explicit calendars and ideal storage budgets."""

import hashlib
import json
import math
from fractions import Fraction
from typing import Literal
from shared.hybrid import (
    Contract,
    Identifier,
    NonNegative,
    Positive,
    Source,
    TimeWindow,
    Scenario,
    Plan,
    Demand,
    Resource,
    validate_plan_references,
)


class GeometryInterval(Contract):
    window: TimeWindow
    state: Literal["available", "unavailable", "unknown"]


class StationCalendar(Contract):
    station_id: Identifier
    capacity_channels: NonNegative | None
    availability: tuple[TimeWindow, ...]
    source: Source


class ContactRequirement(Contract):
    requirement_id: Identifier
    option_id: Identifier
    station_id: Identifier
    window: TimeWindow
    channels: NonNegative = 1
    geometry: tuple[GeometryInterval, ...]
    source: Source


class GenerationSegment(Contract):
    window: TimeWindow
    power_w: NonNegative
    source: Source


class BatteryProfile(Contract):
    battery_id: Identifier
    initial_energy_j: NonNegative
    capacity_j: Positive
    reserve_j: NonNegative
    generation: tuple[GenerationSegment, ...] = ()
    source: Source


class ActivityLoad(Contract):
    activity_id: Identifier
    option_id: Identifier
    battery_id: Identifier
    window: TimeWindow
    power_w: NonNegative
    source: Source


class BatteryTraceRow(Contract):
    window: TimeWindow
    start_energy_j: float
    end_energy_j: float
    generated_energy_j: float
    consumed_energy_j: float
    spilled_energy_j: float
    reserve_violated: bool


def contact_from_r2(
    result: dict,
    *,
    requirement_id: str,
    option_id: str,
    station_id: str,
    window: TimeWindow,
    source: Source,
    channels: float = 1,
) -> ContactRequirement:
    """Adapt R2 per-station geometry, preserving unknown states and explicit provenance.

    No station capacity/booking is inferred. Invalid/missing station timelines raise
    ValueError. Inputs use UTC intervals and concurrent channel demand.
    """
    try:
        rows = result["stations"][station_id]["intervals"]
        geometry = tuple(
            GeometryInterval(
                window=TimeWindow(start_utc=row["start_utc"], end_utc=row["end_utc"]),
                state=row["state"],
            )
            for row in rows
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("Missing or malformed R2 station intervals") from exc
    requirement = ContactRequirement(
        requirement_id=requirement_id,
        option_id=option_id,
        station_id=station_id,
        window=window,
        channels=channels,
        geometry=geometry,
        source=source,
    )
    _check_geometry(requirement)
    return requirement


def integrate_resources(
    scenario: Scenario,
    *,
    stations: tuple[StationCalendar, ...] = (),
    contacts: tuple[ContactRequirement, ...] = (),
    batteries: tuple[BatteryProfile, ...] = (),
    activities: tuple[ActivityLoad, ...] = (),
    max_time_cuts: int = 16,
) -> Scenario:
    """Return a revised H0 scenario with contact gates and cumulative storage inequalities.

    Storage is ideal constant-power, unit efficiency, with spill above capacity and
    a fixed reserve; no physics/uncertainty is inferred. Exact endpoint interval
    inequalities encode storage feasibility. Reject malformed, unsupported event,
    duplicate, unresolved or overly large inputs with ValueError. Unknown geometry
    within required demand and unknown station capacity remain incomplete inputs.
    """
    scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
    if scenario.events:
        raise ValueError(
            "Unapplied events are unsupported; prepare an explicit static snapshot first"
        )
    if type(max_time_cuts) is not int or not 2 <= max_time_cuts <= 32:
        raise ValueError("max_time_cuts must be an integer in [2, 32]")
    stations = _checked(stations, StationCalendar, "station_id")
    contacts = _checked(contacts, ContactRequirement, "requirement_id")
    batteries = _checked(batteries, BatteryProfile, "battery_id")
    activities = _checked(activities, ActivityLoad, "activity_id")
    if not (stations or contacts or batteries or activities):
        raise ValueError("At least one explicit resource input is required")
    station_map = {station.station_id: station for station in stations}
    battery_map = {battery.battery_id: battery for battery in batteries}
    candidates = {c.option_id: c.model_dump(mode="python") for c in scenario.candidates}
    sources = {s.source_id: s for s in scenario.sources}
    parents = set()
    for item in (*stations, *contacts, *batteries, *activities):
        _add_source(sources, item.source)
        parents.add(item.source.source_id)
    for profile in batteries:
        _check_battery(profile, scenario.window)
        for segment in profile.generation:
            _add_source(sources, segment.source)
            parents.add(segment.source.source_id)
    model_source = Source(
        source_id="h3-resource-integration-v1",
        kind="derived",
        description="Explicit R2 geometry gates, station calendars and ideal constant-power storage interval constraints; no operational link or battery certification.",
        version="h3-v1",
        parent_source_ids=tuple(sorted(parents)),
    )
    _add_source(sources, model_source)
    resources = {r.resource_id: r for r in scenario.resources}
    for calendar in stations:
        rid = _key("station", calendar.station_id)
        resource = Resource(
            resource_id=rid,
            mode="concurrent",
            unit="channel",
            capacity=calendar.capacity_channels,
            availability=calendar.availability,
            source_ids=(calendar.source.source_id, model_source.source_id),
        )
        _add_resource(resources, resource)
    for req in contacts:
        _require_option(candidates, req.option_id, req.window)
        if req.station_id not in station_map:
            raise ValueError("Contact refers to unknown station")
        _check_geometry(req)
        unknown = any(
            row.state == "unknown" and _intersection(row.window, req.window) is not None
            for row in req.geometry
        )
        allowed = []
        for row in req.geometry:
            clipped = _intersection(row.window, scenario.window)
            if row.state != "unavailable" and clipped is not None:
                allowed.append(clipped)
        gate_id = _key("geometry", req.requirement_id)
        gate = Resource(
            resource_id=gate_id,
            mode="concurrent",
            unit="channel",
            capacity=None if unknown and req.channels > 0 else req.channels,
            availability=_merge_windows(allowed),
            source_ids=(req.source.source_id, model_source.source_id),
        )
        _add_resource(resources, gate)
        for rid in (_key("station", req.station_id), gate_id):
            candidates[req.option_id]["demands"] += (
                Demand(
                    resource_id=rid,
                    amount=req.channels,
                    unit="channel",
                    window=req.window,
                ),
            )
        candidates[req.option_id]["source_ids"] = tuple(
            sorted(
                set(candidates[req.option_id]["source_ids"])
                | {
                    req.source.source_id,
                    station_map[req.station_id].source.source_id,
                    model_source.source_id,
                }
            )
        )
    for load in activities:
        _require_option(candidates, load.option_id, load.window)
        if load.battery_id not in battery_map:
            raise ValueError("Activity refers to unknown battery")
        candidates[load.option_id]["source_ids"] = tuple(
            sorted(
                set(candidates[load.option_id]["source_ids"])
                | {load.source.source_id, model_source.source_id}
            )
        )
    for profile in batteries:
        loads = tuple(
            load for load in activities if load.battery_id == profile.battery_id
        )
        cuts = _time_cuts(scenario.window, profile, loads)
        if len(cuts) > max_time_cuts:
            raise ValueError("Battery time-cut budget exceeded")
        for i, start in enumerate(cuts[:-1]):
            for end in cuts[i + 1 :]:
                interval = TimeWindow(start_utc=start, end_utc=end)
                stored = profile.initial_energy_j if i == 0 else profile.capacity_j
                budget = (
                    _fraction(stored)
                    - _fraction(profile.reserve_j)
                    + _generated(profile, interval)
                )
                rid = _key(
                    "energy", profile.battery_id, start.isoformat(), end.isoformat()
                )
                resource = Resource(
                    resource_id=rid,
                    mode="budget",
                    unit="J",
                    capacity=_energy_number(budget),
                    availability=(interval,),
                    source_ids=(profile.source.source_id, model_source.source_id),
                )
                _add_resource(resources, resource)
                for load in loads:
                    overlap = _intersection(load.window, interval)
                    if overlap is None or load.power_w == 0:
                        continue
                    energy = _fraction(load.power_w) * _seconds(overlap)
                    candidates[load.option_id]["demands"] += (
                        Demand(
                            resource_id=rid,
                            unit="J",
                            amount=_energy_number(energy),
                            window=overlap,
                        ),
                    )
    raw = scenario.model_dump(mode="python")
    raw.update(
        revision=scenario.revision + 1,
        resources=tuple(resources.values()),
        candidates=tuple(candidates.values()),
        sources=tuple(sources.values()),
    )
    return Scenario.model_validate(raw)


def battery_trace(
    plan: Plan,
    scenario: Scenario,
    battery: BatteryProfile,
    activities: tuple[ActivityLoad, ...],
) -> tuple[BatteryTraceRow, ...]:
    """Independently propagate ideal storage under selected loads at every power boundary.

    Clamp surplus to capacity, report spill and reserve violations without hiding
    deficits. UTC intervals, watts and joules. Reject stale/invalid input with
    ValueError. This diagnostic does not certify physical battery behavior.
    """
    scenario = Scenario.model_validate(scenario.model_dump(mode="python"))
    validate_plan_references(plan, scenario)
    if scenario.events:
        raise ValueError("Unapplied events are unsupported")
    battery = BatteryProfile.model_validate(battery.model_dump(mode="python"))
    _check_battery(battery, scenario.window)
    activities = _checked(activities, ActivityLoad, "activity_id")
    selected = set(plan.selected_option_ids)
    candidates = {c.option_id: c.model_dump(mode="python") for c in scenario.candidates}
    for load in activities:
        _require_option(candidates, load.option_id, load.window)
    loads = tuple(
        load
        for load in activities
        if load.battery_id == battery.battery_id and load.option_id in selected
    )
    cuts = _time_cuts(scenario.window, battery, loads)
    energy = _fraction(battery.initial_energy_j)
    capacity, reserve = _fraction(battery.capacity_j), _fraction(battery.reserve_j)
    rows = []
    for start, end in zip(cuts, cuts[1:]):
        interval = TimeWindow(start_utc=start, end_utc=end)
        generated = _generated(battery, interval)
        consumed = sum(
            (
                _fraction(load.power_w) * _seconds(overlap)
                for load in loads
                if (overlap := _intersection(load.window, interval)) is not None
            ),
            Fraction(0),
        )
        before = energy
        unconstrained = energy + generated - consumed
        spilled = max(Fraction(0), unconstrained - capacity)
        energy = min(capacity, unconstrained)
        rows.append(
            BatteryTraceRow(
                window=interval,
                start_energy_j=float(before),
                end_energy_j=float(energy),
                generated_energy_j=float(generated),
                consumed_energy_j=float(consumed),
                spilled_energy_j=float(spilled),
                reserve_violated=min(before, energy) < reserve,
            )
        )
    return tuple(rows)


def _checked(items, cls, identity):
    checked = tuple(
        cls.model_validate(item.model_dump(mode="python")) for item in items
    )
    ids = [getattr(item, identity) for item in checked]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate {identity}")
    return tuple(sorted(checked, key=lambda item: getattr(item, identity)))


def _key(prefix, *parts):
    return prefix + "-" + hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _fraction(value):
    return Fraction(str(value))


def _energy_number(value):
    # H0 stores floats. Refuse an inexact decimal conversion rather than quietly
    # perturbing a hard budget at the serialization boundary.
    try:
        converted = float(value)
    except OverflowError as exc:
        raise ValueError("Energy exceeds finite H0 range") from exc
    if not math.isfinite(converted) or _fraction(converted) != value:
        raise ValueError(
            "Energy precision exceeds H0 numeric representation; supply explicitly rounded inputs"
        )
    return converted


def _seconds(window):
    delta = window.end_utc - window.start_utc
    return Fraction(delta.days * 86400 + delta.seconds) + Fraction(
        delta.microseconds, 1_000_000
    )


def _intersection(left, right):
    start = max(left.start_utc, right.start_utc)
    end = min(left.end_utc, right.end_utc)
    return TimeWindow(start_utc=start, end_utc=end) if start < end else None


def _contained(inner, outer):
    return outer.start_utc <= inner.start_utc < inner.end_utc <= outer.end_utc


def _check_geometry(req):
    rows = req.geometry
    if (
        not rows
        or rows[0].window.start_utc > req.window.start_utc
        or rows[-1].window.end_utc < req.window.end_utc
    ):
        raise ValueError("Station geometry does not cover requested contact")
    if any(a.window.end_utc != b.window.start_utc for a, b in zip(rows, rows[1:])):
        raise ValueError(
            "Station geometry must be ordered, contiguous and nonoverlapping"
        )


def _merge_windows(windows):
    merged = []
    for window in sorted(windows, key=lambda w: w.start_utc):
        if merged and window.start_utc <= merged[-1].end_utc:
            merged[-1] = TimeWindow(
                start_utc=merged[-1].start_utc,
                end_utc=max(merged[-1].end_utc, window.end_utc),
            )
        else:
            merged.append(window)
    return tuple(merged)


def _require_option(candidates, option_id, interval):
    if option_id not in candidates:
        raise ValueError("Resource demand refers to unknown option")
    candidate_window = TimeWindow.model_validate(candidates[option_id]["window"])
    if not _contained(interval, candidate_window):
        raise ValueError("Resource demand lies outside candidate window")


def _add_source(sources, source):
    if source.source_id in sources and sources[source.source_id] != source:
        raise ValueError("Conflicting source identity")
    sources[source.source_id] = source


def _add_resource(resources, resource):
    if resource.resource_id in resources:
        raise ValueError("Resource already exists; integration cannot be applied twice")
    resources[resource.resource_id] = resource


def _check_battery(profile, horizon):
    if not profile.reserve_j <= profile.initial_energy_j <= profile.capacity_j:
        raise ValueError("Require reserve <= initial energy <= battery capacity")
    segments = sorted(profile.generation, key=lambda segment: segment.window.start_utc)
    if any(not _contained(s.window, horizon) for s in segments):
        raise ValueError("Generation lies outside scenario window")
    if any(
        a.window.end_utc > b.window.start_utc for a, b in zip(segments, segments[1:])
    ):
        raise ValueError(
            "Generation segments overlap; supply one explicit aggregate power profile"
        )


def _time_cuts(horizon, battery, loads):
    return sorted(
        {horizon.start_utc, horizon.end_utc}
        | {
            t
            for item in (*battery.generation, *loads)
            for t in (item.window.start_utc, item.window.end_utc)
        }
    )


def _generated(battery, interval):
    return sum(
        (
            _fraction(segment.power_w) * _seconds(overlap)
            for segment in battery.generation
            if (overlap := _intersection(segment.window, interval)) is not None
        ),
        Fraction(0),
    )
