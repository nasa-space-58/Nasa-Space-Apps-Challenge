"""Native QAOA includes zero-count basis states in its probability report."""

from datetime import datetime, timezone
from pathlib import Path

from app.unified_classical import LandingExclusion, apply_landing_exclusion
from app.unified_planning import UnifiedPlanningEnvelope, seal_inputs
from app.unified_quantum import ProposalBudget, compare_proposals


def test_default_budget_accepts_unsampled_basis_records():
    root = Path(__file__).resolve().parents[2]
    nominal = UnifiedPlanningEnvelope.model_validate_json(
        (root / "shared/examples/unified/classical-pilot.json").read_text()
    )
    now = datetime(2030, 1, 1, 0, 0, 8, tzinfo=timezone.utc)
    current = apply_landing_exclusion(
        seal_inputs(nominal),
        LandingExclusion(
            event_id="obstacle-1",
            epoch_utc=now,
            start_downrange_m=-5.0,
            end_downrange_m=5.0,
        ),
    )
    result = compare_proposals(
        seal_inputs(current),
        current=current,
        now_utc=now,
        mode="local_experiment",
        budget=ProposalBudget(),
    )
    assert any(sample.count == 0 for sample in result.quantum.samples)
    q = next(method for method in result.methods if method.method == "qaoa")
    assert sum(q.raw_counts.values()) == 64
    assert all(count > 0 for count in q.raw_counts.values())
    assert result.final_decision.status == "feasible"
