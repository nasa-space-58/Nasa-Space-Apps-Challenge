"""Bounded classical completion and revalidation of synthetic R11/H6 snapshots."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from shared.campaign import CampaignProblem
from shared.hybrid import Contract, Digest, Identifier

from .campaign_planning import CampaignPlan, CampaignValidation, LinkAllocation
from .unified_planning import PlanningSnapshot, UnifiedPlanningEnvelope


class CompletedMission(Contract):
    """Caller-supplied executed-action ledger; retained exactly or rejected."""

    mission_id: Identifier
    option_id: Identifier
    allocations: tuple[LinkAllocation, ...] = ()


class LandingExclusion(Contract):
    event_id: Identifier
    epoch_utc: datetime
    start_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    end_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]


class ClassicalCandidate(Contract):
    target_id: Identifier
    status: Literal["feasible", "rejected", "infeasible", "limit_reached"]
    reasons: tuple[str, ...] = ()
    physical: dict
    problem: CampaignProblem | None = None
    plan: CampaignPlan | None = None
    validation: CampaignValidation | None = None
    solver_status: str | None = None


class ClassicalDecision(Contract):
    schema_version: Literal[1] = 1
    snapshot_sha256: Digest
    completed: tuple[CompletedMission, ...] = ()
    candidates: tuple[ClassicalCandidate, ...]
    selected_target_id: Identifier | None
    status: Literal["feasible", "no_feasible_continuation", "limit_reached"]
    optimum_proven_over_screened_targets: bool
    elapsed_s: float
    simulated_clock_paused: Literal[True] = True
    execution_mode: Literal["classical"] = "classical"
    decision_sha256: Digest


def plan_classical(
    snapshot: PlanningSnapshot, *, current: UnifiedPlanningEnvelope,
    now_utc: datetime, completed: tuple[CompletedMission, ...] = (),
    time_limit_s: float = 5.0,
) -> ClassicalDecision:
    """Screen physical continuations, derive exact arrival evidence and solve full R11."""
    raise NotImplementedError


def accept_classical(
    decision: ClassicalDecision, snapshot: PlanningSnapshot, *,
    current: UnifiedPlanningEnvelope, now_utc: datetime,
    completed: tuple[CompletedMission, ...] = (),
) -> ClassicalCandidate:
    """Recheck identity, full allocations and physical continuation before acceptance."""
    raise NotImplementedError


def apply_landing_exclusion(
    snapshot: PlanningSnapshot, event: LandingExclusion,
) -> UnifiedPlanningEnvelope:
    """Advance a zero-uncertainty synthetic estimate to the event; invalidate old plans."""
    raise NotImplementedError
