"""DSN ground-station ephemeris messages shared by backend and worker.

Station coordinates come from shared/dsn_stations.json (DSN 810-005 module 301
Rev. P, Table 5, WGS84 ellipsoid). Messages carry only references, never data.
"""

from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DSN_REQUEST_QUEUE = "lunar.dsn.requests.v1"
DSN_DEAD_QUEUE = "lunar.dsn.dead.v1"
STATIONS_FILE = Path(__file__).resolve().parent / "dsn_stations.json"


class DsnStation(BaseModel):
    """One DSN antenna: WGS84 geodetic degrees and ellipsoid height in metres."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)
    station_id: str = Field(pattern=r"^DSS-\d{2}$")
    complex: str = Field(min_length=1)
    antenna: str = Field(min_length=1)
    latitude_deg: float = Field(ge=-90, le=90, strict=True)
    longitude_deg_east: float = Field(ge=0, lt=360, strict=True)
    height_m: float = Field(strict=True)
    ellipsoid: Literal["WGS84"] = "WGS84"


def dms_to_deg(dms: list[float]) -> float:
    """Convert signed [deg, min, sec] (all components share the sign) to degrees."""
    raise NotImplementedError


def load_stations(path: Path = STATIONS_FILE) -> dict[str, DsnStation]:
    """Load sourced stations keyed by station_id; ValueError on malformed data."""
    raise NotImplementedError


def generic_elevation_limits_deg(path: Path = STATIONS_FILE) -> dict[str, float]:
    """Return the documented generic receive/transmit elevation limits."""
    raise NotImplementedError


class DsnRequest(BaseModel):
    """Moon az/el from each listed station over a bounded UTC grid."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    request_id: UUID
    start_utc: datetime
    end_utc: datetime
    step_s: int = Field(default=300, strict=True)
    station_ids: tuple[str, ...]

    @field_validator("start_utc", "end_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        raise NotImplementedError

    @field_validator("step_s")
    @classmethod
    def require_supported_step(cls, value: int) -> int:
        raise NotImplementedError

    @field_validator("station_ids")
    @classmethod
    def require_known_stations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        raise NotImplementedError

    @model_validator(mode="after")
    def validate_interval(self) -> "DsnRequest":
        raise NotImplementedError


class DsnEvent(BaseModel):
    """Ready events reference a checksummed dsn/<sha256> bundle; failures carry errors."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    request_id: UUID
    status: Literal["ready", "failed"]
    dsn_path: str | None = Field(default=None, pattern=r"^dsn/[0-9a-f]{64}$")
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "DsnEvent":
        raise NotImplementedError


async def declare_dsn_topology(channel):
    """Declare the durable DSN request/dead-letter queues and shared event exchange."""
    raise NotImplementedError
