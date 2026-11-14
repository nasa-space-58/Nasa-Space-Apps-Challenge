"""R11 multi-mission campaign planning over explicit, validated evidence.

Pipeline: generate candidates from terrain, sunlight, descent and vehicle
evidence; jointly select landing options, deferrals and cancellations while
scheduling slot-based downlinks through station calendars, capacities, data
rates, exclusivity and relays (HiGHS MILP); validate every plan independently
of the solver; compare with independently planned missions; explain choices
and infeasible mission sets; present a shared timeline.

No DSN booking, link budget, battery trace (see H3), trajectory or landing
safety certification is produced. Feasibility certifies only the declared rules.
"""

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal

import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csr_matrix

import shared.campaign as campaign_contracts
from shared.campaign import (
    CampaignProblem,
    DescentEvidence,
    GeometryInterval,
    SiteEvidence,
    StationGeometry,
    SunInterval,
    TerrainEvidence,
    campaign_fingerprint,
)
from shared.constants import MOON_REFERENCE_RADIUS_M
from shared.hybrid import Contract, Identifier, NonNegative, Number, Source, TimeWindow

SOLVER_NAME = "r11-highs-milp"
MAX_VARIABLES = 20_000
MAX_EXPLAINED_ALTERNATIVES = 3
MAX_LANDING_EVALUATIONS = 20_000
EXAMPLES_DIR = Path(campaign_contracts.__file__).parent / "examples" / "r11"
LIMITATIONS = (
    "Candidates are filtered by supplied terrain, sunlight, descent and vehicle evidence; passing them is not a landing-safety certification.",
    "Station geometry, calendars, capacities, data rates and exclusivity are separate declared inputs; no DSN booking or link budget is implied.",
    "Data rates are assumed sustained rates per mission and station; a link occupies whole slots.",
    "Energy is a net balance over the surface window with only full-disk sunlight generating power; it is not a time-resolved battery trace (see H3).",
    "Relay credit requires relay equipment on both vehicles, supplied line-of-sight evidence, a reference-sphere chord within range and the relay's own station link in the same slot (bent pipe).",
    "Stations with unknown capacity and slots with unknown geometry are never used.",
    "Feasible certifies only the declared campaign rules; the joint optimum is exact for this MILP model and candidate grid.",
)

RejectionCode = Literal[
    "outside_campaign_window",
    "outside_evidence",
    "deadline",
    "terrain_unknown",
    "terrain_slope",
    "terrain_unknown_fraction",
    "descent_unknown",
    "descent_infeasible",
    "propellant_reserve",
    "sunlight_unknown",
    "energy",
]
IssueCode = Literal[
    "missing_mission",
    "cancel_not_allowed",
    "dependency_lag",
    "dependency_cancelled",
    "allocation_for_inactive_mission",
    "invalid_slot",
    "outside_comm_window",
    "station_unknown",
    "station_unavailable",
    "station_capacity_unknown",
    "geometry_unavailable",
    "rate_unknown",
    "bits_mismatch",
    "radio_conflict",
    "station_capacity",
    "exclusive_link",
    "relay_equipment",
    "relay_inactive",
    "relay_range",
    "relay_geometry",
    "relay_capacity",
    "downlink_shortfall",
]


class Rejection(Contract):
    """A generated landing time at a site that failed a hard evidence check."""

    mission_id: Identifier
    site_id: Identifier
    landing_utc: datetime
    codes: tuple[RejectionCode, ...] = Field(min_length=1)
    details: tuple[str, ...] = Field(min_length=1)


class CampaignCandidate(Contract):
    """A landing option that passed every candidate-level evidence check."""

    option_id: Identifier
    mission_id: Identifier
    site_id: Identifier
    landing_utc: datetime
    surface_window: TimeWindow
    comm_window: TimeWindow
    deferred: bool
    defer_s: NonNegative
    wait_s: NonNegative
    science_score: NonNegative
    fuel_kg: NonNegative
    propellant_margin_kg: NonNegative
    generated_energy_j: NonNegative
    energy_margin_j: NonNegative
    score: Number
    exact_score: str
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class CandidateSet(Contract):
    input_fingerprint: str
    candidates: tuple[CampaignCandidate, ...]
    rejections: tuple[Rejection, ...]


class LinkAllocation(Contract):
    """One slot of one link: mission data through a station, optionally via a relay."""

    mission_id: Identifier
    station_id: Identifier
    slot_index: Annotated[int, Field(strict=True, ge=0)]
    window: TimeWindow
    via_relay_mission_id: Identifier | None = None
    bits: NonNegative


class CampaignPlan(Contract):
    """Selected option per mission; null means cancelled."""

    plan_id: Identifier
    campaign_id: Identifier
    campaign_revision: Annotated[int, Field(strict=True, ge=0)]
    input_fingerprint: str
    selections: dict[Identifier, Identifier | None]
    allocations: tuple[LinkAllocation, ...] = ()
    solver_name: Annotated[str, Field(strict=True, min_length=1)]


class CampaignIssue(Contract):
    code: IssueCode
    mission_ids: tuple[Identifier, ...] = ()
    station_id: Identifier | None = None
    window: TimeWindow | None = None
    detail: Annotated[str, Field(strict=True, min_length=1)]


class MissionOutcome(Contract):
    mission_id: Identifier
    status: Literal["nominal", "deferred", "cancelled"]
    option_id: Identifier | None
    required_bits: NonNegative
    delivered_bits: NonNegative
    relayed_bits: NonNegative


class CampaignValidation(Contract):
    """Independent check of a plan against the original campaign rules."""

    input_fingerprint: str
    status: Literal["feasible", "infeasible"]
    issues: tuple[CampaignIssue, ...]
    outcomes: tuple[MissionOutcome, ...]
    score: Number | None
    exact_score: str | None


class OptionExplanation(Contract):
    """Why an option was selected or not, with a measured score change if known."""

    option_id: Identifier | None
    mission_id: Identifier
    status: Literal[
        "selected", "not_selected", "cancel_selected", "cancel_not_selected"
    ]
    reason: Literal[
        "best_joint_score",
        "forcing_infeasible",
        "lower_joint_score",
        "not_evaluated",
    ]
    score_delta: Number | None
    detail: Annotated[str, Field(strict=True, min_length=1)]


class MissionExplanation(Contract):
    mission_id: Identifier
    status: Literal["nominal", "deferred", "cancelled", "unplanned"]
    detail: Annotated[str, Field(strict=True, min_length=1)]
    rejection_counts: dict[str, int]
    alternatives: tuple[OptionExplanation, ...]


class ConflictSet(Contract):
    """A minimal set of missions that cannot all be completed together."""

    mission_ids: tuple[Identifier, ...] = Field(min_length=1)
    detail: Annotated[str, Field(strict=True, min_length=1)]


class SolveOutcome(Contract):
    status: Literal["optimal", "infeasible", "limit_reached"]
    plan: CampaignPlan | None
    validation: CampaignValidation | None
    variables: Annotated[int, Field(strict=True, ge=0)]
    constraints: Annotated[int, Field(strict=True, ge=0)]
    elapsed_s: NonNegative


class CampaignResult(Contract):
    input_fingerprint: str
    candidate_set: CandidateSet
    joint: SolveOutcome
    independent_plan: CampaignPlan
    independent_validation: CampaignValidation
    explanations: tuple[MissionExplanation, ...]
    conflict_sets: tuple[ConflictSet, ...]
    timeline: dict
    limitations: tuple[str, ...]


UTC = timezone.utc
REJECTION_TEXT = {
    "outside_campaign_window": "Surface operations would end after the campaign window.",
    "outside_evidence": "Surface operations extend beyond the site's evidence window.",
    "deadline": "Surface operations would end after the mission deadline.",
    "terrain_unknown": "Landing-area slope is unknown.",
    "terrain_slope": "Landing-area slope exceeds the vehicle limit.",
    "terrain_unknown_fraction": "Too much of the landing area has unknown terrain.",
    "descent_unknown": "No descent propellant evidence for this vehicle at this site.",
    "descent_infeasible": "The descent evidence reports an infeasible descent.",
    "propellant_reserve": "Descent propellant plus reserve exceeds usable propellant.",
    "sunlight_unknown": "Sunlight is unknown during part of the surface operations.",
    "energy": "Initial plus generated energy minus consumption falls below the energy reserve.",
}


