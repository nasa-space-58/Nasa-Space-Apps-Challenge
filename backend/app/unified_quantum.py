"""Opt-in local quantum proposals with full classical completion and admission."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from shared.hybrid import Contract, Digest

from .hybrid_quantum import QaoaResult
from .unified_classical import ClassicalCandidate, ClassicalDecision, CompletedMission
from .unified_planning import PlanningSnapshot, UnifiedPlanningEnvelope


class ProposalBudget(Contract):
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    evaluations: Annotated[int, Field(strict=True, ge=1, le=128)] = 32
    shots: Annotated[int, Field(strict=True, ge=1, le=1024)] = 64
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32-1)] = 17
    max_qubits: Annotated[int, Field(strict=True, ge=1, le=6)] = 4
    max_completions: Annotated[int, Field(strict=True, ge=1, le=8)] = 2
    completion_limit_s: Annotated[float, Field(strict=True, gt=0, le=30)] = 5.0


class ProposalAttempt(Contract):
    option_ids: tuple[str, ...]
    sample_count: int
    status: Literal["accepted", "rejected", "limit_reached"]
    reason: str | None = None
    candidate: ClassicalCandidate | None = None
    completion_s: float
    admission_s: float


class ProposalMethod(Contract):
    method: Literal["qaoa", "random", "greedy"]
    raw_counts: dict[str, int]
    raw_local_feasible_count: int
    attempts: tuple[ProposalAttempt, ...]
    skipped_by_completion_cap: tuple[str, ...] = ()
    best_decision: ClassicalDecision | None = None
    proposal_s: float


class HybridExperiment(Contract):
    schema_version: Literal[1] = 1
    snapshot_sha256: Digest
    mode: Literal["classical", "local_experiment"]
    budget: ProposalBudget
    classical_baseline: ClassicalDecision
    final_decision: ClassicalDecision
    selected_source: Literal["classical", "qaoa_proposal_classical_completion"]
    selection_reason: str
    unsupported_reason: str | None = None
    quantum: QaoaResult | None = None
    methods: tuple[ProposalMethod, ...] = ()
    option_to_target: dict[str, str] = {}
    frozen_outside_selections: dict[str, str | None] = {}
    elapsed_s: float
    hardware_jobs: Literal[0] = 0
    production_policy: Literal["classical_no_go_unchanged"] = "classical_no_go_unchanged"
    result_sha256: Digest


def compare_proposals(
    snapshot: PlanningSnapshot, *, current: UnifiedPlanningEnvelope,
    now_utc: datetime, mode: Literal["classical", "local_experiment"] = "classical",
    budget: ProposalBudget | None = None,
    completed: tuple[CompletedMission, ...] = (),
) -> HybridExperiment:
    """Run a classical baseline; only explicit local experiments sample QAOA."""
    raise NotImplementedError


def accept_hybrid(
    result: HybridExperiment, snapshot: PlanningSnapshot, *,
    current: UnifiedPlanningEnvelope, now_utc: datetime,
    completed: tuple[CompletedMission, ...] = (),
) -> ClassicalCandidate:
    """Revalidate final physics, campaign and frozen choices; never dispatch hardware."""
    raise NotImplementedError
