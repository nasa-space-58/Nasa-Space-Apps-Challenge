"""H9 illustrative, evidence-bound three-mission antenna campaign."""

from typing import Annotated, Literal

from pydantic import Field

from shared.hybrid import Contract, Scenario


class CampaignRequest(Contract):
    mode: Literal["standard", "deadline", "closed", "unknown"] = "standard"
    crew_option: Annotated[int, Field(strict=True, ge=0, le=4)] = 0
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    evaluations: Annotated[int, Field(strict=True, ge=1, le=256)] = 80
    shots: Annotated[int, Field(strict=True, ge=1, le=8192)] = 256
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17


def load_campaign_evidence() -> dict:
    """Load the bounded source-backed analysis and R2 snapshot."""
    raise NotImplementedError


def build_campaign(request: CampaignRequest, evidence: dict) -> Scenario:
    """Bind candidate site/time to checked evidence and explicit calendar assumptions."""
    raise NotImplementedError


def campaign_catalog() -> dict:
    """Describe the example missions, options, sources and calendar in UTC."""
    raise NotImplementedError


def compare_campaign(request: CampaignRequest) -> dict:
    """Compare independent, exact and QAOA selections, with shared timeline evidence."""
    raise NotImplementedError


def campaign_timeline(scenario: Scenario, plan) -> dict:
    """Return validated rows and station occupancy, preserving half-open UTC intervals."""
    raise NotImplementedError


def register_campaign_routes(app):
    """Attach bounded local comparison routes; no provider submission endpoint."""
    raise NotImplementedError


def campaign_hardware() -> dict:
    """Read and independently validate optional H9 hardware evidence; never submit."""
    raise NotImplementedError
