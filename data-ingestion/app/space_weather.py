"""NOAA SWPC space weather snapshot: download, validate and cache.

Products and field meanings: https://services.swpc.noaa.gov/ and
https://www.swpc.noaa.gov/products-and-data. Every timestamp in the stored
snapshot is explicit UTC. Probabilities are percent; X-ray flux is W/m² in the
GOES 0.1-0.8 nm channel; proton flux is pfu (cm^-2 s^-1 sr^-1) at >=10 MeV.
"""

from pathlib import Path

import httpx
from shared.space_weather import SpaceWeatherEvent, SpaceWeatherRequest

SNAPSHOT_VERSION = 1
MAX_MESSAGE_BYTES = 65536
MAX_PRODUCT_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT_S = 60.0
XRAY_CHANNEL = "0.1-0.8nm"
PROTON_CHANNEL = ">=10 MeV"
FORECAST_DAYS = 3


class SpaceWeatherSourceError(ValueError):
    """Invalid SWPC response or corrupt cached snapshot."""


class SpaceWeatherRequestConflict(ValueError):
    """A request UUID was reused with different parameters."""


def product_url(name: str) -> str:
    """Absolute SWPC URL of one named product; KeyError for unknown names."""
    raise NotImplementedError


def parse_probabilities(payload) -> dict:
    """Return the newest 3-day flare/proton forecast in explicit UTC days.

    The entry ``date`` is the first forecast UTC day; ``*_1_day`` covers that
    day, ``*_2_day`` and ``*_3_day`` the next two. Result::

        {"forecast_start_utc", "polar_cap_absorption",
         "days": [{"start_utc", "end_utc", "c_class_percent", "m_class_percent",
                   "x_class_percent", "proton_10mev_percent"}, ...]}
    """
    raise NotImplementedError


def parse_xrays(payload) -> list[dict]:
    """Return [{"time_utc", "flux_w_m2"}] for the 0.1-0.8 nm channel, in time order.

    A null flux stays None (data gap). Duplicate or decreasing times, negative or
    non-finite flux, or a channel without any measured value raise.
    """
    raise NotImplementedError


def parse_protons(payload) -> list[dict]:
    """Return [{"time_utc", "flux_pfu"}] for the >=10 MeV channel, in time order.

    Same validation rules as parse_xrays.
    """
    raise NotImplementedError


def parse_regions(payload) -> dict:
    """Return the newest daily active-region report.

    Result: {"observed_date_utc", "regions": [{"region", "location", "spot_class",
    "area_msh", "c_flare_percent", "m_flare_percent", "x_flare_percent",
    "proton_percent"}]} sorted by region number. Missing probabilities stay None.
    """
    raise NotImplementedError


def build_snapshot(raw: dict[str, bytes], acquired_at_utc: str) -> dict:
    """Decode and parse all products into one normalized snapshot document."""
    raise NotImplementedError


def snapshot_key(raw: dict[str, bytes]) -> str:
    """SHA-256 over product names, raw content digests and the snapshot version."""
    raise NotImplementedError


def download_snapshot(data_dir: Path, client: httpx.Client) -> dict:
    """Fetch every product into an atomic checksummed space-weather/<key> bundle.

    Identical content reuses the verified existing bundle (cache_hit True); a
    corrupt existing bundle raises and is never replaced. HTTP errors propagate.
    Returns {"cache_key", "path", "cache_hit"}.
    """
    raise NotImplementedError


def process_space_weather_job(
    request: SpaceWeatherRequest, data_dir: Path, client: httpx.Client | None = None
) -> SpaceWeatherEvent:
    """Idempotent job: replay the stored outcome and reject reused UUIDs."""
    raise NotImplementedError


async def handle_space_weather_message(message, exchange, data_dir: Path) -> None:
    """Dead-letter invalid messages; publish the event before acknowledging."""
    raise NotImplementedError
