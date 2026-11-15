"""Report integrity, display coordinates and shared admission boundary."""

import hashlib
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import research_api
from app.solver_arena import _RUN_LOCK


def test_fixed_report_allowlist(tmp_path):
    with pytest.raises(ValueError):
        research_api.read_report("../credentials", tmp_path)


def test_checksum_is_required(tmp_path):
    folder = tmp_path / "scaling"
    folder.mkdir()
    body = {"version": "h10-scaling-v1", "points": []}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (folder / "report.json").write_text(json.dumps({"sha256": digest, "payload": body}))
    assert research_api.read_report("scaling", tmp_path) == body
    body["version"] = "changed"
    (folder / "report.json").write_text(json.dumps({"sha256": digest, "payload": body}))
    with pytest.raises(ValueError):
        research_api.read_report("scaling", tmp_path)


def test_chart_coordinates_are_bounded_and_do_not_invent_timings():
    report = {
        "points": [
            dict(
                missions=3,
                options=4,
                logical_qubits=6,
                combinations="64",
                exact_median_s=0.1,
                milp_median_s=0.2,
                exact_runs=[{"complete": True}],
                fingerprint="a",
            )
        ],
        "projections": [
            dict(
                missions=8,
                options=4,
                estimated_exact_s=100,
                measurement_kind="extrapolated",
            )
        ],
    }
    result = research_api.scaling_view(report)
    assert result["points"] == report["points"]
    assert result["plots"]
    for plot in result["plots"]:
        for series in plot["series"]:
            for point in series["points"]:
                assert 0 <= point["x"] <= 100 and 0 <= point["y"] <= 100
                assert point["kind"] in ("measured", "extrapolated", "budget_limited")


def test_routes_report_corruption_as_conflict(monkeypatch):
    app = FastAPI()
    research_api.register_research_routes(app)

    def broken(*args, **kwargs):
        raise ValueError("Checksum mismatch")

    monkeypatch.setattr(research_api, "read_report", broken)
    client = TestClient(app)
    assert client.get("/research/scaling").status_code == 409
    assert client.get("/research/relays").status_code == 409


def test_relay_validation_and_busy_gate():
    app = FastAPI()
    research_api.register_research_routes(app)
    client = TestClient(app)
    assert (
        client.post("/research/relays/compare", json={"relay_count": 100}).status_code
        == 422
    )
    with _RUN_LOCK:
        assert client.post("/research/relays/compare", json={}).status_code == 429
