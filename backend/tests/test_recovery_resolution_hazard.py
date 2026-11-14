"""Require both nominal integration resolutions to satisfy exclusion constraints."""

import math

from app import self_healing as recovery
from tests.test_self_healing import scenario


def test_coarse_contact_in_hazard_cannot_be_overruled_by_fine_contact(monkeypatch):
    original = recovery.propagate_terminal

    def perturbed_coarse(job, **kwargs):
        run = original(job, **kwargs)
        if kwargs.get("integration_dt_s") is None:
            last = run["trace"][-1]
            radius = math.hypot(*last["state"]["position_m"])
            angle = 0.2 / job.terrain.reference_radius_m
            last["state"]["position_m"] = [
                radius * math.sin(angle),
                radius * math.cos(angle),
            ]
            last["downrange_m"] = 0.2
        return run

    monkeypatch.setattr(recovery, "propagate_terminal", perturbed_coarse)
    case = scenario()
    target = next(t for t in case.targets if t.target_id == "primary")
    record = recovery._screen(
        case.initial_request,
        case.initial_request.initial_state,
        target,
        10000.0,
        [(0.1, 0.3)],
        recovery._draws(case.uncertainty, case.seed, 0),
        None,
    )
    assert record["numerically_consistent"]
    assert not record["accepted"]
    assert "landing_hazard" in record["reasons"]
