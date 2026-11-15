"""Untrusted sample identities, admission provenance and frozen cancellations."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.unified_quantum as hybrid
from app.unified_classical import LandingExclusion, apply_landing_exclusion
from app.unified_planning import AdapterError, UnifiedPlanningEnvelope, seal_inputs

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=8)


@pytest.fixture
def envelope():
    nominal = UnifiedPlanningEnvelope.model_validate_json(
        (ROOT / "shared/examples/unified/classical-pilot.json").read_text()
    )
    return apply_landing_exclusion(
        seal_inputs(nominal),
        LandingExclusion(
            event_id="obstacle",
            epoch_utc=NOW,
            start_downrange_m=-5.0,
            end_downrange_m=5.0,
        ),
    )


def compare(envelope):
    return hybrid.compare_proposals(
        seal_inputs(envelope),
        current=envelope,
        now_utc=NOW,
        mode="local_experiment",
        budget=hybrid.ProposalBudget(evaluations=12, shots=32, max_completions=2),
    )


def test_unknown_quantum_ids_are_not_repaired(envelope, monkeypatch):
    original = hybrid.run_qaoa

    def malformed(*args, **kwargs):
        q = original(*args, **kwargs)
        return q.model_copy(
            update={
                "samples": tuple(
                    s.model_copy(update={"selected_option_ids": ("missing",)})
                    for s in q.samples
                )
            }
        )

    monkeypatch.setattr(hybrid, "run_qaoa", malformed)
    result = compare(envelope)
    q = next(m for m in result.methods if m.method == "qaoa")
    assert q.raw_local_feasible_count == 0
    assert all(a.reason == "unknown_proposal" for a in q.attempts)
    assert result.selected_source == "classical"


def test_bad_sample_counts_fail_without_retry(envelope, monkeypatch):
    original = hybrid.run_qaoa
    calls = []

    def malformed(*args, **kwargs):
        calls.append(1)
        q = original(*args, **kwargs)
        return q.model_copy(
            update={
                "samples": tuple(s.model_copy(update={"count": 0}) for s in q.samples)
            }
        )

    monkeypatch.setattr(hybrid, "run_qaoa", malformed)
    with pytest.raises(ValueError, match="counts"):
        compare(envelope)
    assert calls == [1]


def test_frozen_cancelled_outside_mission_stays_cancelled(envelope):
    raw = envelope.model_dump(mode="python")
    for m in raw["campaign"]["missions"]:
        if m["mission_id"] == "other":
            m["policy"]["cancel_allowed"] = True
            m["requirements"]["downlink_bits"] = 1e12
    env = UnifiedPlanningEnvelope.model_validate(raw)
    result = compare(env)
    assert result.frozen_outside_selections["other"] is None
    assert any(a.status == "accepted" for m in result.methods for a in m.attempts)
    for m in result.methods:
        for a in m.attempts:
            if a.status == "accepted":
                assert a.candidate.plan.selections["other"] is None


def test_equal_plan_cannot_be_reattributed_to_quantum_improvement(envelope):
    result = compare(envelope)
    q = next(m for m in result.methods if m.method == "qaoa")
    assert q.best_decision is not None
    forged = result.model_copy(
        update={
            "final_decision": q.best_decision,
            "selected_source": "qaoa_proposal_classical_completion",
            "selection_reason": "strict_validated_full_plan_improvement",
        }
    )
    forged = forged.model_copy(update={"result_sha256": hybrid._result_hash(forged)})
    with pytest.raises(AdapterError, match="no_strict_improvement"):
        hybrid.accept_hybrid(
            forged, seal_inputs(envelope), current=envelope, now_utc=NOW
        )


def test_rehashed_scores_cannot_fake_full_plan_improvement(envelope):
    from app.unified_classical import _decision_hash

    result = compare(envelope)
    q = next(m for m in result.methods if m.method == "qaoa")
    decision = q.best_decision
    changed = decision.model_copy(
        update={
            "candidates": tuple(
                c.model_copy(update={"comparison_score_exact": "999999999"})
                for c in decision.candidates
            )
        }
    )
    changed = changed.model_copy(update={"decision_sha256": _decision_hash(changed)})
    methods = tuple(
        m.model_copy(update={"best_decision": changed}) if m.method == "qaoa" else m
        for m in result.methods
    )
    forged = result.model_copy(
        update={
            "methods": methods,
            "final_decision": changed,
            "selected_source": "qaoa_proposal_classical_completion",
            "selection_reason": "strict_validated_full_plan_improvement",
        }
    )
    forged = forged.model_copy(update={"result_sha256": hybrid._result_hash(forged)})
    with pytest.raises(AdapterError, match="no_strict_improvement"):
        hybrid.accept_hybrid(
            forged, seal_inputs(envelope), current=envelope, now_utc=NOW
        )
