"""H7 optional OpenQuantum experiments; no provider dependency in backend runtime."""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from openquantum_sdk.models import JobCreate, JobPreparationCreate
from openquantum_sdk.utils import strip_gate_definitions
from openquantum_sdk_qiskit import export_circuit_to_qasm
from qiskit import QuantumCircuit, transpile

from backend.app.hybrid_planning import solve_exact, validate_plan
from backend.app.hybrid_quantum import compact_model, compile_qubo, qaoa_statevector
from backend.app.hybrid_recovery import prepare_problem
from shared.hybrid import Plan, Scenario, fingerprint


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("Explicit UTC timestamp required")
    return value


def _shots(value):
    if type(value) is not int or not 1 <= value <= 8192:
        raise ValueError("shots must be an integer in [1, 8192]")
    return value


def _number(value):
    if isinstance(value, bool):
        raise ValueError("Credits must be finite nonnegative numbers")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid credit amount") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Credits must be finite nonnegative numbers")
    return result


def build_qaoa_circuit(scenario: Scenario, gammas, betas, *, measure=True):
    """Exact little-endian H2 Hamiltonian; CX parity chains implement Z products."""
    model = compact_model(compile_qubo(scenario), max_qubits=6)
    # Reuse the independently tested H2 domain and angle checks.
    qaoa_statevector(model.normalized_energies, gammas, betas)
    qc = QuantumCircuit(model.logical_qubits, model.logical_qubits if measure else 0)
    qc.h(range(model.logical_qubits))
    for gamma, beta in zip(gammas, betas):
        for mask, coefficient in model.z_terms:
            angle = float(gamma) * coefficient
            if not mask:
                qc.global_phase -= angle
                continue
            bits = [q for q in range(model.logical_qubits) if mask & (1 << q)]
            for q in bits[:-1]:
                qc.cx(q, bits[-1])
            qc.rz(2 * angle, bits[-1])
            for q in reversed(bits[:-1]):
                qc.cx(q, bits[-1])
        for q in range(model.logical_qubits):
            qc.rx(2 * float(beta), q)
    if measure:
        qc.measure(range(model.logical_qubits), range(model.logical_qubits))
    qc.metadata = dict(
        problem_fingerprint=fingerprint(scenario),
        logical_qubits=model.logical_qubits,
        gammas=list(gammas),
        betas=list(betas),
        parameter_training="local_noiseless",
    )
    return qc


def _trim_idle_tail(circuit):
    """Remove only trailing unused target qubits; retain every physical index."""
    used = [
        circuit.find_bit(q).index
        for instruction in circuit.data
        for q in instruction.qubits
    ]
    width = max(used, default=0) + 1
    trimmed = QuantumCircuit(width, circuit.num_clbits)
    trimmed.global_phase = circuit.global_phase
    trimmed.metadata = circuit.metadata.copy()
    for instruction in circuit.data:
        trimmed.append(
            instruction.operation,
            [circuit.find_bit(q).index for q in instruction.qubits],
            [circuit.find_bit(c).index for c in instruction.clbits],
        )
    return trimmed


def _interval(count, total):
    # Wilson 95% binomial interval; describes shot uncertainty, not device drift.
    z = 1.959963984540054
    p = count / total
    den = 1 + z * z / total
    center = (p + z * z / (2 * total)) / den
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return [max(0.0, center - half), min(1.0, center + half)]


