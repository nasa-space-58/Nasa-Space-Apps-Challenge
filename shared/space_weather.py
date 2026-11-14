"""NOAA SWPC space weather snapshot messages shared by backend and worker.

Sources: NOAA Space Weather Prediction Center public JSON products
(https://services.swpc.noaa.gov/). Messages carry only references, never data.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.ingestion import EVENT_EXCHANGE

SPACE_WEATHER_REQUEST_QUEUE = "lunar.space-weather.requests.v1"
SPACE_WEATHER_DEAD_QUEUE = "lunar.space-weather.dead.v1"
SWPC_BASE_URL = "https://services.swpc.noaa.gov"
# Product name -> path below SWPC_BASE_URL. All are public JSON documents.
PRODUCTS = {
    "solar_probabilities": "/json/solar_probabilities.json",
    "xrays": "/json/goes/primary/xrays-1-day.json",
    "protons": "/json/goes/primary/integral-protons-1-day.json",
    "solar_regions": "/json/solar_regions.json",
}


class SpaceWeatherRequest(BaseModel):
    """Fetch one current SWPC snapshot; request_id is the idempotency key."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    request_id: UUID


class SpaceWeatherEvent(BaseModel):
    """Ready events reference a space-weather/<sha256> bundle; failures carry errors."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    request_id: UUID
    status: Literal["ready", "failed"]
    snapshot_path: str | None = Field(
        default=None, pattern=r"^space-weather/[0-9a-f]{64}$"
    )
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "SpaceWeatherEvent":
        """Ready requires a reference only; failed requires an error only."""
        has_error = bool(self.error_code) or bool(self.error_message)
        if self.status == "ready":
            if not self.snapshot_path or has_error:
                raise ValueError(
                    "Ready events require a snapshot reference and no error"
                )
        elif not self.error_code or not self.error_message or self.snapshot_path:
            raise ValueError("Failed events require an error and no reference")
        return self


async def declare_space_weather_topology(channel):
    """Declare durable request/dead-letter queues and the shared event exchange."""
    import aio_pika

    await channel.declare_queue(SPACE_WEATHER_DEAD_QUEUE, durable=True)
    queue = await channel.declare_queue(
        SPACE_WEATHER_REQUEST_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": SPACE_WEATHER_DEAD_QUEUE,
        },
    )
    exchange = await channel.declare_exchange(
        EVENT_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )
    return queue, exchange
