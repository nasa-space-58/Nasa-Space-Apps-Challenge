"""Durable synthetic planning sessions shared across mission workspaces."""

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import time
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import HTTPException

from pydantic import Field

from shared.hybrid import Contract, Identifier

from .campaign_planning import campaign_timeline, generate_candidates
from .unified_classical import LandingExclusion, apply_landing_exclusion
from .unified_planning import UnifiedPlanningEnvelope, seal_inputs
from .unified_quantum import ProposalBudget, accept_hybrid, compare_proposals


class PlanCommand(Contract):
    request_id: Identifier
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    mode: Literal["classical", "local_experiment"] = "classical"
    ttl_s: Annotated[float, Field(strict=True, gt=0, le=120)] = 30.0
    budget: ProposalBudget = ProposalBudget()


class EventCommand(PlanCommand):
    event_id: Identifier
    elapsed_s: Annotated[float, Field(strict=True, gt=0, lt=10)]
    start_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    end_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]


class CoordinationError(ValueError):
    """An invalid transition, missing session, or conflicting command."""


class UnifiedCoordinator:
    """Single-service durable queue; revisions and deadlines fence late threads."""

    def __init__(self, path, *, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or time.time
        with self._transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_id TEXT NOT NULL, command TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(session_id, request_id))"
            )

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _session(self, db, session_id):
        row = db.execute(
            "SELECT data FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return json.loads(row[0])

    def _save_session(self, db, session):
        db.execute(
            "UPDATE sessions SET data=? WHERE id=?",
            (json.dumps(session), session["id"]),
        )

    def _save_job(self, db, job):
        db.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(job), job["id"]))

    def _jobs(self, db, session_id=None):
        if session_id is None:
            rows = db.execute("SELECT data FROM jobs ORDER BY rowid").fetchall()
        else:
            rows = db.execute(
                "SELECT data FROM jobs WHERE session_id=? ORDER BY rowid DESC",
                (session_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def create(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "shared/examples/unified/classical-pilot.json"
        )
        envelope = UnifiedPlanningEnvelope.model_validate_json(path.read_text())
        session = dict(
            id=str(uuid4()),
            revision=envelope.context.revision,
            created_at=self.clock(),
            simulation_utc=envelope.context.created_utc.isoformat(),
            initial_utc=envelope.context.created_utc.isoformat(),
            input=envelope.model_dump(mode="json"),
            accepted=None,
            history=[],
            events=[],
            active_job_id=None,
            synthetic=True,
        )
        with self._transaction() as db:
            db.execute(
                "INSERT INTO sessions VALUES (?,?)",
                (session["id"], json.dumps(session)),
            )
        return self.get(session["id"])

    def get(self, session_id):
        with self._transaction() as db:
            session = self._session(db, session_id)
            session["jobs"] = [self._public_job(j) for j in self._jobs(db, session_id)]
        session["snapshot_sha256"] = seal_inputs(
            UnifiedPlanningEnvelope.model_validate(session["input"])
        ).snapshot_sha256
        return session

    def recent(self):
        with self._transaction() as db:
            rows = db.execute(
                "SELECT data FROM sessions ORDER BY rowid DESC LIMIT 20"
            ).fetchall()
        return [
            {
                k: s[k]
                for k in (
                    "id",
                    "revision",
                    "created_at",
                    "simulation_utc",
                    "active_job_id",
                )
            }
            for s in (json.loads(row[0]) for row in rows)
        ]

    @staticmethod
    def _public_job(job):
        return {k: v for k, v in job.items() if k not in ("input", "result")}

    def _duplicate(self, db, session_id, command):
        row = db.execute(
            "SELECT command,data FROM jobs WHERE session_id=? AND request_id=?",
            (session_id, command.request_id),
        ).fetchone()
        if row:
            if json.loads(row[0]) != command.model_dump(mode="json"):
                raise CoordinationError(
                    "Request identifier already used with different inputs"
                )
            return json.loads(row[1])
        if isinstance(command, EventCommand):
            for job in self._jobs(db, session_id):
                if job.get("event_id") == command.event_id:
                    raise CoordinationError("Event identifier already used")
        return None

    def _capacity(self, db, superseding=None):
        count = sum(
            j["status"] in ("queued", "running") and j["id"] != superseding
            for j in self._jobs(db)
        )
        if count >= 8:
            raise CoordinationError("Planning queue is full")

    def _enqueue(self, db, session, command):
        now = self.clock()
        job = dict(
            id=str(uuid4()),
            session_id=session["id"],
            revision=session["revision"],
            mode=command.mode,
            budget=command.budget.model_dump(mode="json"),
            input=session["input"],
            simulation_utc=session["simulation_utc"],
            status="queued",
            queued_at=now,
            deadline_at=now + command.ttl_s,
            started_at=None,
            finished_at=None,
            queue_s=None,
            compute_s=None,
            validation_s=None,
            error=None,
            comparison=None,
            result=None,
            event_id=getattr(command, "event_id", None),
        )
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?)",
            (
                job["id"],
                session["id"],
                command.request_id,
                command.model_dump_json(),
                json.dumps(job),
            ),
        )
        session["active_job_id"] = job["id"]
        self._save_session(db, session)
        return self._public_job(job)

    def plan(self, session_id, command: PlanCommand):
        command = PlanCommand.model_validate(command.model_dump())
        with self._transaction() as db:
            session = self._session(db, session_id)
            duplicate = self._duplicate(db, session_id, command)
            if duplicate:
                return self._public_job(duplicate)
            if command.expected_revision != session["revision"]:
                raise CoordinationError(
                    "Session revision changed; refresh before planning"
                )
            if session["active_job_id"]:
                raise CoordinationError("This session already has an active job")
            self._capacity(db)
            return self._enqueue(db, session, command)

    def event(self, session_id, command: EventCommand):
        command = EventCommand.model_validate(command.model_dump())
        # Propagation is deliberately outside the write transaction. Recheck revision below.
        with self._transaction() as db:
            session = self._session(db, session_id)
            duplicate = self._duplicate(db, session_id, command)
            if duplicate:
                return self._public_job(duplicate)
        if command.expected_revision != session["revision"]:
            raise CoordinationError(
                "Session revision changed; refresh before sending an event"
            )
        epoch = datetime.fromisoformat(session["initial_utc"]) + timedelta(
            seconds=command.elapsed_s
        )
        if epoch <= datetime.fromisoformat(session["simulation_utc"]):
            raise CoordinationError("Events must advance the simulation clock")
        envelope = apply_landing_exclusion(
            seal_inputs(UnifiedPlanningEnvelope.model_validate(session["input"])),
            LandingExclusion(
                event_id=command.event_id,
                epoch_utc=epoch,
                start_downrange_m=command.start_downrange_m,
                end_downrange_m=command.end_downrange_m,
            ),
        )
        with self._transaction() as db:
            session = self._session(db, session_id)
            duplicate = self._duplicate(db, session_id, command)
            if duplicate:
                return self._public_job(duplicate)
            if command.expected_revision != session["revision"]:
                raise CoordinationError("Another event changed the session")
            self._capacity(db, session["active_job_id"])
            for job in self._jobs(db, session_id):
                if job["id"] == session["active_job_id"]:
                    job.update(status="superseded", finished_at=self.clock())
                    self._save_job(db, job)
            if session["accepted"] is not None:
                session["history"].append(session["accepted"])
            session.update(
                input=envelope.model_dump(mode="json"),
                revision=envelope.context.revision,
                simulation_utc=epoch.isoformat(),
                accepted=None,
            )
            session["events"].append(command.model_dump(mode="json"))
            return self._enqueue(db, session, command)

    def tick(self):
        with self._transaction() as db:
            jobs = self._jobs(db)
            # Only one CPU-heavy job at a time, including concurrent tick callers.
            if any(j["status"] == "running" for j in jobs):
                return False
            job = next((j for j in jobs if j["status"] == "queued"), None)
            if job is None:
                return False
            job.update(status="running", started_at=self.clock())
            job["queue_s"] = max(0.0, job["started_at"] - job["queued_at"])
            self._save_job(db, job)
        status, accepted = "expired", None
        if self.clock() < job["deadline_at"]:
            try:
                envelope = UnifiedPlanningEnvelope.model_validate(job["input"])
                snapshot = seal_inputs(envelope)
                now = datetime.fromisoformat(job["simulation_utc"])
                start = time.perf_counter()
                result = compare_proposals(
                    snapshot,
                    current=envelope,
                    now_utc=now,
                    mode=job["mode"],
                    budget=ProposalBudget.model_validate(job["budget"]),
                )
                if result.snapshot_sha256 != snapshot.snapshot_sha256:
                    raise ValueError("Result input identity changed")
                job["compute_s"] = time.perf_counter() - start
                job["result"] = result.model_dump(mode="json")
                job["comparison"] = _comparison(result)
                status = result.final_decision.status
                if status == "feasible" and self.clock() < job["deadline_at"]:
                    start = time.perf_counter()
                    candidate = accept_hybrid(
                        result, snapshot, current=envelope, now_utc=now
                    )
                    timeline = campaign_timeline(
                        candidate.problem,
                        generate_candidates(candidate.problem),
                        candidate.plan,
                        candidate.validation,
                    )
                    accepted = dict(
                        job_id=job["id"],
                        revision=job["revision"],
                        plan_id=result.final_decision.decision_sha256,
                        snapshot_sha256=snapshot.snapshot_sha256,
                        target_id=candidate.target_id,
                        source=result.selected_source,
                        physical=candidate.physical,
                        timeline=timeline,
                        score_exact=candidate.comparison_score_exact,
                        simulation_utc=job["simulation_utc"],
                    )
                    job["validation_s"] = time.perf_counter() - start
                    status = "accepted"
            except Exception:
                # Provider paths are not reachable; nevertheless do not persist arbitrary secrets.
                status = "failed"
                job["error"] = "Planning or independent validation failed"
        with self._transaction() as db:
            live = json.loads(
                db.execute("SELECT data FROM jobs WHERE id=?", (job["id"],)).fetchone()[
                    0
                ]
            )
            session = self._session(db, job["session_id"])
            if live["status"] != "running":
                return True
            if (
                session["revision"] != job["revision"]
                or session["active_job_id"] != job["id"]
            ):
                status = "superseded"
            elif self.clock() >= job["deadline_at"]:
                status = "expired"
            if status == "accepted":
                if session["accepted"] is not None:
                    session["history"].append(session["accepted"])
                session["accepted"] = accepted
            if session["active_job_id"] == job["id"]:
                session["active_job_id"] = None
            job.update(status=status, finished_at=self.clock())
            self._save_job(db, job)
            self._save_session(db, session)
        return True

    def recover_interrupted(self):
        with self._transaction() as db:
            for job in self._jobs(db):
                if job["status"] != "running":
                    continue
                job.update(status="interrupted", finished_at=self.clock())
                self._save_job(db, job)
                session = self._session(db, job["session_id"])
                if session["active_job_id"] == job["id"]:
                    session["active_job_id"] = None
                    self._save_session(db, session)


