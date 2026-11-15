"""Offline report construction preserves source identity and geometry uncertainty."""

import json
import sys
from pathlib import Path
from copy import deepcopy
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from quantum.relay_evidence import build_geometry

ROOT = Path(__file__).resolve().parents[2]
INPUTS = json.loads((ROOT / "shared/examples/relays/inputs.json").read_text())


def test_geometry_shape_and_audited_sources():
    g = build_geometry(INPUTS, duration_s=3600, step_s=600)
    assert g["times_s"] == list(range(0, 3601, 600))
    assert len(g["orbits"]) == 4 and len(g["sites"]) == 2
    assert len(g["source_digest"]) == 64
    for rows in g["contacts"].values():
        for row in rows.values():
            assert len(row) == 6 and all(type(x) is bool for x in row)
    assert all(x["closure_error_nd"] < 2e-5 for x in g["orbit_audits"])


def test_unknown_terrain_is_not_clear_and_changes_identity():
    data = deepcopy(INPUTS)
    for s in data["sites"]:
        for d in s["directions"]:
            d["elevation_deg"] = None
    g = build_geometry(data, duration_s=600, step_s=600)
    assert all(
        x is None
        for rows in g["contacts"].values()
        for row in rows.values()
        for x in row
    )
    assert (
        g["source_digest"]
        != build_geometry(INPUTS, duration_s=600, step_s=600)["source_digest"]
    )


@pytest.mark.parametrize(
    "duration,step", [(0, 600), (691201, 600), (1000, 600), (3600, 0), (3600, True)]
)
def test_bounded_sampling(duration, step):
    with pytest.raises(ValueError):
        build_geometry(INPUTS, duration_s=duration, step_s=step)
