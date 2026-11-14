"""JPL Horizons geocentric vector tables for the flight scene, cached.

Query conventions: https://ssd.jpl.nasa.gov/horizons/manual.html. Targets are
the Moon (301) and the Sun (10) from Earth's centre (500@399), ecliptic of
J2000.0, position only, km, UTC times.
"""

from pathlib import Path

import httpx
from shared.flight_ephemeris import FlightEphemerisEvent, FlightEphemerisRequest

CACHE_VERSION = 1
PARSER_VERSION = 1
MAX_MESSAGE_BYTES = 65536


class FlightEphemerisSourceError(ValueError):
    """Invalid Horizons response or corrupt cached flight ephemeris."""


class FlightEphemerisRequestConflict(ValueError):
    """A request UUID was reused with different parameters."""


def build_vector_params(request: FlightEphemerisRequest, target: str) -> dict[str, str]:
    """Horizons VECTORS query for one body."""
    raise NotImplementedError


def flight_cache_key(request: FlightEphemerisRequest) -> str:
    """Stable sha256 over window, step, query and parser versions (not request_id)."""
    raise NotImplementedError


def parse_vector_response(
    decoded: dict, request: FlightEphemerisRequest, target: str
) -> list[dict]:
    """Rows {utc, x_km, y_km, z_km} on the exact request grid; fail loudly otherwise."""
    raise NotImplementedError


def download_flight_ephemeris(
    request: FlightEphemerisRequest, data_dir: Path, client: httpx.Client
) -> dict:
    """Atomic checksummed flight_ephemeris/<key> bundle; verified reuse."""
    raise NotImplementedError


def process_flight_ephemeris_job(
    request: FlightEphemerisRequest, data_dir: Path, client: httpx.Client | None = None
) -> FlightEphemerisEvent:
    """Idempotent job keyed by request UUID."""
    raise NotImplementedError


async def handle_flight_ephemeris_message(message, exchange, data_dir: Path) -> None:
    """Dead-letter invalid messages; publish the event before acknowledging."""
    raise NotImplementedError
