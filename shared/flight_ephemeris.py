"""Geocentric Moon and Sun ephemerides for the illustrative flight.

JPL Horizons VECTORS tables: Earth-centred (500@399), ecliptic of J2000.0,
kilometres, UTC sample times. Messages carry only references, never data.
The mission timeline (shared/mission_timeline.json) gives Apollo 11 elapsed
times used to place an illustrative flight before a chosen landing time.
"""

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.ingestion import EVENT_EXCHANGE

FLIGHT_EPHEMERIS_REQUEST_QUEUE = "lunar.flight-ephemeris.requests.v1"
FLIGHT_EPHEMERIS_DEAD_QUEUE = "lunar.flight-ephemeris.dead.v1"
TIMELINE_FILE = Path(__file__).resolve().parent / "mission_timeline.json"
# Horizons body IDs and the names used in results.
BODIES = {"301": "moon", "10": "sun"}
SUPPORTED_STEPS_S = (600, 1800)
MAX_DURATION_S = 7 * 86400
EVENT_ORDER = (
    "earth_orbit_insertion",
    "translunar_injection",
    "lunar_orbit_insertion",
    "powered_descent_initiation",
    "landing",
)


class FlightEphemerisRequest(BaseModel):
    """Moon and Sun geocentric vectors on a UTC grid from start to end."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    request_id: UUID
    start_utc: datetime
    end_utc: datetime
    step_s: int = Field(default=1800, strict=True)

    @field_validator("start_utc", "end_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Require explicit zero-offset UTC with whole seconds."""
        if value.utcoffset() != timedelta(0):
            raise ValueError("Timestamp must include UTC (Z or +00:00)")
        if value.microsecond:
            raise ValueError("Timestamp must be a whole UTC second")
        return value

    @field_validator("step_s")
    @classmethod
    def require_supported_step(cls, value: int) -> int:
        if value not in SUPPORTED_STEPS_S:
            raise ValueError("step_s must be 600 or 1800 seconds")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> "FlightEphemerisRequest":
        """Bound the window and require the end on the step grid."""
        duration_s = (self.end_utc - self.start_utc).total_seconds()
        if duration_s <= 0:
            raise ValueError("end_utc must follow start_utc")
        if duration_s > MAX_DURATION_S:
            raise ValueError("Window must be at most 7 days")
        if duration_s % self.step_s:
            raise ValueError("end_utc must lie on the step_s grid from start_utc")
        return self


class FlightEphemerisEvent(BaseModel):
    """Ready events reference a flight_ephemeris/<sha256> bundle."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    request_id: UUID
    status: Literal["ready", "failed"]
    ephemeris_path: str | None = Field(
        default=None, pattern=r"^flight_ephemeris/[0-9a-f]{64}$"
    )
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "FlightEphemerisEvent":
        """Ready requires a reference only; failed requires an error only."""
        has_error = bool(self.error_code) or bool(self.error_message)
        if self.status == "ready":
            if not self.ephemeris_path or has_error:
                raise ValueError("Ready events require a reference and no error")
        elif not self.error_code or not self.error_message or self.ephemeris_path:
            raise ValueError("Failed events require an error and no reference")
        return self


def load_mission_timeline(path: Path = TIMELINE_FILE) -> dict[str, float]:
    """Mission event times in seconds after launch, strictly increasing."""
    try:
        events = json.loads(Path(path).read_text())["events_s"]
        timeline = {name: float(events[name]) for name in EVENT_ORDER}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Cannot read mission timeline {path}: {exc}") from exc
    previous = 0.0
    for name, value in timeline.items():
        if not math.isfinite(value) or value <= previous:
            raise ValueError(f"Mission event {name} must follow the previous event")
        previous = value
    return timeline


async def declare_flight_ephemeris_topology(channel):
    """Declare durable request/dead-letter queues and the shared event exchange."""
    import aio_pika

    await channel.declare_queue(FLIGHT_EPHEMERIS_DEAD_QUEUE, durable=True)
    queue = await channel.declare_queue(
        FLIGHT_EPHEMERIS_REQUEST_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": FLIGHT_EPHEMERIS_DEAD_QUEUE,
        },
    )
    exchange = await channel.declare_exchange(
        EVENT_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )
    return queue, exchange
