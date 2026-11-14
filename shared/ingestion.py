"""Versioned ingestion messages shared by backend and worker."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from shared.contracts import AnalysisRequest

REQUEST_QUEUE = 'lunar.ingestion.requests.v1'
DEAD_QUEUE = 'lunar.ingestion.dead.v1'
EVENT_EXCHANGE = 'lunar.ingestion.events.v1'
EVENT_QUEUE = 'lunar.ingestion.events.v1'


class IngestionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    request_id: UUID
    analysis: AnalysisRequest
    terrain_radius_m: int = Field(default=20000, ge=80, le=20000, strict=True)


class IngestionEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    request_id: UUID
    status: Literal['ready', 'failed']
    horizons_path: str | None = None
    terrain_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None


async def declare_topology(channel):
    """Declare durable request, dead-letter and result queues/exchange."""
    raise NotImplementedError
