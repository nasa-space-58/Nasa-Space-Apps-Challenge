"""Geocentric Moon and Sun ephemerides for the illustrative flight.

JPL Horizons VECTORS tables: Earth-centred (500@399), ecliptic of J2000.0,
kilometres, UTC sample times. Messages carry only references, never data.
The mission timeline (shared/mission_timeline.json) gives Apollo 11 elapsed
times used to place an illustrative flight before a chosen landing time.
"""

from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared.ingestion import EVENT_EXCHANGE  # noqa: F401

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


def load_mission_timeline(path: Path = TIMELINE_FILE) -> dict[str, float]:
    """Mission event times in seconds after launch, strictly increasing."""
    raise NotImplementedError


async def declare_flight_ephemeris_topology(channel):
    """Declare durable request/dead-letter queues and the shared event exchange."""
    raise NotImplementedError
