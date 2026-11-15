"""Local proposals are untrusted until full R11 and physical admission passes."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.unified_quantum as hybrid
from app.campaign_planning import SolveOutcome, validate_campaign_plan
from app.unified_classical import LandingExclusion, apply_landing_exclusion
from app.unified_planning import AdapterError, UnifiedPlanningEnvelope, seal_inputs

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
EVENT_TIME = NOW + timedelta(seconds=8)


@pytest.fixture(scope="module")
def nominal():
    return UnifiedPlanningEnvelope.model_validate_json(
        (ROOT / "shared/examples/unified/classical-pilot.json").read_text()
    )


@pytest.fixture(scope="module")
def envelope(nominal):
    return apply_landing_exclusion(
        seal_inputs(nominal),
        LandingExclusion(
            event_id="obstacle-1",
            epoch_utc=EVENT_TIME,
            start_downrange_m=-5.0,
            end_downrange_m=5.0,
        ),
    )


@pytest.fixture(scope="module")
def budget():
    return hybrid.ProposalBudget(evaluations=12, shots=32, max_completions=2)


@pytest.fixture(scope="module")
def experiment(envelope, budget):
    return hybrid.compare_proposals(
        seal_inputs(envelope),
        current=envelope,
        now_utc=EVENT_TIME,
        mode="local_experiment",
        budget=budget,
    )


def test_default_is_classical_and_never_samples(nominal, monkeypatch):
    monkeypatch.setattr(
        hybrid,
        "run_qaoa",
        lambda *a, **k: pytest.fail("production must stay classical"),
    )
    result = hybrid.compare_proposals(
        seal_inputs(nominal), current=nominal, now_utc=NOW
    )
    assert result.mode == "classical"
    assert result.quantum is None
    assert result.methods == ()
    assert result.selected_source == "classical"
    assert result.hardware_jobs == 0
    assert result.production_policy == "classical_no_go_unchanged"


def test_supported_quantum_path_runs_only_local_one_qubit(experiment):
    assert experiment.quantum is not None
    assert experiment.quantum.logical_qubits == 1
    assert experiment.quantum.execution_mode == "noiseless_simulator"
    assert experiment.quantum.hardware_jobs == 0
    assert experiment.unsupported_reason is None
    assert set(experiment.option_to_target.values()) == {"backup", "remote"}
    assert experiment.selected_source == "classical"
    assert experiment.selection_reason == "no_strict_full_plan_improvement"


def test_matched_methods_have_same_caps_and_full_checks(experiment):
    assert {m.method for m in experiment.methods} == {"qaoa", "random", "greedy"}
    for method in experiment.methods:
        assert sum(method.raw_counts.values()) == 32
        assert 0 <= method.raw_local_feasible_count <= 32
        assert len(method.attempts) <= 2
        assert sum(a.sample_count for a in method.attempts) <= 32
        assert method.proposal_s >= 0
        for attempt in method.attempts:
            assert attempt.completion_s >= 0 and attempt.admission_s >= 0
            if attempt.status == "accepted":
                c = attempt.candidate
                assert c is not None
                assert set(c.plan.selections) == {"landing", "other", "rover"}
                assert validate_campaign_plan(c.plan, c.problem).status == "feasible"
                assert c.physical["accepted"]
                assert {
                    mid: c.plan.selections[mid]
                    for mid in experiment.frozen_outside_selections
                } == experiment.frozen_outside_selections
    assert any(
        a.status == "accepted"
        for m in experiment.methods
        if m.method == "qaoa"
        for a in m.attempts
    )


def test_final_plan_repeats_admission(experiment, envelope, monkeypatch):
    calls = []
    original = hybrid.accept_classical

    def observe(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(hybrid, "accept_classical", observe)
    chosen = hybrid.accept_hybrid(
        experiment, seal_inputs(envelope), current=envelope, now_utc=EVENT_TIME
    )
    assert chosen.validation.status == "feasible"
    assert calls


def test_no_silent_padding_or_option_deletion(nominal, budget, monkeypatch):
    monkeypatch.setattr(
        hybrid,
        "run_qaoa",
        lambda *a, **k: pytest.fail("unsupported mapping must not sample"),
    )
    result = hybrid.compare_proposals(
        seal_inputs(nominal),
        current=nominal,
        now_utc=NOW,
        mode="local_experiment",
        budget=budget,
    )
    assert result.quantum is None
    assert result.unsupported_reason
    assert result.final_decision.status == "feasible"
    assert result.selected_source == "classical"


def test_completion_cap_and_unexamined_samples(envelope, budget):
    limited = budget.model_copy(update={"max_completions": 1})
    result = hybrid.compare_proposals(
        seal_inputs(envelope),
        current=envelope,
        now_utc=EVENT_TIME,
        mode="local_experiment",
        budget=limited,
    )
    assert all(len(m.attempts) <= 1 for m in result.methods)
    for method in result.methods:
        assert len(method.attempts) + len(method.skipped_by_completion_cap) == len(
            method.raw_counts
        )


def test_full_completion_failure_does_not_inherit_local_feasibility(
    envelope, budget, monkeypatch
):
    monkeypatch.setattr(
        hybrid,
        "solve_joint",
        lambda *a, **k: SolveOutcome(
            status="infeasible",
            plan=None,
            validation=None,
            variables=1,
            constraints=1,
            elapsed_s=0.1,
        ),
    )
    result = hybrid.compare_proposals(
        seal_inputs(envelope),
        current=envelope,
        now_utc=EVENT_TIME,
        mode="local_experiment",
        budget=budget,
    )
    assert result.quantum.sampled_feasible_rate > 0
    assert all(a.status != "accepted" for m in result.methods for a in m.attempts)
    assert result.final_decision == result.classical_baseline
    assert result.selected_source == "classical"


def test_expired_and_stale_results_reject(envelope, experiment):
    with pytest.raises(AdapterError, match="outside_decision_window"):
        hybrid.accept_hybrid(
            experiment,
            seal_inputs(envelope),
            current=envelope,
            now_utc=NOW + timedelta(seconds=10),
        )
    raw = envelope.model_dump(mode="python")
    raw["campaign"]["revision"] += 1
    current = UnifiedPlanningEnvelope.model_validate(raw)
    with pytest.raises(AdapterError, match="stale_inputs"):
        hybrid.accept_hybrid(
            experiment, seal_inputs(envelope), current=current, now_utc=EVENT_TIME
        )


def test_tampered_result_rejects(envelope, experiment):
    bad = experiment.model_copy(
        update={"selected_source": "qaoa_proposal_classical_completion"}
    )
    with pytest.raises(AdapterError, match="experiment_tampered"):
        hybrid.accept_hybrid(
            bad, seal_inputs(envelope), current=envelope, now_utc=EVENT_TIME
        )


def test_same_seed_same_raw_counts(envelope, budget, experiment):
    again = hybrid.compare_proposals(
        seal_inputs(envelope),
        current=envelope,
        now_utc=EVENT_TIME,
        mode="local_experiment",
        budget=budget,
    )
    assert [(m.method, m.raw_counts) for m in experiment.methods] == [
        (m.method, m.raw_counts) for m in again.methods
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("shots", 0),
        ("shots", 1025),
        ("shots", True),
        ("evaluations", 0),
        ("evaluations", 129),
        ("max_qubits", 0),
        ("max_qubits", 7),
        ("depth", 4),
        ("seed", -1),
        ("max_completions", 0),
        ("max_completions", 9),
        ("completion_limit_s", float("nan")),
        ("completion_limit_s", 31.0),
    ],
)
def test_budget_rejects_invalid_values(field, value):
    with pytest.raises(ValidationError):
        hybrid.ProposalBudget(**{field: value})


def test_invalid_mode_and_mutated_budget_reject_before_work(
    envelope, budget, monkeypatch
):
    monkeypatch.setattr(
        hybrid,
        "plan_classical",
        lambda *a, **k: pytest.fail("invalid request must fail before solving"),
    )
    with pytest.raises(ValueError):
        hybrid.compare_proposals(
            seal_inputs(envelope), current=envelope, now_utc=EVENT_TIME, mode="hardware"
        )
    with pytest.raises(ValueError):
        hybrid.compare_proposals(
            seal_inputs(envelope),
            current=envelope,
            now_utc=EVENT_TIME,
            budget=budget.model_copy(update={"shots": 0}),
        )


def test_all_targets_blocked_no_quantum_submission(nominal, budget, monkeypatch):
    blocked = apply_landing_exclusion(
        seal_inputs(nominal),
        LandingExclusion(
            event_id="all",
            epoch_utc=EVENT_TIME,
            start_downrange_m=-50000.0,
            end_downrange_m=50000.0,
        ),
    )
    monkeypatch.setattr(
        hybrid,
        "run_qaoa",
        lambda *a, **k: pytest.fail("no continuation must not sample"),
    )
    result = hybrid.compare_proposals(
        seal_inputs(blocked),
        current=blocked,
        now_utc=EVENT_TIME,
        mode="local_experiment",
        budget=budget,
    )
    assert result.final_decision.status == "no_feasible_continuation"
    assert result.quantum is None
    with pytest.raises(AdapterError, match="no_accepted_continuation"):
        hybrid.accept_hybrid(
            result, seal_inputs(blocked), current=blocked, now_utc=EVENT_TIME
        )
