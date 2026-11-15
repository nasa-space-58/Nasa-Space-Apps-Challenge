"""Real API reuse, bounded resource ownership and cooperative cancellation."""

import asyncio
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from app import descent, risk
from app.analysis_api import create_app
from app.risk import RiskCancelled, RiskRequest, run_monte_carlo
from app.risk_runtime import BoundedPhysicsCache, RiskBusy, RiskClosed, RiskRuntime
from app.surrogate_readiness import audit_readiness
from shared.constants import MOON_REFERENCE_RADIUS_M


def body(**kwargs):
    return RiskRequest(
        site_id="pole",
        latitude_deg=-90,
        longitude_deg_east=0,
        runs=10,
        seed=831,
        **kwargs,
    )


def write_dem(root, *, ramp=False, height=0.0):
    folder = Path(root) / "terrain/raw" / ("b" * 64)
    folder.mkdir(parents=True, exist_ok=True)
    size = 81
    heights = np.full((size, size), height, dtype="float32")
    if ramp:
        heights += (np.arange(size) - size // 2)[None, :] * 80.0
    path = folder / "source.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=size,
        height=size,
        count=1,
        dtype="float32",
        transform=from_origin(-size * 40, size * 40, 80, 80),
        crs=f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={MOON_REFERENCE_RADIUS_M} +units=m",
        nodata=np.nan,
    ) as dataset:
        dataset.write(heights, 1)
    (folder / "manifest.json").write_text(
        json.dumps(
            dict(
                product_id="LDEM_80S_80MPP_ADJ",
                sha256={"source.tif": hashlib.sha256(path.read_bytes()).hexdigest()},
            )
        )
    )
    return path


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(max_bytes=0),
        dict(max_bytes=True),
        dict(max_bytes=1.5),
        dict(max_entries=0),
        dict(revision=""),
    ],
)
def test_invalid_cache_configuration(kwargs):
    with pytest.raises(ValueError):
        BoundedPhysicsCache(**kwargs)


def test_payload_byte_limit_and_oversized_bypass(monkeypatch):
    monkeypatch.setattr(
        descent, "simulate_powered_descent", lambda *a, **kw: {"data": "x" * 600}
    )
    cache = BoundedPhysicsCache(max_bytes=1000, max_entries=10)
    p = descent.load_profiles()["apollo-lm-11"]
    for i in range(5):
        cache.simulate(p, 1, i, 3)
        assert cache.snapshot()["retained_bytes"] <= 1000
    assert cache.snapshot()["entries"] == 1
    small = BoundedPhysicsCache(max_bytes=8)
    small.simulate(p, 1, 2, 3)
    assert small.snapshot()["entries"] == 0
    assert small.snapshot()["bypassed_oversize"] == 1


def test_payload_copies_clear_and_namespace(monkeypatch):
    monkeypatch.setattr(
        descent, "simulate_powered_descent", lambda *a, **kw: {"x": [1.0]}
    )
    cache = BoundedPhysicsCache()
    p = descent.load_profiles()["apollo-lm-11"]
    cache.simulate(p, 1, 2, 3)["x"][0] = 7
    assert cache.simulate(p, 1, 2, 3)["x"] == [1.0]
    cache.clear(revision="v2")
    assert cache.snapshot()["entries"] == 0
    assert cache.snapshot()["retained_bytes"] == 0
    assert cache.snapshot()["revision"] == "v2"
    cache.simulate(p, 1, 2, 3)
    assert cache.snapshot()["dynamics"]["misses"] == 2


