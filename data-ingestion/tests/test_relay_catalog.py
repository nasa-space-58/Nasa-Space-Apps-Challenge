"""Bounded public catalog acquisition, raw preservation and schema failures."""

import hashlib
import io
import json
from urllib.error import URLError
import pytest
from app.relay_catalog import save_catalog, CATALOG_URL


def payload():
    return dict(
        signature={"version": "1.0"},
        system={"name": "Earth-Moon"},
        family="halo",
        libration_point="2",
        branch="S",
        fields=["x", "y", "z", "vx", "vy", "vz", "jacobi", "period", "stability"],
        count="1",
        data=[[0] * 9],
    )


def test_raw_catalog_preserved_and_hashed(tmp_path, monkeypatch):
    raw = json.dumps(payload()).encode()

    def fetch(url, timeout):
        assert url == CATALOG_URL and timeout == 30
        return io.BytesIO(raw)

    monkeypatch.setattr("urllib.request.urlopen", fetch)
    r = save_catalog(tmp_path)
    assert r["sha256"] == hashlib.sha256(raw).hexdigest()
    assert (tmp_path / "catalog.json").read_bytes() == raw
    assert json.loads((tmp_path / "manifest.json").read_text()) == r


@pytest.mark.parametrize(
    "change",
    [
        {"signature": {"version": "99"}},
        {"count": "0", "data": []},
        {"family": "dro"},
        {"fields": []},
    ],
)
def test_bad_catalog_not_published(tmp_path, monkeypatch, change):
    raw = json.dumps(payload() | change).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(raw))
    with pytest.raises(ValueError):
        save_catalog(tmp_path)
    assert not (tmp_path / "manifest.json").exists()


def test_network_error_propagates(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(URLError):
        save_catalog(tmp_path)