def _q(value) -> Fraction:
    """Exact rational of a validated number's decimal spelling."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    return Fraction(str(value))


def _seconds(delta: timedelta) -> Fraction:
    return Fraction(delta // timedelta(microseconds=1), 1_000_000)


def _iso(moment: datetime) -> str:
    text = moment.astimezone(UTC).isoformat()
    return text.replace("+00:00", "Z")


def _window(start, end) -> dict:
    return {"start_utc": _iso(start), "end_utc": _iso(end)}


def _union_covers(start, end, windows) -> bool:
    """True when [start, end) lies inside the union of half-open windows."""
    cursor = start
    for a, b in sorted(windows):
        if a > cursor:
            break
        if b > cursor:
            cursor = b
        if cursor >= end:
            return True
    return cursor >= end


def _state_covers(start, end, intervals, state="available") -> bool:
    return _union_covers(
        start,
        end,
        [(i.window.start_utc, i.window.end_utc) for i in intervals if i.state == state],
    )


def _sun_full_s(intervals, start, end):
    """(known, full-disk seconds) over [start, end); gaps or unknown -> not known."""
    cursor, full = start, Fraction(0)
    for row in intervals:
        a, b = row.window.start_utc, row.window.end_utc
        if b <= cursor:
            continue
        if a > cursor or row.state == "unknown":
            return False, Fraction(0)
        stop = min(b, end)
        if row.state == "full":
            full += _seconds(stop - cursor)
        cursor = stop
        if cursor >= end:
            return True, full
    return cursor >= end, full


def _unit_vector(site):
    lat, lon = math.radians(site.latitude_deg), math.radians(site.longitude_deg_east)
    return (
        math.cos(lat) * math.cos(lon),
        math.cos(lat) * math.sin(lon),
        math.sin(lat),
    )


def _chord_m(a, b) -> float:
    """Straight-line distance between two sites on the reference sphere."""
    return MOON_REFERENCE_RADIUS_M * math.dist(_unit_vector(a), _unit_vector(b))


def _revalidated(problem: CampaignProblem) -> CampaignProblem:
    return CampaignProblem.model_validate(problem.model_dump(mode="python"))


class _Context:
    """Indexed view of one validated campaign and its generated candidates."""

    def __init__(self, problem: CampaignProblem, candidate_set=None):
        self.problem = problem
        self.digest = campaign_fingerprint(problem)
        self.missions = {m.mission_id: m for m in problem.missions}
        self.vehicles = {v.vehicle_id: v for v in problem.vehicles}
        self.sites = {s.site.site_id: s for s in problem.sites}
        self.stations = {s.station_id: s for s in problem.stations}
        self.rates = {
            (r.mission_id, r.station_id): _q(r.rate_bps) for r in problem.link_rates
        }
        self.relay_geometry = {
            (g.client_site_id, g.relay_site_id): g.intervals
            for g in problem.relay_geometry
        }
        self.slot_s = problem.slot_s
        start = problem.window.start_utc
        count = int((problem.window.end_utc - start).total_seconds()) // problem.slot_s
        self.slots = [
            (
                start + timedelta(seconds=k * problem.slot_s),
                start + timedelta(seconds=(k + 1) * problem.slot_s),
            )
            for k in range(count)
        ]
        self.candidate_set = candidate_set or _generate(problem, self.digest)
        self.candidates = {c.option_id: c for c in self.candidate_set.candidates}
        self.by_mission = {mid: [] for mid in self.missions}
        for c in self.candidate_set.candidates:
            self.by_mission[c.mission_id].append(c)
        self.geometry = {
            sid: {g.station_id: g.intervals for g in s.station_geometry}
            for sid, s in self.sites.items()
        }

    def required_bits(self, mission_id) -> Fraction:
        return _q(self.missions[mission_id].requirements.downlink_bits)

    def slot_range(self, window):
        """Indices of slots fully inside a window."""
        first = math.ceil(
            (window.start_utc - self.problem.window.start_utc).total_seconds()
            / self.slot_s
        )
        last = (
            window.end_utc - self.problem.window.start_utc
        ).total_seconds() // self.slot_s
        return range(max(first, 0), min(int(last), len(self.slots)))

    def in_window(self, k, window) -> bool:
        a, b = self.slots[k]
        return window.start_utc <= a and b <= window.end_utc

    def station_issue(self, mission_id, site_id, station_id, k):
        """None when the station path (calendar, capacity, geometry, rate) is usable."""
        a, b = self.slots[k]
        station = self.stations.get(station_id)
        if station is None:
            return "station_unknown", f"Station {station_id} is not declared."
        windows = [(w.start_utc, w.end_utc) for w in station.availability]
        if not _union_covers(a, b, windows):
            return (
                "station_unavailable",
                f"Station {station_id} calendar does not cover the slot.",
            )
        if station.capacity_links is None:
            return (
                "station_capacity_unknown",
                f"Station {station_id} capacity is unknown.",
            )
        intervals = self.geometry[site_id].get(station_id)
        if intervals is None or not _state_covers(a, b, intervals):
            return (
                "geometry_unavailable",
                f"Station {station_id} geometry from {site_id} is not available for the whole slot.",
            )
        if (mission_id, station_id) not in self.rates:
            return (
                "rate_unknown",
                f"No data rate is declared for {mission_id} via {station_id}.",
            )
        return None

    def link(self, owner, station_id, k, relay=None):
        """(issue code, detail, exact bits) for one owner link in slot k."""
        if not self.in_window(k, owner.comm_window):
            return (
                "outside_comm_window",
                "Slot lies outside the mission's communication window.",
                None,
            )
        if relay is None:
            issue = self.station_issue(owner.mission_id, owner.site_id, station_id, k)
            if issue:
                return (*issue, None)
            return None, None, self.rates[(owner.mission_id, station_id)] * self.slot_s
        client = self.missions[owner.mission_id]
        service = self.missions[relay.mission_id].relay_service
        if (
            relay.mission_id == owner.mission_id
            or not client.relay_terminal
            or service is None
        ):
            return (
                "relay_equipment",
                "Relay terminal or relay service equipment is missing.",
                None,
            )
        if not self.in_window(k, relay.comm_window):
            return (
                "relay_inactive",
                "The relay mission is not on the surface in this slot.",
                None,
            )
        distance = _chord_m(
            self.sites[owner.site_id].site, self.sites[relay.site_id].site
        )
        if distance > service.max_range_m:
            return (
                "relay_range",
                f"Sites are {distance:.0f} m apart; relay range is {service.max_range_m:.0f} m.",
                None,
            )
        a, b = self.slots[k]
        geometry = self.relay_geometry.get((owner.site_id, relay.site_id))
        if geometry is None or not _state_covers(a, b, geometry):
            return (
                "relay_geometry",
                "Relay line of sight is not available for the whole slot.",
                None,
            )
        issue = self.station_issue(relay.mission_id, relay.site_id, station_id, k)
        if issue:
            return (*issue, None)
        rate = min(_q(service.hop_rate_bps), self.rates[(relay.mission_id, station_id)])
        return None, None, rate * self.slot_s


# ------------------------------------------------------------------ generation


def generate_candidates(problem: CampaignProblem) -> CandidateSet:
    """Grid landing times per mission/site and keep only evidence-backed options."""
    problem = _revalidated(problem)
    return _generate(problem, campaign_fingerprint(problem))


def _generate(problem, digest) -> CandidateSet:
    prefs = problem.preferences
    vehicles = {v.vehicle_id: v for v in problem.vehicles}
    sites = {s.site.site_id: s for s in problem.sites}
    step = timedelta(seconds=problem.landing_step_s)
    candidates, rejections, evaluations = [], [], 0
    for mission in sorted(problem.missions, key=lambda m: m.mission_id):
        vehicle = vehicles[mission.vehicle_id]
        policy = mission.policy
        nominal_end = mission.landing_window.end_utc
        last = nominal_end + timedelta(seconds=policy.max_defer_s)
        duration = timedelta(seconds=mission.requirements.surface_duration_s)
        for site_id in sorted(mission.site_ids):
            evidence = sites[site_id]
            descent = {d.vehicle_id: d for d in evidence.descents}.get(
                vehicle.vehicle_id
            )
            landing = mission.landing_window.start_utc
            while landing < last:
                evaluations += 1
                if evaluations > MAX_LANDING_EVALUATIONS:
                    raise ValueError(
                        f"Candidate grid exceeds {MAX_LANDING_EVALUATIONS} landing evaluations"
                    )
                result = _evaluate_landing(
                    problem,
                    mission,
                    vehicle,
                    evidence,
                    descent,
                    landing,
                    duration,
                    prefs,
                )
                if isinstance(result, CampaignCandidate):
                    candidates.append(result)
                else:
                    rejections.append(
                        Rejection(
                            mission_id=mission.mission_id,
                            site_id=site_id,
                            landing_utc=landing,
                            codes=tuple(result),
                            details=tuple(REJECTION_TEXT[c] for c in result),
                        )
                    )
                landing += step
    candidates.sort(key=lambda c: c.option_id)
    rejections.sort(key=lambda r: (r.mission_id, r.site_id, r.landing_utc))
    return CandidateSet(
        input_fingerprint=digest,
        candidates=tuple(candidates),
        rejections=tuple(rejections),
    )


def _evaluate_landing(
    problem, mission, vehicle, evidence, descent, landing, duration, prefs
):
    policy = mission.policy
    deferred = landing >= mission.landing_window.end_utc
    defer_s = (
        _seconds(landing - mission.landing_window.end_utc) if deferred else Fraction(0)
    )
    deadline = mission.deadline_utc
    if deferred:
        deadline += timedelta(seconds=policy.max_defer_s)
    surface_end = landing + duration
    codes = []
    if surface_end > problem.window.end_utc:
        codes.append("outside_campaign_window")
    if surface_end > deadline:
        codes.append("deadline")
    window = evidence.evidence_window
    if not (window.start_utc <= landing and surface_end <= window.end_utc):
        codes.append("outside_evidence")
    terrain = evidence.terrain
    if terrain.max_landing_slope_deg is None:
        codes.append("terrain_unknown")
    elif terrain.max_landing_slope_deg > vehicle.max_landing_slope_deg:
        codes.append("terrain_slope")
    if terrain.unknown_landing_fraction > vehicle.max_unknown_landing_fraction:
        codes.append("terrain_unknown_fraction")
    used = None
    if descent is None or descent.propellant_used_kg is None:
        codes.append("descent_unknown")
    else:
        used = _q(descent.propellant_used_kg)
        if not descent.feasible:
            codes.append("descent_infeasible")
        if used + _q(vehicle.propellant_reserve_kg) > _q(vehicle.usable_propellant_kg):
            codes.append("propellant_reserve")
    known, full_s = _sun_full_s(evidence.sunlight, landing, surface_end)
    generated = _q(vehicle.solar_power_w) * full_s
    balance = (
        _q(vehicle.initial_energy_j)
        + generated
        - _q(mission.requirements.energy_j)
        - _q(vehicle.energy_reserve_j)
    )
    if not known:
        codes.append("sunlight_unknown")
    elif balance < 0:
        codes.append("energy")
    if codes:
        return codes
    margin = _q(vehicle.usable_propellant_kg) - used - _q(vehicle.propellant_reserve_kg)
    wait_s = _seconds(landing - mission.landing_window.start_utc)
    science = _q(mission.science_score_by_site[evidence.site.site_id])
    score = (
        _q(prefs.priority_weights[mission.priority - 1])
        * _q(prefs.science_weight)
        * science
        / _q(prefs.science_scale_score)
        - _q(prefs.fuel_weight) * used / _q(prefs.fuel_scale_kg)
        - _q(prefs.wait_weight) * wait_s / _q(prefs.wait_scale_s)
        + _q(prefs.reserve_weight) * margin / _q(prefs.reserve_scale_kg)
    )
    if deferred:
        score -= (
            _q(policy.defer_penalty_score)
            + _q(policy.defer_penalty_score_per_h) * defer_s / 3600
        )
    comm_end = min(deadline, problem.window.end_utc)
    sources = {
        *mission.source_ids,
        *vehicle.source_ids,
        *evidence.source_ids,
        *evidence.terrain.source_ids,
        *descent.source_ids,
    }
    site_id = evidence.site.site_id
    return CampaignCandidate(
        option_id=f"{mission.mission_id}.{site_id}.{landing.strftime('%Y%m%dT%H%M%SZ')}",
        mission_id=mission.mission_id,
        site_id=site_id,
        landing_utc=landing,
        surface_window=TimeWindow(start_utc=landing, end_utc=surface_end),
        comm_window=TimeWindow(start_utc=landing, end_utc=comm_end),
        deferred=deferred,
        defer_s=float(defer_s),
        wait_s=float(wait_s),
        science_score=float(science),
        fuel_kg=float(used),
        propellant_margin_kg=float(margin),
        generated_energy_j=float(generated),
        energy_margin_j=float(balance),
        score=float(score),
        exact_score=str(score),
        source_ids=tuple(sorted(sources)),
    )


# ------------------------------------------------------------------- validator


def validate_campaign_plan(
    plan: CampaignPlan, problem: CampaignProblem
) -> CampaignValidation:
    """Check a plan against the original rules without using any solver."""
    problem = _revalidated(problem)
    plan = CampaignPlan.model_validate(plan.model_dump(mode="python"))
    return _validate(plan, _Context(problem))


def _validate(plan: CampaignPlan, ctx: _Context) -> CampaignValidation:
    problem = ctx.problem
    if (
        plan.campaign_id != problem.campaign_id
        or plan.campaign_revision != problem.revision
        or plan.input_fingerprint != ctx.digest
    ):
        raise ValueError("Plan input identity does not match the campaign")
    chosen = {}
    for mission_id, option_id in plan.selections.items():
        if mission_id not in ctx.missions:
            raise ValueError(f"Plan refers to unknown mission {mission_id}")
        if option_id is None:
            chosen[mission_id] = None
            continue
        candidate = ctx.candidates.get(option_id)
        if candidate is None:
            raise ValueError(f"Plan refers to unknown option {option_id}")
        if candidate.mission_id != mission_id:
            raise ValueError(
                f"Option {option_id} does not belong to mission {mission_id}"
            )
        chosen[mission_id] = candidate
    for allocation in plan.allocations:
        for mid in (allocation.mission_id, allocation.via_relay_mission_id):
            if mid is not None and mid not in ctx.missions:
                raise ValueError(f"Allocation refers to unknown mission {mid}")
    issues = []

    def issue(code, detail, missions=(), station=None, window=None):
        issues.append(
            CampaignIssue(
                code=code,
                mission_ids=tuple(missions),
                station_id=station,
                window=window,
                detail=detail,
            )
        )

    for mission_id in sorted(ctx.missions):
        mission = ctx.missions[mission_id]
        if mission_id not in chosen:
            issue(
                "missing_mission",
                f"{mission_id} has neither an option nor a cancellation.",
                [mission_id],
            )
        elif chosen[mission_id] is None and not mission.policy.cancel_allowed:
            issue(
                "cancel_not_allowed",
                f"{mission_id} may not be cancelled.",
                [mission_id],
            )
    for mission_id in sorted(ctx.missions):
        successor = chosen.get(mission_id)
        if successor is None:
            continue
        for dependency in ctx.missions[mission_id].dependencies:
            pred_id = dependency.predecessor_mission_id
            predecessor = chosen.get(pred_id)
            if predecessor is None:
                issue(
                    "dependency_cancelled",
                    f"{mission_id} depends on {pred_id}, which is not planned.",
                    [pred_id, mission_id],
                )
            elif successor.landing_utc < predecessor.landing_utc + timedelta(
                seconds=dependency.min_lag_s
            ):
                issue(
                    "dependency_lag",
                    f"{mission_id} must land at least {dependency.min_lag_s:.0f} s after {pred_id}.",
                    [pred_id, mission_id],
                )
    valid = []
    ordered = sorted(
        plan.allocations,
        key=lambda a: (
            a.slot_index,
            a.mission_id,
            a.station_id,
            a.via_relay_mission_id or "",
        ),
    )
    for allocation in ordered:
        mid = allocation.mission_id
        owner = chosen.get(mid)
        where = dict(
            missions=[mid], station=allocation.station_id, window=allocation.window
        )
        if owner is None:
            issue(
                "allocation_for_inactive_mission",
                f"{mid} is not planned but has a link.",
                **where,
            )
            continue
        k = allocation.slot_index
        if (
            k >= len(ctx.slots)
            or (
                allocation.window.start_utc,
                allocation.window.end_utc,
            )
            != ctx.slots[k]
        ):
            issue(
                "invalid_slot",
                "Allocation window does not match its slot index.",
                **where,
            )
            continue
        relay = None
        via = allocation.via_relay_mission_id
        if via is not None:
            relay = chosen.get(via)
            if (
                via == mid
                or ctx.missions[via].relay_service is None
                or not ctx.missions[mid].relay_terminal
            ):
                issue(
                    "relay_equipment",
                    "Relay terminal or relay service equipment is missing.",
                    **where,
                )
                continue
            if relay is None:
                issue("relay_inactive", f"Relay mission {via} is not planned.", **where)
                continue
        code, detail, bits = ctx.link(owner, allocation.station_id, k, relay)
        if code:
            issue(code, detail, **where)
            continue
        if _q(allocation.bits) != bits:
            issue(
                "bits_mismatch",
                f"Allocation claims {allocation.bits} bits; the link carries {float(bits)}.",
                **where,
            )
            continue
        valid.append((allocation, bits))
    radio, usage, relay_use = Counter(), {}, Counter()
    for allocation, _ in valid:
        k = allocation.slot_index
        radio[(allocation.mission_id, k)] += 1
        usage.setdefault((allocation.station_id, k), []).append(allocation.mission_id)
        if allocation.via_relay_mission_id:
            relay_use[(allocation.via_relay_mission_id, k)] += 1
    for (mid, k), count in sorted(radio.items(), key=lambda i: (i[0][1], i[0][0])):
        if count > 1:
            issue(
                "radio_conflict",
                f"{mid} uses {count} links in one slot.",
                [mid],
                window=_slot_window(ctx, k),
            )
    for (sid, k), owners in sorted(usage.items(), key=lambda i: (i[0][1], i[0][0])):
        capacity = ctx.stations[sid].capacity_links
        if len(owners) > capacity:
            issue(
                "station_capacity",
                f"{sid} carries {len(owners)} links; capacity is {capacity}.",
                sorted(set(owners)),
                sid,
                _slot_window(ctx, k),
            )
        exclusive = [m for m in owners if ctx.missions[m].requires_exclusive_link]
        if exclusive and len(set(owners)) > 1:
            issue(
                "exclusive_link",
                f"{', '.join(sorted(set(exclusive)))} requires exclusive use of {sid}.",
                sorted(set(owners)),
                sid,
                _slot_window(ctx, k),
            )
    for (via, k), count in sorted(relay_use.items(), key=lambda i: (i[0][1], i[0][0])):
        capacity = ctx.missions[via].relay_service.capacity_links
        if count > capacity:
            issue(
                "relay_capacity",
                f"{via} forwards {count} links; capacity is {capacity}.",
                [via],
                window=_slot_window(ctx, k),
            )
    delivered, relayed = Counter(), Counter()
    for allocation, bits in valid:
        delivered[allocation.mission_id] += bits
        if allocation.via_relay_mission_id:
            relayed[allocation.mission_id] += bits
    outcomes = []
    for mission_id in sorted(ctx.missions):
        candidate = chosen.get(mission_id)
        required = ctx.required_bits(mission_id)
        if candidate is not None and delivered[mission_id] < required:
            issue(
                "downlink_shortfall",
                f"{mission_id} delivers {float(delivered[mission_id]):.0f} of {float(required):.0f} bits.",
                [mission_id],
            )
        status = (
            "cancelled"
            if candidate is None
            else "deferred"
            if candidate.deferred
            else "nominal"
        )
        outcomes.append(
            MissionOutcome(
                mission_id=mission_id,
                status=status,
                option_id=candidate.option_id if candidate else None,
                required_bits=float(required),
                delivered_bits=float(delivered[mission_id]),
                relayed_bits=float(relayed[mission_id]),
            )
        )
    score = None
    if set(chosen) == set(ctx.missions):
        score = sum(
            (
                Fraction(c.exact_score)
                if c is not None
                else -_q(ctx.missions[mid].policy.cancel_penalty_score)
                for mid, c in chosen.items()
            ),
            Fraction(0),
        )
    return CampaignValidation(
        input_fingerprint=ctx.digest,
        status="infeasible" if issues else "feasible",
        issues=tuple(issues),
        outcomes=tuple(outcomes),
        score=None if score is None else float(score),
        exact_score=None if score is None else str(score),
    )


def _slot_window(ctx, k):
    a, b = ctx.slots[k]
    return TimeWindow(start_utc=a, end_utc=b)


# ---------------------------------------------------------------------- solver


def _effective_problem(problem, only_mission_ids, required_mission_ids):
    if only_mission_ids is None and required_mission_ids is None:
        return problem
    data = problem.model_dump(mode="python")
    known = {m.mission_id for m in problem.missions}
    if only_mission_ids is not None:
        keep = set(only_mission_ids)
        if not keep or not keep <= known:
            raise ValueError("only_mission_ids must name known missions")
        data["missions"] = [
            {
                **m,
                "dependencies": [
                    d for d in m["dependencies"] if d["predecessor_mission_id"] in keep
                ],
            }
            for m in data["missions"]
            if m["mission_id"] in keep
        ]
        data["link_rates"] = [r for r in data["link_rates"] if r["mission_id"] in keep]
    if required_mission_ids is not None:
        required = set(required_mission_ids)
        if not required <= known:
            raise ValueError("required_mission_ids must name known missions")
        for m in data["missions"]:
            m["policy"] = {
                **m["policy"],
                "cancel_allowed": m["mission_id"] not in required,
                "cancel_penalty_score": 0,
            }
    return CampaignProblem.model_validate(data)


def solve_joint(
    problem: CampaignProblem,
    *,
    required_mission_ids: tuple[str, ...] | None = None,
    forced_option_ids: tuple[str, ...] = (),
    excluded_option_ids: tuple[str, ...] = (),
    forbid_cancel_mission_ids: tuple[str, ...] = (),
    only_mission_ids: tuple[str, ...] | None = None,
    time_limit_s: float = 10.0,
) -> SolveOutcome:
    """Jointly select options, cancellations and link slots by MILP.

    only_mission_ids plans a subset (dependencies on absent missions are
    dropped); required_mission_ids makes exactly those missions mandatory and
    all others cancellable without penalty. Both produce an effective campaign
    whose fingerprint the plan carries. Forced/excluded options and forbidden
    cancellations are solver constraints on the unchanged campaign.
    """
    started = perf_counter()
    if (
        type(time_limit_s) not in (int, float)
        or not math.isfinite(time_limit_s)
        or not 0 < time_limit_s <= 3600
    ):
        raise ValueError("time_limit_s must be finite and in (0, 3600]")
    problem = _effective_problem(
        _revalidated(problem), only_mission_ids, required_mission_ids
    )
    ctx = _Context(problem)
    unknown = set(forced_option_ids) | set(excluded_option_ids)
    if not unknown <= set(ctx.candidates):
        raise ValueError("Forced or excluded options must be generated candidates")
    if not set(forbid_cancel_mission_ids) <= set(ctx.missions):
        raise ValueError("forbid_cancel_mission_ids must name known missions")
    model = _build_model(
        ctx,
        set(forced_option_ids),
        set(excluded_option_ids),
        set(forbid_cancel_mission_ids),
    )
    if model is None:
        return SolveOutcome(
            status="infeasible",
            plan=None,
            validation=None,
            variables=0,
            constraints=0,
            elapsed_s=perf_counter() - started,
        )
    n, rows = model["n"], model["rows"]
    deadline = started + time_limit_s
    result = _milp(model, model["c"], model["lower"], model["upper"], deadline)
    common = dict(variables=n, constraints=len(rows))
    if result.status == 2:
        return SolveOutcome(
            status="infeasible",
            plan=None,
            validation=None,
            elapsed_s=perf_counter() - started,
            **common,
        )
    if result.status not in (0, 1):
        raise RuntimeError(f"HiGHS failed: {result.message}")
    if result.x is None:
        return SolveOutcome(
            status="limit_reached",
            plan=None,
            validation=None,
            elapsed_s=perf_counter() - started,
            **common,
        )
    x = np.round(result.x)
    selection = np.zeros(n)
    selection[: model["n_select"]] = x[: model["n_select"]]
    # Phase 2: keep the selection, prefer fewer and earlier link slots.
    lower = np.where(np.arange(n) < model["n_select"], selection, 0.0)
    upper = np.where(np.arange(n) < model["n_select"], selection, 1.0)
    second = _milp(
        model, model["link_cost"], lower, upper, max(deadline, perf_counter() + 1.0)
    )
    if second.status == 0 and second.x is not None:
        x = np.round(second.x)
    plan = _plan_from(ctx, model, x, "joint", SOLVER_NAME)
    validation = _validate(plan, ctx)
    status = "optimal" if result.status == 0 else "limit_reached"
    if validation.status != "feasible":
        raise RuntimeError(
            "Independent validator rejected the MILP plan: "
            + "; ".join(i.code for i in validation.issues)
        )
    return SolveOutcome(
        status=status,
        plan=plan,
        validation=validation,
        elapsed_s=perf_counter() - started,
        **common,
    )


def _milp(model, cost, lower, upper, deadline):
    matrix = csr_matrix(
        (model["values"], (model["row_index"], model["col_index"])),
        shape=(len(model["rows"]), model["n"]),
    )
    lb = np.array([r[0] for r in model["rows"]], dtype=float)
    ub = np.array([r[1] for r in model["rows"]], dtype=float)
    constraints = [LinearConstraint(matrix, lb, ub)] if model["rows"] else []
    remaining = max(deadline - perf_counter(), 0.05)
    return milp(
        c=np.asarray(cost, dtype=float),
        integrality=np.ones(model["n"]),
        bounds=Bounds(lower, upper),
        constraints=constraints,
        options={"time_limit": remaining, "mip_rel_gap": 0.0, "presolve": True},
    )


def _build_model(ctx, forced, excluded, forbid):
    candidates = [
        c for c in ctx.candidate_set.candidates if c.option_id not in excluded
    ]
    x_index = {c.option_id: i for i, c in enumerate(candidates)}
    cancellable = [
        mid
        for mid in sorted(ctx.missions)
        if ctx.missions[mid].policy.cancel_allowed and mid not in forbid
    ]
    for mid in sorted(ctx.missions):
        if mid not in cancellable and not any(c.mission_id == mid for c in candidates):
            return None
    z_index = {mid: len(candidates) + i for i, mid in enumerate(cancellable)}
    n_select = len(candidates) + len(cancellable)
    links = []  # (owner option, owner mission, station, slot, relay option | None, bits)
    relays = [
        c for c in candidates if ctx.missions[c.mission_id].relay_service is not None
    ]
    stations = sorted(sid for sid, s in ctx.stations.items() if s.capacity_links)

    def add(item):
        links.append(item)
        if n_select + len(links) > MAX_VARIABLES:
            raise ValueError(f"Campaign model exceeds {MAX_VARIABLES} variables")

    for c in candidates:
        if ctx.required_bits(c.mission_id) == 0:
            continue
        terminal = ctx.missions[c.mission_id].relay_terminal
        for k in ctx.slot_range(c.comm_window):
            for sid in stations:
                code, _, bits = ctx.link(c, sid, k)
                if code is None:
                    add((c.option_id, c.mission_id, sid, k, None, bits))
                if terminal:
                    for r in relays:
                        if r.mission_id == c.mission_id:
                            continue
                        code, _, bits = ctx.link(c, sid, k, r)
                        if code is None:
                            add((c.option_id, c.mission_id, sid, k, r.option_id, bits))
    n = n_select + len(links)
    rows, row_index, col_index, values = [], [], [], []

    def row(entries, lb, ub):
        r = len(rows)
        rows.append((lb, ub))
        for col, value in entries:
            row_index.append(r)
            col_index.append(col)
            values.append(float(value))

    for mid in sorted(ctx.missions):
        entries = [(x_index[c.option_id], 1) for c in candidates if c.mission_id == mid]
        if mid in z_index:
            entries.append((z_index[mid], 1))
        row(entries, 1, 1)
    by_owner, by_radio, by_station, by_relay = {}, {}, {}, {}
    for j, (owner, mid, sid, k, via, bits) in enumerate(links):
        col = n_select + j
        row([(col, 1), (x_index[owner], -1)], -np.inf, 0)
        if via is not None:
            row([(col, 1), (x_index[via], -1)], -np.inf, 0)
            relay_mission = ctx.candidates[via].mission_id
            by_relay.setdefault((relay_mission, k), []).append(col)
        by_owner.setdefault(owner, []).append((col, bits))
        by_radio.setdefault((mid, k), []).append(col)
        by_station.setdefault((sid, k), []).append((col, mid))
    for cols in by_radio.values():
        if len(cols) > 1:
            row([(c, 1) for c in cols], -np.inf, 1)
    for (sid, k), entries in by_station.items():
        capacity = ctx.stations[sid].capacity_links
        if len(entries) > capacity:
            row([(c, 1) for c, _ in entries], -np.inf, capacity)
        owners = {m for _, m in entries}
        for mid in sorted(owners):
            if not ctx.missions[mid].requires_exclusive_link or len(owners) < 2:
                continue
            row(
                [(c, capacity if m == mid else 1) for c, m in entries],
                -np.inf,
                capacity,
            )
    for (relay_mission, k), cols in by_relay.items():
        capacity = ctx.missions[relay_mission].relay_service.capacity_links
        if len(cols) > capacity:
            row([(c, 1) for c in cols], -np.inf, capacity)
    for c in candidates:
        required = ctx.required_bits(c.mission_id)
        if required == 0:
            continue
        entries = [
            (col, bits / required) for col, bits in by_owner.get(c.option_id, [])
        ]
        entries.append((x_index[c.option_id], -1))
        row(entries, 0, np.inf)
    for c in candidates:
        for dependency in ctx.missions[c.mission_id].dependencies:
            lag = timedelta(seconds=dependency.min_lag_s)
            compatible = [
                (x_index[p.option_id], -1)
                for p in candidates
                if p.mission_id == dependency.predecessor_mission_id
                and p.landing_utc + lag <= c.landing_utc
            ]
            row([(x_index[c.option_id], 1), *compatible], -np.inf, 0)
    score = np.zeros(n)
    for c in candidates:
        score[x_index[c.option_id]] = float(Fraction(c.exact_score))
    for mid, col in z_index.items():
        score[col] = -float(_q(ctx.missions[mid].policy.cancel_penalty_score))
    lower = np.zeros(n)
    upper = np.ones(n)
    for option_id in forced:
        if option_id in x_index:
            lower[x_index[option_id]] = 1
    link_cost = np.zeros(n)
    slots = max(len(ctx.slots), 1)
    for j, (_, _, _, k, via, _) in enumerate(links):
        link_cost[n_select + j] = 1 + 0.5 * k / slots + (0.25 if via else 0)
    return {
        "n": n,
        "n_select": n_select,
        "candidates": candidates,
        "x_index": x_index,
        "z_index": z_index,
        "links": links,
        "rows": rows,
        "row_index": row_index,
        "col_index": col_index,
        "values": values,
        "c": -score,
        "link_cost": link_cost,
        "lower": lower,
        "upper": upper,
    }


def _plan_id(prefix, digest, selections, allocations):
    text = json.dumps(
        {
            "digest": digest,
            "selections": selections,
            "allocations": [a.model_dump(mode="json") for a in allocations],
        },
        sort_keys=True,
    )
    return f"{prefix}-{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def _plan_from(ctx, model, x, prefix, solver_name):
    selections = {mid: None for mid in ctx.missions}
    for c in model["candidates"]:
        if x[model["x_index"][c.option_id]] > 0.5:
            selections[c.mission_id] = c.option_id
    allocations = []
    for j, (owner, mid, sid, k, via, bits) in enumerate(model["links"]):
        if x[model["n_select"] + j] > 0.5:
            allocations.append(_allocation(ctx, mid, sid, k, via, bits))
    return _make_plan(ctx, selections, allocations, prefix, solver_name)


def _allocation(ctx, mid, sid, k, via_option, bits):
    a, b = ctx.slots[k]
    return LinkAllocation(
        mission_id=mid,
        station_id=sid,
        slot_index=k,
        window=TimeWindow(start_utc=a, end_utc=b),
        via_relay_mission_id=ctx.candidates[via_option].mission_id
        if via_option
        else None,
        bits=float(bits),
    )


def _make_plan(ctx, selections, allocations, prefix, solver_name):
    allocations = sorted(
        allocations,
        key=lambda a: (
            a.slot_index,
            a.mission_id,
            a.station_id,
            a.via_relay_mission_id or "",
        ),
    )
    selections = dict(sorted(selections.items()))
    return CampaignPlan(
        plan_id=_plan_id(prefix, ctx.digest, selections, allocations),
        campaign_id=ctx.problem.campaign_id,
        campaign_revision=ctx.problem.revision,
        input_fingerprint=ctx.digest,
        selections=selections,
        allocations=tuple(allocations),
        solver_name=solver_name,
    )


# ------------------------------------------------------------- independent plan


def plan_independently(problem: CampaignProblem) -> CampaignPlan:
    """Each mission picks and schedules its best option as if it were alone.

    A mission takes its highest-scoring candidate whose downlink fits by itself,
    using the earliest usable slots at the fastest station (ties by station ID).
    Other missions, dependencies and relays are ignored; null means no option
    fits even alone.
    """
    ctx = _Context(_revalidated(problem))
    selections, allocations = {}, []
    for mid in sorted(ctx.missions):
        ranked = sorted(
            ctx.by_mission[mid], key=lambda c: (-Fraction(c.exact_score), c.option_id)
        )
        selections[mid] = None
        for candidate in ranked:
            links = _alone_schedule(ctx, candidate)
            if links is not None:
                selections[mid] = candidate.option_id
                allocations += links
                break
    return _make_plan(
        ctx, selections, allocations, "independent", "r11-independent-greedy"
    )


def _alone_schedule(ctx, candidate):
    required = ctx.required_bits(candidate.mission_id)
    delivered, links = Fraction(0), []
    if required == 0:
        return links
    for k in ctx.slot_range(candidate.comm_window):
        best = None
        for sid in sorted(ctx.stations):
            if not ctx.stations[sid].capacity_links:
                continue
            code, _, bits = ctx.link(candidate, sid, k)
            if code is None and (best is None or bits > best[0]):
                best = (bits, sid)
        if best:
            links.append(
                _allocation(ctx, candidate.mission_id, best[1], k, None, best[0])
            )
            delivered += best[0]
            if delivered >= required:
                return links
    return None


# ----------------------------------------------------------- infeasibility


def explain_infeasibility(problem: CampaignProblem) -> tuple[ConflictSet, ...]:
    """Minimal mandatory mission sets that have no joint plan, with reasons.

    A deletion filter over mandatory missions (others may be cancelled without
    penalty) returns one minimal set: dropping any member restores feasibility.
    A time-limited solve is never treated as proof of infeasibility.
    """
    problem = _revalidated(problem)
    if solve_joint(problem).status != "infeasible":
        return ()
    members = sorted(
        m.mission_id for m in problem.missions if not m.policy.cancel_allowed
    )
    for mid in list(members):
        trial = tuple(m for m in members if m != mid)
        if solve_joint(problem, required_mission_ids=trial).status == "infeasible":
            members = list(trial)
    ctx = _Context(problem)
    return (
        ConflictSet(mission_ids=tuple(members), detail=_conflict_detail(ctx, members)),
    )


def _rejection_summary(ctx, mid):
    counts = Counter(
        code
        for r in ctx.candidate_set.rejections
        if r.mission_id == mid
        for code in r.codes
    )
    return (
        ", ".join(f"{code}: {count}" for code, count in sorted(counts.items()))
        or "none"
    )


def _alone_capacity(ctx, candidate):
    total = Fraction(0)
    for k in ctx.slot_range(candidate.comm_window):
        options = [
            ctx.link(candidate, sid, k)[2]
            for sid in sorted(ctx.stations)
            if ctx.stations[sid].capacity_links
        ]
        options = [b for b in options if b is not None]
        total += max(options, default=Fraction(0))
    return total


def _conflict_detail(ctx, members):
    if len(members) == 1:
        (mid,) = members
        candidates = ctx.by_mission[mid]
        if not candidates:
            return (
                f"{mid} has no option that passes the evidence checks "
                f"(rejections: {_rejection_summary(ctx, mid)})."
            )
        required = ctx.required_bits(mid)
        best = max(_alone_capacity(ctx, c) for c in candidates)
        if best < required:
            return (
                f"Even alone, {mid} can deliver at most {float(best):.3g} of the "
                f"{float(required):.3g} required bits through direct station links before its deadline."
            )
        return (
            f"{mid} cannot be completed even when every other mission may be cancelled "
            "(its dependencies or relay needs cannot be met)."
        )
    stations = set()
    for mid in members:
        for c in ctx.by_mission[mid]:
            for k in ctx.slot_range(c.comm_window):
                stations |= {
                    sid
                    for sid in ctx.stations
                    if ctx.stations[sid].capacity_links
                    and ctx.link(c, sid, k)[0] is None
                }
    demand = ", ".join(
        f"{mid} {float(ctx.required_bits(mid)):.3g} bits" for mid in members
    )
    shared = ", ".join(sorted(stations)) or "no common station"
    return (
        f"Mandatory missions {', '.join(members)} cannot all be completed together "
        f"(downlink: {demand}; usable stations: {shared}). Dropping any one of them "
        "restores a feasible plan; allow cancellation or deferral, add station time "
        "or relax a deadline."
    )


# ------------------------------------------------------------- explanations


def _explain(problem, ctx, joint, conflicts, time_limit_s):
    explanations = []
    in_conflict = {mid for s in conflicts for mid in s.mission_ids}
    for mid in sorted(ctx.missions):
        mission = ctx.missions[mid]
        counts = dict(
            sorted(
                Counter(
                    code
                    for r in ctx.candidate_set.rejections
                    if r.mission_id == mid
                    for code in r.codes
                ).items()
            )
        )
        if joint.plan is None:
            detail = (
                f"Not planned: part of the minimal conflicting set. {conflicts[0].detail}"
                if mid in in_conflict
                else "Not planned: no joint plan exists for the mandatory missions."
            )
            explanations.append(
                MissionExplanation(
                    mission_id=mid,
                    status="unplanned",
                    detail=detail,
                    rejection_counts=counts,
                    alternatives=(),
                )
            )
            continue
        base = Fraction(joint.validation.exact_score)
        outcome = {o.mission_id: o for o in joint.validation.outcomes}[mid]
        selected = joint.plan.selections[mid]

        def measure(**kwargs):
            result = solve_joint(problem, time_limit_s=time_limit_s, **kwargs)
            if result.status == "infeasible":
                return "forcing_infeasible", None
            if result.status != "optimal":
                return "not_evaluated", None
            return "lower_joint_score", float(
                Fraction(result.validation.exact_score) - base
            )

        alternatives = [
            OptionExplanation(
                option_id=selected,
                mission_id=mid,
                status="selected" if selected else "cancel_selected",
                reason="best_joint_score",
                score_delta=0.0,
                detail="Chosen: part of the best-scoring feasible joint plan.",
            )
        ]
        others = sorted(
            (c for c in ctx.by_mission[mid] if c.option_id != selected),
            key=lambda c: (-Fraction(c.exact_score), c.option_id),
        )[:MAX_EXPLAINED_ALTERNATIVES]
        for c in others:
            reason, delta = measure(forced_option_ids=(c.option_id,))
            alternatives.append(
                OptionExplanation(
                    option_id=c.option_id,
                    mission_id=mid,
                    status="not_selected",
                    reason=reason,
                    score_delta=delta,
                    detail=_alternative_text(reason, delta, c),
                )
            )
        if mission.policy.cancel_allowed and selected is not None:
            reason, delta = measure(
                excluded_option_ids=tuple(c.option_id for c in ctx.by_mission[mid])
            )
            alternatives.append(
                OptionExplanation(
                    option_id=None,
                    mission_id=mid,
                    status="cancel_not_selected",
                    reason=reason,
                    score_delta=delta,
                    detail=_alternative_text(reason, delta, None),
                )
            )
        explanations.append(
            MissionExplanation(
                mission_id=mid,
                status=outcome.status,
                detail=_mission_text(joint.plan, ctx, mid, outcome, selected, measure),
                rejection_counts=counts,
                alternatives=tuple(alternatives),
            )
        )
    return tuple(explanations)


def _alternative_text(reason, delta, candidate):
    what = (
        f"Landing at {candidate.site_id} {_iso(candidate.landing_utc)}"
        + (" (deferred)" if candidate and candidate.deferred else "")
        if candidate
        else "Cancelling this mission"
    )
    if reason == "forcing_infeasible":
        return f"{what} leaves no feasible joint plan."
    if reason == "lower_joint_score":
        return f"{what} is feasible but lowers the best joint score by {-delta:.3f}."
    return f"{what} was not evaluated within the time limit."


def _mission_text(plan, ctx, mid, outcome, selected, measure):
    if outcome.status == "cancelled":
        reason, delta = measure(forbid_cancel_mission_ids=(mid,))
        penalty = float(ctx.missions[mid].policy.cancel_penalty_score)
        if reason == "forcing_infeasible":
            alone, _ = measure(
                only_mission_ids=(mid,), forbid_cancel_mission_ids=(mid,)
            )
            if alone == "forcing_infeasible":
                return (
                    f"Cancelled (penalty {penalty:g}): {_conflict_detail(ctx, [mid])}"
                )
            return f"Cancelled (penalty {penalty:g}): no option for {mid} fits with the other missions."
        if reason == "lower_joint_score":
            return f"Cancelled (penalty {penalty:g}): completing {mid} lowers the joint score by {-delta:.3f}."
        return f"Cancelled (penalty {penalty:g})."
    candidate = ctx.candidates[selected]
    text = f"Lands at {candidate.site_id} {_iso(candidate.landing_utc)}"
    relay = ""
    if outcome.relayed_bits:
        vias = sorted(
            {
                a.via_relay_mission_id
                for a in plan.allocations
                if a.mission_id == mid and a.via_relay_mission_id
            }
        )
        relay = (
            f" {outcome.relayed_bits:.3g} bits are relayed through {', '.join(vias)}."
        )
    if outcome.status == "deferred":
        closed = _iso(ctx.missions[mid].landing_window.end_utc)
        text += f", after its nominal landing window closed at {closed}"
        deferred = tuple(c.option_id for c in ctx.by_mission[mid] if c.deferred)
        reason, delta = measure(
            excluded_option_ids=deferred, forbid_cancel_mission_ids=(mid,)
        )
        if reason == "forcing_infeasible":
            return f"{text}: no nominal option fits with the other missions.{relay}"
        if reason == "lower_joint_score":
            return f"{text}: the best nominal plan scores {-delta:.3f} lower.{relay}"
        return f"{text}.{relay}"
    return f"{text}, within its nominal window.{relay}"


# ---------------------------------------------------------------- timeline


def campaign_timeline(
    problem: CampaignProblem,
    candidate_set: CandidateSet,
    plan: CampaignPlan | None,
    validation: CampaignValidation | None,
    independent_validation: CampaignValidation | None = None,
) -> dict:
    """Presentation-ready shared timeline: lanes, usage, conflicts, dependencies."""
    problem = _revalidated(problem)
    ctx = _Context(problem, candidate_set)
    if candidate_set.input_fingerprint != ctx.digest:
        raise ValueError("Candidate set does not belong to this campaign")
    outcomes = {o.mission_id: o for o in validation.outcomes} if validation else {}
    lanes = []
    for mid in sorted(ctx.missions):
        mission = ctx.missions[mid]
        option_id = plan.selections.get(mid) if plan else None
        candidate = ctx.candidates.get(option_id) if option_id else None
        outcome = outcomes.get(mid)
        lanes.append(
            {
                "mission_id": mid,
                "mission_type": mission.mission_type,
                "priority": mission.priority,
                "vehicle_id": mission.vehicle_id,
                "status": outcome.status if outcome else "unplanned",
                "option_id": option_id,
                "site_id": candidate.site_id if candidate else None,
                "landing_utc": _iso(candidate.landing_utc) if candidate else None,
                "surface_window": _window(
                    candidate.surface_window.start_utc, candidate.surface_window.end_utc
                )
                if candidate
                else None,
                "comm_window": _window(
                    candidate.comm_window.start_utc, candidate.comm_window.end_utc
                )
                if candidate
                else None,
                "landing_window": _window(
                    mission.landing_window.start_utc, mission.landing_window.end_utc
                ),
                "deadline_utc": _iso(mission.deadline_utc),
                "deferred": bool(candidate and candidate.deferred),
                "defer_s": candidate.defer_s if candidate else 0.0,
                "required_bits": float(ctx.required_bits(mid)),
                "delivered_bits": outcome.delivered_bits if outcome else 0.0,
                "relayed_bits": outcome.relayed_bits if outcome else 0.0,
                "cancel_allowed": mission.policy.cancel_allowed,
                "defer_allowed": mission.policy.defer_allowed,
                "requires_exclusive_link": mission.requires_exclusive_link,
            }
        )
    allocations = list(plan.allocations) if plan else []
    stations = []
    for sid in sorted(ctx.stations):
        station = ctx.stations[sid]
        used = {}
        for a in allocations:
            if a.station_id == sid:
                used.setdefault(a.slot_index, []).append(a.mission_id)
        stations.append(
            {
                "station_id": sid,
                "capacity_links": station.capacity_links,
                "availability": [
                    _window(w.start_utc, w.end_utc)
                    for w in sorted(station.availability, key=lambda w: w.start_utc)
                ],
                "slots": [
                    {
                        "slot_index": k,
                        **_window(*ctx.slots[k]),
                        "used": len(owners),
                        "capacity": station.capacity_links,
                        "mission_ids": sorted(owners),
                    }
                    for k, owners in sorted(used.items())
                ],
            }
        )
    dependencies = []
    for mid in sorted(ctx.missions):
        for dependency in ctx.missions[mid].dependencies:
            pred = dependency.predecessor_mission_id
            satisfied = None
            if plan is not None:
                a = ctx.candidates.get(plan.selections.get(pred) or "")
                b = ctx.candidates.get(plan.selections.get(mid) or "")
                if b is None:
                    satisfied = True
                else:
                    satisfied = (
                        a is not None
                        and b.landing_utc
                        >= a.landing_utc + timedelta(seconds=dependency.min_lag_s)
                    )
            dependencies.append(
                {
                    "predecessor_mission_id": pred,
                    "successor_mission_id": mid,
                    "min_lag_s": dependency.min_lag_s,
                    "satisfied": satisfied,
                }
            )
    conflicts, independent = [], []
    if independent_validation is not None:
        for issue in independent_validation.issues:
            conflicts.append(
                {
                    "code": issue.code,
                    "mission_ids": list(issue.mission_ids),
                    "station_id": issue.station_id,
                    "start_utc": _iso(issue.window.start_utc) if issue.window else None,
                    "end_utc": _iso(issue.window.end_utc) if issue.window else None,
                    "detail": issue.detail,
                }
            )
        for outcome in independent_validation.outcomes:
            candidate = ctx.candidates.get(outcome.option_id or "")
            independent.append(
                {
                    "mission_id": outcome.mission_id,
                    "option_id": outcome.option_id,
                    "site_id": candidate.site_id if candidate else None,
                    "landing_utc": _iso(candidate.landing_utc) if candidate else None,
                    "surface_window": _window(
                        candidate.surface_window.start_utc,
                        candidate.surface_window.end_utc,
                    )
                    if candidate
                    else None,
                }
            )
    return {
        "window": _window(problem.window.start_utc, problem.window.end_utc),
        "slot_s": problem.slot_s,
        "missions": lanes,
        "stations": stations,
        "allocations": [
            {
                "mission_id": a.mission_id,
                "station_id": a.station_id,
                "slot_index": a.slot_index,
                **_window(a.window.start_utc, a.window.end_utc),
                "via_relay_mission_id": a.via_relay_mission_id,
                "bits": a.bits,
            }
            for a in allocations
        ],
        "dependencies": dependencies,
        "conflicts": conflicts,
        "independent": independent,
    }


# ----------------------------------------------------------------- pipeline


def plan_campaign(
    problem: CampaignProblem, *, time_limit_s: float = 10.0
) -> CampaignResult:
    """Generate, solve, validate, compare, explain and present a campaign."""
    problem = _revalidated(problem)
    ctx = _Context(problem)
    joint = solve_joint(problem, time_limit_s=time_limit_s)
    independent = plan_independently(problem)
    independent_validation = _validate(independent, ctx)
    conflicts = explain_infeasibility(problem) if joint.status == "infeasible" else ()
    explanations = _explain(problem, ctx, joint, conflicts, time_limit_s)
    return CampaignResult(
        input_fingerprint=ctx.digest,
        candidate_set=ctx.candidate_set,
        joint=joint,
        independent_plan=independent,
        independent_validation=independent_validation,
        explanations=explanations,
        conflict_sets=conflicts,
        timeline=campaign_timeline(
            problem,
            ctx.candidate_set,
            joint.plan,
            joint.validation,
            independent_validation,
        ),
        limitations=LIMITATIONS,
    )


# ----------------------------------------------------------------- adapters


def _parse(value) -> datetime:
    moment = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if moment.utcoffset() != timedelta(0):
        raise ValueError("Result timestamps must be explicit UTC")
    return moment


def _row_window(row):
    return TimeWindow(
        start_utc=_parse(row["start_utc"]), end_utc=_parse(row["end_utc"])
    )


def site_evidence_from_results(
    *,
    site_id: str,
    latitude_deg: float,
    longitude_deg_east: float,
    analysis_id: str,
    visibility: dict,
    terrain_safety: dict,
    descents: dict[str, dict],
    communication_id: str | None = None,
    communication_windows: dict | None = None,
) -> tuple:
    """Adapt Task 3, R2, terrain-safety and R4 result shapes into SiteEvidence.

    Returns (SiteEvidence, tuple[Source, ...]) with observed/derived provenance.
    Sun intervals come from visibility["sun"]["intervals"], station geometry from
    the R2 per-station intervals, slope from the terrain-safety result and
    propellant from each R4 plan's totals. Nothing is filled in when absent.
    """
    if (communication_id is None) != (communication_windows is None):
        raise ValueError("communication_id and communication_windows go together")
    analysis_source = Source(
        source_id=f"analysis:{analysis_id}",
        kind="observed",
        description="Task 3 terrain-relative Sun/Earth visibility (LOLA horizon, JPL Horizons ephemerides).",
        locator=f"analysis:{analysis_id}",
        analysis_id=analysis_id,
    )
    sources = [analysis_source]
    product = terrain_safety.get("dem_product")
    if product:
        dem = Source(
            source_id=f"dem:{product}",
            kind="observed",
            description="LRO LOLA polar DEM used for slope and terrain height.",
            locator=str(product),
            version=str(product),
        )
        terrain_source = Source(
            source_id=f"terrain-safety:{site_id}",
            kind="derived",
            description=str(
                terrain_safety.get("method", "R3 terrain-safety slope evaluation")
            ),
            version="r3-terrain-safety",
            parent_source_ids=(dem.source_id,),
        )
        sources += [dem, terrain_source]
    else:
        terrain_source = Source(
            source_id=f"terrain-safety:{site_id}",
            kind="assumption",
            description="Terrain-safety values supplied without a DEM product identifier.",
        )
        sources.append(terrain_source)
    descent_evidence = []
    for vehicle_id, result in sorted(descents.items()):
        source = Source(
            source_id=f"descent:{vehicle_id}:{site_id}",
            kind="derived",
            description="R4 powered-descent plan (planar point-mass model); propellant to touchdown.",
            version="r4-plan-descent",
            parent_source_ids=(terrain_source.source_id,),
        )
        sources.append(source)
        descent_evidence.append(
            DescentEvidence(
                vehicle_id=vehicle_id,
                feasible=bool(result["feasible"]),
                propellant_used_kg=result["totals"]["propellant_used_kg"],
                source_ids=(source.source_id,),
            )
        )
    geometry = []
    evidence_sources = [analysis_source.source_id]
    if communication_windows is not None:
        comm = Source(
            source_id=f"communication:{communication_id}",
            kind="observed",
            description="R2 DSN station geometric visibility (station elevation and Earth above the terrain horizon); not a booking or link budget.",
            locator=f"communication:{communication_id}",
            analysis_id=communication_id,
            parent_source_ids=(analysis_source.source_id,),
        )
        sources.append(comm)
        evidence_sources.append(comm.source_id)
        for station_id, view in sorted(communication_windows["stations"].items()):
            geometry.append(
                StationGeometry(
                    station_id=station_id,
                    intervals=tuple(
                        GeometryInterval(window=_row_window(row), state=row["state"])
                        for row in view["intervals"]
                    ),
                )
            )
    request = visibility["request"]
    evidence = SiteEvidence(
        site={
            "site_id": site_id,
            "latitude_deg": latitude_deg,
            "longitude_deg_east": longitude_deg_east,
        },
        evidence_window=TimeWindow(
            start_utc=_parse(request["start_utc"]), end_utc=_parse(request["end_utc"])
        ),
        terrain=TerrainEvidence(
            max_landing_slope_deg=terrain_safety["max_landing_slope_deg"],
            unknown_landing_fraction=terrain_safety["unknown_landing_fraction"],
            hazard_distance_m=terrain_safety.get("hazard_distance_m"),
            source_ids=(terrain_source.source_id,),
        ),
        sunlight=tuple(
            SunInterval(window=_row_window(row), state=row["state"])
            for row in visibility["sun"]["intervals"]
        ),
        station_geometry=tuple(geometry),
        descents=tuple(descent_evidence),
        source_ids=tuple(evidence_sources),
    )
    return evidence, tuple(sorted(sources, key=lambda s: s.source_id))


# --------------------------------------------------------------------- API


class CampaignEvidenceRequest(BaseModel):
    """Optional R2 job, R4 vehicle profiles and R3 slope settings for evidence."""

    model_config = ConfigDict(extra="forbid")
    communication_id: str | None = None
    vehicle_profile_ids: tuple[str, ...] = ("apollo-lm-11",)
    safety: dict = Field(default_factory=dict)


def _example_paths() -> dict[str, Path]:
    return {path.stem: path for path in sorted(EXAMPLES_DIR.glob("*.json"))}


def register_campaign_routes(app, analyses, communications, data_dir) -> None:
    """GET /campaigns/examples[/{name}], POST /campaigns/plan and evidence routes."""
    from app.descent import (
        DescentError,
        DescentRequest,
        load_profiles,
        plan_descent,
        terrain_height_m,
    )
    from app.terrain_safety import SafetySettings, TerrainSafetyError, terrain_safety

    @app.get("/campaigns/examples")
    def campaign_examples():
        rows = []
        for name, path in _example_paths().items():
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "name": name,
                    "campaign_id": data["campaign_id"],
                    "description": data["description"],
                }
            )
        return rows

    @app.get("/campaigns/examples/{name}")
    def campaign_example(name: str):
        path = _example_paths().get(name)
        if path is None:
            raise HTTPException(404, "Unknown example campaign")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/campaigns/plan")
    def campaign_plan(body: CampaignProblem):
        try:
            return plan_campaign(body).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/analyses/{analysis_id}/campaign-evidence")
    def campaign_evidence(analysis_id: str, body: CampaignEvidenceRequest):
        job = analyses.get(analysis_id)
        if job is None:
            raise HTTPException(404, "Analysis not found")
        if job["status"] not in ("ready", "insufficient_data") or not job["result"]:
            raise HTTPException(409, "Analysis is not complete")
        windows = None
        if body.communication_id is not None:
            comm = communications.get(body.communication_id)
            if comm is None:
                raise HTTPException(404, "Communication job not found")
            if comm["analysis_id"] != analysis_id:
                raise HTTPException(
                    422, "Communication job belongs to another analysis"
                )
            if (
                comm["status"] not in ("ready", "insufficient_data")
                or not comm["result"]
            ):
                raise HTTPException(409, "Communication job is not complete")
            windows = comm["result"]["windows"]
        profiles = load_profiles()
        unknown = sorted(set(body.vehicle_profile_ids) - set(profiles))
        if unknown:
            raise HTTPException(422, f"Unknown vehicle profile(s): {unknown}")
        try:
            settings = SafetySettings(**body.safety)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        submission = job["submission"]
        lat, lon = submission["latitude_deg"], submission["longitude_deg_east"]
        try:
            safety = terrain_safety(data_dir, lat, lon, settings)
            height = terrain_height_m(data_dir, lat, lon)
            descents = {
                pid: plan_descent(
                    DescentRequest(
                        site_id=submission["site_id"],
                        latitude_deg=lat,
                        longitude_deg_east=lon,
                        profile_id=pid,
                    ),
                    height,
                )
                for pid in body.vehicle_profile_ids
            }
            evidence, sources = site_evidence_from_results(
                site_id=submission["site_id"],
                latitude_deg=lat,
                longitude_deg_east=lon,
                analysis_id=analysis_id,
                visibility=job["result"]["analysis"],
                terrain_safety=safety,
                descents=descents,
                communication_id=body.communication_id,
                communication_windows=windows,
            )
        except FileNotFoundError as exc:
            raise HTTPException(409, f"Terrain must be acquired first: {exc}") from exc
        except (TerrainSafetyError, DescentError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "evidence": evidence.model_dump(mode="json"),
            "sources": [s.model_dump(mode="json") for s in sources],
        }
