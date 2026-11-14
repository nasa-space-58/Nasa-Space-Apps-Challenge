"""Reproduce the published R10 benchmark JSON from the frozen compute budget.

docker compose run --rm -v ./docs:/workspace/docs backend \
    python -m app.run_r10_benchmark --output /workspace/docs/verification/<file>.json
"""

from pathlib import Path

from .hybrid_benchmark import BenchmarkConfig, BenchmarkReport


def published_config() -> BenchmarkConfig:
    """Return the pre-registered dev/held-out/scaling suite and go/no-go rules."""
    raise NotImplementedError


def write_report(report: BenchmarkReport, path: Path) -> str:
    """Write sorted, indented JSON and return its SHA-256 hex digest."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    """Parse --output (required), run the published config, write and report."""
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
