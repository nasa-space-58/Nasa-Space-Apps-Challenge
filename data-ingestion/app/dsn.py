"""Moon ephemerides seen from DSN stations: JPL Horizons download and cache.

Query conventions: https://ssd.jpl.nasa.gov/horizons/manual.html. Observer is a
WGS84 geodetic site on Earth (coord@399); target is the Moon (301). Airless
apparent azimuth/elevation only; no horizon masks or link budget are applied.
"""

from pathlib import Path

import httpx
from shared.dsn import DsnEvent, DsnRequest, DsnStation

CACHE_VERSION = 1
PARSER_VERSION = 1


class DsnSourceError(ValueError):
    """Invalid Horizons response or corrupt cached DSN data."""


class DsnRequestConflict(ValueError):
    """A DSN request UUID was reused with different parameters."""


def build_station_params(request: DsnRequest, station: DsnStation) -> dict[str, str]:
    """Build the Horizons OBSERVER query for the Moon from one station."""
    raise NotImplementedError


def dsn_cache_key(request: DsnRequest) -> str:
    """Hash every result-affecting parameter; the request UUID is excluded."""
    raise NotImplementedError


def parse_station_response(
    payload: dict, request: DsnRequest, station: DsnStation
) -> list[dict]:
    """Validate metadata and the exact UTC grid; return Moon az/el samples."""
    raise NotImplementedError


def download_dsn(request: DsnRequest, data_dir: Path, client: httpx.Client) -> dict:
    """Download all stations into an atomic checksummed dsn/<key> bundle."""
    raise NotImplementedError


def process_dsn_job(
    request: DsnRequest, data_dir: Path, client: httpx.Client | None = None
) -> DsnEvent:
    """Idempotent job: replay stored results and reject reused UUIDs."""
    raise NotImplementedError


async def handle_dsn_message(message, exchange, data_dir: Path) -> None:
    """Dead-letter invalid messages; publish the event before acknowledging."""
    raise NotImplementedError
