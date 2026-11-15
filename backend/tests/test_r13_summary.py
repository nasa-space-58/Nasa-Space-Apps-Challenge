"""R13 pre-registered configuration, builders, compact summary and CLI."""

import hashlib
import json

import pytest

from app import run_r13_benchmark as runner
from app.quantum_applications import (
    APPLICATIONS,
    ApplicationSplit,
    BenchmarkConfig,
    SearchConfig,
    config_fingerprint,
    run_benchmark,
)
from app.run_r13_benchmark import (
    FAMILY_SIZES,
    SUMMARY_SCHEMA,
    builders,
    published_config,
    summarize,
)

TINY = BenchmarkConfig(
    applications=(
        ApplicationSplit(
            application="comm_slots", dev_seeds=(101,), held_out_seeds=(201,)
        ),
        ApplicationSplit(
            application="science_activities", dev_seeds=(), held_out_seeds=(201,)
        ),
    ),
    qaoa_seeds=(17,),
    search=SearchConfig(evaluations=8, shots=32, steps=2),
)


def test_published_config_is_the_pre_registration():
    config = published_config()
    assert tuple(a.application for a in config.applications) == APPLICATIONS
    for split in config.applications:
        assert split.dev_seeds == (101, 102)
        assert split.held_out_seeds == (201, 202, 203, 204)
    assert config.qaoa_seeds == (17, 23, 42)
    assert config.search == SearchConfig()
    assert config.exact_max_variables == 16 and config.milp_time_limit_s == 30.0
    assert config_fingerprint(config) == config_fingerprint(published_config())


def test_family_sizes_cover_every_seed():
    config = published_config()
    assert set(FAMILY_SIZES) == set(APPLICATIONS)
    for split in config.applications:
        for seed in split.dev_seeds + split.held_out_seeds:
            assert isinstance(FAMILY_SIZES[split.application][seed], dict)


def test_builders_produce_distinct_bounded_instances():
    table = builders()
    assert set(table) == set(APPLICATIONS)
    config = published_config()
    for split in config.applications:
        if split.application == "robust_plans":
            continue  # R6 flights are exercised in test_application_robust.py.
        prints = set()
        for seed in split.dev_seeds + split.held_out_seeds:
            instance = table[split.application](seed)
            assert instance.application == split.application
            assert 1 <= len(instance.variable_labels) <= 40
            prints.add(instance.input_fingerprint)
        assert len(prints) == len(split.dev_seeds + split.held_out_seeds)


@pytest.fixture(scope="module")
def tiny():
    return run_benchmark(TINY, builders())


def test_summary_structure(tiny):
    summary = summarize(tiny, full_report_sha256="c" * 64)
    json.dumps(summary, allow_nan=False)
    assert summary["schema_version"] == SUMMARY_SCHEMA
    assert summary["benchmark_schema"] == tiny.schema_version
    assert summary["config_fingerprint"] == tiny.config_fingerprint
    assert summary["full_report_sha256"] == "c" * 64
    assert "no quantum advantage" in summary["statement"]
    assert summary["hardware_jobs"] == 0
    assert set(summary["applications"]) == {"comm_slots", "science_activities"}
    for app, block in summary["applications"].items():
        decision = block["decision"]
        assert decision["verdict"] in ("go", "no_go")
        assert block["quantum_enabled"] is (decision["verdict"] == "go")
        assert block["quantum_enabled"] is tiny.policy.quantum_enabled[app]
        assert {c["name"] for c in decision["criteria"]} >= {
            "validated_feasibility",
            "end_to_end_runtime",
        }
        rows = block["instances"]
        assert rows and all(
            {"split", "seed", "variables", "qubo_exact", "optimum_source"} <= set(r)
            for r in rows
        )
        aggregates = block["aggregates"]
        keys = {(a["method"], a["execution_mode"], a["split"]) for a in aggregates}
        assert ("milp", "classical", "held_out") in keys
        assert ("lns_qaoa", "noiseless_simulator", "held_out") in keys
        assert ("lns_qaoa", "noise_model", "held_out") in keys
        for row in aggregates:
            assert row["runs"] >= row["feasible_runs"] >= 0
            assert "mean_metrics" in row and isinstance(row["mean_metrics"], dict)


def test_cli_writes_report_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "published_config", lambda: TINY)
    output = tmp_path / "full.json"
    summary_path = tmp_path / "summary.json"
    assert runner.main(["--output", str(output), "--summary", str(summary_path)]) == 0
    report = json.loads(output.read_text())
    summary = json.loads(summary_path.read_text())
    assert report["schema_version"] == "r13-application-benchmark-v1"
    assert (
        summary["full_report_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    )
    assert summary["config_fingerprint"] == config_fingerprint(TINY)
