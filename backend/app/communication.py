"""R2 direct-to-Earth communication windows (scenario S4).

A window requires the Earth center above the local lunar terrain horizon (from a
saved analysis) and the Moon above a DSN station's elevation limit. Results are
geometric only: no station horizon masks, keyhole, scheduling or link budget.
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from shared.dsn import DsnRequest

DEFAULT_MIN_ELEVATION_DEG = 6.0
LIMITATIONS = (
    "Geometric line of sight only; not a guaranteed radio link.",
    "Generic DSN elevation limit; station-specific horizon masks are not applied.",
    "Antenna keyhole, station scheduling, availability and link budget are not modeled.",
    "Earth visibility refers to the Earth center above the local terrain horizon.",
)


class CommunicationRequest(BaseModel):
    """Station elevation limit in degrees; 6.0 receive, 10.0 low-power transmit."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    min_elevation_deg: float = Field(default=DEFAULT_MIN_ELEVATION_DEG, ge=6, le=90)


def dsn_request_for(analysis_job: dict, request_id) -> DsnRequest:
    """Build the station ephemeris request covering a saved analysis interval."""
    raise NotImplementedError


def load_dsn_samples(data_dir: Path, dsn_path: str) -> dict[str, list[dict]]:
    """Read a verified dsn/<sha256> bundle; ValueError on checksum or path errors."""
    raise NotImplementedError


def station_intervals(
    samples: list[dict],
    min_elevation_deg: float,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict]:
    """Split [start, end] into above/below/unknown intervals for one station."""
    raise NotImplementedError


def communication_windows(
    earth_intervals: list[dict],
    stations: dict[str, list[dict]],
    start_utc: datetime,
    end_utc: datetime,
) -> dict:
    """Intersect Earth visibility with each station and combine all stations."""
    raise NotImplementedError


class CommunicationStore:
    """Communication jobs in the analysis SQLite database."""

    def __init__(self, jobs):
        raise NotImplementedError

    def create(self, analysis_id: str, request: CommunicationRequest) -> dict:
        raise NotImplementedError

    def get(self, job_id: str) -> dict | None:
        raise NotImplementedError

    def update(self, job_id: str, status: str, result=None, error=None) -> None:
        raise NotImplementedError

    def pending(self) -> list[dict]:
        raise NotImplementedError

    def recover(self) -> None:
        raise NotImplementedError


async def execute_communication(store, analyses, job, data_dir, broker_url) -> None:
    """Acquire station ephemerides via RabbitMQ and persist the windows."""
    raise NotImplementedError


def register_communication_routes(app, analyses, communications) -> None:
    """POST /analyses/{id}/communication and GET /communications/{id}."""
    raise NotImplementedError
