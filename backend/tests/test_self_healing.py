"""R12 self-healing landing: event-triggered replanning on the R9 twin.

References for expected values:
- R4 Apollo 11 profile timeline (TN D-6846 gates): braking 0-506 s, approach
  506-606 s, terminal descent 606-714 s; the default decision deadline is low
  gate + 10 s = 616 s.
- Minimum-energy lateral divert: delta-v 3|c|/T (R7).
- A k-sigma margin rejects a hazard closer than k times the navigation
  uncertainty; with perfect sensing the margin is zero.
"""

import hashlib
import json
import math

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from pydantic import ValidationError
from rasterio.transform import from_origin

from app.alternatives import LocalTerrain
from app.analysis_api import create_app
from app.descent import (
    PDI_ALTITUDE_M,
    DescentRequest,
    hohmann_deorbit,
    load_profiles,
    plan_descent,
)
from app.self_healing import (
    ACTIONS,
    CONVERGENCE_STEPS_S,
    HealingRequest,
    convergence_check,
    default_rank,
    evaluate_option,
    margin_ok,
    run_self_healing,
)
from app.twin import SensorSettings, TwinFlight
from shared.constants import MOON_REFERENCE_RADIUS_M as R

STEREO = f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m +no_defs"
POLE = dict(site_id="pole", latitude_deg=-90.0, longitude_deg_east=0.0)
PERFECT = dict(
    position_sigma_m=0.0, speed_sigma_mps=0.0, gain=1.0, thrust_noise_fraction=0.0
)
SMALL = dict(
    search_radius_m=1500.0,
    approach_azimuth_deg=0.0,
    safety={"landing_radius_m": 40.0, "hazard_search_m": 200.0},
)
APOLLO = load_profiles()["apollo-lm-11"]
OBSTACLE = {
    "kind": "obstacle_detected",
    "t_s": 560.0,
    "down_m": 0.0,
    "cross_m": 0.0,
    "radius_m": 150.0,
}


def request(events=(), **changes):
    body = POLE | SMALL | {"seed": 1, "sensor": PERFECT, "events": list(events)}
    return HealingRequest(**(body | changes))


def flat(size=101):
    half = size * 80 / 2
    return LocalTerrain(
        heights_m=np.zeros((size, size)),
        transform=from_origin(-half, half, 80, 80),
        site_xy_m=(0.0, 0.0),
        north_unit_xy=(0.0, 1.0),
        east_unit_xy=(1.0, 0.0),
        pixel_m=80.0,
        crs=STEREO,
    )


def heal(events=(), select_fn=None, **changes):
    return run_self_healing(request(events, **changes), 0.0, flat(), select_fn)


def acted(result):
    return [d for d in result["decisions"] if d["action"] != "stale_result_rejected"]


@pytest.fixture(scope="module")
def nominal():
    plan = plan_descent(DescentRequest(**POLE), 0.0)
    speed = hohmann_deorbit(R + 111120.0, R + PDI_ALTITUDE_M)["periapsis_speed_mps"]
    return plan["pdi_range_m"], speed


@pytest.fixture(scope="module")
def high_gate(nominal):
    pdi, speed = nominal
    flight = TwinFlight(APOLLO, R, pdi, speed, SensorSettings(**PERFECT), [], 1)
    while flight.phase == "braking":
        flight.step()
    return flight


# ------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "changes",
    [
        {"max_evaluations": 0},
        {"max_evaluations": 2.0},
        {"max_candidates": 13},
        {"k_sigma": -1.0},
        {"solver_latency_s": -1.0},
        {"search_radius_m": 100.0},
        {"unknown": 1},
    ],
)
def test_invalid_requests(changes):
    with pytest.raises(ValidationError):
        HealingRequest(**(POLE | {"seed": 1} | changes))


def test_defaults_and_actions():
    body = HealingRequest(**POLE, seed=1)
    assert body.k_sigma == 3.0
    assert body.max_evaluations == 12
    assert body.solver_latency_s == 0.0
    assert body.decision_deadline_s is None
    assert set(ACTIONS) >= {"continue", "retarget", "abort_required"}


# ------------------------------------------------------------ pieces


def test_margin_ok():
    assert margin_ok(None, 10.0, 3.0) is True
    assert margin_ok(31.0, 10.0, 3.0) is True
    assert margin_ok(29.0, 10.0, 3.0) is False
    assert margin_ok(0.0, 10.0, 0.0) is True
    assert margin_ok(5.0, 0.0, 3.0) is True


