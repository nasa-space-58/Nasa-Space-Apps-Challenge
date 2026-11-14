"""H4 physics acceptance tests. Frozen independently before implementation."""

import math
from datetime import datetime, timezone

import numpy as np
import pytest
from pydantic import ValidationError

from app.descent_dynamics import advance_burn
from app.terminal_descent import (
    LandingTarget,
    TerminalRequest,
    TerminalState,
    TerminalVehicle,
    TerrainCorridor,
    evaluate_targets,
    simulate_terminal,
    terrain_at,
    vehicle_from_r4,
)
from shared.constants import MOON_REFERENCE_RADIUS_M as R
from shared.constants import STANDARD_GRAVITY_M_S2 as G0
from shared.hybrid import Source

SOURCE = Source(
    source_id="h4-fixture",
    kind="synthetic",
    description="Declared test corridor and vehicle",
)


def request_data():
    return {
        "initial_state": {
            "epoch_utc": "2030-01-01T00:00:00Z",
            "vehicle_id": "lander",
            "position_m": (0.0, R + 100),
            "velocity_mps": (0.0, -3.0),
            "propellant_kg": 100.0,
        },
        "vehicle": {
            "vehicle_id": "lander",
            "dry_mass_kg": 900.0,
            "propellant_capacity_kg": 100.0,
            "max_thrust_n": 10000.0,
            "design_thrust_n": 10000.0,
            "isp_s": 300.0,
            "source": SOURCE,
        },
        "terrain": {
            "points": [
                {"downrange_m": -20000.0, "height_m": 0.0},
                {"downrange_m": 20000.0, "height_m": 0.0},
            ],
            "source": SOURCE,
        },
        "reserve_kg": 10.0,
    }


def make_request(**updates):
    data = request_data()
    data.update(updates)
    return TerminalRequest.model_validate(data)


def burn(**updates):
    data = dict(
        position_m=(0.0, R),
        velocity_mps=(0.0, 0.0),
        mass_kg=1000.0,
        dry_mass_kg=900.0,
        thrust_vector_n=(0.0, 1000.0),
        isp_s=300.0,
        dt_s=1.0,
        gm_m3_s2=0.0,
    )
    data.update(updates)
    return advance_burn(**data)


def test_constant_thrust_matches_analytic_rocket_velocity_and_position():
    step = burn(dt_s=20.0, thrust_vector_n=(0.0, 5000.0))
    ve, flow = 300 * G0, 5000 / (300 * G0)
    m1 = 1000 - flow * 20
    expected_v = ve * math.log(1000 / m1)
    expected_y = R + ve * (20 - m1 / flow * math.log(1000 / m1))
    assert step.mass_kg == pytest.approx(m1)
    assert step.velocity_mps[1] == pytest.approx(expected_v, abs=1e-6)
    assert step.position_m[1] == pytest.approx(expected_y, abs=1e-5)
    assert step.delta_v_mps == pytest.approx(expected_v)
    assert step.burn_s == 20
    assert step.coast_s == 0


def test_fuel_exhaustion_splits_burn_and_coast_without_free_impulse():
    step = burn(dry_mass_kg=999.0, thrust_vector_n=(0.0, 500000.0), dt_s=0.1)
    ve, flow = 300 * G0, 500000 / (300 * G0)
    tb = 1 / flow
    dv = ve * math.log(1000 / 999)
    expected_y = R + ve * (tb - 999 / flow * math.log(1000 / 999)) + dv * (0.1 - tb)
    assert step.burn_s == pytest.approx(tb)
    assert step.coast_s == pytest.approx(0.1 - tb)
    assert step.mass_kg == 999
    assert step.velocity_mps == pytest.approx((0, dv), abs=1e-7)
    assert step.position_m[1] == pytest.approx(expected_y, abs=1e-6)
    assert step.propellant_exhausted


def test_no_fuel_never_produces_thrust():
    step = burn(mass_kg=900.0, velocity_mps=(3, -2), dt_s=2.0)
    assert step.mass_kg == 900
    assert step.velocity_mps == pytest.approx((3, -2))
    assert step.position_m == pytest.approx((6, R - 4))
    assert step.burn_s == 0
    assert step.delta_v_mps == 0
    assert step.propellant_exhausted


