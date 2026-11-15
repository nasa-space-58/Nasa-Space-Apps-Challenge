"""Full no-go outcomes, persisted evidence and route registration."""

from pathlib import Path

import pytest

from app.analysis_api import create_app
from app.unified_coordinator import (
    CoordinationError,
    EventCommand,
    PlanCommand,
    UnifiedCoordinator,
)


@pytest.fixture
def store(tmp_path):
    return UnifiedCoordinator(tmp_path / "coordination.sqlite")


def test_no_feasible_event_has_no_fallback_to_old_plan(store):
    session = store.create()
    store.plan(session["id"], PlanCommand(request_id="nominal", expected_revision=0))
    store.tick()
    assert store.get(session["id"])["accepted"] is not None
    store.event(
        session["id"],
        EventCommand(
            request_id="block-all",
            event_id="block-all",
            expected_revision=0,
            elapsed_s=8.0,
            start_downrange_m=-50000.0,
            end_downrange_m=50000.0,
        ),
    )
    store.tick()
    result = store.get(session["id"])
    assert result["jobs"][0]["status"] == "no_feasible_continuation"
    assert result["accepted"] is None
    assert result["history"][0]["target_id"] == "primary"
    assert result["jobs"][0]["comparison"]["candidates"]
    assert result["active_job_id"] is None


def test_comparison_timings_are_separate_and_records_remain_local(store):
    session = store.create()
    store.event(
        session["id"],
        EventCommand(
            request_id="event",
            event_id="event",
            expected_revision=0,
            mode="local_experiment",
            elapsed_s=8.0,
            start_downrange_m=-5.0,
            end_downrange_m=5.0,
        ),
    )
    store.tick()
    view = store.get(session["id"])
    comparison = view["jobs"][0]["comparison"]
    assert comparison["preparation_s"] >= 0
    assert comparison["baseline_s"] >= 0
    assert comparison["hardware_jobs"] == 0
    assert comparison["logical_qubits"] == 1
    for method in comparison["methods"]:
        assert method["proposal_s"] >= 0
        assert all(
            a["completion_s"] >= 0 and a["admission_s"] >= 0 for a in method["attempts"]
        )
    assert view["accepted"]["revision"] == view["revision"]
    assert view["accepted"]["snapshot_sha256"] == view["snapshot_sha256"]
    assert "result" not in view["jobs"][0]
    assert "input" not in view["jobs"][0]
    with store._transaction() as db:
        stored = store._jobs(db, session["id"])[0]
    assert stored["result"]["result_sha256"]


def test_backward_event_and_reused_event_id_are_rejected(store):
    session = store.create()
    first = EventCommand(
        request_id="first",
        event_id="obstacle",
        expected_revision=0,
        elapsed_s=8.0,
        start_downrange_m=-5.0,
        end_downrange_m=5.0,
    )
    store.event(session["id"], first)
    for changes in (
        {"request_id": "second", "expected_revision": 1, "elapsed_s": 9.0},
        {
            "request_id": "second",
            "event_id": "new",
            "expected_revision": 1,
            "elapsed_s": 7.0,
        },
    ):
        with pytest.raises(CoordinationError):
            store.event(session["id"], first.model_copy(update=changes))
    assert store.get(session["id"])["revision"] == 1
    assert len(store.get(session["id"])["events"]) == 1


def test_application_registers_persistent_coordinator(tmp_path):
    app = create_app(state_dir=tmp_path, data_dir=tmp_path, start_worker=False)
    assert isinstance(app.state.unified, UnifiedCoordinator)
    assert app.state.unified.path == Path(tmp_path) / "unified.sqlite"
    assert "/unified/sessions/{session_id}/events" in {r.path for r in app.routes}