def test_convergence_with_time_step(nominal):
    pdi, speed = nominal
    rows = convergence_check(APOLLO, R, pdi, speed)
    assert [r["step_s"] for r in rows] == list(CONVERGENCE_STEPS_S)
    finest = rows[-1]
    gaps = [abs(r["final_mass_kg"] - finest["final_mass_kg"]) for r in rows[:-1]]
    assert gaps == sorted(gaps, reverse=True)
    assert gaps[-1] < 1.0
    assert all(r["position_error_m"] < 5.0 for r in rows)


def test_default_rank():
    options = [
        {"id": "B", "safety_score": 0.9, "total_delta_v_mps": 20.0},
        {"id": "A", "safety_score": 0.9, "total_delta_v_mps": 10.0},
        {"id": "C", "safety_score": 0.95, "total_delta_v_mps": 50.0},
        {"id": "D", "safety_score": 0.9, "total_delta_v_mps": 10.0},
    ]
    assert [o["id"] for o in default_rank(options, {})] == ["C", "A", "D", "B"]


def test_evaluate_option_from_high_gate(high_gate):
    state = high_gate.estimated_state()
    gates = high_gate.remaining_gates()
    body = request()
    here = evaluate_option(
        state, gates, {"id": "HERE", "down_m": 0.0, "cross_m": 0.0,
                       "target_height_m": 0.0, "hazard_clearance_m": None},
        body, [], (0.0, 0.0), 3.0, 0.2,
    )  # fmt: skip
    assert here["reachable"] is True and here["reasons"] == []
    assert here["lateral_delta_v_mps"] == 0.0
    assert here["touchdown"]["position_error_m"] < 5.0
    side = evaluate_option(
        state, gates, {"id": "SIDE", "down_m": 300.0, "cross_m": 600.0,
                       "target_height_m": 0.0, "hazard_clearance_m": None},
        body, [], (0.0, 0.0), 3.0, 0.2,
    )  # fmt: skip
    assert side["reachable"] is True
    assert side["lateral_delta_v_mps"] > 0
    assert side["propellant_remaining_kg"] < here["propellant_remaining_kg"]
    far = evaluate_option(
        state, gates, {"id": "FAR", "down_m": 0.0, "cross_m": 60000.0,
                       "target_height_m": 0.0, "hazard_clearance_m": None},
        body, [], (0.0, 0.0), 3.0, 0.2,
    )  # fmt: skip
    assert far["reachable"] is False
    assert {r["reason"] for r in far["reasons"]} & {"thrust", "reserve", "propellant"}


def test_evaluate_option_obstacles_and_margins(high_gate):
    state = high_gate.estimated_state() | {"estimate_std_m": 50.0}
    gates = high_gate.remaining_gates()
    body = request()
    obstacle = [{"down_m": 0.0, "cross_m": 0.0, "radius_m": 150.0}]
    inside = evaluate_option(
        state, gates, {"id": "IN", "down_m": 0.0, "cross_m": 0.0,
                       "target_height_m": 0.0, "hazard_clearance_m": None},
        body, obstacle, (0.0, 0.0), 3.0, 0.2,
    )  # fmt: skip
    assert "obstacle" in {r["reason"] for r in inside["reasons"]}
    near = evaluate_option(
        state, gates, {"id": "NEAR", "down_m": 0.0, "cross_m": 250.0,
                       "target_height_m": 0.0, "hazard_clearance_m": None},
        body, obstacle, (0.0, 0.0), 3.0, 0.2,
    )  # fmt: skip
    assert "uncertainty_margin" in {r["reason"] for r in near["reasons"]}
    clear = evaluate_option(
        state, gates, {"id": "OK", "down_m": 0.0, "cross_m": 400.0,
                       "target_height_m": 0.0, "hazard_clearance_m": 200.0},
        body, obstacle, (0.0, 0.0), 3.0, 0.2,
    )  # fmt: skip
    assert clear["reachable"] is True
    hazard = evaluate_option(
        state, gates, {"id": "HZ", "down_m": 0.0, "cross_m": 400.0,
                       "target_height_m": 0.0, "hazard_clearance_m": 100.0},
        body, obstacle, (0.0, 0.0), 3.0, 0.2,
    )  # fmt: skip
    assert "uncertainty_margin" in {r["reason"] for r in hazard["reasons"]}
    # Obstacle coordinates are in the original frame: after a retarget to
    # (0, 400) the same obstacle lies 400 m away from the new target.
    shifted = evaluate_option(
        state, gates, {"id": "S", "down_m": 0.0, "cross_m": 0.0,
                       "target_height_m": 0.0, "hazard_clearance_m": None},
        body, obstacle, (0.0, 400.0), 3.0, 0.2,
    )  # fmt: skip
    assert shifted["reachable"] is True