def _comparison(result):
    return dict(
        mode=result.mode,
        preparation_s=result.preparation_s,
        baseline_s=result.classical_baseline.elapsed_s,
        selected_source=result.selected_source,
        selection_reason=result.selection_reason,
        unsupported_reason=result.unsupported_reason,
        hardware_jobs=result.hardware_jobs,
        logical_qubits=result.quantum.logical_qubits if result.quantum else None,
        candidates=[
            dict(target_id=c.target_id, status=c.status, reasons=c.reasons)
            for c in result.final_decision.candidates
        ],
        methods=[
            dict(
                method=m.method,
                proposal_s=m.proposal_s,
                raw_counts=m.raw_counts,
                raw_local_feasible_count=m.raw_local_feasible_count,
                skipped_by_completion_cap=m.skipped_by_completion_cap,
                attempts=[
                    dict(
                        option_ids=a.option_ids,
                        sample_count=a.sample_count,
                        status=a.status,
                        reason=a.reason,
                        completion_s=a.completion_s,
                        admission_s=a.admission_s,
                    )
                    for a in m.attempts
                ],
            )
            for m in result.methods
        ],
    )


def register_unified_routes(app, coordinator):
    def call(method, *args):
        try:
            return method(*args)
        except KeyError as exc:
            raise HTTPException(404, "Session not found") from exc
        except CoordinationError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, "Invalid event or planning input") from exc

    @app.post("/unified/sessions", status_code=201)
    def create_session():
        return coordinator.create()

    @app.get("/unified/sessions")
    def list_sessions():
        return coordinator.recent()

    @app.get("/unified/sessions/{session_id}")
    def get_session(session_id: str):
        return call(coordinator.get, session_id)

    @app.post("/unified/sessions/{session_id}/plans", status_code=202)
    def plan_session(session_id: str, command: PlanCommand):
        return call(coordinator.plan, session_id, command)

    @app.post("/unified/sessions/{session_id}/events", status_code=202)
    def event_session(session_id: str, command: EventCommand):
        return call(coordinator.event, session_id, command)
