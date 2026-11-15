"""Stage 2 contracts: preserve native semantics; reject unsafe identity bridges."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.campaign_planning import solve_joint, validate_campaign_plan
from app.hybrid_recovery import HybridRecoveryRequest, prepare_problem
from app.unified_planning import (
    AdapterError,
    PlanningSnapshot,
    UnifiedPlanningEnvelope,
    map_h6_proposal,
    seal_inputs,
    to_h6_request,
    to_r11_problem,
)
from shared.campaign import CampaignProblem, campaign_fingerprint
from shared.hybrid import fingerprint
from shared.unified_planning import ModuleEvidence, module_catalog

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def raw():
    return json.loads((ROOT / "shared/examples/unified/r11-h6.json").read_text())


@pytest.fixture
def envelope(raw):
    return UnifiedPlanningEnvelope.model_validate(raw)


def test_native_json_roundtrip_is_lossless(envelope):
    snapshot = seal_inputs(envelope)
    restored = PlanningSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored == snapshot
    r11 = to_r11_problem(restored, current=envelope, now_utc=NOW)
    h6 = to_h6_request(restored, current=envelope, now_utc=NOW)
    assert r11.model_dump(mode="json") == envelope.campaign.model_dump(mode="json")
    assert h6.model_dump(mode="json") == envelope.recovery.model_dump(mode="json")
    assert snapshot.campaign_sha256 == campaign_fingerprint(r11)
    assert fingerprint(h6.campaign) == fingerprint(envelope.recovery.campaign)
    assert r11.slot_s == 60
    assert h6.campaign.candidates[0].window.end_utc.second != 0
    assert (
        h6.checkpoint.estimated_state.reference_frame
        == "PLANAR_MOON_CENTERED_NONROTATING"
    )
    assert r11.sites[0].site.reference_frame == "MOON_ME"


def test_adapter_returns_detached_models(envelope):
    snapshot = seal_inputs(envelope)
    out = to_r11_problem(snapshot, current=envelope, now_utc=NOW)
    out.missions[0].science_score_by_site.clear()
    assert envelope.campaign.missions[0].science_score_by_site
    assert snapshot.envelope.campaign.missions[0].science_score_by_site


def test_native_h6_preparation_still_accepts_extracted_input(envelope):
    job = to_h6_request(seal_inputs(envelope), current=envelope, now_utc=NOW)
    prepared = prepare_problem(job)
    assert len(prepared["physical_candidates"]) == 3
    assert set(prepared["option_to_target"]) == {
        "land-primary",
        "land-backup",
        "land-remote",
    }


def test_mapped_selection_is_not_a_validated_campaign(envelope):
    snap = seal_inputs(envelope)
    proposal = map_h6_proposal(snap, ("land-backup",), current=envelope, now_utc=NOW)
    assert proposal.selections == {"landing": "landing.land-backup.20300101T000030Z"}
    assert proposal.snapshot_sha256 == snap.snapshot_sha256
    assert proposal.validation_status == "unvalidated"
    assert proposal.requires_classical_completion
    assert proposal.requires_physical_revalidation
    assert "allocations" not in proposal.model_dump()


def test_r11_validation_retains_data_allocation_rules(envelope):
    problem = to_r11_problem(seal_inputs(envelope), current=envelope, now_utc=NOW)
    result = solve_joint(problem)
    assert result.status == "optimal"
    assert result.plan is not None
    assert validate_campaign_plan(result.plan, problem).status == "feasible"
    broken = result.plan.model_copy(update={"allocations": ()})
    assert validate_campaign_plan(broken, problem).status == "infeasible"


@pytest.mark.parametrize(
    "selected",
    [(), ("missing",), ("land-primary", "land-backup"), ("land-backup", "land-backup")],
)
def test_malformed_selections_rejected(envelope, selected):
    with pytest.raises(AdapterError, match="invalid_selection"):
        map_h6_proposal(seal_inputs(envelope), selected, current=envelope, now_utc=NOW)


@pytest.mark.parametrize("adapter", [to_h6_request, to_r11_problem])
@pytest.mark.parametrize("offset", [-1, 10, 11])
def test_deadline_is_half_open(envelope, adapter, offset):
    with pytest.raises(AdapterError, match="outside_decision_window"):
        adapter(
            seal_inputs(envelope),
            current=envelope,
            now_utc=NOW + timedelta(seconds=offset),
        )


def test_deadline_last_microsecond_allowed(envelope):
    snap = seal_inputs(envelope)
    assert to_r11_problem(
        snap, current=envelope, now_utc=NOW + timedelta(seconds=10, microseconds=-1)
    )


@pytest.mark.parametrize(
    "moment",
    [datetime(2030, 1, 1), datetime(2030, 1, 1, tzinfo=timezone(timedelta(hours=1)))],
)
def test_now_requires_explicit_utc(envelope, moment):
    with pytest.raises(AdapterError, match="invalid_time"):
        to_h6_request(seal_inputs(envelope), current=envelope, now_utc=moment)


@pytest.mark.parametrize(
    "field", ["campaign_sha256", "recovery_sha256", "snapshot_sha256"]
)
def test_tampered_seal_rejected(envelope, field):
    snap = seal_inputs(envelope).model_copy(update={field: "0" * 64})
    with pytest.raises(AdapterError, match="snapshot_tampered"):
        to_r11_problem(snap, current=envelope, now_utc=NOW)


@pytest.mark.parametrize(
    "change",
    [
        "campaign_revision",
        "state_revision",
        "source",
        "rate",
        "h0_metric",
        "deadline",
        "module",
    ],
)
def test_current_input_changes_invalidate_snapshot(raw, change):
    original = UnifiedPlanningEnvelope.model_validate(raw)
    snap = seal_inputs(original)
    if change == "campaign_revision":
        raw["campaign"]["revision"] += 1
    elif change == "state_revision":
        raw["recovery"]["checkpoint"]["estimated_state"]["revision"] += 1
    elif change == "source":
        raw["context"]["sources"][0]["description"] += " changed"
    elif change == "rate":
        raw["campaign"]["link_rates"][0]["rate_bps"] = 11.0
    elif change == "h0_metric":
        raw["recovery"]["campaign"]["candidates"][0]["metrics"]["utility"] = 199.0
    elif change == "deadline":
        raw["context"]["decision_deadline_utc"] = "2030-01-01T00:00:09Z"
    else:
        raw["module_evidence"] = [
            {
                "module_id": "H11",
                "contract_name": "orbital-relay-v1",
                "revision": 0,
                "sha256": "a" * 64,
                "source_ids": ["synthetic-registration"],
            }
        ]
    current = UnifiedPlanningEnvelope.model_validate(raw)
    with pytest.raises(AdapterError, match="stale_inputs"):
        to_r11_problem(snap, current=current, now_utc=NOW)
    with pytest.raises(AdapterError, match="stale_inputs"):
        map_h6_proposal(snap, ("land-primary",), current=current, now_utc=NOW)


def test_nested_mutation_does_not_bypass_seal(envelope):
    snap = seal_inputs(envelope)
    snap.envelope.recovery.campaign.candidates[0].metrics["utility"] = 123.0
    with pytest.raises(AdapterError, match="snapshot_tampered"):
        to_h6_request(snap, current=envelope, now_utc=NOW)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate_h6",
        "duplicate_r11",
        "unknown_h6",
        "unknown_r11",
        "source",
        "site",
        "vehicle",
        "time",
        "rate",
        "frame",
        "truth",
        "naive",
        "expiry",
        "checkpoint_time",
    ],
)
def test_invalid_connections_fail_closed(raw, change):
    if change == "missing":
        raw["option_links"].pop()
    elif change == "duplicate_h6":
        raw["option_links"].append(deepcopy(raw["option_links"][0]))
    elif change == "duplicate_r11":
        raw["option_links"][1]["r11_option_id"] = raw["option_links"][0][
            "r11_option_id"
        ]
    elif change == "unknown_h6":
        raw["option_links"][0]["h6_option_id"] = "missing"
    elif change == "unknown_r11":
        raw["option_links"][0]["r11_option_id"] = "missing"
    elif change == "source":
        raw["option_links"][0]["source_id"] = "missing"
    elif change == "site":
        raw["recovery"]["campaign"]["candidates"][0]["site"]["longitude_deg_east"] = (
            99.0
        )
    elif change == "vehicle":
        raw["campaign"]["missions"][0]["vehicle_id"] = "different"
        raw["campaign"]["vehicles"][0]["vehicle_id"] = "different"
        for site in raw["campaign"]["sites"]:
            site["descents"][0]["vehicle_id"] = "different"
    elif change == "time":
        raw["recovery"]["campaign"]["candidates"][0]["window"]["start_utc"] = (
            "2030-01-01T00:00:31Z"
        )
        raw["recovery"]["campaign"]["candidates"][0]["demands"][0]["window"][
            "start_utc"
        ] = "2030-01-01T00:00:31Z"
    elif change == "rate":
        raw["campaign"]["link_rates"] = []
    elif change == "frame":
        raw["context"]["correspondence"] = "automatic_frame_conversion"
    elif change == "truth":
        raw["context"]["state_kind"] = "ground_truth"
    elif change == "naive":
        raw["context"]["created_utc"] = "2030-01-01T00:00:00"
    elif change == "expiry":
        raw["context"]["decision_deadline_utc"] = raw["context"]["created_utc"]
    else:
        raw["context"]["created_utc"] = "2029-12-31T23:59:59Z"
    with pytest.raises(ValueError):
        UnifiedPlanningEnvelope.model_validate(raw)


def test_set_order_does_not_change_seal(raw):
    first = seal_inputs(UnifiedPlanningEnvelope.model_validate(raw))
    raw["option_links"].reverse()
    raw["campaign"]["sites"].reverse()
    assert (
        seal_inputs(UnifiedPlanningEnvelope.model_validate(raw)).snapshot_sha256
        == first.snapshot_sha256
    )


def test_module_registry_covers_all_h_without_claiming_all_connected():
    rows = {r.module_id: r for r in module_catalog()}
    assert set(rows) == {f"H{i}" for i in range(12)}
    assert rows["H7"].stage2_status == "approval_required"
    assert rows["H6"].stage2_status == "native_input"
    assert rows["H11"].stage2_status == "reference_only"
    with pytest.raises(ValidationError):
        ModuleEvidence(
            module_id="H99",
            contract_name="x",
            revision=0,
            sha256="a" * 64,
            source_ids=("x",),
        )


def test_module_references_require_unique_ids_and_known_sources(raw):
    entry = {
        "module_id": "H11",
        "contract_name": "orbit-v1",
        "revision": 0,
        "sha256": "a" * 64,
        "source_ids": ["synthetic-registration"],
    }
    raw["module_evidence"] = [entry]
    assert UnifiedPlanningEnvelope.model_validate(raw).module_evidence
    raw["module_evidence"] = [entry, entry]
    with pytest.raises(ValueError):
        UnifiedPlanningEnvelope.model_validate(raw)
    raw["module_evidence"] = [{**entry, "source_ids": ["missing"]}]
    with pytest.raises(ValueError):
        UnifiedPlanningEnvelope.model_validate(raw)


def test_fixture_native_contracts_valid_before_bridge(raw):
    assert CampaignProblem.model_validate(raw["campaign"])
    assert HybridRecoveryRequest.model_validate(raw["recovery"])


def test_unmapped_r11_mission_keeps_dependency_and_cancellation(raw):
    other = deepcopy(raw["campaign"]["missions"][0])
    other.update(mission_id="later", vehicle_id="later-vehicle", priority=5)
    other["policy"].update(cancel_allowed=True, cancel_penalty_score=3.0)
    other["dependencies"] = [
        {"predecessor_mission_id": "landing", "min_lag_s": 0.0}
    ]
    raw["campaign"]["missions"].append(other)
    vehicle = deepcopy(raw["campaign"]["vehicles"][0])
    vehicle["vehicle_id"] = "later-vehicle"
    raw["campaign"]["vehicles"].append(vehicle)
    for site in raw["campaign"]["sites"]:
        descent = deepcopy(site["descents"][0])
        descent["vehicle_id"] = "later-vehicle"
        site["descents"].append(descent)
    native = CampaignProblem.model_validate(raw["campaign"])
    envelope = UnifiedPlanningEnvelope.model_validate(raw)
    snapshot = seal_inputs(envelope)
    assert to_r11_problem(snapshot, current=envelope, now_utc=NOW) == native
    proposal = map_h6_proposal(
        snapshot, ("land-primary",), current=envelope, now_utc=NOW
    )
    assert "later" not in proposal.selections
    assert proposal.requires_classical_completion


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_source",
        "non_synthetic",
        "unknown_parent",
        "duplicate_module",
        "extra_field",
        "schema_bool",
    ],
)
def test_metadata_cannot_hide_invalid_provenance(raw, change):
    if change == "duplicate_source":
        raw["context"]["sources"].append(deepcopy(raw["context"]["sources"][0]))
    elif change == "non_synthetic":
        raw["context"]["sources"][0]["kind"] = "observed"
    elif change == "unknown_parent":
        raw["context"]["sources"][0]["parent_source_ids"] = ["missing"]
    elif change == "extra_field":
        raw["context"]["quantum_enabled"] = True
    elif change == "schema_bool":
        raw["schema_version"] = True
    else:
        e = {
            "module_id": "H0",
            "contract_name": "scenario-v1",
            "revision": 0,
            "sha256": "a" * 64,
            "source_ids": ["synthetic-registration"],
        }
        raw["module_evidence"] = [e, e]
    with pytest.raises(ValueError):
        UnifiedPlanningEnvelope.model_validate(raw)