# ------------------------------------------------------------ scenarios


@pytest.fixture(scope="module")
def quiet():
    return heal()


def test_no_events_means_no_decisions(quiet):
    assert quiet["decisions"] == []
    assert quiet["replanned"]["final"] == quiet["original"]["final"]
    assert quiet["replanned"]["success"] is True
    assert quiet["replanned"]["target_offset_m"] == {"down_m": 0.0, "cross_m": 0.0}
    assert len(quiet["result_id"]) == 64
    assert quiet["assumptions"]
    labels = [b["label"] for b in quiet["backups_over_time"]]
    assert "high_gate" in labels and "low_gate" in labels


@pytest.fixture(scope="module")
def obstacle_case():
    return heal([OBSTACLE])


def test_obstacle_on_target_triggers_a_validated_retarget(obstacle_case):
    result = obstacle_case
    decision = acted(result)[0]
    assert decision["action"] == "retarget"
    assert "obstacle_detected" in decision["trigger"]
    assert decision["chosen"] is not None
    assert any(
        v["id"] == decision["chosen"] and v["passed"] for v in decision["validation"]
    )
    offset = result["replanned"]["target_offset_m"]
    assert math.hypot(offset["down_m"], offset["cross_m"]) >= 150.0
    assert result["replanned"]["success"] is True
    assert result["original"]["success"] is False
    assert "obstacle" in {r["reason"] for r in result["original"]["reasons"]}
    assert decision["evaluations"] <= decision["budget"]
    assert decision["solver_wall_ms"] >= 0
    assert decision["explanation"]
    considered_ids = [c["id"] for c in decision["considered"]]
    assert considered_ids[0] == "CURRENT"
    current = decision["considered"][0]
    assert current["reachable"] is False


def test_backups_over_time_track_options(obstacle_case):
    entries = obstacle_case["backups_over_time"]
    assert [e["t_s"] for e in entries] == sorted(e["t_s"] for e in entries)
    decision = [e for e in entries if e["label"] == "decision"]
    assert decision and decision[0]["reachable"] >= 1
    for entry in entries:
        assert 0 <= entry["reachable"] <= entry["evaluated"]


def test_moderate_thrust_loss_continues():
    result = heal([{"kind": "thrust_loss", "t_s": 520.0, "available_fraction": 0.85}])
    decision = acted(result)[0]
    assert decision["action"] == "continue"
    assert decision["evaluations"] == 1
    assert result["replanned"]["target_offset_m"] == {"down_m": 0.0, "cross_m": 0.0}
    assert result["replanned"]["success"] is True
    assert result["replanned"]["plan_valid"] is True


def test_unrecoverable_faults_require_abort():
    result = heal(
        [
            {"kind": "thrust_loss", "t_s": 550.0, "available_fraction": 0.5},
            {"kind": "propellant_leak", "t_s": 550.0, "leaked_kg": 1500.0},
        ]
    )
    decision = acted(result)[0]
    assert decision["action"] == "abort_required"
    assert decision["chosen"] is None
    assert decision["considered"]
    assert all(c["reasons"] for c in decision["considered"])
    assert result["replanned"]["abort_required"] is True
    assert result["replanned"]["success"] is False


def test_degraded_sensor_widens_the_margin(obstacle_case):
    degraded = heal(
        [
            OBSTACLE,
            {"kind": "sensor_degradation", "t_s": 560.0, "health": "degraded",
             "position_sigma_m": 150.0},
        ]
    )  # fmt: skip

    def margin_rejections(result):
        decision = acted(result)[0]
        return sum(
            "uncertainty_margin" in {r["reason"] for r in c["reasons"]}
            for c in decision["considered"]
        )

    assert margin_rejections(obstacle_case) == 0
    assert margin_rejections(degraded) > 0
    assert acted(degraded)[0]["margin_m"] == pytest.approx(450.0)


