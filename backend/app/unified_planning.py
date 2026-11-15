"""Lossless native input envelope and bounded R11/H6 identity adapters."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from shared.campaign import CampaignProblem
from shared.hybrid import Contract, Digest
from shared.unified_planning import (
    CampaignProposal,
    IntegrationContext,
    ModuleEvidence,
    OptionLink,
)

from .hybrid_recovery import HybridRecoveryRequest


class UnifiedPlanningEnvelope(Contract):
    schema_version: Literal[1] = 1
    context: IntegrationContext
    campaign: CampaignProblem
    recovery: HybridRecoveryRequest
    option_links: tuple[OptionLink, ...] = Field(min_length=1, max_length=32)
    module_evidence: tuple[ModuleEvidence, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def check_connections(self):
        raise NotImplementedError


class PlanningSnapshot(Contract):
    schema_version: Literal[1] = 1
    envelope: UnifiedPlanningEnvelope
    campaign_sha256: Digest
    recovery_sha256: Digest
    snapshot_sha256: Digest


class AdapterError(ValueError):
    """Stable machine-readable code plus an explanation; no silent fallback."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def seal_inputs(envelope: UnifiedPlanningEnvelope) -> PlanningSnapshot:
    """Revalidate and detach inputs; bind all original constraints and evidence."""
    raise NotImplementedError


def to_r11_problem(
    snapshot: PlanningSnapshot, *, current: UnifiedPlanningEnvelope, now_utc: datetime
) -> CampaignProblem:
    """Return the complete current R11 model, including nonprojected constraints."""
    raise NotImplementedError


def to_h6_request(
    snapshot: PlanningSnapshot, *, current: UnifiedPlanningEnvelope, now_utc: datetime
) -> HybridRecoveryRequest:
    """Return the native current H6 request without any frame/time conversion."""
    raise NotImplementedError


def map_h6_proposal(
    snapshot: PlanningSnapshot,
    selected_option_ids: tuple[str, ...],
    *,
    current: UnifiedPlanningEnvelope,
    now_utc: datetime,
) -> CampaignProposal:
    """Map a complete H0 selection to a partial, unvalidated R11 proposal."""
    raise NotImplementedError
