"""Long requests retain useful exact entries within strict resource bounds."""

import pytest

from app import descent
from app.risk_runtime import BoundedPhysicsCache


def test_request_scan_retains_hot_prefix_and_replaces_old_context(monkeypatch):
    monkeypatch.setattr(descent, "simulate_powered_descent", lambda *a, **kw: {"x": 1})
    cache = BoundedPhysicsCache(max_entries=3, max_bytes=10000)
    profile = descent.load_profiles()["apollo-lm-11"]
    for _ in range(2):
        with cache.batch():
            for index in range(6):
                cache.simulate(profile, 1, index, 3)
    assert cache.snapshot()["entries"] == 3
    assert cache.snapshot()["dynamics"]["misses"] == 9
    assert cache.snapshot()["dynamics"]["hits"] == 3
    with cache.batch():
        for index in range(10, 16):
            cache.simulate(profile, 1, index, 3)
    with cache.batch():
        for index in range(10, 13):
            cache.simulate(profile, 1, index, 3)
    assert cache.snapshot()["dynamics"]["misses"] == 15
    assert cache.snapshot()["dynamics"]["hits"] == 6


def test_byte_limit_and_exception_release_batch_protection(monkeypatch):
    monkeypatch.setattr(
        descent, "simulate_powered_descent", lambda *a, **kw: {"x": "a" * 600}
    )
    cache = BoundedPhysicsCache(max_entries=100, max_bytes=1000)
    profile = descent.load_profiles()["apollo-lm-11"]
    with pytest.raises(ValueError):
        with cache.batch():
            for index in range(3):
                cache.simulate(profile, 1, index, 3)
                assert cache.snapshot()["retained_bytes"] <= 1000
            raise ValueError("cancel or error")
    with cache.batch():
        cache.simulate(profile, 1, 0, 3)
    assert cache.snapshot()["dynamics"]["hits"] == 1
    # Outside batch mode the normal bounded LRU remains usable.
    cache.simulate(profile, 1, 8, 3)
    cache.simulate(profile, 1, 8, 3)
    assert cache.snapshot()["dynamics"]["hits"] == 2
    assert cache.snapshot()["entries"] == 1


def test_nested_batch_is_rejected_and_outer_protection_survives(monkeypatch):
    monkeypatch.setattr(descent, "simulate_powered_descent", lambda *a, **kw: {})
    cache = BoundedPhysicsCache()
    with cache.batch():
        with pytest.raises(RuntimeError):
            with cache.batch():
                pass
    with cache.batch():
        cache.clear()
    assert cache.snapshot()["entries"] == 0
