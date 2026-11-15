"""Independent admission checks beyond stored feasibility flags/checksums."""

import json
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import pytest

import app.unified_classical as classical
from app.unified_planning import AdapterError, UnifiedPlanningEnvelope, seal_inputs

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def envelope():
    raw = json.loads(
        (ROOT / "shared/examples/unified/classical-pilot.json").read_text()
    )
    return UnifiedPlanningEnvelope.model_validate(raw)


def test_rehashed_invalid_allocations_still_fail_independent_validation(envelope):
    snap = seal_inputs(envelope)
    result = classical.plan_classical(snap, current=envelope, now_utc=NOW)
    raw = result.model_dump(mode="python")
    selected = next(
        c for c in raw["candidates"] if c["target_id"] == result.selected_target_id
    )
    selected["plan"]["allocations"] = ()
    forged = type(result).model_validate(raw)
    forged = forged.model_copy(
        update={"decision_sha256": classical._decision_hash(forged)}
    )
    with pytest.raises(AdapterError, match="campaign_revalidation_failed"):
        classical.accept_classical(forged, snap, current=envelope, now_utc=NOW)


def test_sampled_reserve_uses_r11_reserve_as_well_as_h6(envelope, monkeypatch):
    raw = envelope.model_dump(mode="python")
    for v in raw["campaign"]["vehicles"]:
        if v["vehicle_id"] == "demo-lander":
            v["propellant_reserve_kg"] = 20.0
    envelope = UnifiedPlanningEnvelope.model_validate(raw)
    original = classical.prepare_problem

    def samples_above_h6_but_below_r11(job):
        prepared = original(job)
        for row in prepared["physical_candidates"]:
            if row["accepted"]:
                for sample in row["uncertainty"]["outcomes"]:
                    sample["propellant_kg"] = 10.0
        return prepared

    monkeypatch.setattr(classical, "prepare_problem", samples_above_h6_but_below_r11)
    result = classical.plan_classical(
        seal_inputs(envelope), current=envelope, now_utc=NOW
    )
    assert result.status == "no_feasible_continuation"
    assert all("r11_sampled_reserve" in c.reasons for c in result.candidates)


def test_limited_incumbent_is_feasible_without_optimum_claim(envelope, monkeypatch):
    original = classical.solve_joint

    def limited(*args, **kwargs):
        return original(*args, **kwargs).model_copy(update={"status": "limit_reached"})

    monkeypatch.setattr(classical, "solve_joint", limited)
    result = classical.plan_classical(
        seal_inputs(envelope), current=envelope, now_utc=NOW
    )
    assert result.status == "feasible"
    assert not result.optimum_proven_over_screened_targets


def test_allocation_lock_failure_is_not_proved_infeasible(envelope, monkeypatch):
    monkeypatch.setattr(classical, "_preserves", lambda *args: False)
    result = classical.plan_classical(
        seal_inputs(envelope), current=envelope, now_utc=NOW
    )
    assert result.status == "limit_reached"
    assert result.selected_target_id is None
    assert all(
        "completed_allocation_lock_unsupported" in c.reasons for c in result.candidates
    )


def test_branch_comparison_keeps_original_wait_penalty(envelope):
    result = classical.plan_classical(
        seal_inputs(envelope), current=envelope, now_utc=NOW
    )
    mission = next(m for m in envelope.campaign.missions if m.mission_id == "landing")
    for candidate in result.candidates:
        if candidate.status != "feasible":
            continue
        arrival = datetime.fromisoformat(
            candidate.physical["fine"]["state"]["epoch_utc"]
        )
        delta = arrival - mission.landing_window.start_utc
        seconds = Fraction(delta.days * 86400 + delta.seconds) + Fraction(
            delta.microseconds, 1000000
        )
        prefs = envelope.campaign.preferences
        penalty = (
            Fraction(str(prefs.wait_weight))
            * seconds
            / Fraction(str(prefs.wait_scale_s))
        )
        assert (
            Fraction(candidate.comparison_score_exact)
            == Fraction(candidate.validation.exact_score) - penalty
        )
