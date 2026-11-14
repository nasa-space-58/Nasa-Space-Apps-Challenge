"""Real Moon/Sun positions and Earth rotation for the illustrative flight.

The flight is placed so it lands at a chosen UTC time, using Apollo 11 elapsed
times for its phases. Moon and Sun positions are JPL Horizons geocentric
vectors (ecliptic of J2000.0, km); Earth's rotation is the IERS Earth Rotation
Angle with UT1 approximated by UTC. The spacecraft path itself stays
illustrative and is drawn by the interface.
"""

from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from shared.flight_ephemeris import FlightEphemerisRequest

from app.submit_flight_ephemeris import submit_flight_ephemeris  # noqa: F401

STEP_S = 1800
PHASE_IDS = ("launch", "parking_orbit", "translunar_coast", "lunar_orbit", "descent")
EARLIEST_LANDING_UTC = "2020-01-01T00:00:00Z"
LATEST_LANDING_UTC = "2030-01-01T00:00:00Z"
LIMITATIONS = (
    "Moon and Sun positions are JPL Horizons geocentric vectors; the spacecraft path is illustrative, not a computed trajectory.",
    "Phase durations follow Apollo 11 (launch to landing 102 h 45 min 40 s); a real mission's timeline differs.",
    "Earth rotation uses the IERS Earth Rotation Angle with UTC for UT1 (|UT1-UTC| < 0.9 s).",
    "The Moon is shown in synchronous rotation facing Earth; libration is not modelled.",
)
ACTIVE = ("pending", "acquiring", "computing")
STATUSES = (*ACTIVE, "ready", "failed")
MAX_ACTIVE_JOBS = 4


class FlightEphemerisDataError(ValueError):
    """Missing, corrupt or inconsistent cached flight ephemeris."""


class FlightRequest(BaseModel):
    """Landing time (explicit UTC offset, whole seconds) the flight ends at."""

    model_config = ConfigDict(extra="forbid")
    landing_utc: datetime


def earth_rotation_deg(utc: datetime) -> float:
    """IERS Earth Rotation Angle in degrees [0, 360) with UT1 = UTC."""
    raise NotImplementedError


def mission_window(landing_utc: datetime) -> dict:
    """Launch time and UTC bounds of each flight phase ending at landing_utc."""
    raise NotImplementedError


def ephemeris_request_for(landing_utc: datetime, request_id: UUID) -> FlightEphemerisRequest:
    """Request on the STEP_S grid covering launch to landing."""
    raise NotImplementedError


def load_flight_ephemeris(data_dir: Path, path: str) -> dict[str, list[dict]]:
    """Verified rows per body ('moon', 'sun') from a cached bundle."""
    raise NotImplementedError


def flight_result(landing_utc: datetime, rows: dict[str, list[dict]]) -> dict:
    """Window, aligned samples with Earth rotation, frame and limitations."""
    raise NotImplementedError


class FlightEphemerisStore:
    """Flight ephemeris jobs in the analysis SQLite database."""

    def __init__(self, jobs):
        raise NotImplementedError

    def create(self, landing_utc: datetime) -> dict:
        raise NotImplementedError

    def get(self, job_id: str) -> dict | None:
        raise NotImplementedError

    def update(self, job_id: str, status: str, result=None, error=None) -> None:
        raise NotImplementedError

    def pending(self) -> list[dict]:
        raise NotImplementedError

    def recover(self) -> None:
        raise NotImplementedError


async def execute_flight_ephemeris(store, job, data_dir, broker_url) -> None:
    """Acquire vectors via RabbitMQ and persist the flight result."""
    raise NotImplementedError


def register_flight_ephemeris_routes(app, store) -> None:
    """POST /flight-ephemeris and GET /flight-ephemeris/{job_id}."""
    raise NotImplementedError
