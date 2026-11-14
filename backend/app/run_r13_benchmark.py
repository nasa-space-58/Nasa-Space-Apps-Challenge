"""Pre-registered R13 benchmark configuration, instance builders and CLI.

python -m app.run_r13_benchmark --output full.json --summary summary.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .application_comm_slots import build_comm_slots, generate_comm_slots
from .application_relays import build_relays, generate_relays
from .application_robust import build_robust, generate_robust
from .application_rover import build_rover, generate_rover
from .application_science import build_science, generate_science
from .quantum_applications import (
    APPLICATIONS,
    ApplicationSplit,
    BenchmarkConfig,
    BenchmarkReport,
    Builder,
    SearchConfig,
    run_benchmark,
)

SUMMARY_SCHEMA = "r13-application-summary-v1"
# Pre-registered per-seed family sizes; held-out instances are not tuned on.
FAMILY_SIZES: dict[str, dict[int, dict]] = {
    "comm_slots": {
        101: {"missions": 2, "requests_per_mission": 2, "stations": 2, "slots": 4},
        102: {"missions": 3, "requests_per_mission": 1, "stations": 2, "slots": 5},
        201: {"missions": 2, "requests_per_mission": 2, "stations": 2, "slots": 4},
        202: {"missions": 3, "requests_per_mission": 2, "stations": 2, "slots": 5},
        203: {
            "missions": 3,
            "requests_per_mission": 2,
            "stations": 2,
            "slots": 6,
            "capacity_links": 2,
        },
        204: {"missions": 4, "requests_per_mission": 2, "stations": 3, "slots": 6},
    },
    "science_activities": {
        101: {"activities": 3, "slots": 5, "precedence_pairs": 1},
        102: {"activities": 4, "slots": 6, "precedence_pairs": 1},
        201: {"activities": 3, "slots": 5, "precedence_pairs": 1},
        202: {"activities": 4, "slots": 6, "precedence_pairs": 1},
        203: {"activities": 4, "slots": 6, "precedence_pairs": 2},
        204: {"activities": 5, "slots": 7, "precedence_pairs": 2},
    },
    "relay_selection": {
        101: {"sites": 3, "candidates": 6, "slots": 8, "select_count": 2},
        102: {"sites": 3, "candidates": 8, "slots": 8, "select_count": 2},
        201: {"sites": 3, "candidates": 6, "slots": 8, "select_count": 2},
        202: {"sites": 3, "candidates": 8, "slots": 8, "select_count": 2},
        203: {"sites": 4, "candidates": 10, "slots": 8, "select_count": 3},
        204: {"sites": 4, "candidates": 12, "slots": 10, "select_count": 3},
    },
    "rover_routes": {
        101: {"targets": 4},
        102: {"targets": 5},
        201: {"targets": 4},
        202: {"targets": 5},
        203: {"targets": 5},
        204: {"targets": 5},
    },
    "robust_plans": {seed: {"runs": 24} for seed in (101, 102, 201, 202, 203, 204)},
}


def published_config() -> BenchmarkConfig:
    """The frozen configuration of docs/quantum-applications.md."""
    return BenchmarkConfig(
        applications=tuple(ApplicationSplit(application=a) for a in APPLICATIONS),
        qaoa_seeds=(17, 23, 42),
        search=SearchConfig(),
        exact_max_variables=16,
        milp_time_limit_s=30.0,
    )


def builders() -> dict[str, Builder]:
    """Seed -> instance builders for every application."""

    def make(generate, build, application):
        def builder(seed: int):
            return build(generate(seed, **FAMILY_SIZES[application][seed]))

        return builder

    return {
        "comm_slots": make(generate_comm_slots, build_comm_slots, "comm_slots"),
        "science_activities": make(
            generate_science, build_science, "science_activities"
        ),
        "relay_selection": make(generate_relays, build_relays, "relay_selection"),
        "rover_routes": make(generate_rover, build_rover, "rover_routes"),
        "robust_plans": make(generate_robust, build_robust, "robust_plans"),
    }


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def summarize(report: BenchmarkReport, *, full_report_sha256: str) -> dict:
    """Compact, committed summary of a full report."""
    decisions = {d.application: d for d in report.decisions}
    applications = {}
    for split in report.config.applications:
        app = split.application
        records = [i for i in report.instances if i.application == app]
        groups = defaultdict(list)
        for record in records:
            for run in record.methods:
                groups[(run.method, run.execution_mode, record.split)].append(run)
        aggregates = []
        for (method, mode, split_name), runs in sorted(groups.items()):
            names = sorted({k for r in runs for k in r.metrics})
            aggregates.append(
                {
                    "method": method,
                    "execution_mode": mode,
                    "split": split_name,
                    "runs": len(runs),
                    "feasible_runs": sum(r.status == "feasible" for r in runs),
                    "optimum_hits": sum(
                        r.optimum_gap_value is not None and r.optimum_gap_value <= 1e-9
                        for r in runs
                    ),
                    "mean_gap": _mean([r.optimum_gap_value for r in runs]),
                    "mean_raw_feasible_rate": _mean(
                        [r.raw_feasible_rate for r in runs]
                    ),
                    "mean_total_s": _mean([r.timing.total_s for r in runs]),
                    "max_qubits": max((r.max_qubits or 0 for r in runs), default=0),
                    "mean_metrics": {
                        k: _mean(
                            [r.metrics.get(k) for r in runs if r.status == "feasible"]
                        )
                        for k in names
                    },
                }
            )
        decision = decisions.get(app)
        applications[app] = {
            "decision": None
            if decision is None
            else {
                "verdict": decision.verdict,
                "retained_solver": decision.retained_solver,
                "criteria": [
                    {
                        "name": c.name,
                        "passed": c.passed,
                        "measured": c.measured,
                        "threshold": c.threshold,
                    }
                    for c in decision.criteria
                ],
            },
            "quantum_enabled": report.policy.quantum_enabled[app],
            "policy_reason": report.policy.reasons[app],
            "instances": [
                {
                    "split": r.split,
                    "seed": r.seed,
                    "instance_id": r.instance_id,
                    "input_fingerprint": r.input_fingerprint,
                    "variables": r.variables,
                    "qubo_exact": r.qubo_exact,
                    "qubo_encoding": r.qubo_encoding,
                    "audit": None
                    if r.audit is None
                    else r.audit.model_dump(mode="json"),
                    "optimum_source": r.optimum_source,
                    "optimum": r.optimum,
                    "optimum_value": r.optimum_value,
                    "rejections": list(r.rejections),
                }
                for r in records
            ],
            "aggregates": aggregates,
        }
    return {
        "schema_version": SUMMARY_SCHEMA,
        "benchmark_schema": report.schema_version,
        "config_fingerprint": report.config_fingerprint,
        "full_report_sha256": full_report_sha256,
        "config": report.config.model_dump(mode="json"),
        "environment": report.environment,
        "statement": report.statement,
        "hardware_jobs": sum(
            m.hardware_jobs for i in report.instances for m in i.methods
        ),
        "applications": applications,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args(argv)
    report = run_benchmark(published_config(), builders())
    data = report.model_dump_json(indent=1).encode("utf-8")
    args.output.write_bytes(data)
    summary = summarize(report, full_report_sha256=hashlib.sha256(data).hexdigest())
    args.summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
