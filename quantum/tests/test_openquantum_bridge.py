"""H7 acceptance: actual Qiskit unitaries, credit gating and provider lifecycle.

All service calls use a fake transport. These tests never submit remote work.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
from qiskit.providers.fake_provider import GenericBackendV2
from qiskit.quantum_info import Statevector

from backend.app.hybrid_quantum import compact_model, compile_qubo, qaoa_statevector
from backend.app.hybrid_recovery import (
    HybridRecoveryRequest,
    checkpoint_from_decision,
    prepare_problem,
)
from backend.app.self_healing import RecoveryScenario, replay_recovery
from shared.hybrid import fingerprint, load_scenario
from quantum.openquantum_bridge import (
    OpenQuantumGateway,
    analyze_counts,
    build_qaoa_circuit,
    choose_free_quote,
    resolve_hardware_recovery,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


@lru_cache
def recovery_request():
    raw = json.loads(
        (ROOT / "shared/examples/recovery/hybrid_campaign.json").read_text()
    )
    raw.pop("quantum_budget")
    raw["recovery"]["uncertainty"]["samples"] = 2
    r = RecoveryScenario.model_validate(raw["recovery"])
    raw["checkpoint"] = checkpoint_from_decision(
        replay_recovery(r)["decisions"][-1]
    ).model_dump()
    return HybridRecoveryRequest.model_validate(raw)


@lru_cache
def problem():
    return prepare_problem(recovery_request())["scenario"]


def prep_quote(price=1):
    return NS(
        status="Completed",
        organization_id="org",
        backend_class_id="device",
        shots=16,
        configuration_data={"shots": 16},
        quote=[
            NS(
                name="Private",
                price=0,
                execution_plan_id="private",
                queue_priorities=[
                    NS(name="Standard", price_increase=0, queue_priority_id="standard")
                ],
            ),
            NS(
                name="Public",
                price=price,
                execution_plan_id="public",
                queue_priorities=[
                    NS(name="Instant", price_increase=0, queue_priority_id="instant"),
                    NS(name="Standard", price_increase=0, queue_priority_id="standard"),
                ],
            ),
        ],
    )


class FakeService:
    def __init__(self):
        self.management = self.scheduler = self
        self.balance = NS(spark_credits=25, full_credits=0)
        self.devices = [
            dict(
                id="device",
                short_code="test:qpu",
                type="QPU",
                status="Online",
                accepting_jobs=True,
            )
        ]
        self.orgs = [dict(id="org", name="test")]
        self.quote = prep_quote()
        self.created = []
        self.uploads = []
        self.status = "Completed"
        self.counts = {"00": 4, "01": 4, "10": 4, "11": 4}
        self.fail_create = False
        self.receipt_mismatch = False

    def organizations(self):
        return deepcopy(self.orgs)

    def backends(self):
        return deepcopy(self.devices)

    def get_credit_balance(self, org):
        assert org == "org"
        return self.balance

    def return_backend(self, code):
        assert code == "test:qpu"
        return GenericBackendV2(2, basis_gates=["id", "rz", "sx", "x", "cx"], seed=17)

    def upload_job_input(self, *, file_content):
        self.uploads.append(file_content)
        return "upload"

    def prepare_job(self, config):
        self.preparation_config = config
        self.quote.shots = config.configuration_data["shots"]
        self.quote.configuration_data["shots"] = config.configuration_data["shots"]
        return NS(id="prep")

    def wait_for_preparation(self, pid, **kwargs):
        return self.quote

    def get_preparation_result(self, pid):
        return self.quote

    def create_job(self, config):
        self.created.append(config)
        if self.fail_create:
            raise TimeoutError("Ambiguous network outcome")
        return NS(id="job", status="Queued")

    def get_job(self, jid):
        return NS(
            id=jid,
            status=self.status,
            job_preparation_id="other" if self.receipt_mismatch else "prep",
            execution_plan_id="public",
            queue_priority_id="standard",
            submitted_at=NOW.isoformat(),
        )

    def download_job_output(self, job):
        return deepcopy(self.counts)


@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("kind", ["recovery", "six-qubit"])
def test_exported_unitary_matches_existing_qaoa(depth, kind):
    source = (
        problem()
        if kind == "recovery"
        else load_scenario(ROOT / "shared/examples/hybrid/shared-conflict.json")
    )
    model = compact_model(compile_qubo(source))
    gammas = [0.17, -0.93, 1.2][:depth]
    betas = [-0.3, 0.27, 0.61][:depth]
    circuit = build_qaoa_circuit(source, gammas, betas, measure=False)
    actual = Statevector.from_instruction(circuit).data
    expected = qaoa_statevector(model.normalized_energies, gammas, betas)
    assert np.allclose(actual, expected, atol=1e-11, rtol=0)
    assert circuit.num_qubits == model.logical_qubits


@pytest.mark.parametrize(
    "g,b", [([], []), ([1], [1, 2]), ([float("nan")], [0]), ([0] * 4, [0] * 4)]
)
def test_invalid_angles_rejected(g, b):
    with pytest.raises(ValueError):
        build_qaoa_circuit(problem(), g, b)


def test_measurements_keep_logical_bit_order():
    circuit = build_qaoa_circuit(problem(), [0.2], [0.4])
    measurements = [i for i in circuit.data if i.operation.name == "measure"]
    assert [
        (circuit.find_bit(i.qubits[0]).index, circuit.find_bit(i.clbits[0]).index)
        for i in measurements
    ] == [(0, 0), (1, 1)]
    assert circuit.metadata["problem_fingerprint"] == fingerprint(problem())


def test_asymmetric_counts_decode_rightmost_bit_as_qubit_zero():
    source = problem()
    model = compact_model(compile_qubo(source))
    report = analyze_counts(source, {"01": 16}, 16, mode="hardware")
    row = report["samples"][1]
    mask = model.one_hot_masks[1]
    expected = [oid for i, oid in enumerate(model.qubo.option_ids) if mask & (1 << i)]
    assert row["selected_option_ids"] == expected
    assert row["count"] == 16 and report["execution_mode"] == "hardware"
    assert "probability" not in row
    lo, hi = row["frequency_interval_95"]
    assert 0 < lo < hi <= 1


@pytest.mark.parametrize(
    "counts,shots",
    [
        ({}, 16),
        ({"00": 15}, 16),
        ({"000": 16}, 16),
        ({"2": 16}, 16),
        ({"00": -1, "01": 17}, 16),
        ({"00": True}, 1),
        ({"00": 1.5}, 1),
        ({"00": 16}, True),
    ],
)
def test_malformed_counts_rejected(counts, shots):
    with pytest.raises(ValueError):
        analyze_counts(problem(), counts, shots, mode="hardware")


def test_counts_report_invalid_outcomes_without_repair():
    r = analyze_counts(
        problem(), {"00": 4, "01": 4, "10": 4, "11": 4}, 16, mode="noise_model"
    )
    assert r["sampled_feasible_rate"] == 0.75
    assert r["sampled_optimal_rate"] == 0.25
    assert r["repair_applied"] is False
    assert r["best_plan"]["execution_mode"] == "noise_model"
    with pytest.raises(ValueError):
        analyze_counts(problem(), {"00": 16}, 16, mode="classical")


def test_selects_only_public_standard():
    choice = choose_free_quote(
        prep_quote(),
        NS(spark_credits=25, full_credits=0),
        max_credits=2,
        auto_refill_disabled=True,
    )
    assert choice == dict(
        execution_plan_id="public", queue_priority_id="standard", credits=1.0
    )


@pytest.mark.parametrize(
    "change",
    [
        "paid",
        "unknown_autorefill",
        "insufficient",
        "budget",
        "nan",
        "negative",
        "private_only",
        "priority_only",
        "pending",
    ],
)
def test_spend_guard_fails_closed(change):
    prep = prep_quote()
    balance = NS(spark_credits=25, full_credits=0)
    disabled = True
    cap = 2
    if change == "paid":
        balance.full_credits = 10
    elif change == "unknown_autorefill":
        disabled = False
    elif change == "insufficient":
        balance.spark_credits = 0.5
    elif change == "budget":
        cap = 0.5
    elif change == "nan":
        balance.spark_credits = float("nan")
    elif change == "negative":
        prep.quote[1].price = -1
    elif change == "private_only":
        prep.quote = prep.quote[:1]
    elif change == "priority_only":
        prep.quote[1].queue_priorities = prep.quote[1].queue_priorities[:1]
    elif change == "pending":
        prep.status = "Pending"
    with pytest.raises((ValueError, PermissionError)):
        choose_free_quote(prep, balance, max_credits=cap, auto_refill_disabled=disabled)


def gateway(tmp_path):
    service = FakeService()
    return service, OpenQuantumGateway(service, tmp_path, clock=lambda: NOW)


def prepare(g):
    return g.prepare(
        problem(),
        build_qaoa_circuit(problem(), [0.2], [0.4]),
        backend_code="test:qpu",
        organization_id="org",
        shots=16,
        trial="trial-1",
    )


def test_prepare_never_creates_or_spends_and_account_is_explicit(tmp_path):
    s, g = gateway(tmp_path)
    a = g.account_snapshot()
    assert a["organization_id"] == "org" and a["spark_credits"] == 25
    t = prepare(g)
    assert t.problem_fingerprint == fingerprint(problem())
    assert t.shots == 16 and len(t.circuit_sha256) == 64
    assert s.created == [] and len(s.uploads) == 1
    s.orgs.append(dict(id="second", name="second"))
    with pytest.raises(ValueError):
        g.account_snapshot()


@pytest.mark.parametrize("change", ["simulator", "offline", "paused", "unknown"])
def test_non_qpu_or_unavailable_backend_rejected_before_upload(tmp_path, change):
    s, g = gateway(tmp_path)
    if change == "simulator":
        s.devices[0]["type"] = "Simulator"
    elif change == "offline":
        s.devices[0]["status"] = "Offline"
    elif change == "paused":
        s.devices[0]["accepting_jobs"] = False
    else:
        s.devices = []
    with pytest.raises(ValueError):
        prepare(g)
    assert not s.uploads and not s.created


def test_free_submission_receipt_and_duplicate_survive_restart(tmp_path):
    s, g = gateway(tmp_path)
    t = prepare(g)
    assert g.submit_free(t, max_credits=2, auto_refill_disabled=True) == "job"
    new = OpenQuantumGateway(s, tmp_path, clock=lambda: NOW)
    with pytest.raises(FileExistsError):
        new.submit_free(t, max_credits=2, auto_refill_disabled=True)
    assert len(s.created) == 1
    r = g.collect(t, "job", problem())
    assert r["status"] == "Completed" and r["execution_mode"] == "hardware"
    assert r["job_id"] == "job" and r["analysis"]["sampled_optimal_rate"] == 0.25


def test_balance_is_refetched_before_submission(tmp_path):
    s, g = gateway(tmp_path)
    t = prepare(g)
    s.balance.spark_credits = 0
    with pytest.raises(PermissionError):
        g.submit_free(t, max_credits=2, auto_refill_disabled=True)
    assert not s.created


def test_uncertain_submission_is_not_automatically_retried(tmp_path):
    s, g = gateway(tmp_path)
    t = prepare(g)
    s.fail_create = True
    with pytest.raises(TimeoutError):
        g.submit_free(t, max_credits=2, auto_refill_disabled=True)
    with pytest.raises(FileExistsError):
        g.submit_free(t, max_credits=2, auto_refill_disabled=True)
    assert len(s.created) == 1


@pytest.mark.parametrize("change", ["old", "organization", "shots", "backend"])
def test_changed_quote_or_expired_ticket_cannot_submit(tmp_path, change):
    s, g = gateway(tmp_path)
    t = prepare(g)
    if change == "old":
        t = replace(t, created_utc=NOW - timedelta(hours=1))
    elif change == "organization":
        s.quote.organization_id = "other"
    elif change == "shots":
        s.quote.configuration_data["shots"] = 32
    else:
        s.quote.backend_class_id = "other"
    with pytest.raises(ValueError):
        g.submit_free(t, max_credits=2, auto_refill_disabled=True)
    assert not s.created


def test_pending_job_is_polled_without_resubmission(tmp_path):
    s, g = gateway(tmp_path)
    t = prepare(g)
    jid = g.submit_free(t, max_credits=2, auto_refill_disabled=True)
    s.status = "Running"
    r = g.collect(t, jid, problem())
    assert r["status"] == "Running" and "analysis" not in r
    assert len(s.created) == 1


def test_receipt_mismatch_is_rejected(tmp_path):
    s, g = gateway(tmp_path)
    t = prepare(g)
    jid = g.submit_free(t, max_credits=2, auto_refill_disabled=True)
    s.receipt_mismatch = True
    with pytest.raises(ValueError):
        g.collect(t, jid, problem())


def test_hardware_recovery_accepts_current_validated_optimum(tmp_path):
    s, g = gateway(tmp_path)
    t = prepare(g)
    jid = g.submit_free(t, max_credits=2, auto_refill_disabled=True)
    evidence = g.collect(t, jid, problem())
    r = resolve_hardware_recovery(
        recovery_request(),
        evidence,
        deadline_utc=NOW + timedelta(seconds=10),
        now_utc=NOW,
    )
    assert r["source"] == "hardware" and r["selected_target_id"] == "remote"
    assert r["validation"]["status"] == "feasible"


@pytest.mark.parametrize(
    "change", ["deadline", "stale", "simulator", "invalid_counts", "infeasible_current"]
)
def test_hardware_recovery_retains_current_classical_fallback(tmp_path, change):
    s, g = gateway(tmp_path)
    t = prepare(g)
    jid = g.submit_free(t, max_credits=2, auto_refill_disabled=True)
    evidence = g.collect(t, jid, problem())
    req = recovery_request()
    deadline = NOW + timedelta(seconds=10)
    if change == "deadline":
        deadline = NOW
    elif change == "stale":
        evidence["problem_fingerprint"] = "0" * 64
    elif change == "simulator":
        evidence["execution_mode"] = "noiseless_simulator"
    elif change == "invalid_counts":
        evidence["counts"] = {"00": 0}
    else:
        raw = req.model_dump()
        raw["campaign"]["resources"][0]["capacity"] = 0.0
        req = HybridRecoveryRequest.model_validate(raw)
    r = resolve_hardware_recovery(req, evidence, deadline_utc=deadline, now_utc=NOW)
    assert r["source"] == "classical_fallback"
    if change == "infeasible_current":
        assert r["plan"] is None
    else:
        assert r["validation"]["status"] == "feasible"
