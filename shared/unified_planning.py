"""Versioned integration metadata; no solver or provider dependencies."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from shared.hybrid import Contract, Digest, Identifier, Revision, Source

ModuleId = Literal[
    "H0", "H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10", "H11"
]


class ModuleEvidence(Contract):
    """Provenance reference, never a claim that a module is connected or enabled."""

    module_id: ModuleId
    contract_name: Identifier
    revision: Revision
    sha256: Digest
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class IntegrationContext(Contract):
    integration_id: Identifier
    revision: Revision
    created_utc: datetime
    decision_deadline_utc: datetime
    state_kind: Literal["estimate"]
    correspondence: Literal["declared_synthetic"]
    sources: tuple[Source, ...] = Field(min_length=1)


class OptionLink(Contract):
    """Declared nominal choice identity, not a coordinate transformation."""

    h6_option_id: Identifier
    r11_option_id: Identifier
    source_id: Identifier


class CampaignProposal(Contract):
    """Untrusted partial selections awaiting full classical completion/validation."""

    schema_version: Literal[1] = 1
    snapshot_sha256: Digest
    campaign_fingerprint: Digest
    proposed_utc: datetime
    source_module: Literal["H6"] = "H6"
    selections: dict[Identifier, Identifier]
    validation_status: Literal["unvalidated"] = "unvalidated"
    requires_classical_completion: Literal[True] = True
    requires_physical_revalidation: Literal[True] = True


class ModuleRole(Contract):
    module_id: ModuleId
    role: Annotated[str, Field(min_length=1)]
    stage2_status: Literal["native_input", "reference_only", "approval_required"]


def module_catalog() -> tuple[ModuleRole, ...]:
    """Describe all H modules without enabling execution or provider access."""
    raise NotImplementedError