def test_stale_results_are_rejected_and_recomputed():
    result = heal(
        [OBSTACLE, {"kind": "thrust_loss", "t_s": 562.0, "available_fraction": 0.9}],
        solver_latency_s=5.0,
    )
    actions = [d["action"] for d in result["decisions"]]
    assert "stale_result_rejected" in actions
    stale = next(
        d for d in result["decisions"] if d["action"] == "stale_result_rejected"
    )
    final = acted(result)[-1]
    assert final["action"] in ("retarget", "continue")
    assert final["state_revision"] > stale["state_revision"]
    assert final["applied_at_s"] >= final["t_s"] + 5.0 - 1e-9


def test_exhausted_budget_keeps_the_current_plan():
    result = heal([OBSTACLE], max_evaluations=1)
    decision = acted(result)[0]
    assert decision["action"] == "budget_exhausted"
    assert decision["evaluations"] == 1
    assert decision["chosen"] is None
    assert result["replanned"]["target_offset_m"] == {"down_m": 0.0, "cross_m": 0.0}


def test_validation_catches_a_bad_selection():
    def bad_first(options, state):
        bad = {
            "id": "BAD",
            "down_m": 0.0,
            "cross_m": 60000.0,
            "target_height_m": 0.0,
            "hazard_clearance_m": None,
            "safety_score": 1.0,
            "total_delta_v_mps": 0.0,
        }
        return [bad] + default_rank(options, state)

    result = heal([OBSTACLE], select_fn=bad_first)
    decision = acted(result)[0]
    assert any(v["id"] == "BAD" and not v["passed"] for v in decision["validation"])
    assert decision["chosen"] not in (None, "BAD")
    assert decision["action"] == "retarget"


def test_selector_hook_is_used():
    def farthest(options, state):
        return sorted(options, key=lambda o: -math.hypot(o["down_m"], o["cross_m"]))

    default = acted(heal([OBSTACLE]))[0]
    custom = acted(heal([OBSTACLE], select_fn=farthest))[0]
    reachable = [
        c for c in custom["considered"] if c["reachable"] and c["id"] != "CURRENT"
    ]
    farthest_id = max(reachable, key=lambda c: math.hypot(c["down_m"], c["cross_m"]))[
        "id"
    ]
    assert custom["chosen"] == farthest_id
    assert default["chosen"] != custom["chosen"] or len(reachable) == 1


def test_events_after_the_deadline_are_not_replanned():
    late = dict(OBSTACLE, t_s=650.0)
    result = heal([late])
    decision = acted(result)[0]
    assert decision["action"] == "no_time_to_replan"
    assert result["replanned"]["final"] == result["original"]["final"]


def test_replanning_is_deterministic(obstacle_case):
    # Wall-clock solver time is reported but excluded from the result ID.
    again = heal([OBSTACLE])
    assert again["result_id"] == obstacle_case["result_id"]

    def strip(decisions):
        return [
            {k: v for k, v in d.items() if k != "solver_wall_ms"} for d in decisions
        ]

    assert strip(again["decisions"]) == strip(obstacle_case["decisions"])
    assert again["replanned"] == obstacle_case["replanned"]


# ------------------------------------------------------------ API


def write_dem(root, value=0.0, size=81):
    folder = root / "terrain/raw" / ("a" * 64)
    folder.mkdir(parents=True)
    path = folder / "source.tif"
    half = size * 80 / 2
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=1,
        dtype="float32", crs=f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m",
        transform=from_origin(-half, half, 80, 80), nodata=np.nan,
    ) as ds:  # fmt: skip
        ds.write(np.full((size, size), value, dtype="float32"), 1)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "product_id": "LDEM_80S_80MPP_ADJ",
                "sha256": {"source.tif": hashlib.sha256(path.read_bytes()).hexdigest()},
            }
        )
    )


def test_route(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as client:
        payload = POLE | SMALL | {"seed": 2, "sensor": PERFECT, "events": [OBSTACLE]}
        assert client.post("/descents/self-healing", json=payload).status_code == 409
        write_dem(tmp_path / "data")
        response = client.post("/descents/self-healing", json=payload)
        assert response.status_code == 200
        result = response.json()
        assert result["decisions"]
        for change in (
            {"profile_id": "missing"},
            {"latitude_deg": -85.0},
            {"k_sigma": -1},
        ):
            assert (
                client.post("/descents/self-healing", json=payload | change).status_code
                == 422
            )
