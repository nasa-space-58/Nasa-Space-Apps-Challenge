"""Terrain-relative sampled solar disk and Earth-center visibility in UTC."""

from pathlib import Path
from shared.contracts import AnalysisRequest


class VisibilityError(ValueError):
    """Invalid ephemeris, profile or analysis interval."""


def load_ephemeris(data_dir: Path, reference: str):
    """Verify a local Horizons bundle and return its request, rows and provenance."""
    raise NotImplementedError


def analyze_visibility(request: AnalysisRequest, samples: list, profile: dict,
                       selected_utc=None, sensitivity_deg=0.75) -> dict:
    """Produce intervals, estimated metrics, bracketed events and spatial sensitivity."""
    raise NotImplementedError


def refinement_requests(result: dict, request: AnalysisRequest, step_s: int):
    """Build deterministic ingestion envelopes for merged transition brackets."""
    raise NotImplementedError
