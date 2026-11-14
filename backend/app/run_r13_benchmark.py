"""Pre-registered R13 benchmark configuration, instance builders and CLI.

python -m app.run_r13_benchmark --output full.json --summary summary.json
"""

from __future__ import annotations

from .quantum_applications import BenchmarkConfig, BenchmarkReport, Builder

SUMMARY_SCHEMA = "r13-application-summary-v1"
# Pre-registered per-seed family sizes; held-out instances are not tuned on.
FAMILY_SIZES: dict[str, dict[int, dict]] = {}


def published_config() -> BenchmarkConfig:
    """The frozen configuration of docs/quantum-applications.md."""
    raise NotImplementedError


def builders() -> dict[str, Builder]:
    """Seed -> instance builders for every application."""
    raise NotImplementedError


def summarize(report: BenchmarkReport, *, full_report_sha256: str) -> dict:
    """Compact, committed summary of a full report."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