def test_zero_thrust_does_not_consume_fuel():
    step = burn(thrust_vector_n=(0, 0), velocity_mps=(2, 0))
    assert step.mass_kg == 1000
    assert step.position_m == pytest.approx((2, R))
    assert not step.propellant_exhausted


def test_burn_does_not_mutate_input_arrays():
    p, v, f = np.array([0.0, R]), np.zeros(2), np.array([0.0, 1000.0])
    burn(position_m=p, velocity_mps=v, thrust_vector_n=f)
    assert p == pytest.approx((0, R))
    assert v == pytest.approx((0, 0))
    assert f == pytest.approx((0, 1000))


@pytest.mark.parametrize(
    "updates",
    [
        {"mass_kg": 899},
        {"dry_mass_kg": 0},
        {"dt_s": 0},
        {"dt_s": -1},
        {"isp_s": 0},
        {"mass_kg": math.inf},
        {"thrust_vector_n": (math.nan, 1)},
        {"velocity_mps": (0, 0, 0)},
        {"position_m": (0, 0)},
        {"gm_m3_s2": -1},
    ],
)
def test_invalid_physics_rejected(updates):
    with pytest.raises(ValueError):
        burn(**updates)


def test_point_mass_ballistic_energy_converges():
    from shared.constants import MOON_GM_M3_S2 as mu

    p = np.array([0.0, R + 100000.0])
    v = np.array([1000.0, 0.0])
    initial_energy = np.dot(v, v) / 2 - mu / np.linalg.norm(p)
    for _ in range(100):
        step = burn(position_m=p, velocity_mps=v, thrust_vector_n=(0, 0), gm_m3_s2=mu)
        p, v = step.position_m, step.velocity_mps
    assert np.dot(v, v) / 2 - mu / np.linalg.norm(p) == pytest.approx(
        initial_energy, abs=1e-5
    )


def test_r4_last_partial_burn_respects_rocket_equation(monkeypatch):
    import app.descent as r4

    profile = r4.VehicleProfile(
        mass_at_pdi_kg=1000,
        usable_propellant_kg=1,
        max_thrust_n=500000,
        design_thrust_n=1000000,
        min_throttle_fraction=0.5,
        max_throttle_fraction=0.6,
        isp_s=300,
    )
    monkeypatch.setattr(
        r4,
        "gate_states",
        lambda *args: [
            {
                "name": "probe",
                "duration_s": 0.1,
                "position_m": np.array([0.0, R]),
                "velocity_mps": np.zeros(2),
            }
        ],
    )
    result = r4.simulate_powered_descent(profile, R, 0, 0, dt_s=0.1)
    # Gravity can only reduce the upward rocket-equation velocity in this probe.
    dv = 300 * G0 * math.log(1000 / 999)
    assert dv - 0.2 < result["final"]["vertical_speed_mps"] < dv
    assert result["final"]["mass_kg"] == 999
    assert result["propellant_exhausted"]


def test_vehicle_adapter_preserves_r4_si_values():
    from app.descent import load_profiles

    profile = load_profiles()["apollo-lm-11"]
    adapted = vehicle_from_r4(profile.model_dump(), source=SOURCE)
    assert adapted.dry_mass_kg == pytest.approx(
        profile.mass_at_pdi_kg - profile.usable_propellant_kg
    )
    assert adapted.propellant_capacity_kg == profile.usable_propellant_kg
    assert adapted.isp_s == profile.isp_s
    assert adapted.max_thrust_n == profile.max_thrust_n
    assert adapted.source == SOURCE


@pytest.mark.parametrize(
    "updates",
    [
        {"dry_mass_kg": -1},
        {"isp_s": math.nan},
        {"min_throttle_fraction": 0.8, "max_throttle_fraction": 0.2},
        {"max_thrust_n": 100, "min_throttle_fraction": 0.5},
    ],
)
def test_vehicle_rejects_invalid_engine(updates):
    data = request_data()["vehicle"]
    data.update(updates)
    with pytest.raises(ValueError):
        TerminalVehicle.model_validate(data)


