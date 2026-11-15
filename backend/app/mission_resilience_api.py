"""Durable bounded mission replay jobs, with cancellation and expiry fences."""
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Annotated
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field
from shared.hybrid import Contract, Identifier
from .mission_resilience import (
    MissionScenario, ReplayCancelled, fingerprint, replay_mission, resilience_examples,
)


class ReplayCommand(Contract):
    request_id: Identifier
    scenario: MissionScenario
    ttl_s: Annotated[float, Field(strict=True, gt=0, le=600)] = 120.0


class ReplayConflict(ValueError):
    pass


class MissionReplayStore:
    """One application owner, atomic claims, bounded pending queue and retention."""
    def __init__(self, path, *, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or time.time
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, command TEXT, data TEXT)")

    @contextmanager
    def transaction(self):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def _get(db, run_id):
        row = db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return json.loads(row[0])

    @staticmethod
    def _save(db, job):
        db.execute("UPDATE runs SET data=? WHERE id=?", (json.dumps(job, allow_nan=False), job['id']))

    def submit(self, command: ReplayCommand):
        c = ReplayCommand.model_validate(command.model_dump())
        with self.transaction() as db:
            row = db.execute("SELECT command,data FROM runs WHERE request_id=?", (c.request_id,)).fetchone()
            if row:
                if json.loads(row[0]) != c.model_dump(mode="json"):
                    raise ReplayConflict("Request ID already bound to different input")
                return json.loads(row[1])
            jobs = [json.loads(row[0]) for row in db.execute("SELECT data FROM runs")]
            if sum(j['status'] in ('queued', 'running') for j in jobs) >= 8:
                raise ReplayConflict("Replay queue full (maximum eight pending jobs)")
            # Retention fails closed instead of deleting idempotency history.
            if len(jobs) >= 100:
                raise ReplayConflict("Local replay archive full (100 jobs); use a new state directory")
            now = self.clock()
            job = dict(id=str(uuid4()), request_id=c.request_id, input_sha256=fingerprint(c.scenario),
                       scenario_id=c.scenario.config.scenario_id, status='queued', queued_at=now,
                       deadline_at=now+c.ttl_s, started_at=None, finished_at=None,
                       compute_s=None, result=None, error=None)
            db.execute("INSERT INTO runs VALUES (?,?,?,?)", (job['id'], c.request_id, c.model_dump_json(), json.dumps(job)))
            return job

    def get(self, run_id):
        with self.transaction() as db:
            return self._get(db, run_id)

    def recent(self):
        with self.transaction() as db:
            jobs = [json.loads(r[0]) for r in db.execute("SELECT data FROM runs ORDER BY rowid DESC LIMIT 20")]
        return [{k: v for k, v in j.items() if k != 'result'} for j in jobs]

    def cancel(self, run_id):
        with self.transaction() as db:
            job = self._get(db, run_id)
            if job['status'] in ('queued', 'running'):
                job.update(status='cancelled', finished_at=self.clock(), result=None)
                self._save(db, job)
            return job

    def tick(self, *, runner=None):
        runner = runner or replay_mission
        with self.transaction() as db:
            rows = db.execute("SELECT command,data FROM runs ORDER BY rowid").fetchall()
            job, command = None, None
            # A second worker cannot concurrently run the same store's queue.
            if any(json.loads(row[1])['status'] == 'running' for row in rows):
                return False
            for raw, payload in rows:
                item = json.loads(payload)
                if item['status'] == 'queued':
                    job, command = item, ReplayCommand.model_validate_json(raw)
                    break
            if job is None:
                return False
            if self.clock() >= job['deadline_at']:
                job.update(status='expired', finished_at=self.clock())
                self._save(db, job)
                return True
            job.update(status='running', started_at=self.clock())
            self._save(db, job)
        def stopped():
            live = self.get(job['id'])
            return live['status'] != 'running' or self.clock() >= job['deadline_at']
        start = time.perf_counter()
        output, status, error = None, 'completed', None
        try:
            output = runner(command.scenario, cancelled=stopped)
            if output.get('input_sha256') != job['input_sha256']:
                raise ValueError('Replay input fingerprint mismatch')
        except ReplayCancelled:
            status = 'cancelled'
        except Exception as exc:
            logging.getLogger(__name__).exception('Mission replay failed')
            status, error = 'failed', type(exc).__name__ + ': replay failed; inspect local service log'
        with self.transaction() as db:
            live = self._get(db, job['id'])
            if live['status'] != 'running':
                return True
            if self.clock() >= job['deadline_at']:
                status = 'expired'
            live.update(status=status, error=error, finished_at=self.clock(),
                        compute_s=time.perf_counter()-start,
                        result=output if status == 'completed' else None)
            self._save(db, live)
        return True

    def recover_interrupted(self):
        with self.transaction() as db:
            for row in db.execute("SELECT data FROM runs").fetchall():
                job = json.loads(row[0])
                if job['status'] == 'running':
                    job.update(status='interrupted', finished_at=self.clock(), result=None)
                    self._save(db, job)


def register_resilience_routes(app, store):
    def call(method, *args):
        try:
            return method(*args)
        except KeyError as exc:
            raise HTTPException(404, 'Replay not found') from exc
        except ReplayConflict as exc:
            raise HTTPException(409, str(exc)) from exc
    @app.get('/resilience/examples')
    def examples():
        return resilience_examples()
    @app.get('/resilience/runs')
    def recent():
        return store.recent()
    @app.post('/resilience/runs', status_code=202)
    def submit(command: ReplayCommand):
        return call(store.submit, command)
    @app.get('/resilience/runs/{run_id}')
    def get(run_id: str):
        return call(store.get, run_id)
    @app.post('/resilience/runs/{run_id}/cancel')
    def cancel(run_id: str):
        return call(store.cancel, run_id)