def analyze_counts(scenario: Scenario, counts: dict, shots: int, *, mode: str):
    """Decode observed counts without substituting ideal probabilities or repairing plans."""
    _shots(shots)
    if mode not in ("hardware", "noise_model", "noiseless_simulator"):
        raise ValueError("Explicit quantum execution mode required")
    model = compact_model(compile_qubo(scenario), max_qubits=6)
    width = model.logical_qubits
    if not isinstance(counts, dict) or not counts:
        raise ValueError("Nonempty counts required")
    for key, count in counts.items():
        if not isinstance(key, str) or not re.fullmatch(
            "[01]{" + str(width) + "}", key
        ):
            raise ValueError("Counts must use fixed-width Qiskit bitstrings")
        if type(count) is not int or count < 0:
            raise ValueError("Counts must be nonnegative integers")
    if sum(counts.values()) != shots:
        raise ValueError("Count total does not match submitted shots")
    exact = solve_exact(scenario, max_evaluations=4096)
    if exact.status != "optimal":
        raise ValueError("H7 benchmark requires a completed exact reference")
    optimal_score = Fraction(validate_plan(exact.plan, scenario).exact_score)
    best = best_score = None
    rows = []
    feasible_count = optimal_count = 0
    for basis, mask in enumerate(model.one_hot_masks):
        ids = [oid for i, oid in enumerate(model.qubo.option_ids) if mask & (1 << i)]
        count = counts.get(format(basis, f"0{width}b"), 0)
        plan = Plan(
            plan_id="h7-" + _hash(str([fingerprint(scenario), ids, mode])),
            scenario_id=scenario.scenario_id,
            scenario_revision=scenario.revision,
            input_fingerprint=fingerprint(scenario),
            state_revisions={s.state_id: s.revision for s in scenario.states},
            selected_option_ids=tuple(ids),
            execution_mode=mode,
            solver_name="h7-observed-qaoa",
        )
        checked = validate_plan(plan, scenario)
        feasible = checked.status == "feasible"
        score = Fraction(checked.exact_score) if feasible else None
        optimal = feasible and score == optimal_score
        feasible_count += count if feasible else 0
        optimal_count += count if optimal else 0
        if count and feasible and (best_score is None or score > best_score):
            best, best_score = plan, score
        rows.append(
            dict(
                basis_index=basis,
                selected_option_ids=ids,
                count=count,
                observed_frequency=count / shots,
                frequency_interval_95=_interval(count, shots),
                feasible=feasible,
                optimal=optimal,
                exact_score=checked.exact_score,
            )
        )
    return dict(
        execution_mode=mode,
        problem_fingerprint=fingerprint(scenario),
        shots=shots,
        logical_qubits=width,
        samples=rows,
        repair_applied=False,
        sampled_feasible_rate=feasible_count / shots,
        sampled_optimal_rate=optimal_count / shots,
        optimal_rate_interval_95=_interval(optimal_count, shots),
        best_plan=None if best is None else best.model_dump(mode="json"),
        optimum_gap=None if best_score is None else float(optimal_score - best_score),
    )


def choose_free_quote(preparation, balance, *, max_credits, auto_refill_disabled=False):
    """Fail closed: explicitly Public + Standard, Spark coverage, no paid balance/refill."""
    cap = _number(max_credits)
    spark, full = _number(balance.spark_credits), _number(balance.full_credits)
    if preparation.status != "Completed":
        raise ValueError("Preparation is not completed")
    if auto_refill_disabled is not True:
        raise PermissionError("Verify Auto Refill is off before submitting")
    if full != 0:
        raise PermissionError(
            "Paid balance exists; this free-only adapter cannot authorize its use"
        )
    choices = []
    for plan in preparation.quote:
        if plan.name.casefold() not in ("public", "public plan"):
            continue
        for priority in plan.queue_priorities:
            if priority.name.casefold() not in ("standard", "standard queue"):
                continue
            cost = _number(plan.price) + _number(priority.price_increase)
            if cost <= cap and cost <= spark:
                choices.append(
                    (cost, plan.execution_plan_id, priority.queue_priority_id)
                )
    if not choices:
        raise PermissionError(
            "No Public/Standard quote covered by Spark and the credit cap"
        )
    cost, plan_id, priority_id = min(choices)
    return dict(
        execution_plan_id=plan_id, queue_priority_id=priority_id, credits=float(cost)
    )