@pytest.mark.parametrize(
    "updates",
    [
        {"reference_frame": "MOON_ME"},
        {"epoch_utc": "2030-01-01T00:00:00"},
        {"propellant_kg": -1},
        {"position_m": (0.0, 0.0)},
        {"velocity_mps": (0, math.inf)},
    ],
)
def test_state_rejects_ambiguous_or_invalid_inputs(updates):
    data = request_data()["initial_state"]
    data.update(updates)
    with pytest.raises(ValueError):
        TerminalState.model_validate(data)


def test_terrain_interpolation_and_conservative_corner_slope():
    terrain = TerrainCorridor(
        source=SOURCE,
        points=[
            {"downrange_m": -100, "height_m": 0},
            {"downrange_m": 0, "height_m": 10},
            {"downrange_m": 100, "height_m": 10},
        ],
    )
    assert terrain_at(terrain, -50)[0] == 5
    assert terrain_at(terrain, 50) == (10, 0)
    assert terrain_at(terrain, 0)[1] == pytest.approx(math.degrees(math.atan(0.1)))
    with pytest.raises(ValueError, match="coverage"):
        terrain_at(terrain, 101)


@pytest.mark.parametrize(
    "points",
    [
        [{"downrange_m": 0, "height_m": 0}, {"downrange_m": 0, "height_m": 1}],
        [{"downrange_m": 1, "height_m": 0}, {"downrange_m": 0, "height_m": 1}],
    ],
)
def test_terrain_requires_strictly_increasing_samples(points):
    with pytest.raises(ValueError):
        TerrainCorridor(points=points, source=SOURCE)


@pytest.mark.parametrize(
    "field,value", [("dt_s", 0), ("duration_s", 301), ("reserve_kg", -1)]
)
def test_request_bounds(field, value):
    with pytest.raises(ValidationError):
        make_request(**{field: value})


def test_mismatched_vehicle_and_overfilled_tank_rejected():
    for field, value in [("vehicle_id", "other"), ("propellant_kg", 101)]:
        data = request_data()
        data["initial_state"][field] = value
        with pytest.raises(ValueError):
            TerminalRequest.model_validate(data)


def test_nominal_terminal_landing_and_mass_balance():
    job = make_request()
    result = simulate_terminal(job)
    assert result["feasible"]
    assert result["reasons"] == []
    assert result["termination"] == "contact"
    assert result["trace"][0]["state"] == job.initial_state.model_dump(mode="json")
    final = result["trace"][-1]
    assert abs(final["clearance_m"]) < 1e-4
    assert abs(final["vertical_speed_mps"]) <= 2
    assert abs(final["horizontal_speed_mps"]) <= 1
    assert abs(final["downrange_m"]) <= 5
    fuel = [row["state"]["propellant_kg"] for row in result["trace"]]
    assert all(a >= b >= 0 for a, b in zip(fuel, fuel[1:]))
    assert result["propellant_used_kg"] == pytest.approx(100 - fuel[-1])
    assert fuel[-1] >= 10
    assert 0 < final["t_s"] <= job.duration_s
    assert datetime.fromisoformat(
        final["state"]["epoch_utc"].replace("Z", "+00:00")
    ) > datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_repeatability_and_fingerprint_change_with_inputs():
    a = simulate_terminal(make_request())
    b = simulate_terminal(make_request())
    assert a == b
    c = simulate_terminal(make_request(reserve_kg=99.0))
    assert a["request_sha256"] != c["request_sha256"]
    assert len(a["request_sha256"]) == 64
    assert not c["feasible"]
    assert "reserve" in c["reasons"]


def test_no_fuel_rejects_high_speed_impact_and_preserves_mass():
    data = request_data()
    data["initial_state"]["propellant_kg"] = 0
    result = simulate_terminal(TerminalRequest.model_validate(data))
    assert not result["feasible"]
    assert result["termination"] == "contact"
    assert "vertical_speed" in result["reasons"]
    assert "reserve" in result["reasons"]
    assert result["propellant_exhausted"]
    assert result["propellant_used_kg"] == 0
    assert all(row["state"]["propellant_kg"] == 0 for row in result["trace"])


def test_insufficient_thrust_rejects_fast_contact():
    data = request_data()
    data["vehicle"].update(max_thrust_n=100.0, design_thrust_n=100.0)
    result = simulate_terminal(TerminalRequest.model_validate(data))
    assert not result["feasible"]
    assert "vertical_speed" in result["reasons"]


