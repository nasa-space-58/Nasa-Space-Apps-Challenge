"""Reproduce the published R10 benchmark JSON from the frozen compute budget.

docker compose run --rm -v ./docs:/workspace/docs backend \
    python -m app.run_r10_benchmark --output /workspace/docs/verification/<file>.json
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .hybrid_benchmark import (
    BenchmarkConfig,
    BenchmarkReport,
    GoNoGoCriteria,
    NoiseModel,
    run_benchmark,
)
from .hybrid_scaling import InstanceSpec


def _spec(family, missions, slots, antennas, density, tightness, seed, options=4):
    return InstanceSpec(
        family=family,
        missions=missions,
        options_per_mission=options,
        slots=slots,
        antennas=antennas,
        incompatibility_probability=density,
        deadline_tightness=tightness,
        seed=seed,
    )


def published_config() -> BenchmarkConfig:
    """Return the pre-registered dev/held-out/scaling suite and go/no-go rules."""
    # Settings follow H2/H7 (two layers, 160 evaluations, 512 shots, seeds 17,
    # 23 and 42). Dev seeds 1xx and held-out seeds 2xx never overlap.
    dev = (
        _spec("dev", 3, 4, 1, 0.1, 0.2, 101),
        _spec("dev", 3, 6, 2, 0.2, 0.4, 102),
        _spec("dev", 6, 8, 2, 0.1, 0.2, 103),
    )
    held_out = (
        _spec("held-out", 3, 4, 1, 0.1, 0.3, 201),
        _spec("held-out", 3, 3, 1, 0.0, 0.0, 202),
        _spec("held-out", 3, 6, 2, 0.3, 0.5, 203),
        _spec("held-out", 4, 6, 2, 0.15, 0.3, 204),
        _spec("held-out", 8, 10, 3, 0.1, 0.3, 205),
        _spec("held-out", 12, 12, 3, 0.1, 0.3, 206),
    )
    scaling = (
        *(
            _spec("scale-size", m, 2 * m, 4, 0.1, 0.2, 300 + m)
            for m in (2, 4, 8, 16, 32)
        ),
        *(
            _spec("scale-density", 8, 16, 4, p, 0.2, 340 + n)
            for n, p in enumerate((0.0, 0.05, 0.2, 0.4))
        ),
        *(_spec("scale-contention", 8, 16, a, 0.1, 0.2, 360 + a) for a in (1, 2, 4, 8)),
        _spec("scale-options", 8, 16, 4, 0.1, 0.2, 380, options=8),
        _spec("scale-options", 8, 16, 4, 0.1, 0.2, 381, options=3),
    )
    strict = dict(
        min_feasible_run_fraction=1.0,
        max_mean_gap_excess=0.0,
        max_runtime_ratio=1.0,
        require_raw_rate_above_random=True,
    )
    return BenchmarkConfig(
        dev_specs=dev,
        held_out_specs=held_out,
        scaling_specs=scaling,
        qaoa_seeds=(17, 23, 42),
        depth=2,
        evaluations=160,
        shots=512,
        candidate_budget=8,
        max_moves=64,
        annealing_steps=64,
        lns_iterations=8,
        subproblem_missions=3,
        direct_max_qubits=8,
        exact_max_assignments=4096,
        milp_time_limit_s=30.0,
        noise=NoiseModel(),
        criteria=(
            GoNoGoCriteria(
                application="direct_qaoa_selection",
                hybrid_method="qaoa",
                random_method="random",
                **strict,
            ),
            GoNoGoCriteria(
                application="lns_qaoa_subproblem",
                hybrid_method="lns_qaoa",
                random_method="lns_random",
                **strict,
            ),
        ),
    )


def write_report(report: BenchmarkReport, path: Path) -> str:
    """Write sorted, indented JSON and return its SHA-256 hex digest."""
    data = (
        json.dumps(report.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    Path(path).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    """Parse --output (required), run the published config, write and report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = run_benchmark(published_config(), generated_utc=datetime.now(timezone.utc))
    digest = write_report(report, args.output)
    print(f"wrote {args.output} sha256={digest}")
    for decision in report.decisions:
        print(
            f"{decision.application}: {decision.verdict} -> {decision.retained_solver}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
