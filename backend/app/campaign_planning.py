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

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from shared.campaign import CampaignProblem
from shared.hybrid import Contract, Identifier, NonNegative, Number, TimeWindow

SOLVER_NAME = "r11-highs-milp"
MAX_VARIABLES = 20_000
MAX_EXPLAINED_ALTERNATIVES = 3

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


def generate_candidates(problem: CampaignProblem) -> CandidateSet:
    """Grid landing times per mission/site and keep only evidence-backed options."""
    raise NotImplementedError


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
    """Jointly select options, cancellations and link slots by MILP."""
    raise NotImplementedError


def validate_campaign_plan(
    plan: CampaignPlan, problem: CampaignProblem
) -> CampaignValidation:
    """Check a plan against the original rules without using any solver."""
    raise NotImplementedError


def plan_independently(problem: CampaignProblem) -> CampaignPlan:
    """Each mission picks and schedules its best option as if it were alone."""
    raise NotImplementedError


def explain_infeasibility(problem: CampaignProblem) -> tuple[ConflictSet, ...]:
    """Minimal mandatory mission sets that have no joint plan, with reasons."""
    raise NotImplementedError


def campaign_timeline(
    problem: CampaignProblem,
    candidate_set: CandidateSet,
    plan: CampaignPlan | None,
    validation: CampaignValidation | None,
    independent_validation: CampaignValidation | None = None,
) -> dict:
    """Presentation-ready shared timeline: lanes, usage, conflicts, dependencies."""
    raise NotImplementedError


def plan_campaign(
    problem: CampaignProblem, *, time_limit_s: float = 10.0
) -> CampaignResult:
    """Generate, solve, validate, compare, explain and present a campaign."""
    raise NotImplementedError


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
    """
    raise NotImplementedError


def register_campaign_routes(app, analyses, communications, data_dir) -> None:
    """GET /campaigns/examples[/{name}], POST /campaigns/plan and evidence routes."""
    raise NotImplementedError
