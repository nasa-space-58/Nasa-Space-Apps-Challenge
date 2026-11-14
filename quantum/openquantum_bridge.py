"""H7 Qiskit circuit export, observed-result validation and free-credit gating."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shared.hybrid import Scenario


def build_qaoa_circuit(scenario: Scenario, gammas, betas, *, measure=True):
    raise NotImplementedError


def analyze_counts(scenario: Scenario, counts: dict, shots: int, *, mode: str):
    raise NotImplementedError


def choose_free_quote(preparation, balance, *, max_credits, auto_refill_disabled=False):
    raise NotImplementedError


def resolve_hardware_recovery(current_request, evidence: dict, *, deadline_utc, now_utc):
    raise NotImplementedError


@dataclass(frozen=True)
class PreparedRun:
    preparation_id: str
    organization_id: str
    backend_id: str
    backend_code: str
    problem_fingerprint: str
    circuit_sha256: str
    shots: int
    trial: str
    created_utc: datetime


class OpenQuantumGateway:
    """Explicit prepare/submit/collect boundary; never auto-spend in a notebook."""

    def __init__(self, service, ledger_dir: Path, *, clock=None):
        raise NotImplementedError

    def account_snapshot(self, organization_id=None):
        raise NotImplementedError

    def prepare(self, scenario, circuit, *, backend_code, organization_id, shots, trial):
        raise NotImplementedError

    def submit_free(self, prepared, *, max_credits, auto_refill_disabled=False):
        raise NotImplementedError

    def collect(self, prepared, job_id, scenario):
        raise NotImplementedError
