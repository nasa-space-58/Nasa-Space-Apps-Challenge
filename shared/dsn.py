"""DSN ground-station ephemeris messages shared by backend and worker.

Station coordinates come from shared/dsn_stations.json (DSN 810-005 module 301
Rev. P, Table 5, WGS84 ellipsoid). Messages carry only references, never data.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.ingestion import EVENT_EXCHANGE

DSN_REQUEST_QUEUE = "lunar.dsn.requests.v1"
DSN_DEAD_QUEUE = "lunar.dsn.dead.v1"
STATIONS_FILE = Path(__file__).resolve().parent / "dsn_stations.json"
SUPPORTED_STEPS_S = (60, 300)
MAX_DURATION_S = 31 * 86400
MAX_SAMPLES = 10000
EARLIEST_UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATEST_UTC = datetime(2028, 1, 1, tzinfo=timezone.utc)


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
    if not isinstance(dms, (list, tuple)) or len(dms) != 3:
        raise ValueError("DMS value must have exactly three components")
    degrees, minutes, seconds = (float(v) for v in dms)
    signs = {v > 0 for v in (degrees, minutes, seconds) if v != 0}
    if len(signs) > 1:
        raise ValueError("DMS components must share one sign")
    if abs(minutes) >= 60 or abs(seconds) >= 60:
        raise ValueError("DMS minutes and seconds must be below 60")
    return degrees + minutes / 60 + seconds / 3600


def _document(path: Path) -> dict:
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read DSN station file {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("DSN station file must contain an object")
    return document


def load_stations(path: Path = STATIONS_FILE) -> dict[str, DsnStation]:
    """Load sourced stations keyed by station_id; ValueError on malformed data."""
    rows = _document(path).get("stations")
    if not isinstance(rows, list) or not rows:
        raise ValueError("DSN station file lists no stations")
    stations = {}
    for row in rows:
        try:
            station = DsnStation(
                station_id=row["station_id"],
                complex=row["complex"],
                antenna=row["antenna"],
                latitude_deg=dms_to_deg(row["latitude_dms"]),
                longitude_deg_east=dms_to_deg(row["longitude_east_dms"]),
                height_m=float(row["height_m"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Malformed DSN station entry: {exc}") from exc
        if station.station_id in stations:
            raise ValueError(f"Duplicate DSN station {station.station_id}")
        stations[station.station_id] = station
    return stations


def generic_elevation_limits_deg(path: Path = STATIONS_FILE) -> dict[str, float]:
    """Return the documented generic receive/transmit elevation limits."""
    limits = _document(path).get("generic_elevation_limits_deg")
    if not isinstance(limits, dict) or not limits:
        raise ValueError("DSN station file has no elevation limits")
    return {name: float(value) for name, value in limits.items()}


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
        """Require explicit zero-offset UTC with whole seconds."""
        if value.utcoffset() != timedelta(0):
            raise ValueError("Timestamp must include UTC (Z or +00:00)")
        if value.microsecond:
            raise ValueError("Timestamp must be a whole UTC second")
        return value

    @field_validator("step_s")
    @classmethod
    def require_supported_step(cls, value: int) -> int:
        """Accept one-minute and five-minute station sampling."""
        if value not in SUPPORTED_STEPS_S:
            raise ValueError("step_s must be 60 or 300 seconds")
        return value

    @field_validator("station_ids")
    @classmethod
    def require_known_stations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require a non-empty set of sourced stations, sorted for stable identity."""
        if not value:
            raise ValueError("At least one DSN station is required")
        if len(set(value)) != len(value):
            raise ValueError("Duplicate DSN station")
        unknown = set(value) - set(load_stations())
        if unknown:
            raise ValueError(f"Unknown DSN station(s): {sorted(unknown)}")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_interval(self) -> "DsnRequest":
        """Bound work and require the end to lie exactly on the sample grid."""
        if not EARLIEST_UTC <= self.start_utc < self.end_utc <= LATEST_UTC:
            raise ValueError("Require 2026-01-01 <= start < end <= 2028-01-01 UTC")
        duration_s = (self.end_utc - self.start_utc).total_seconds()
        if duration_s > MAX_DURATION_S:
            raise ValueError("DSN request duration must be at most 31 days")
        if duration_s % self.step_s:
            raise ValueError("end_utc must lie on the step_s grid from start_utc")
        if int(duration_s // self.step_s) + 1 > MAX_SAMPLES:
            raise ValueError("Request exceeds 10000 samples; shorten the interval")
        return self


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
        """Ready requires a reference only; failed requires an error only."""
        has_error = bool(self.error_code) or bool(self.error_message)
        if self.status == "ready":
            if not self.dsn_path or has_error:
                raise ValueError("Ready events require a DSN reference and no error")
        elif not self.error_code or not self.error_message or self.dsn_path:
            raise ValueError("Failed events require an error and no reference")
        return self


async def declare_dsn_topology(channel):
    """Declare the durable DSN request/dead-letter queues and shared event exchange."""
    import aio_pika

    await channel.declare_queue(DSN_DEAD_QUEUE, durable=True)
    queue = await channel.declare_queue(
        DSN_REQUEST_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": DSN_DEAD_QUEUE,
        },
    )
    exchange = await channel.declare_exchange(
        EVENT_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )
    return queue, exchange
