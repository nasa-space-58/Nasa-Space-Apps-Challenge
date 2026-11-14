"""Versioned ingestion messages shared by backend and worker."""

from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shared.contracts import AnalysisRequest

REQUEST_QUEUE = "lunar.ingestion.requests.v1"
DEAD_QUEUE = "lunar.ingestion.dead.v1"
EVENT_EXCHANGE = "lunar.ingestion.events.v1"
EVENT_QUEUE = "lunar.ingestion.events.v1"


class IngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    request_id: UUID
    analysis: AnalysisRequest
    terrain_radius_m: int = Field(default=20000, ge=80, le=20000, strict=True)


class IngestionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    request_id: UUID
    status: Literal["ready", "failed"]
    horizons_path: str | None = Field(default=None, pattern=r"^horizons/[0-9a-f]{64}$")
    terrain_path: str | None = Field(
        default=None, pattern=r"^terrain/processed/[0-9a-f]{64}$"
    )
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_status(self):
        if self.status == "ready":
            if (
                not self.horizons_path
                or not self.terrain_path
                or self.error_code
                or self.error_message
            ):
                raise ValueError("Ready events require both references and no error")
        elif (
            not self.error_code
            or not self.error_message
            or self.horizons_path
            or self.terrain_path
        ):
            raise ValueError("Failed events require an error and no ready references")
        return self


async def declare_topology(channel):
    """Declare durable request, dead-letter and result queues/exchange."""
    import aio_pika

    await channel.declare_queue(DEAD_QUEUE, durable=True)
    queue = await channel.declare_queue(
        REQUEST_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": DEAD_QUEUE,
        },
    )
    exchange = await channel.declare_exchange(
        EVENT_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )
    events = await channel.declare_queue(EVENT_QUEUE, durable=True)
    await events.bind(exchange, routing_key="#")
    return queue, exchange
