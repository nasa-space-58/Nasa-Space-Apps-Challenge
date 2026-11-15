"""Compact R10 publication summary: content kept, per-run detail removed."""

import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.hybrid_benchmark import (
    NO_ADVANTAGE_STATEMENT,
    BenchmarkConfig,
    GoNoGoCriteria,
    run_benchmark,
)
from app.hybrid_scaling import InstanceSpec
from app.run_r10_benchmark import main, summarize_report, write_report

UTC_NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def spec(family, missions, seed):
    return InstanceSpec(
        family=family,
        missions=missions,
        options_per_mission=2,
        slots=3,
        antennas=2,
        incompatibility_probability=0.1,
        deadline_tightness=0.2,
        seed=seed,
    )


@pytest.fixture(scope="module")
def report():
    config = BenchmarkConfig(
        dev_specs=(spec("dev", 2, 1),),
        held_out_specs=(spec("held", 3, 2),),
        scaling_specs=(spec("scale", 4, 3),),
        qaoa_seeds=(17,),
        evaluations=8,
        shots=32,
        candidate_budget=2,
        max_moves=16,
        annealing_steps=8,
        lns_iterations=2,
        subproblem_missions=2,
        criteria=(
            GoNoGoCriteria(
                application="direct_qaoa_selection",
                hybrid_method="qaoa",
                random_method="random",
            ),
        ),
    )
    return run_benchmark(config, generated_utc=UTC_NOW)


DIGEST = "a" * 64


def test_summary_keeps_identity_aggregates_and_decisions(report):
    summary = summarize_report(report, DIGEST)
    assert summary["schema_id"] == "r10-benchmark-v1"
    assert summary["full_report_sha256"] == DIGEST
    assert summary["generated_utc"] == "2026-11-15T12:00:00Z"
    assert summary["config_fingerprint"] == report.config_fingerprint
    assert summary["config"] == report.config.model_dump(mode="json")
    assert summary["environment"] == report.environment
    assert summary["hardware_jobs"] == 0
    assert summary["statement"] == NO_ADVANTAGE_STATEMENT
    assert summary["aggregates"] == [
        a.model_dump(mode="json") for a in report.aggregates
    ]
    assert summary["decisions"] == [d.model_dump(mode="json") for d in report.decisions]
    assert summary["scaling"] == [s.model_dump(mode="json") for s in report.scaling]


def test_summary_instances_drop_per_run_detail(report):
    summary = summarize_report(report, DIGEST)
    assert len(summary["instances"]) == len(report.instances)
    for compact, full in zip(summary["instances"], report.instances):
        assert compact["split"] == full.split
        assert compact["spec"] == full.spec.model_dump(mode="json")
        assert compact["scenario_id"] == full.scenario_id
        assert compact["input_fingerprint"] == full.input_fingerprint
        assert compact["profile"] == full.profile.model_dump(mode="json")
        assert compact["optimum_exact_score"] == full.optimum_exact_score
        assert compact["optimum_source"] == full.optimum_source
        assert compact["exact_status"] == full.exact_status
        assert compact["milp_status"] == full.milp.status
        assert compact["milp_elapsed_s"] == full.milp.elapsed_s
        assert compact["method_runs"] == len(full.methods)
        assert "methods" not in compact and "milp" not in compact
    text = json.dumps(summary, sort_keys=True)
    assert len(text) < len(report.model_dump_json())
    assert "outcomes" not in text


@pytest.mark.parametrize("digest", ["", "A" * 64, "a" * 63, "g" * 64, 123])
def test_summary_rejects_invalid_digest(report, digest):
    with pytest.raises(ValueError):
        summarize_report(report, digest)


def test_main_requires_output():
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2


def test_write_report_digest_is_what_the_summary_cites(report, tmp_path):
    path = tmp_path / "full.json"
    digest = write_report(report, path)
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert summarize_report(report, digest)["full_report_sha256"] == digest
