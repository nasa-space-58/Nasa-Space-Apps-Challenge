"""Additional H4 boundary cases frozen before boundary refinements."""

import numpy as np
import pytest

from app.terminal_descent import (
    LandingTarget,
    TerminalRequest,
    evaluate_targets,
    simulate_terminal,
)
from tests.test_terminal_descent import R, make_request, request_data


def test_departing_surface_is_not_a_successful_landing():
    data = request_data()
    data["initial_state"].update(position_m=(0.0, R), velocity_mps=(0.0, 1.0))
    result = simulate_terminal(TerminalRequest.model_validate(data))
    assert not result["feasible"]
    assert "vertical_speed" in result["reasons"]


def test_known_ridge_stops_flight_at_early_contact():
    data = request_data()
    data["initial_state"].update(velocity_mps=(100.0, -3.0), propellant_kg=0.0)
    data["terrain"]["points"] = [
        {"downrange_m": -100.0, "height_m": 0.0},
        {"downrange_m": 0.0, "height_m": 0.0},
        {"downrange_m": 100.0, "height_m": 200.0},
        {"downrange_m": 200.0, "height_m": 0.0},
        {"downrange_m": 2000.0, "height_m": 0.0},
    ]
    data["target_downrange_m"] = 1000.0
    result = simulate_terminal(TerminalRequest.model_validate(data))
    final = result["trace"][-1]
    assert result["termination"] == "contact"
    assert final["t_s"] < 1
    assert 0 < final["downrange_m"] < 100
    assert abs(final["clearance_m"]) < 1e-4
    assert not result["feasible"]
    assert "position" in result["reasons"]
    assert "terrain_slope" in result["reasons"]


def test_leaving_corridor_remains_unknown():
    data = request_data()
    data["initial_state"].update(velocity_mps=(100.0, 0.0), propellant_kg=0.0)
    data["terrain"]["points"] = [
        {"downrange_m": -10.0, "height_m": 0.0},
        {"downrange_m": 10.0, "height_m": 0.0},
    ]
    result = simulate_terminal(TerminalRequest.model_validate(data))
    assert result["termination"] == "terrain_unknown"
    assert not result["feasible"]
    assert result["trace"][-1]["clearance_m"] is None


def test_unknown_target_does_not_start_a_burn():
    result = simulate_terminal(make_request(target_downrange_m=30000.0))
    assert not result["feasible"]
    assert result["termination"] == "terrain_unknown"
    assert result["propellant_used_kg"] == 0


def test_resolution_halving_supports_minimum_input_step():
    result = evaluate_targets(
        make_request(dt_s=0.02),
        (LandingTarget(target_id="near", downrange_m=0.0, duration_s=30.0),),
    )
    candidate = result["candidates"][0]
    assert candidate["fine"]["integration_dt_s"] == 0.01
    assert candidate["reachable"]


def test_fuel_exhaustion_during_terminal_run_is_conservative():
    data = request_data()
    data["initial_state"]["propellant_kg"] = 0.01
    result = simulate_terminal(TerminalRequest.model_validate(data))
    assert result["propellant_exhausted"]
    assert result["propellant_used_kg"] == pytest.approx(0.01)
    rows = result["trace"]
    first_empty = next(
        i for i, row in enumerate(rows) if row["state"]["propellant_kg"] == 0
    )
    assert all(row["thrust_n"] == 0 for row in rows[first_empty + 1 :])
    assert not result["feasible"]


def test_execution_revalidates_unchecked_model_copy():
    job = make_request()
    bad = job.model_copy(update={"dt_s": -1.0})
    with pytest.raises(ValueError):
        simulate_terminal(bad)


def test_nominal_half_step_agrees_in_physical_units():
    a = simulate_terminal(make_request(dt_s=0.1))
    b = simulate_terminal(make_request(dt_s=0.05))
    pa = np.array(a["trace"][-1]["state"]["position_m"])
    pb = np.array(b["trace"][-1]["state"]["position_m"])
    assert np.linalg.norm(pa - pb) < 0.01
    assert abs(a["propellant_used_kg"] - b["propellant_used_kg"]) < 0.001
