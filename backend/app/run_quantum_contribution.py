"""Frozen contribution protocol and CLI, with separate preparation accounting."""

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from .application_comm_slots import build_comm_slots, generate_comm_slots
from .application_robust import build_robust, generate_robust
from .application_science import build_science, generate_science
from .quantum_contribution import (
    METHODS,
    ContributionBudget,
    contribution_verdict,
    run_contribution_method,
)


def published_protocol():
    """Return fresh instance seeds, fixed budgets and pre-declared stop rules."""
    return dict(
        version="contribution-c1",
        instance_seeds=dict(
            dev=[701, 702], held_out=[711, 712, 713, 714], scaling=[721, 722]
        ),
        solver_seeds=[17, 23, 42],
        methods=list(METHODS),
        applications=["comm_slots", "science_activities", "robust_plans"],
        budget=ContributionBudget().model_dump(mode="json"),
        family_sizes=dict(
            dev=dict(
                comm_slots=dict(
                    missions=2, requests_per_mission=1, stations=2, slots=4
                ),
                science_activities=dict(activities=3, slots=5, precedence_pairs=1),
                robust_plans=dict(runs=24),
            ),
            held_out=dict(
                comm_slots=dict(
                    missions=3, requests_per_mission=1, stations=2, slots=5
                ),
                science_activities=dict(activities=4, slots=6, precedence_pairs=1),
                robust_plans=dict(runs=24),
            ),
            scaling=dict(
                comm_slots=dict(
                    missions=8, requests_per_mission=3, stations=3, slots=12
                ),
                science_activities=dict(activities=8, slots=12, precedence_pairs=2),
                robust_plans=dict(runs=24),
            ),
        ),
        criteria=dict(
            max_mean_check_ratio=0.8,
            max_mean_runtime_ratio=1.0,
            require_all_paired_quality=True,
        ),
        physics_oracle="precomputed_R6_records_not_online_flight_simulation",
        hardware_jobs=0,
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def run_protocol(*, split=None):
    """Run native application builders and paired methods, with no network access."""
    protocol = published_protocol()
    if split is not None and split not in protocol["instance_seeds"]:
        raise ValueError("Unknown experiment split")
    builders = dict(
        comm_slots=(generate_comm_slots, build_comm_slots),
        science_activities=(generate_science, build_science),
        robust_plans=(generate_robust, build_robust),
    )
    records, preparations = [], []
    started = perf_counter()
    for family, seeds in protocol["instance_seeds"].items():
        if split is not None and family != split:
            continue
        for application, (generate, build) in builders.items():
            for instance_seed in seeds:
                mark = perf_counter()
                native = generate(
                    instance_seed, **protocol["family_sizes"][family][application]
                )
                generated_s = perf_counter() - mark
                mark = perf_counter()
                instance = build(native)
                build_s = perf_counter() - mark
                plans = (
                    [p for lander in native.landers for p in lander.plans]
                    if application == "robust_plans"
                    else []
                )
                preparation = dict(
                    application=application,
                    split=family,
                    instance_seed=instance_seed,
                    instance_id=instance.instance_id,
                    input_fingerprint=instance.input_fingerprint,
                    native_inputs=native.model_dump(mode="json"),
                    variables=len(instance.variable_labels),
                    generate_s=generated_s,
                    build_s=build_s,
                    upstream_physics_runs=sum(len(p.success) for p in plans),
                    upstream_physics_batches=len(plans),
                    online_physics_runs=0,
                )
                preparations.append(preparation)
                for seed in protocol["solver_seeds"]:
                    # Rotate method order without changing any method's random stream.
                    offset = (instance_seed + seed) % len(METHODS)
                    order = METHODS[offset:] + METHODS[:offset]
                    methods = [
                        run_contribution_method(
                            instance,
                            method,
                            budget=ContributionBudget(**protocol["budget"]),
                            seed=seed,
                        )
                        for method in order
                    ]
                    records.append(
                        dict(
                            application=application,
                            split=family,
                            instance_id=instance.instance_id,
                            input_fingerprint=instance.input_fingerprint,
                            seed=seed,
                            instance_seed=instance_seed,
                            variables=len(instance.variable_labels),
                            common_preparation_s=generated_s + build_s,
                            methods=methods,
                        )
                    )
    verdict = contribution_verdict(records)
    source_files = (
        "quantum_contribution.py",
        "run_quantum_contribution.py",
        "quantum_applications.py",
        "application_comm_slots.py",
        "application_science.py",
        "application_robust.py",
        "risk.py",
    )
    return dict(
        protocol=protocol,
        protocol_sha256=_digest(protocol),
        source_sha256={
            name: hashlib.sha256(
                Path(__file__).with_name(name).read_bytes()
            ).hexdigest()
            for name in source_files
        },
        preparations=preparations,
        records=records,
        verdict=verdict,
        elapsed_s=perf_counter() - started,
        hardware_jobs=0,
    )


def main():
    """Write JSON evidence to --output; never submit a hardware job."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "held_out", "scaling"))
    args = parser.parse_args()
    report = run_protocol(split=args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["verdict"], indent=2))


if __name__ == "__main__":
    main()
