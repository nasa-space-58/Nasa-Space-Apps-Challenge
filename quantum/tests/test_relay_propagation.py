"""NASA/JPL periodic-orbit reference, closure and integrator-convergence checks."""

import json
from pathlib import Path
import sys
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.relay_orbits import propagate_orbit, jacobi_constant

DATA = json.loads(
    (
        Path(__file__).resolve().parents[2] / "shared/examples/relays/inputs.json"
    ).read_text()
)


@pytest.mark.parametrize("orbit", DATA["orbits"])
def test_catalog_invariant_closure_and_refinement(orbit):
    mu = DATA["system"]["mass_ratio"]
    assert jacobi_constant(orbit["state_nd"], mu) == pytest.approx(
        orbit["jacobi"], abs=2e-12
    )
    a = propagate_orbit(orbit, DATA["system"], samples=101, rtol=1e-10, atol=1e-12)
    b = propagate_orbit(orbit, DATA["system"], samples=101, rtol=1e-12, atol=1e-14)
    assert a["closure_error_nd"] < 2e-5
    assert b["jacobi_drift"] < 1e-8
    assert np.max(np.abs(np.array(a["states_nd"]) - np.array(b["states_nd"]))) < 2e-5
    assert b["minimum_moon_radius_km"] > DATA["system"]["radius_secondary"]


@pytest.mark.parametrize("samples", [1, 100001, True])
def test_bad_sample_budget(samples):
    with pytest.raises(ValueError):
        propagate_orbit(DATA["orbits"][0], DATA["system"], samples=samples)
