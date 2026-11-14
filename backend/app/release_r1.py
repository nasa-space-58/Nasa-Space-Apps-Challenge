"""Exports, portable scenario manifests, teaching presets and search ranking."""


def rank_candidates(search: dict) -> dict:
    """Rank known candidates by equal-weight normalized threshold deficits."""
    raise NotImplementedError


def scenario_manifest(jobs: list[dict]) -> dict:
    """Export inputs and expected immutable result fingerprints; never executable code."""
    raise NotImplementedError


def validate_manifest(document: dict) -> dict:
    """Validate a portable manifest and return bounded analysis submissions."""
    raise NotImplementedError


def export_csv(jobs: list[dict]) -> str:
    """Export exact sampled frames, units and provenance as spreadsheet-safe CSV."""
    raise NotImplementedError


def export_report(jobs: list[dict]) -> str:
    """Produce an escaped, standalone printable HTML report."""
    raise NotImplementedError


def classroom_lessons() -> list[dict]:
    """Return source-backed, bounded lessons without permanent-shadow claims."""
    raise NotImplementedError


def register_r1_routes(app, store, searches):
    """Register export, manifest validation, ranking and classroom endpoints."""
    raise NotImplementedError