def resolve_hardware_recovery(
    current_request, evidence: dict, *, deadline_utc, now_utc
):
    """Current physical/classical gate. Cloud benchmark evidence can outlive its live deadline."""
    _utc(deadline_utc)
    _utc(now_utc)
    prepared = prepare_problem(current_request)
    problem = prepared["scenario"]
    classic = (
        solve_exact(problem, max_evaluations=4096) if problem is not None else None
    )
    plan = classic.plan if classic is not None else None
    checked = validate_plan(plan, problem) if plan is not None else None
    source, reason = "classical_fallback", "invalid_hardware"
    if now_utc >= deadline_utc:
        reason = "deadline"
    elif problem is None or evidence.get("problem_fingerprint") != fingerprint(problem):
        reason = "stale_snapshot"
    elif (
        evidence.get("execution_mode") == "hardware"
        and evidence.get("status") == "Completed"
        and evidence.get("job_id")
    ):
        try:
            observed = analyze_counts(
                problem, evidence["counts"], evidence["shots"], mode="hardware"
            )
            if observed["best_plan"] is not None:
                candidate = Plan.model_validate(observed["best_plan"])
                report = validate_plan(candidate, problem)
                if report.status == "feasible" and (
                    checked is None
                    or Fraction(report.exact_score) >= Fraction(checked.exact_score)
                ):
                    plan, checked, source, reason = (
                        candidate,
                        report,
                        "hardware",
                        "hardware_accepted",
                    )
                else:
                    reason = "inferior_hardware"
        except (ValueError, TypeError, KeyError):
            reason = "invalid_hardware"
    target = None
    if plan is not None:
        target = next(
            prepared["option_to_target"][oid]
            for oid in plan.selected_option_ids
            if oid in prepared["option_to_target"]
        )
    return dict(
        source=source,
        reason=reason,
        selected_target_id=target,
        plan=None if plan is None else plan.model_dump(mode="json"),
        validation=None if checked is None else checked.model_dump(mode="json"),
    )


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
    """One controlling caller; durable local submission claims prevent accidental retries.

    No high-level SDK run call is used: those may spend before returning control.
    Billing-setting attestation is supplied only after checking the account UI.
    Keep this ledger between notebook runs; it is not a distributed spend lock.
    """

    def __init__(self, service, ledger_dir: Path, *, clock=None):
        self.service = service
        self.ledger_dir = Path(ledger_dir)
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(UTC))

    def account_snapshot(self, organization_id=None):
        orgs = self.service.organizations()
        if organization_id is None:
            if len(orgs) != 1:
                raise ValueError(
                    "Select an explicit organization when membership is ambiguous"
                )
            organization_id = orgs[0]["id"]
        if organization_id not in {o["id"] for o in orgs}:
            raise ValueError("Organization is not accessible")
        balance = self.service.management.get_credit_balance(organization_id)
        return dict(
            organization_id=organization_id,
            spark_credits=float(_number(balance.spark_credits)),
            full_credits=float(_number(balance.full_credits)),
            checked_utc=_utc(self.clock()).isoformat(),
            backends=self.service.backends(),
        )

    def _backend(self, code):
        backend = next(
            (b for b in self.service.backends() if b["short_code"] == code), None
        )
        if (
            backend is None
            or backend.get("type", "").upper() != "QPU"
            or backend.get("status", "").casefold() != "online"
            or backend.get("accepting_jobs") is not True
        ):
            raise ValueError("An online, accepting QPU is required")
        return backend

    def _key(self, prepared):
        # Deliberately excludes preparation ID and creation time: a repeated
        # preparation of the same experiment must not create a second charged job.
        return _hash(
            json.dumps(
                [
                    prepared.organization_id,
                    prepared.backend_id,
                    prepared.problem_fingerprint,
                    prepared.circuit_sha256,
                    prepared.shots,
                    prepared.trial,
                ]
            )
        )

    def _ticket_path(self, prepared):
        return self.ledger_dir / (
            "prepared-" + _hash(prepared.preparation_id) + ".json"
        )

    def _check_ticket(self, prepared):
        saved = json.loads(self._ticket_path(prepared).read_text())
        if saved != json.loads(json.dumps(asdict(prepared), default=str)):
            raise ValueError("Prepared ticket differs from its saved manifest")

    def _quote(self, prepared):
        p = self.service.scheduler.get_preparation_result(prepared.preparation_id)
        if (
            p.organization_id != prepared.organization_id
            or p.backend_class_id != prepared.backend_id
            or p.configuration_data.get("shots", p.shots) != prepared.shots
            or (p.shots is not None and p.shots != prepared.shots)
        ):
            raise ValueError("Preparation identity or shot count changed")
        return p

    def prepare(
        self, scenario, circuit, *, backend_code, organization_id, shots, trial
    ):
        _shots(shots)
        if not isinstance(trial, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", trial
        ):
            raise ValueError("Use a stable explicit trial label")
        width = compact_model(compile_qubo(scenario), max_qubits=6).logical_qubits
        if (
            circuit.num_qubits != width
            or circuit.num_clbits != width
            or circuit.metadata.get("problem_fingerprint") != fingerprint(scenario)
        ):
            raise ValueError("Circuit and problem identity do not match")
        backend = self._backend(backend_code)
        self.account_snapshot(organization_id)
        provider_backend = self.service.return_backend(backend_code)
        native = transpile(
            circuit,
            backend=provider_backend,
            optimization_level=1,
            seed_transpiler=17,
        )
        native = _trim_idle_tail(native)
        # Match SDK 1.2.1's upload normalization before hashing the evidence.
        payload = strip_gate_definitions(
            export_circuit_to_qasm(native, "qasm2", backend=provider_backend)
        )
        upload = self.service.scheduler.upload_job_input(file_content=payload)
        response = self.service.scheduler.prepare_job(
            JobPreparationCreate(
                organization_id=organization_id,
                backend_class_id=backend["id"],
                name="H7 " + trial,
                upload_endpoint_id=upload,
                job_subcategory_id="oth:oth",
                configuration_data={"shots": shots},
                submitted_with="qiskit",
                input_format="qasm",
            )
        )
        self.service.scheduler.wait_for_preparation(response.id, timeout=60, interval=2)
        ticket = PreparedRun(
            response.id,
            organization_id,
            backend["id"],
            backend_code,
            fingerprint(scenario),
            hashlib.sha256(payload).hexdigest(),
            shots,
            trial,
            _utc(self.clock()),
        )
        self._quote(ticket)
        # Local circuit is retained for reproducibility without credentials or signed URLs.
        (self.ledger_dir / (ticket.circuit_sha256 + ".qasm")).write_bytes(payload)
        self._ticket_path(ticket).write_text(
            json.dumps(asdict(ticket), default=str, indent=2)
        )
        return ticket

    def submit_free(self, prepared, *, max_credits, auto_refill_disabled=False):
        self._check_ticket(prepared)
        age = (_utc(self.clock()) - _utc(prepared.created_utc)).total_seconds()
        if not 0 <= age <= 900:
            raise ValueError("Prepared ticket is expired or from the future")
        if self._backend(prepared.backend_code)["id"] != prepared.backend_id:
            raise ValueError("Backend identity changed")
        quote = self._quote(prepared)
        balance = self.service.management.get_credit_balance(prepared.organization_id)
        choice = choose_free_quote(
            quote,
            balance,
            max_credits=max_credits,
            auto_refill_disabled=auto_refill_disabled,
        )
        path = self.ledger_dir / ("submitted-" + self._key(prepared) + ".json")
        claim = dict(
            preparation_id=prepared.preparation_id,
            choice=choice,
            submitted_utc=self.clock().isoformat(),
            job_id=None,
            status="submission_unknown",
        )
        # Persist before the network side effect; a timeout leaves an uncertain
        # claim requiring reconciliation, never an automatic second submission.
        with path.open("x") as f:
            json.dump(claim, f, indent=2)
        job = self.service.scheduler.create_job(
            JobCreate(
                job_preparation_id=prepared.preparation_id,
                execution_plan_id=choice["execution_plan_id"],
                queue_priority_id=choice["queue_priority_id"],
                organization_id=prepared.organization_id,
            )
        )
        claim.update(job_id=job.id, status=job.status)
        path.write_text(json.dumps(claim, indent=2))
        return job.id

    def collect(self, prepared, job_id, scenario):
        self._check_ticket(prepared)
        if fingerprint(scenario) != prepared.problem_fingerprint:
            raise ValueError("Result must be decoded with its submitted problem")
        claim = json.loads(
            (
                self.ledger_dir / ("submitted-" + self._key(prepared) + ".json")
            ).read_text()
        )
        if claim["job_id"] != job_id:
            raise ValueError("Job does not match the local submission receipt")
        job = self.service.scheduler.get_job(job_id)
        if (
            job.id != job_id
            or job.job_preparation_id != prepared.preparation_id
            or job.execution_plan_id != claim["choice"]["execution_plan_id"]
            or job.queue_priority_id != claim["choice"]["queue_priority_id"]
        ):
            raise ValueError(
                "Remote job identity differs from the submitted experiment"
            )
        report = dict(
            job_id=job_id,
            status=job.status,
            backend_code=prepared.backend_code,
            problem_fingerprint=prepared.problem_fingerprint,
            circuit_sha256=prepared.circuit_sha256,
            shots=prepared.shots,
            quoted_credits=claim["choice"]["credits"],
            submitted_utc=claim["submitted_utc"],
            observed_utc=_utc(self.clock()).isoformat(),
        )
        if job.status == "Completed":
            counts = self.service.scheduler.download_job_output(job)
            report.update(
                execution_mode="hardware",
                counts=counts,
                analysis=analyze_counts(
                    scenario, counts, prepared.shots, mode="hardware"
                ),
            )
        (self.ledger_dir / ("result-" + _hash(job_id) + ".json")).write_text(
            json.dumps(report, indent=2)
        )
        return report