def test_cache_exception_does_not_poison_retry(monkeypatch):
    calls = []

    def engine(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("failed dynamics")
        return {"x": 1}

    monkeypatch.setattr(descent, "simulate_powered_descent", engine)
    cache = BoundedPhysicsCache()
    p = descent.load_profiles()["apollo-lm-11"]
    with pytest.raises(ValueError):
        cache.simulate(p, 1, 2, 3)
    assert cache.snapshot()["entries"] == 0
    assert cache.simulate(p, 1, 2, 3) == {"x": 1}
    assert cache.snapshot()["dynamics"]["misses"] == 2


def test_atomic_invalidation_during_computation(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def engine(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return {"x": 1}

    monkeypatch.setattr(descent, "simulate_powered_descent", engine)
    cache = BoundedPhysicsCache()
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(
            cache.simulate, descent.load_profiles()["apollo-lm-11"], 1, 2, 3
        )
        assert entered.wait(3)
        cleared = pool.submit(cache.clear, revision="v2")
        release.set()
        assert future.result(timeout=3) == {"x": 1}
        cleared.result(timeout=3)
    assert cache.snapshot()["entries"] == 0


def test_queue_cancel_and_close_release_resources(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    observed = []

    def compute(self, request_body, stop):
        observed.append(request_body.seed)
        entered.set()
        assert release.wait(3)
        if stop.is_set():
            raise RiskCancelled("cancelled")
        return {"ok": True}

    monkeypatch.setattr(RiskRuntime, "_compute", compute, raising=False)
    runtime = RiskRuntime(tmp_path, max_requests=2)
    first = runtime.submit(body())
    assert entered.wait(3)
    second = runtime.submit(body())
    with pytest.raises(RiskBusy):
        runtime.submit(body())
    second.cancel()
    first.cancel()
    release.set()
    with pytest.raises(RiskCancelled):
        first.future.result(timeout=3)
    assert second.future.cancelled()
    runtime.close()
    assert observed == [831]
    assert runtime.snapshot()["pending"] == 0
    assert runtime.snapshot()["cache"]["entries"] == 0
    with pytest.raises(RiskClosed):
        runtime.submit(body())


def test_shutdown_signals_running_worker(tmp_path, monkeypatch):
    entered = threading.Event()

    def compute(self, request_body, stop):
        entered.set()
        assert stop.wait(3)
        raise RiskCancelled("shutdown")

    monkeypatch.setattr(RiskRuntime, "_compute", compute, raising=False)
    runtime = RiskRuntime(tmp_path)
    ticket = runtime.submit(body())
    assert entered.wait(3)
    runtime.close()
    with pytest.raises(RiskCancelled):
        ticket.future.result()
    assert runtime.snapshot()["closed"] is True


def test_coroutine_cancellation_propagates_stop(tmp_path, monkeypatch):
    entered, stopped = threading.Event(), threading.Event()

    def compute(self, request_body, stop):
        entered.set()
        assert stop.wait(3)
        stopped.set()
        raise RiskCancelled("cancelled")

    monkeypatch.setattr(RiskRuntime, "_compute", compute, raising=False)
    runtime = RiskRuntime(tmp_path)

    async def exercise():
        task = asyncio.create_task(runtime.evaluate(body()))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(stopped.wait, 3)

    try:
        asyncio.run(exercise())
    finally:
        runtime.close()


def test_client_disconnect_stops_work(tmp_path, monkeypatch):
    def compute(self, request_body, stop):
        assert stop.wait(3)
        raise RiskCancelled("cancelled")

    class Disconnected:
        async def is_disconnected(self):
            return True

    monkeypatch.setattr(RiskRuntime, "_compute", compute, raising=False)
    runtime = RiskRuntime(tmp_path)
    try:
        with pytest.raises(RiskCancelled):
            asyncio.run(runtime.evaluate(body(), Disconnected()))
    finally:
        runtime.close()


def test_process_owner_guard(tmp_path, monkeypatch):
    runtime = RiskRuntime(tmp_path)
    monkeypatch.setattr("app.risk_runtime.os.getpid", lambda: -1)
    try:
        with pytest.raises(RiskClosed):
            runtime.submit(body())
    finally:
        runtime.close()


def test_cancel_before_and_between_integrations(monkeypatch):
    with pytest.raises(RiskCancelled):
        run_monte_carlo(body(), 0, cancelled=lambda: True)
    calls = []
    real = risk.simulate_powered_descent

    def simulate(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(risk, "simulate_powered_descent", simulate)
    with pytest.raises(RiskCancelled):
        run_monte_carlo(body(), 0, cancelled=lambda: bool(calls))
    assert len(calls) == 1


def test_real_api_cold_warm_disabled_and_lifecycle(tmp_path, monkeypatch):
    write_dem(tmp_path / "data")
    monkeypatch.setenv("RISK_PHYSICS_CACHE_ENABLED", "1")
    app = create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    with TestClient(app) as client:
        first = client.post("/descents/risk", json=body().model_dump(mode="json"))
        warm = client.post("/descents/risk", json=body().model_dump(mode="json"))
        assert first.status_code == warm.status_code == 200
        assert first.json() == warm.json()
        stats = app.state.risk_runtime.snapshot()["cache"]
        assert stats["dynamics"]["misses"] == 10
        assert stats["dynamics"]["hits"] == 10
        assert 0 < stats["retained_bytes"] <= 32 * 1024 * 1024
    assert app.state.risk_runtime.snapshot()["cache"]["entries"] == 0
    monkeypatch.setenv("RISK_PHYSICS_CACHE_ENABLED", "0")
    off = create_app(tmp_path / "off", tmp_path / "data", start_worker=False)
    with TestClient(off) as client:
        response = client.post("/descents/risk", json=body().model_dump(mode="json"))
        assert response.json() == first.json()
        assert off.state.risk_runtime.snapshot()["cache"]["entries"] == 0


def test_real_api_reloads_changed_verified_terrain(tmp_path):
    write_dem(tmp_path / "data")
    app = create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    with TestClient(app) as client:
        first = client.post(
            "/descents/risk", json=body().model_dump(mode="json")
        ).json()
        write_dem(tmp_path / "data", ramp=True)
        changed = client.post(
            "/descents/risk", json=body().model_dump(mode="json")
        ).json()
        assert first["failure_causes"]["hazard_slope"] == 0
        assert changed["failure_causes"]["hazard_slope"] == 10
        assert first["result_id"] != changed["result_id"]
        # The changed map has identical centre height, so dynamics can be reused.
        assert app.state.risk_runtime.snapshot()["cache"]["dynamics"]["hits"] == 10
        path = tmp_path / "data/terrain/raw" / ("b" * 64) / "source.tif"
        with path.open("ab") as stream:
            stream.write(b"changed")
        assert (
            client.post(
                "/descents/risk", json=body().model_dump(mode="json")
            ).status_code
            == 422
        )


@pytest.mark.parametrize(
    "change",
    [
        dict(orbit={"altitude_km": 60}),
        dict(seed=832),
        dict(
            custom_profile=descent.load_profiles()["apollo-lm-11"].model_dump()
            | {"isp_s": 300.0}
        ),
        dict(approach_azimuth_deg=0),
        dict(reserve_delta_v_mps=500),
        dict(tolerances={"position_m": 0.01}),
        dict(uncertainty={"navigation_sigma_m": 1000}),
    ],
)
def test_changed_settings_equal_uncached(tmp_path, change):
    write_dem(tmp_path / "data")
    runtime = RiskRuntime(tmp_path / "data")
    off = RiskRuntime(tmp_path / "data", enabled=False)
    try:
        runtime.submit(body()).future.result(timeout=15)
        changed = RiskRequest(**(body().model_dump(mode="json") | change))
        actual = runtime.submit(changed).future.result(timeout=15)
        expected = off.submit(changed).future.result(timeout=15)
        assert actual == expected
    finally:
        runtime.close()
        off.close()


def test_concurrent_real_requests_reuse_one_cache(tmp_path):
    write_dem(tmp_path / "data")
    runtime = RiskRuntime(tmp_path / "data")
    try:
        a = runtime.submit(body())
        b = runtime.submit(body())
        assert a.future.result(timeout=15) == b.future.result(timeout=15)
        assert runtime.snapshot()["cache"]["dynamics"]["misses"] == 10
        assert runtime.snapshot()["cache"]["dynamics"]["hits"] == 10
    finally:
        runtime.close()


def test_api_overflow_and_error_recovery(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def compute(self, request_body, stop):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        if len(calls) == 1:
            raise ValueError("unexpected engine failure")
        return {"ok": True}

    monkeypatch.setattr(RiskRuntime, "_compute", compute, raising=False)
    app = create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        runtime = app.state.risk_runtime
        a = runtime.submit(body())
        assert entered.wait(3)
        b = runtime.submit(body())
        assert (
            client.post(
                "/descents/risk", json=body().model_dump(mode="json")
            ).status_code
            == 429
        )
        release.set()
        with pytest.raises(ValueError):
            a.future.result(timeout=3)
        assert b.future.result(timeout=3) == {"ok": True}
        assert (
            client.post(
                "/descents/risk", json=body().model_dump(mode="json")
            ).status_code
            == 200
        )


def test_surrogate_audit_deduplicates_exposed_groups():
    record = {
        "plan_id": "p0",
        "r6_result_id": "hash",
        "success": [True] * 24,
        "miss_m": [0.0] * 24,
    }
    row = {
        "outcome_records": {"0:0": record, "1:0": record | {"r6_result_id": "other"}}
    }
    report = {
        "protocol": "physics-c2",
        "cases": [
            {"seed": 811, "methods": {"a": row, "b": row}},
            {"seed": 811, "methods": {"warm": row}},
            {"seed": 812, "methods": {"a": row}},
        ],
    }
    result = audit_readiness(report)
    assert result["independent_scenario_groups"] == 2
    assert result["unique_candidate_records"] == 4
    assert result["blind_holdout_groups"] == 0
    assert result["fit_performed"] is False
    assert result["quantum_experiment_justified"] is False
    assert result["physics_calls"] == result["circuit_evaluations"] == 0


def test_surrogate_audit_rejects_inconsistent_duplicates():
    a = {"outcome_records": {"0:0": {"plan_id": "p", "r6_result_id": "one"}}}
    b = {"outcome_records": {"0:0": {"plan_id": "p", "r6_result_id": "two"}}}
    with pytest.raises(ValueError, match="inconsistent"):
        audit_readiness(
            {
                "protocol": "physics-c2",
                "cases": [{"seed": 811, "methods": {"a": a, "b": b}}],
            }
        )


@pytest.mark.parametrize(
    "name,value",
    [
        ("RISK_PHYSICS_CACHE_ENABLED", "maybe"),
        ("RISK_PHYSICS_CACHE_BYTES", "0"),
        ("RISK_PHYSICS_CACHE_ENTRIES", "-1"),
    ],
)
def test_invalid_environment_configuration_fails_startup(
    tmp_path, monkeypatch, name, value
):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