def test_no_touchdown_by_deadline_is_not_feasible():
    data = request_data()
    data["initial_state"].update(position_m=(0.0, R + 10000), velocity_mps=(0.0, 100.0))
    data["vehicle"].update(max_thrust_n=100.0, design_thrust_n=100.0)
    result = simulate_terminal(
        TerminalRequest.model_validate({**data, "duration_s": 5.0})
    )
    assert result["termination"] == "timeout"
    assert "no_contact" in result["reasons"]
    assert not result["feasible"]


def test_unknown_terrain_is_not_assumed_flat():
    data = request_data()
    data["terrain"]["points"] = [
        {"downrange_m": 10.0, "height_m": 0.0},
        {"downrange_m": 20.0, "height_m": 0.0},
    ]
    result = simulate_terminal(TerminalRequest.model_validate(data))
    assert not result["feasible"]
    assert result["termination"] == "terrain_unknown"
    assert "terrain_coverage" in result["reasons"]


def test_initial_below_terrain_is_rejected_without_flying_through_it():
    data = request_data()
    data["initial_state"]["position_m"] = (0.0, R - 1)
    result = simulate_terminal(TerminalRequest.model_validate(data))
    assert not result["feasible"]
    assert result["termination"] == "initial_below_terrain"
    assert result["trace"][-1]["t_s"] == 0


def test_elevated_surface_sets_contact_radius():
    data = request_data()
    for point in data["terrain"]["points"]:
        point["height_m"] = 40.0
    result = simulate_terminal(TerminalRequest.model_validate(data))
    assert result["termination"] == "contact"
    p = result["trace"][-1]["state"]["position_m"]
    assert np.linalg.norm(p) == pytest.approx(R + 40, abs=1e-4)


def test_steep_touchdown_is_rejected():
    data = request_data()
    data["terrain"]["points"] = [
        {"downrange_m": -100.0, "height_m": -100.0},
        {"downrange_m": 100.0, "height_m": 100.0},
    ]
    result = simulate_terminal(TerminalRequest.model_validate(data))
    assert not result["feasible"]
    assert "terrain_slope" in result["reasons"]


def test_restart_uses_remaining_fuel_and_given_epoch_without_refill():
    job = make_request()
    first = simulate_terminal(job)
    row = first["trace"][50]
    restarted = make_request(
        initial_state=row["state"], duration_s=job.duration_s - row["t_s"]
    )
    second = simulate_terminal(restarted)
    assert second["trace"][0]["state"] == row["state"]
    assert second["feasible"]
    assert second["trace"][-1]["state"]["propellant_kg"] == pytest.approx(
        first["trace"][-1]["state"]["propellant_kg"], abs=0.05
    )


def test_reachability_requires_two_resolutions_and_retains_failures():
    targets = (
        LandingTarget(target_id="near", downrange_m=0.0, duration_s=30.0),
        LandingTarget(target_id="far", downrange_m=10000.0, duration_s=5.0),
    )
    result = evaluate_targets(make_request(), targets)
    assert [x["target_id"] for x in result["candidates"]] == ["near", "far"]
    near, far = result["candidates"]
    assert near["reachable"] and near["numerically_consistent"]
    assert near["coarse"]["feasible"] and near["fine"]["feasible"]
    assert not far["reachable"]
    assert result["recommended_target_id"] == "near"
    assert near["position_difference_m"] < 0.5
    assert near["velocity_difference_mps"] < 0.1
    assert near["fuel_difference_kg"] < 0.05


def test_no_feasible_target_has_no_recommendation():
    result = evaluate_targets(
        make_request(reserve_kg=1000.0),
        (LandingTarget(target_id="reserve", downrange_m=0.0, duration_s=30.0),),
    )
    assert result["recommended_target_id"] is None
    assert not result["candidates"][0]["reachable"]


def test_candidate_limits_and_duplicate_ids_rejected():
    item = LandingTarget(target_id="one", downrange_m=0.0, duration_s=30.0)
    for targets in [
        (),
        (item, item),
        tuple(item.model_copy(update={"target_id": f"x{i}"}) for i in range(17)),
    ]:
        with pytest.raises(ValueError):
            evaluate_targets(make_request(), targets)
