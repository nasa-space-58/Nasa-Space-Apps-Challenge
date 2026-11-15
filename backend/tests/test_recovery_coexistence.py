"""Terrain recovery must coexist with the unchanged H5/H6 and R9 APIs."""
from fastapi.testclient import TestClient
from app.analysis_api import create_app
from app.twin import TwinRequest as ExistingTwinRequest, run_twin as existing_run
from app.terrain_twin import TwinRequest as TerrainTwinRequest, run_twin as terrain_run


def test_both_recovery_routes_and_original_twin_remain_available(tmp_path):
    app = create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/descents/self-healing" in paths
        assert "/recovery/replays" in paths
        assert "/twin/runs" in paths
        assert len([r for r in app.routes if getattr(r, "path", None) == "/twin/runs"]) == 1
        assert client.get("/recovery/examples").status_code == 200
        assert client.post("/descents/self-healing", json={}).status_code == 422


def test_isolated_stepwise_twin_preserves_nominal_original_outcome():
    payload = dict(site_id="pole", latitude_deg=-90.0, longitude_deg_east=0.0, seed=2026)
    original = existing_run(ExistingTwinRequest(**payload), 0.0)
    terrain = terrain_run(TerrainTwinRequest(**payload), 0.0)
    assert terrain == original
