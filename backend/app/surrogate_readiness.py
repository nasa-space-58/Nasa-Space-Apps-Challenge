"""Audit independent support before proposing another quantum experiment."""

import argparse
import json
from pathlib import Path


def audit_readiness(report):
    """Count exposed C2 groups/candidates without fitting or inspecting scores.

    C2 has already been analyzed, so it cannot supply an untouched test group.
    Repeated cost limits, solvers and warm calls are not independent samples.
    """
    if report.get("protocol") != "physics-c2" or not report.get("cases"):
        raise ValueError("A nonempty C2 evidence report is required")
    records = {}
    occurrences = 0
    for case in report["cases"]:
        for method in case["methods"].values():
            for candidate, record in method["outcome_records"].items():
                key = (case["seed"], candidate)
                occurrences += 1
                if key in records and records[key] != record:
                    raise ValueError("inconsistent repeated candidate evidence")
                records[key] = record
    groups = sorted({seed for seed, _ in records})
    spaces = {}
    for seed in groups:
        counts = [
            sum(group == seed and key.startswith(f"{side}:") for group, key in records)
            for side in (0, 1)
        ]
        spaces[str(seed)] = dict(
            candidate_counts=counts, full_pair_count=counts[0] * counts[1]
        )
    return dict(
        independent_scenario_groups=len(groups),
        scenario_groups=groups,
        unique_candidate_records=len(records),
        repeated_record_occurrences=occurrences - len(records),
        finite_pair_spaces=spaces,
        blind_holdout_groups=0,
        fit_performed=False,
        physics_calls=0,
        circuit_evaluations=0,
        quantum_experiment_justified=False,
        decision="stop_before_surrogate_training_or_quantum_submission",
        missing=[
            "untouched group holdout",
            "validated input-only surrogate",
            "evidence of selection benefit over full cheap classical enumeration",
        ],
        scope="Readiness audit of exposed synthetic C2 data, not a new performance benchmark",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_readiness(json.loads(args.report.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
