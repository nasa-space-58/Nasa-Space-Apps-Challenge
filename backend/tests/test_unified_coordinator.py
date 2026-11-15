"""Session revision, deadline and idempotency fences around real planning results."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.unified_coordinator as coordination
from app.unified_planning import UnifiedPlanningEnvelope, seal_inputs
from app.unified_quantum import compare_proposals

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def result():
    envelope = UnifiedPlanningEnvelope.model_validate_json(
        (ROOT / "shared/examples/unified/classical-pilot.json").read_text()
    )
    return compare_proposals(
        seal_inputs(envelope), current=envelope, now_utc=envelope.context.created_utc
    )


@pytest.fixture
def setup(tmp_path, monkeypatch, result):
    clock = [100.0]
    coordinator = coordination.UnifiedCoordinator(
        tmp_path / "sessions.sqlite", clock=lambda: clock[0]
    )
    monkeypatch.setattr(coordination, "compare_proposals", lambda *a, **k: result)
    return coordinator, clock, coordinator.create()


def command(session, key="plan-1", **kwargs):
    return coordination.PlanCommand(
        request_id=key, expected_revision=session["revision"], **kwargs
    )


def event(session, key="event-1", **kwargs):
    return coordination.EventCommand(
        request_id=key,
        event_id=key,
        expected_revision=session["revision"],
        elapsed_s=8.0,
        start_downrange_m=-5.0,
        end_downrange_m=5.0,
        **kwargs,
    )


def test_durable_acceptance_shared_identity_and_physics(setup):
    store, _, session = setup
    job = store.plan(session["id"], command(session))
    assert job["status"] == "queued"
    assert store.tick() is True
    view = store.get(session["id"])
    assert view["active_job_id"] is None
    assert view["accepted"]["job_id"] == job["id"]
    assert view["accepted"]["revision"] == session["revision"]
    assert view["accepted"]["target_id"] == "primary"
    assert view["accepted"]["plan_id"]
    assert view["accepted"]["physical"]
    assert len(view["accepted"]["timeline"]["missions"]) == 3
    assert view["jobs"][0]["status"] == "accepted"
    assert view["jobs"][0]["queue_s"] >= 0
    assert view["jobs"][0]["validation_s"] >= 0
    reopened = coordination.UnifiedCoordinator(store.path)
    assert reopened.get(session["id"])["accepted"] == view["accepted"]
    assert reopened.recent()[0]["id"] == session["id"]
    assert store.tick() is False


def test_duplicate_and_conflicting_requests(setup):
    store, _, session = setup
    first = store.plan(session["id"], command(session))
    assert store.plan(session["id"], command(session))["id"] == first["id"]
    with pytest.raises(coordination.CoordinationError):
        store.plan(session["id"], command(session, mode="local_experiment"))
    with pytest.raises(coordination.CoordinationError):
        store.plan(session["id"], command(session, "another"))
    store.tick()
    assert store.plan(session["id"], command(session))["id"] == first["id"]


def test_event_archives_plan_invalidates_work_and_is_idempotent(setup):
    store, _, session = setup
    store.plan(session["id"], command(session))
    store.tick()
    accepted = store.get(session["id"])["accepted"]
    old = store.plan(session["id"], command(session, "second"))
    submitted = store.event(session["id"], event(session))
    current = store.get(session["id"])
    assert current["revision"] == session["revision"] + 1
    assert current["accepted"] is None
    assert current["history"][-1] == accepted
    assert current["active_job_id"] == submitted["id"]
    assert (
        next(j for j in current["jobs"] if j["id"] == old["id"])["status"]
        == "superseded"
    )
    assert store.event(session["id"], event(session))["id"] == submitted["id"]
    assert len(store.get(session["id"])["events"]) == 1
    changed = event(session).model_copy(update={"end_downrange_m": 7.0})
    with pytest.raises(coordination.CoordinationError):
        store.event(session["id"], changed)
    with pytest.raises(coordination.CoordinationError):
        store.plan(session["id"], command(session, "stale"))


def test_real_event_replanning_links_three_missions(setup, monkeypatch):
    store, _, session = setup
    monkeypatch.setattr(coordination, "compare_proposals", compare_proposals)
    store.event(session["id"], event(session, mode="local_experiment"))
    store.tick()
    current = store.get(session["id"])
    assert current["jobs"][0]["status"] == "accepted"
    assert current["accepted"]["target_id"] == "remote"
    assert current["accepted"]["source"] == "classical"
    assert len(current["accepted"]["timeline"]["missions"]) == 3
    assert {m["method"] for m in current["jobs"][0]["comparison"]["methods"]} == {
        "qaoa",
        "random",
        "greedy",
    }
    assert current["jobs"][0]["comparison"]["hardware_jobs"] == 0
    assert current["simulation_utc"] == "2030-01-01T00:00:08+00:00"


def test_expired_queued_work_does_not_compute(setup, monkeypatch):
    store, clock, session = setup
    store.plan(session["id"], command(session, ttl_s=1.0))
    clock[0] += 1
    monkeypatch.setattr(
        coordination, "compare_proposals", lambda *a, **k: pytest.fail("expired work")
    )
    store.tick()
    current = store.get(session["id"])
    assert current["jobs"][0]["status"] == "expired"
    assert current["accepted"] is None
    assert current["active_job_id"] is None


def test_expiry_after_computation_and_validation_prevents_acceptance(
    setup, monkeypatch, result
):
    store, clock, session = setup
    original = coordination.accept_hybrid

    def delayed(*args, **kwargs):
        candidate = original(*args, **kwargs)
        clock[0] += 2
        return candidate

    monkeypatch.setattr(coordination, "accept_hybrid", delayed)
    store.plan(session["id"], command(session, ttl_s=1.0))
    store.tick()
    view = store.get(session["id"])
    assert view["jobs"][0]["status"] == "expired"
    assert view["accepted"] is None


def test_new_event_wins_over_running_result(setup, monkeypatch, result):
    store, _, session = setup
    entered, release = Event(), Event()

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(20)
        return result

    monkeypatch.setattr(coordination, "compare_proposals", delayed)
    old = store.plan(session["id"], command(session))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.tick)
        assert entered.wait(10)
        try:
            new = store.event(session["id"], event(session))
        finally:
            release.set()
        assert future.result() is True
    view = store.get(session["id"])
    assert view["active_job_id"] == new["id"]
    assert view["accepted"] is None
    assert (
        next(j for j in view["jobs"] if j["id"] == old["id"])["status"] == "superseded"
    )


def test_single_claim_and_shutdown_fence(setup, monkeypatch, result):
    store, _, session = setup
    entered, release = Event(), Event()
    calls = []

    def delayed(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(20)
        return result

    monkeypatch.setattr(coordination, "compare_proposals", delayed)
    store.plan(session["id"], command(session))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.tick)
        assert entered.wait(10)
        try:
            assert store.tick() is False
            reopened = coordination.UnifiedCoordinator(store.path)
            assert reopened.get(session["id"])["jobs"][0]["status"] == "running"
            store.recover_interrupted()
        finally:
            release.set()
        future.result()
    assert len(calls) == 1
    view = store.get(session["id"])
    assert view["jobs"][0]["status"] == "interrupted"
    assert view["active_job_id"] is None
    assert view["accepted"] is None


def test_invalid_event_leaves_state_unchanged(setup):
    store, _, session = setup
    before = store.get(session["id"])
    invalid = event(session).model_copy(update={"start_downrange_m": 20.0})
    with pytest.raises((coordination.CoordinationError, ValueError)):
        store.event(session["id"], invalid)
    assert store.get(session["id"]) == before


def test_old_result_cannot_be_used_for_new_inputs(setup):
    store, _, session = setup
    store.event(session["id"], event(session))
    store.tick()
    view = store.get(session["id"])
    assert view["jobs"][0]["status"] == "failed"
    assert view["accepted"] is None


def test_compute_failure_is_durable_without_retry(setup, monkeypatch):
    store, _, session = setup

    def fail(*args, **kwargs):
        raise RuntimeError("private provider message")

    monkeypatch.setattr(coordination, "compare_proposals", fail)
    store.plan(session["id"], command(session))
    store.tick()
    view = store.get(session["id"])
    assert view["jobs"][0]["status"] == "failed"
    assert "private provider" not in str(view)
    assert store.tick() is False


def test_queue_bound_and_queued_jobs_survive_recovery(setup):
    store, _, session = setup
    for index in range(8):
        item = session if index == 0 else store.create()
        store.plan(item["id"], command(item))
    extra = store.create()
    with pytest.raises(coordination.CoordinationError):
        store.plan(extra["id"], command(extra))
    store.recover_interrupted()
    assert store.get(session["id"])["jobs"][0]["status"] == "queued"


def test_api_contract_and_no_hardware_mode(setup):
    store, _, _ = setup
    app = FastAPI()
    coordination.register_unified_routes(app, store)
    with TestClient(app) as client:
        session = client.post("/unified/sessions").json()
        assert client.get("/unified/sessions").status_code == 200
        assert client.get(f"/unified/sessions/{session['id']}").status_code == 200
        payload = command(session).model_dump(mode="json")
        assert (
            client.post(
                f"/unified/sessions/{session['id']}/plans", json=payload
            ).status_code
            == 202
        )
        payload["mode"] = "hardware"
        assert (
            client.post(
                f"/unified/sessions/{session['id']}/plans", json=payload
            ).status_code
            == 422
        )
        payload["mode"] = "classical"
        payload["request_id"] = "conflict"
        assert (
            client.post(
                f"/unified/sessions/{session['id']}/plans", json=payload
            ).status_code
            == 409
        )
        assert client.get("/unified/sessions/missing").status_code == 404
        response = client.post(
            f"/unified/sessions/{session['id']}/events",
            json=event(session).model_dump(mode="json"),
        )
        assert response.status_code == 202


@pytest.mark.parametrize(
    "field,value",
    [
        ("ttl_s", 0),
        ("ttl_s", 121),
        ("ttl_s", float("nan")),
        ("expected_revision", -1),
        ("mode", "hardware"),
    ],
)
def test_command_limits(field, value):
    payload = {"request_id": "test", "expected_revision": 0, field: value}
    with pytest.raises(ValidationError):
        coordination.PlanCommand.model_validate(payload)


def test_simulation_clock_does_not_follow_wall_clock(setup):
    store, clock, session = setup
    original = datetime.fromisoformat(session["simulation_utc"])
    clock[0] += 10000
    assert (
        datetime.fromisoformat(store.get(session["id"])["simulation_utc"]) == original
    )
