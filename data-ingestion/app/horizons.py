"""Validated JPL Horizons downloads and immutable local cache."""
from pathlib import Path
import httpx
from shared.contracts import AnalysisRequest

ENDPOINT = "https://ssd.jpl.nasa.gov/api/horizons.api"
CACHE_VERSION = 1


class HorizonsError(ValueError):
    """Invalid source response or corrupt cached data."""


def build_params(request: AnalysisRequest, target: str) -> dict[str, str]:
    """Build a lunar observer query; reject unsupported target/time precision."""
    raise NotImplementedError


def cache_key(request: AnalysisRequest) -> str:
    """Identify the exact request, endpoint and parser/model version."""
    raise NotImplementedError


def parse_response(payload: dict, request: AnalysisRequest, target: str) -> dict:
    """Validate metadata and samples, preserving unavailable values as null."""
    raise NotImplementedError


def download(request: AnalysisRequest, data_dir: Path, client: httpx.Client) -> dict:
    """Cache both targets atomically; bounded retries apply to transient HTTP failures."""
    raise NotImplementedError
