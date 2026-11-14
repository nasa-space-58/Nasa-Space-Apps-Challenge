"""Read-only H10/H11 evidence and bounded local relay comparison; no cloud jobs."""

from pathlib import Path

REPORT_ROOT = Path(__file__).resolve().parents[2] / "shared/examples"


def read_report(name, root=None):
    """Read a fixed checksummed report; never accept arbitrary paths from HTTP."""
    raise NotImplementedError


def scaling_view(report):
    """Prepare log-scaled chart coordinates in Python; preserve raw measurements."""
    raise NotImplementedError


def register_research_routes(app):
    """Attach fixed read-only evidence and bounded H8-backed local comparison."""
    raise NotImplementedError
