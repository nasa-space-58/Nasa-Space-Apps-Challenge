"""H3 adapters for geometric contact, explicit calendars and ideal storage budgets."""

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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError
