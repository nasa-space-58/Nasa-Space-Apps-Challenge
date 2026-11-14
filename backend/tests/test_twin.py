"""R9 descent digital twin: truth, observations, estimates, events, replay.

References for expected values:
- Constant-gain filter on white measurement noise: steady-state error
  sigma * sqrt(K / (2 - K)) (alpha filter; e.g. Brown & Hwang, Introduction to
  Random Signals and Applied Kalman Filtering, 4th ed., ch. 7).
- R4 `fly_gates` is the reference trajectory: with perfect sensing, unit gain,
  no engine bias and no events the twin must reproduce it exactly.
- Apollo 11 LM terminal descent (~12 kN thrust, Isp 309.7 s) burns about
  4 kg/s; a 100 kg leak in the last ~60 s saves only a few kg of propellant.
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

from app.analysis_api import create_app
from app.descent import (
    PDI_ALTITUDE_M,
    DescentRequest,
    _state,
    fly_gates,
    gate_states,
    hohmann_deorbit,
    load_profiles,
    plan_descent,
)
from app.twin import (
    MAX_TIMELINE_ROWS,
    SensorSettings,
    TwinError,
    TwinRequest,
    canonical_hash,
    correct,
    estimate_sigma,
    fly_twin,
    observe,
    run_twin,
)
from shared.constants import MOON_REFERENCE_RADIUS_M as R

POLE = dict(site_id="pole", latitude_deg=-90.0, longitude_deg_east=0.0)
APOLLO = load_profiles()["apollo-lm-11"]
PERFECT = SensorSettings(
    position_sigma_m=0.0, speed_sigma_mps=0.0, gain=1.0, thrust_noise_fraction=0.0
)


@pytest.fixture(scope="module")
def nominal():
    plan = plan_descent(DescentRequest(**POLE), 0.0)
    speed = hohmann_deorbit(R + 111120.0, R + PDI_ALTITUDE_M)["periapsis_speed_mps"]
    return plan["pdi_range_m"], speed


def fly(nominal, sensor=PERFECT, events=(), seed=1, **kwargs):
    pdi, speed = nominal
    return fly_twin(APOLLO, R, pdi, speed, sensor, list(events), seed, **kwargs)


def request(**changes):
    return TwinRequest(**(POLE | {"seed": 1} | changes))


# ------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": -1},
        {"step_s": 0.01},
        {"sensor": {"gain": 0.0}},
        {"sensor": {"gain": 1.5}},
        {"sensor": {"position_sigma_m": -1.0}},
        {"events": [{"kind": "meteor", "t_s": 10}]},
        {"events": [{"kind": "thrust_loss", "t_s": 10, "available_fraction": 0.0}]},
        {"events": [{"kind": "propellant_leak", "t_s": 10, "leaked_kg": -5}]},
        {"events": [{"kind": "thrust_loss", "t_s": 1, "available_fraction": 0.5}] * 21},
        {"plan_dependencies": ["gravity"]},
        {"extra": 1},
    ],
)
def test_invalid_requests(changes):
    with pytest.raises(ValidationError):
        TwinRequest(**(POLE | {"seed": 1} | changes))


def test_seed_is_required_and_defaults():
    with pytest.raises(ValidationError):
        TwinRequest(**POLE)
    body = request()
    assert body.sensor.position_sigma_m == 5.0
    assert 0 < body.sensor.gain <= 1
    assert body.events == []
    assert "ground_uplink" not in body.plan_dependencies


# ------------------------------------------------------------ estimator pieces


def test_estimate_sigma_formula():
    assert estimate_sigma(10.0, 1.0) == pytest.approx(10.0)
    assert estimate_sigma(10.0, 0.5) == pytest.approx(10.0 * math.sqrt(1 / 3))
    assert estimate_sigma(0.0, 0.3) == 0.0


def test_correct_blends_and_copies_exactly():
    estimate = np.array([1.0, 2.0])
    observation = np.array([3.0, 6.0])
    assert np.array_equal(correct(estimate, observation, 1.0), observation)
    assert np.allclose(correct(estimate, observation, 0.5), [2.0, 4.0])
    assert np.allclose(correct(estimate, observation, 1e-9), estimate, atol=1e-8)


def test_observe_zero_noise_is_exact_and_lost_is_none():
    rng = np.random.default_rng(0)
    position = np.array([10.0, R + 100.0])
    velocity = np.array([50.0, -3.0])
    pos, vel = observe(position, velocity, "nominal", 0.0, 0.0, rng)
    assert np.array_equal(pos, position) and np.array_equal(vel, velocity)
    assert observe(position, velocity, "lost", 5.0, 0.5, rng) is None


def test_observe_noise_matches_sigma_and_draw_count_is_fixed():
    position = np.array([0.0, R])
    velocity = np.array([10.0, 0.0])
    rng = np.random.default_rng(42)
    errors = np.array(
        [
            observe(position, velocity, "nominal", 10.0, 1.0, rng)[0] - position
            for _ in range(4000)
        ]
    )
    # Site on +y: altitude noise is along y, downrange noise along x.
    assert np.std(errors[:, 1]) == pytest.approx(10.0, rel=0.1)
    assert np.std(errors[:, 0]) == pytest.approx(10.0, rel=0.1)
    assert abs(np.mean(errors[:, 1])) < 1.0
    a, b = np.random.default_rng(7), np.random.default_rng(7)
    observe(position, velocity, "lost", 10.0, 1.0, a)
    observe(position, velocity, "nominal", 10.0, 1.0, b)
    assert a.standard_normal() == b.standard_normal()


def test_canonical_hash_is_order_independent():
    assert canonical_hash({"a": 1, "b": [1, 2]}) == canonical_hash(
        {"b": [1, 2], "a": 1}
    )
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})
    assert len(canonical_hash({})) == 64


# ------------------------------------------------------------ flight


def test_perfect_twin_reproduces_fly_gates(nominal):
    pdi, speed = nominal
    position, velocity = _state(R, -pdi, PDI_ALTITUDE_M, speed, 0.0)
    reference = fly_gates(
        APOLLO, R, position, velocity, APOLLO.mass_at_pdi_kg, gate_states(R), 0.1,
        record=False,
    )  # fmt: skip
    twin = fly(nominal, step_s=0.1)
    for key in (
        "position_error_m",
        "vertical_speed_mps",
        "horizontal_speed_mps",
        "mass_kg",
    ):
        assert twin["final"][key] == pytest.approx(reference["final"][key], abs=1e-9)
    assert twin["propellant_exhausted"] is reference["propellant_exhausted"]
    assert twin["max_estimate_error_m"] == 0.0
    assert twin["plan"]["valid"] is True


def test_rows_streams_and_labels(nominal):
    run = fly(nominal, sensor=SensorSettings(), record_every=10)
    rows = run["rows"]
    assert rows[-1]["phase"] == "landed"
    for row in rows:
        assert row["record_label"] == "simulated"
        assert set(row["truth"]) == {
            "downrange_m", "altitude_m", "vertical_speed_mps", "horizontal_speed_mps"
        }  # fmt: skip
        assert set(row["estimate"]) == set(row["truth"])
        assert row["sensor_health"] == "nominal"
        assert row["observation"] is not None
        assert row["estimate_error_m"] >= 0
        assert row["propellant_kg"] >= 0
    assert run["max_estimate_error_m"] > 0
    assert rows[0]["battery_energy_j"] > rows[-1]["battery_energy_j"]


def test_same_seed_replays_identically_and_seed_matters(nominal):
    events = [{"kind": "thrust_loss", "t_s": 300.0, "available_fraction": 0.9}]
    first = fly(nominal, SensorSettings(), events, seed=5)
    second = fly(nominal, SensorSettings(), events, seed=5)
    other = fly(nominal, SensorSettings(), events, seed=6)
    assert first["log_hash"] == second["log_hash"]
    assert first["log"] == second["log"]
    assert first["log_hash"] != other["log_hash"]


def test_lost_sensor_lets_the_estimate_drift(nominal):
    sensor = SensorSettings(thrust_noise_fraction=0.02)
    lost = [{"kind": "sensor_degradation", "t_s": 450.0, "health": "lost"}]
    drift, normal = [], []
    for seed in range(5):
        with_loss = fly(nominal, sensor, lost, seed=seed, record_every=20)
        without = fly(nominal, sensor, [], seed=seed, record_every=20)
        drift.append(with_loss["final_estimate_error_m"])
        normal.append(without["final_estimate_error_m"])
        after = [r for r in with_loss["rows"] if r["t_s"] >= 450.0]
        assert all(r["sensor_health"] == "lost" for r in after)
        assert all(r["observation"] is None for r in after)
        stds = [r["estimate_std_m"] for r in after]
        assert stds == sorted(stds)
    assert np.mean(drift) > 2 * np.mean(normal)


def test_thrust_loss_changes_the_flight_and_invalidates_the_plan(nominal):
    base = fly(nominal)
    weak = fly(
        nominal,
        events=[{"kind": "thrust_loss", "t_s": 100.0, "available_fraction": 0.7}],
    )
    saturated = lambda run: sum(p["saturated_s"] for p in run["phases"])  # noqa: E731
    assert saturated(weak) > saturated(base)
    assert weak["plan"]["valid"] is False
    invalid = [e for e in weak["log"] if e["kind"] == "plan_invalidated"]
    assert invalid and invalid[0]["dependency"] == "available_thrust"
    assert any(r["available_thrust_n"] < APOLLO.max_thrust_n for r in weak["rows"])


def test_leak_costs_about_the_leaked_mass(nominal):
    base = fly(nominal)
    leak = fly(
        nominal, events=[{"kind": "propellant_leak", "t_s": 650.0, "leaked_kg": 100.0}]
    )
    lost = base["propellant_remaining_kg"] - leak["propellant_remaining_kg"]
    assert 90.0 < lost <= 100.0 + 1e-6
    assert leak["propellant_exhausted"] is False


def test_huge_leak_exhausts_propellant(nominal):
    run = fly(
        nominal, events=[{"kind": "propellant_leak", "t_s": 200.0, "leaked_kg": 5000.0}]
    )
    assert run["propellant_exhausted"] is True
    assert run["propellant_remaining_kg"] == pytest.approx(0.0, abs=1e-6)


def test_events_invalidate_only_dependent_plans(nominal):
    events = [
        {"kind": "thrust_loss", "t_s": 100.0, "available_fraction": 0.95},
        {"kind": "comm_outage", "t_s": 150.0, "duration_s": 60.0},
        {"kind": "propellant_leak", "t_s": 200.0, "leaked_kg": 10.0},
    ]
    run = fly(nominal, events=events, plan_dependencies=("propellant",))
    kinds = [(e["kind"], e.get("event_kind")) for e in run["log"]]
    assert ("no_plan_effect", "thrust_loss") in kinds
    assert ("no_plan_effect", "comm_outage") in kinds
    assert ("plan_invalidated", "propellant_leak") in kinds
    assert run["plan"]["invalidated_by"] == ["propellant"]
    default = fly(nominal, events=[events[1]])
    assert default["plan"]["valid"] is True


def test_obstacles_are_synthetic_and_only_covering_ones_invalidate(nominal):
    away = fly(
        nominal,
        events=[{"kind": "obstacle_detected", "t_s": 600.0, "down_m": 3000.0,
                 "cross_m": 0.0, "radius_m": 100.0}],
    )  # fmt: skip
    assert away["plan"]["valid"] is True
    assert away["obstacles"][0]["label"] == "synthetic"
    assert away["obstacles"][0]["covers_target"] is False
    covering = fly(
        nominal,
        events=[{"kind": "obstacle_detected", "t_s": 600.0, "down_m": 20.0,
                 "cross_m": -10.0, "radius_m": 50.0}],
    )  # fmt: skip
    assert covering["obstacles"][0]["covers_target"] is True
    assert covering["plan"]["valid"] is False
    assert covering["plan"]["invalidated_by"] == ["target_hazard"]


def test_events_are_logged_in_time_order_with_revisions(nominal):
    events = [
        {"kind": "propellant_leak", "t_s": 400.0, "leaked_kg": 5.0},
        {"kind": "thrust_loss", "t_s": 100.0, "available_fraction": 0.95},
        {"kind": "comm_outage", "t_s": 5000.0 / 2, "duration_s": 10.0},
    ]
    run = fly(nominal, events=events)
    applied = [e for e in run["log"] if e["kind"] == "event_applied"]
    assert [e["event"]["kind"] for e in applied] == ["thrust_loss", "propellant_leak"]
    assert [e["state_revision"] for e in applied] == [1, 2]
    assert applied[0]["t_s"] >= 100.0
    late = [e for e in run["log"] if e["kind"] == "event_not_reached"]
    assert late and late[0]["event"]["kind"] == "comm_outage"
    assert run["log"][-1]["kind"] == "event_not_reached" or any(
        e["kind"] == "touchdown" for e in run["log"]
    )


# ------------------------------------------------------------ run_twin


@pytest.fixture(scope="module")
def quiet():
    return run_twin(request(sensor=PERFECT.model_dump()), 0.0)


def test_no_events_has_zero_differences(quiet):
    assert all(value == 0.0 for value in quiet["differences"].values())
    assert quiet["run"]["log_hash"] == quiet["baseline"]["log_hash"]
    assert quiet["run"]["touchdown_check"]["success"] is True
    assert len(quiet["run"]["timeline"]) <= MAX_TIMELINE_ROWS
    assert quiet["run"]["timeline"][-1]["phase"] == "landed"
    assert len(quiet["result_id"]) == 64
    assert quiet["assumptions"]


def test_event_run_differs_from_baseline():
    result = run_twin(
        request(events=[{"kind": "propellant_leak", "t_s": 650.0, "leaked_kg": 100.0}],
                sensor=PERFECT.model_dump()),
        0.0,
    )  # fmt: skip
    assert result["differences"]["propellant_remaining_kg"] < -90.0
    assert result["run"]["plan"]["valid"] is False
    assert result["baseline"]["touchdown_check"]["success"] is True


def test_run_twin_is_deterministic(quiet):
    assert run_twin(request(sensor=PERFECT.model_dump()), 0.0) == quiet


def test_unknown_vehicle_raises():
    with pytest.raises(TwinError):
        run_twin(request(profile_id="missing"), 0.0)


# ------------------------------------------------------------ API


def write_dem(root, value=0.0, size=21):
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
        payload = POLE | {"seed": 3}
        assert client.post("/twin/runs", json=payload).status_code == 409
        write_dem(tmp_path / "data")
        response = client.post("/twin/runs", json=payload)
        assert response.status_code == 200
        assert response.json()["run"]["timeline"]
        for change in (
            {"profile_id": "missing"},
            {"latitude_deg": -85.0},
            {"seed": -2},
        ):
            assert client.post("/twin/runs", json=payload | change).status_code == 422
