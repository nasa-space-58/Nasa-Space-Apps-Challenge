"""Mission run: physics-gated outcomes with injected fault scenarios.

References and tolerances:
- Nominal faulted-descent flight must reproduce the R4 integration
  (simulate_powered_descent, 0.1 s steps) because it applies the same guidance,
  throttle rule and RK4 steps; final position within 1e-6 m.
- Structural limits: Apollo Experience Report, LM Landing Gear Subsystem
  (NASA TN D-6850): 10 ft/s vertical, 4 ft/s horizontal touchdown velocity.
- Battery and contact tests use hand-computed piecewise-linear values: an ideal
  battery with constant net power changes energy linearly, so depletion times
  are exact (1 s tolerance for timestamp rounding).
- Great-circle destination: spherical trigonometry on a sphere of radius R.
"""

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from pydantic import ValidationError
from rasterio.transform import from_origin

from app.analysis_api import Submission, create_app
from app.descent import (
    DescentRequest,
    load_profiles,
    plan_descent,
    simulate_powered_descent,
)
from app.mission_run import (
    CONTACT_ALTITUDE_M,
    DEFAULT_CRASH_HORIZONTAL_MPS,
    DEFAULT_CRASH_VERTICAL_MPS,
    EXTENSION_LIMIT_S,
    MAX_DESCENT_SAMPLES,
    MAX_FAULTS,
    CommunicationOutage,
    CommunicationRules,
    EngineThrustLoss,
    FaultScenario,
    IspDegradation,
    LandingDelay,
    MissionRunError,
    MissionRunRequest,
    NavigationBias,
    PowerLoadIncrease,
    PowerSystem,
    PropellantLeak,
    BatteryCapacityLoss,
    SolarArrayDegradation,
    contact_check,
    destination_point,
    effective_faults,
    fly_faulted_descent,
    load_scenarios,
    power_trace,
    run_mission,
    touchdown_offset,
)
from app.terrain_safety import TerrainSafetyError
from shared.constants import FOOT_M, MOON_REFERENCE_RADIUS_M as R

T0 = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
SITE = dict(latitude_deg=-89.45, longitude_deg_east=222.69)


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iv(start_h, end_h, state):
    start, end = T0 + start_h * H, T0 + end_h * H
    return {
        "start_utc": start.isoformat().replace("+00:00", "Z"),
        "end_utc": end.isoformat().replace("+00:00", "Z"),
        "state": state,
        "duration_s": (end - start).total_seconds(),
    }


def analysis(sun=None, earth=None, hours=240, site=SITE):
    return {
        "request": {
            "site": {"site_id": "test", "altitude_km": 0.0, **site},
            "start_utc": T0.isoformat().replace("+00:00", "Z"),
            "end_utc": (T0 + hours * H).isoformat().replace("+00:00", "Z"),
            "step_s": 3600,
        },
        "sun": {"intervals": sun or [iv(0, hours, "full")]},
        "earth": {"intervals": earth or [iv(0, hours, "visible")]},
    }


def safe_terrain(slope=2.0, max_slope=4.0):
    calls = []

    def terrain_at(lat, lon):
        calls.append((lat, lon))
        return {
            "site_slope_deg": slope,
            "max_landing_slope_deg": max_slope,
            "hazard_distance_m": None,
            "unknown_landing_fraction": 0.0,
            "covered": True,
        }

    terrain_at.calls = calls
    return terrain_at


def gate(result, name):
    return next(g for g in result["gates"] if g["gate"] == name)


# ------------------------------------------------------------- nominal plan


@pytest.fixture(scope="module")
def nominal():
    plan = plan_descent(DescentRequest(**SITE), 0.0)
    profile = load_profiles()["apollo-lm-11"]
    return dict(
        plan=plan,
        profile=profile,
        radius=plan["site"]["radius_m"],
        pdi=plan["pdi_range_m"],
        speed=plan["deorbit"]["periapsis_speed_mps"],
    )


def fly(nominal, faults=()):
    return fly_faulted_descent(
        nominal["profile"], nominal["radius"], nominal["pdi"], nominal["speed"], faults
    )


# ------------------------------------------------------------- fault models


def test_fault_models_validate_bounds_and_kind():
    EngineThrustLoss(start_after_pdi_s=0, thrust_fraction=0.5)
    for bad in (0, 1, 1.2):
        with pytest.raises(ValidationError):
            EngineThrustLoss(start_after_pdi_s=0, thrust_fraction=bad)
    with pytest.raises(ValidationError):
        PropellantLeak(start_after_pdi_s=-1, leak_rate_kg_s=1, duration_s=10)
    with pytest.raises(ValidationError):
        PropellantLeak(start_after_pdi_s=0, leak_rate_kg_s=0, duration_s=10)
    with pytest.raises(ValidationError):
        NavigationBias(offset_m=100, bearing_deg=360)
    with pytest.raises(ValidationError):
        SolarArrayDegradation(start_after_landing_h=0, output_fraction=1)
    with pytest.raises(ValidationError):
        MissionRunRequest(faults=[{"kind": "meteor", "start_after_pdi_s": 0}])
    with pytest.raises(ValidationError):
        MissionRunRequest(faults=[{"kind": "landing_delay", "delay_h": 1, "x": 2}])
    with pytest.raises(ValidationError):
        MissionRunRequest(faults=[{"kind": "landing_delay", "delay_h": 1}] * 13)
    request = MissionRunRequest(
        faults=[
            {"kind": "navigation_bias", "offset_m": 10, "bearing_deg": 0},
            {
                "kind": "communication_outage",
                "start_after_landing_h": 1,
                "duration_h": 2,
            },
        ]
    )
    assert isinstance(request.faults[0], NavigationBias)
    assert isinstance(request.faults[1], CommunicationOutage)


def test_settings_defaults_are_documented_values():
    request = MissionRunRequest()
    assert request.profile_id == "apollo-lm-11"
    assert request.touchdown.crash_vertical_mps == pytest.approx(10 * FOOT_M)
    assert request.touchdown.crash_horizontal_mps == pytest.approx(4 * FOOT_M)
    assert DEFAULT_CRASH_VERTICAL_MPS == pytest.approx(3.048)
    assert DEFAULT_CRASH_HORIZONTAL_MPS == pytest.approx(1.2192)
    assert request.terrain.slope_limit_deg == 10.0
    assert request.faults == ()
    with pytest.raises(ValidationError):
        MissionRunRequest(power={"battery_capacity_wh": 0})
    with pytest.raises(ValidationError):
        MissionRunRequest(approach_azimuth_deg=400)


# ------------------------------------------------------------- scenarios


def test_curated_catalog_is_valid_and_contains_nominal():
    scenarios = load_scenarios()
    assert "nominal" in scenarios
    assert scenarios["nominal"].faults == ()
    assert len(scenarios) >= 8
    kinds = {f.kind for s in scenarios.values() for f in s.faults}
    # Every fault family is exercised by at least one curated scenario.
    assert kinds == {
        "engine_thrust_loss",
        "propellant_leak",
        "isp_degradation",
        "navigation_bias",
        "landing_delay",
        "battery_capacity_loss",
        "power_load_increase",
        "solar_array_degradation",
        "communication_outage",
    }
    for scenario_id, scenario in scenarios.items():
        assert scenario.scenario_id == scenario_id
        assert scenario.summary


def test_catalog_rejects_duplicates_and_invalid_entries(tmp_path):
    entry = {"scenario_id": "a", "summary": "x", "faults": []}
    path = tmp_path / "faults.json"
    path.write_text(json.dumps({"scenarios": [entry, entry]}))
    with pytest.raises(ValueError):
        load_scenarios(path)
    path.write_text(
        json.dumps({"scenarios": [entry | {"faults": [{"kind": "landing_delay"}]}]})
    )
    with pytest.raises(ValueError):
        load_scenarios(path)


def test_effective_faults_combine_scenario_then_custom():
    scenarios = {
        "s": FaultScenario(
            scenario_id="s",
            summary="x",
            faults=(NavigationBias(offset_m=5, bearing_deg=0),),
        )
    }
    custom = LandingDelay(delay_h=2)
    request = MissionRunRequest(scenario_id="s", faults=[custom])
    faults = effective_faults(request, scenarios)
    assert [f.kind for f in faults] == ["navigation_bias", "landing_delay"]
    assert effective_faults(MissionRunRequest(), scenarios) == ()
    with pytest.raises(MissionRunError):
        effective_faults(MissionRunRequest(scenario_id="missing"), scenarios)
    many = FaultScenario(
        scenario_id="many",
        summary="x",
        faults=(NavigationBias(offset_m=5, bearing_deg=0),) * MAX_FAULTS,
    )
    with pytest.raises(MissionRunError):
        effective_faults(
            MissionRunRequest(scenario_id="many", faults=[custom]), {"many": many}
        )
    with pytest.raises(MissionRunError):
        effective_faults(MissionRunRequest(faults=[custom, custom]), scenarios)


# ------------------------------------------------------------- geometry


def test_destination_point_on_sphere():
    degree = R * math.pi / 180
    assert destination_point(0, 0, degree, 0, R) == pytest.approx((1, 0), abs=1e-9)
    assert destination_point(0, 0, degree, 90, R) == pytest.approx((0, 1), abs=1e-9)
    assert destination_point(0, 0, degree, 180, R) == pytest.approx((-1, 0), abs=1e-9)
    # Longitudes wrap into [0, 360).
    lat, lon = destination_point(0, 359.5, degree, 90, R)
    assert (lat, lon) == pytest.approx((0, 0.5), abs=1e-9)
    lat, lon = destination_point(0, 0.5, degree, 270, R)
    assert (lat, lon) == pytest.approx((0, 359.5), abs=1e-9)
    # Crossing the south pole flips the meridian.
    lat, lon = destination_point(-89.9, 0, 0.2 * degree, 180, R)
    assert (lat, lon) == pytest.approx((-89.9, 180), abs=1e-7)
    assert destination_point(-89.45, 222.69, 0, 37, R) == pytest.approx(
        (-89.45, 222.69), abs=1e-12
    )
    # Distance is preserved along a great circle.
    lat, lon = destination_point(-89.45, 222.69, 600, 90, R)
    a, b = math.radians(-89.45), math.radians(lat)
    dlon = math.radians(lon - 222.69)
    central = math.acos(
        min(1, math.sin(a) * math.sin(b) + math.cos(a) * math.cos(b) * math.cos(dlon))
    )
    assert central * R == pytest.approx(600, rel=1e-6)


def test_touchdown_offset_combines_along_track_miss_and_navigation_bias():
    assert touchdown_offset(0.0, 0.0, ()) == pytest.approx((0, 0), abs=1e-12)
    assert touchdown_offset(100.0, 90.0, ()) == pytest.approx((100, 90))
    assert touchdown_offset(-100.0, 90.0, ()) == pytest.approx((100, 270))
    distance, bearing = touchdown_offset(
        100.0, 90.0, (NavigationBias(offset_m=100, bearing_deg=0),)
    )
    assert (distance, bearing) == pytest.approx((100 * math.sqrt(2), 45))
    distance, bearing = touchdown_offset(
        0.0,
        0.0,
        (
            NavigationBias(offset_m=50, bearing_deg=180),
            NavigationBias(offset_m=50, bearing_deg=180),
        ),
    )
    assert (distance, bearing) == pytest.approx((100, 180))
    # Surface-only faults do not move the touchdown point.
    assert touchdown_offset(
        0.0,
        0.0,
        (
            LandingDelay(delay_h=1),
            CommunicationOutage(start_after_landing_h=0, duration_h=1),
        ),
    ) == pytest.approx((0, 0), abs=1e-12)


# ------------------------------------------------------------- descent


def test_unfaulted_flight_reproduces_r4_integration(nominal):
    run = fly(nominal)
    reference = simulate_powered_descent(
        nominal["profile"],
        nominal["radius"],
        nominal["pdi"],
        nominal["speed"],
        0.1,
        record=False,
    )
    assert np.allclose(
        run["final_position_m"], reference["final_position_m"], atol=1e-6, rtol=0
    )
    assert np.allclose(
        run["final_velocity_mps"], reference["final_velocity_mps"], atol=1e-9, rtol=0
    )
    assert run["mass_kg"] == pytest.approx(reference["final"]["mass_kg"], abs=1e-9)
    contact = run["contact"]
    assert contact is not None
    assert contact["t_s"] == pytest.approx(reference["phases"][-1]["end_s"], abs=0.11)
    assert abs(contact["vertical_speed_mps"]) < 1.0
    assert contact["horizontal_speed_mps"] < 0.5
    assert abs(contact["downrange_m"]) < 5
    assert run["exhausted_at"] is None
    assert run["propellant_leaked_kg"] == 0
    used = sum(p["propellant_kg"] for p in reference["phases"])
    assert run["propellant_used_kg"] == pytest.approx(used, abs=1e-6)
    assert run["propellant_remaining_kg"] == pytest.approx(
        nominal["profile"].usable_propellant_kg - used, abs=1e-6
    )
    assert [e["kind"] for e in run["events"]] == ["contact"]
    assert run["samples"][0]["t_s"] == 0
    assert run["samples"][-1]["altitude_m"] == pytest.approx(
        contact["altitude_m"], abs=1e-9
    )


def test_fault_scheduled_after_touchdown_changes_nothing(nominal):
    base = fly(nominal)
    late = fly(
        nominal,
        (
            PropellantLeak(start_after_pdi_s=1000, leak_rate_kg_s=5, duration_s=60),
            EngineThrustLoss(start_after_pdi_s=1100, thrust_fraction=0.1),
        ),
    )
    assert late["final_position_m"] == pytest.approx(base["final_position_m"])
    assert late["contact"] == base["contact"]
    assert late["propellant_leaked_kg"] == 0
    assert "fault_activated" not in {e["kind"] for e in late["events"]}


def test_major_leak_exhausts_propellant_and_crashes(nominal):
    run = fly(
        nominal,
        (PropellantLeak(start_after_pdi_s=0, leak_rate_kg_s=5, duration_s=1200),),
    )
    kinds = [e["kind"] for e in run["events"]]
    assert kinds[0] == "fault_activated"
    assert "propellant_exhausted" in kinds
    assert kinds.index("propellant_exhausted") < kinds.index("contact")
    exhausted = run["exhausted_at"]
    assert exhausted is not None and exhausted["altitude_m"] > 10
    assert run["propellant_remaining_kg"] == pytest.approx(0, abs=1e-6)
    profile = nominal["profile"]
    assert run["propellant_used_kg"] + run["propellant_leaked_kg"] == pytest.approx(
        profile.usable_propellant_kg, abs=1e-6
    )
    # Leak bookkeeping follows the constant rate until exhaustion.
    assert run["propellant_leaked_kg"] == pytest.approx(
        5 * exhausted["t_s"], abs=5 * 0.1 + 1e-6
    )
    contact = run["contact"]
    assert contact is not None
    assert contact["altitude_m"] == pytest.approx(0, abs=1e-6)
    assert (
        abs(contact["vertical_speed_mps"]) > DEFAULT_CRASH_VERTICAL_MPS
        or contact["horizontal_speed_mps"] > DEFAULT_CRASH_HORIZONTAL_MPS
    )
    after = [s for s in run["samples"] if s["t_s"] > exhausted["t_s"] + 1e-9]
    assert after and all(s["thrust_n"] == 0 for s in after)


def test_leak_window_bounds_lost_mass(nominal):
    run = fly(
        nominal,
        (PropellantLeak(start_after_pdi_s=100, leak_rate_kg_s=0.5, duration_s=200),),
    )
    assert run["propellant_leaked_kg"] == pytest.approx(100, abs=1e-6)
    assert run["exhausted_at"] is None
    base = fly(nominal)
    assert run["propellant_remaining_kg"] < base["propellant_remaining_kg"] - 100 + 50
    assert run["contact"] is not None
    assert abs(run["contact"]["vertical_speed_mps"]) < 1.0


def test_severe_thrust_loss_cannot_brake_and_crashes_early(nominal):
    profile = nominal["profile"]
    run = fly(nominal, (EngineThrustLoss(start_after_pdi_s=0, thrust_fraction=0.5),))
    cap = 0.5 * profile.max_thrust_n
    assert all(s["thrust_n"] <= cap + 1e-6 for s in run["samples"])
    contact = run["contact"]
    assert contact is not None
    assert (
        abs(contact["vertical_speed_mps"]) > DEFAULT_CRASH_VERTICAL_MPS
        or contact["horizontal_speed_mps"] > DEFAULT_CRASH_HORIZONTAL_MPS
    )


def test_non_binding_thrust_cap_matches_nominal(nominal):
    # Terminal descent uses about a quarter of design thrust; a 95 % cap late in
    # the flight never binds.
    base = fly(nominal)
    run = fly(nominal, (EngineThrustLoss(start_after_pdi_s=650, thrust_fraction=0.95),))
    assert run["final_position_m"] == pytest.approx(base["final_position_m"])
    assert [e["kind"] for e in run["events"]] == ["fault_activated", "contact"]
    assert run["events"][0]["t_s"] == pytest.approx(650, abs=0.1)


def test_isp_loss_burns_more_propellant_deterministically(nominal):
    faults = (IspDegradation(start_after_pdi_s=100, isp_fraction=0.9),)
    first, second = fly(nominal, faults), fly(nominal, faults)
    assert first == second
    base = fly(nominal)
    assert first["propellant_used_kg"] > base["propellant_used_kg"] + 100


def test_flight_always_ends_in_bounded_time(nominal):
    for faults in (
        (EngineThrustLoss(start_after_pdi_s=400, thrust_fraction=0.7),),
        (IspDegradation(start_after_pdi_s=0, isp_fraction=0.5),),
        (PropellantLeak(start_after_pdi_s=500, leak_rate_kg_s=3, duration_s=300),),
    ):
        run = fly(nominal, faults)
        last = run["samples"][-1]["t_s"]
        assert last <= 714 + EXTENSION_LIMIT_S + 1
        if run["contact"] is None:
            assert run["events"][-1]["kind"] == "no_contact"
        else:
            assert run["contact"]["altitude_m"] <= CONTACT_ALTITUDE_M
            assert run["events"][-1]["kind"] == "contact"


# ------------------------------------------------------------- power


def power(sun, hours=48, faults=(), unknown="none", **settings):
    return power_trace(
        sun, T0, T0 + hours * H, PowerSystem(**settings), faults, unknown
    )


def test_battery_depletes_linearly_in_darkness():
    result = power([iv(0, 48, "dark")])
    # 0.9 * 4000 Wh / 350 W
    expected = T0 + timedelta(hours=3600 / 350)
    assert abs(utc(result["depleted_at_utc"]) - expected) <= timedelta(seconds=1)
    assert result["min_energy_wh"] == pytest.approx(0, abs=1e-6)
    assert result["trace"][0]["energy_wh"] == pytest.approx(3600)
    assert utc(result["trace"][0]["utc"]) == T0
    assert utc(result["trace"][-1]["utc"]) <= T0 + 48 * H


def test_sunlight_charges_and_clamps_at_capacity():
    result = power([iv(0, 48, "full")])
    assert result["depleted_at_utc"] is None
    assert result["min_energy_wh"] == pytest.approx(3600)
    assert max(r["energy_wh"] for r in result["trace"]) == pytest.approx(4000)
    clamp = power([iv(0, 1, "full"), iv(1, 48, "dark")])
    # +550 W for 1 h would reach 4150 Wh; capacity caps it at 4000 Wh.
    expected = T0 + H + timedelta(hours=4000 / 350)
    assert abs(utc(clamp["depleted_at_utc"]) - expected) <= timedelta(seconds=1)


def test_partial_sun_uses_configured_fraction():
    result = power([iv(0, 10, "partial"), iv(10, 48, "dark")])
    # +100 W for 10 h -> 4000 Wh (exactly full), then 4000 / 350 h.
    expected = T0 + 10 * H + timedelta(hours=4000 / 350)
    assert abs(utc(result["depleted_at_utc"]) - expected) <= timedelta(seconds=1)
    none = power([iv(0, 48, "partial")], partial_sun_output_fraction=0)
    assert none["depleted_at_utc"] is not None


def test_surface_power_faults_change_the_energy_budget():
    lost = power(
        [iv(0, 48, "dark")],
        faults=(
            BatteryCapacityLoss(start_after_landing_h=0, capacity_fraction_lost=0.5),
        ),
    )
    expected = T0 + timedelta(hours=1800 / 350)
    assert abs(utc(lost["depleted_at_utc"]) - expected) <= timedelta(seconds=1)
    heater = power(
        [iv(0, 48, "dark")],
        faults=(
            PowerLoadIncrease(start_after_landing_h=2, duration_h=1, extra_load_w=250),
        ),
    )
    expected = T0 + timedelta(hours=(3600 - 250) / 350)
    assert abs(utc(heater["depleted_at_utc"]) - expected) <= timedelta(seconds=1)
    dusty = power(
        [iv(0, 48, "full")],
        faults=(SolarArrayDegradation(start_after_landing_h=0, output_fraction=0.3),),
    )
    # 270 W generated against 350 W: -80 W from 3600 Wh.
    expected = T0 + timedelta(hours=3600 / 80)
    assert abs(utc(dusty["depleted_at_utc"]) - expected) <= timedelta(seconds=1)


def test_unknown_and_boundary_sunlight_are_bracketed():
    sun = [
        iv(0, 2, "full"),
        iv(2, 30, "unknown"),
        iv(30, 31, "boundary"),
        iv(31, 48, "full"),
    ]
    pessimistic = power(sun)
    optimistic = power(sun, unknown="full")
    assert pessimistic["depleted_at_utc"] is not None
    assert optimistic["depleted_at_utc"] is None


def test_power_trace_clips_to_the_surface_window():
    sun = [iv(-5, 3, "dark"), iv(3, 100, "full")]
    result = power_trace(sun, T0 + H, T0 + 10 * H, PowerSystem(), (), "none")
    assert utc(result["trace"][0]["utc"]) == T0 + H
    assert utc(result["trace"][-1]["utc"]) == T0 + 10 * H
    # 2 h dark (-700 Wh), then charging to capacity.
    assert result["min_energy_wh"] == pytest.approx(3600 - 700)
    # Gaps in the interval list count as unknown sunlight.
    gap = power([iv(0, 1, "full"), iv(40, 48, "full")])
    assert gap["depleted_at_utc"] is not None
    assert (
        power([iv(0, 1, "full"), iv(40, 48, "full")], unknown="full")["depleted_at_utc"]
        is None
    )


# ------------------------------------------------------------- communication


def contact(earth, hours=240, faults=(), unknown_visible=False, **rules):
    return contact_check(
        earth, T0, T0 + hours * H, CommunicationRules(**rules), faults, unknown_visible
    )


def test_continuous_visibility_satisfies_rules():
    result = contact([iv(0, 240, "visible")])
    assert result["violation"] is None
    assert utc(result["first_contact_utc"]) == T0
    assert result["longest_gap_h"] == pytest.approx(0)
    assert len(result["windows"]) == 1


def test_first_contact_deadline():
    result = contact([iv(0, 2, "blocked"), iv(2, 240, "visible")])
    assert result["violation"]["kind"] == "first_contact_deadline"
    assert utc(result["violation"]["at_utc"]) == T0 + H
    assert utc(result["first_contact_utc"]) == T0 + 2 * H
    ok = contact(
        [iv(0, 2, "blocked"), iv(2, 240, "visible")], first_contact_deadline_h=3
    )
    assert ok["violation"] is None
    never = contact([iv(0, 240, "blocked")])
    assert never["first_contact_utc"] is None
    assert never["violation"]["kind"] == "first_contact_deadline"


def test_contact_gap_limit_and_trailing_gap():
    earth = [iv(0, 10, "visible"), iv(10, 90, "blocked"), iv(90, 240, "visible")]
    result = contact(earth)
    assert result["violation"]["kind"] == "contact_gap"
    assert utc(result["violation"]["at_utc"]) == T0 + 82 * H
    assert result["longest_gap_h"] == pytest.approx(80)
    trailing = contact([iv(0, 10, "visible"), iv(10, 50, "blocked")], hours=50)
    assert trailing["violation"] is None
    assert trailing["longest_gap_h"] == pytest.approx(40)
    long_tail = contact([iv(0, 10, "visible"), iv(10, 100, "blocked")], hours=100)
    assert long_tail["violation"]["kind"] == "contact_gap"
    assert utc(long_tail["violation"]["at_utc"]) == T0 + 82 * H


def test_outage_removes_contact():
    result = contact(
        [iv(0, 240, "visible")],
        faults=(CommunicationOutage(start_after_landing_h=2, duration_h=80),),
    )
    assert result["violation"]["kind"] == "contact_gap"
    assert utc(result["violation"]["at_utc"]) == T0 + 74 * H
    assert [utc(w["start_utc"]) for w in result["windows"]] == [T0, T0 + 82 * H]
    early = contact(
        [iv(0, 240, "visible")],
        faults=(CommunicationOutage(start_after_landing_h=0, duration_h=5),),
    )
    assert early["violation"]["kind"] == "first_contact_deadline"


def test_unknown_earth_visibility_is_bracketed():
    earth = [iv(0, 1, "visible"), iv(1, 100, "unknown"), iv(100, 240, "boundary")]
    assert contact(earth)["violation"]["kind"] == "contact_gap"
    assert contact(earth, unknown_visible=True)["violation"] is None


# ------------------------------------------------------------- full run


def run(request=None, data=None, terrain=None, height=0.0):
    return run_mission(
        request or MissionRunRequest(),
        data or analysis(),
        height,
        terrain or safe_terrain(),
    )


def test_nominal_run_succeeds_and_reports_every_gate():
    terrain = safe_terrain()
    result = run(terrain=terrain)
    assert result["outcome"] == "success"
    assert result["failure"] is None
    assert [g["gate"] for g in result["gates"]] == [
        "descent",
        "touchdown_terrain",
        "power",
        "communication",
    ]
    assert all(g["status"] == "pass" for g in result["gates"])
    assert utc(result["landing_utc"]) == T0
    assert utc(result["pdi_utc"]) == T0 - timedelta(seconds=714)
    site = result["touchdown_site"]
    assert site["offset_m"] < 5
    assert terrain.calls == [
        pytest.approx((site["latitude_deg"], site["longitude_deg_east"]))
    ]
    assert len(result["descent"]["samples"]) <= MAX_DESCENT_SAMPLES
    assert result["descent"]["propellant_margin_kg"] == pytest.approx(
        result["descent"]["nominal_propellant_margin_kg"], abs=1e-3
    )
    assert result["assumptions"]
    assert result["faults"] == []
    times = [utc(e["utc"]) for e in result["events"]]
    assert times == sorted(times)
    assert run() == result


def test_crash_stops_the_mission_at_the_descent_gate():
    request = MissionRunRequest(
        faults=[
            {
                "kind": "propellant_leak",
                "start_after_pdi_s": 0,
                "leak_rate_kg_s": 5,
                "duration_s": 1200,
            }
        ]
    )
    terrain = safe_terrain()
    result = run(request, terrain=terrain)
    assert result["outcome"] == "failure"
    assert result["failure"]["gate"] == "descent"
    assert result["failure"]["code"] == "crash"
    assert utc(result["failure"]["at_utc"]) < T0
    assert [g["status"] for g in result["gates"]] == [
        "fail",
        "not_reached",
        "not_reached",
        "not_reached",
    ]
    assert terrain.calls == []
    kinds = [e["kind"] for e in result["events"]]
    assert "propellant_exhausted" in kinds
    assert result["faults"][0]["kind"] == "propellant_leak"


def test_small_leak_lands_with_reduced_margin():
    result = run(
        MissionRunRequest(
            faults=[
                {
                    "kind": "propellant_leak",
                    "start_after_pdi_s": 60,
                    "leak_rate_kg_s": 0.5,
                    "duration_s": 600,
                }
            ]
        )
    )
    assert result["outcome"] == "success"
    descent = result["descent"]
    assert (
        descent["propellant_margin_kg"] < descent["nominal_propellant_margin_kg"] - 250
    )


def test_steep_touchdown_tips_over():
    result = run(terrain=safe_terrain(slope=14.0, max_slope=16.0))
    assert result["outcome"] == "failure"
    assert result["failure"]["gate"] == "touchdown_terrain"
    assert result["failure"]["code"] == "tip_over"
    assert [g["status"] for g in result["gates"]][2:] == ["not_reached", "not_reached"]
    assert utc(result["failure"]["at_utc"]) == pytest.approx(
        T0, abs=timedelta(seconds=1)
    )


def test_steep_terrain_nearby_is_a_warning():
    result = run(terrain=safe_terrain(slope=3.0, max_slope=12.0))
    assert result["outcome"] == "success_with_warnings"
    assert gate(result, "touchdown_terrain")["status"] == "warn"
    assert {w["code"] for w in result["warnings"]} == {"steep_terrain_nearby"}


def test_unknown_terrain_is_inconclusive():
    result = run(terrain=safe_terrain(slope=None, max_slope=None))
    assert result["outcome"] == "inconclusive"
    assert gate(result, "touchdown_terrain")["status"] == "unknown"

    def outside(lat, lon):
        raise TerrainSafetyError("Site search area is outside terrain coverage")

    result = run(terrain=outside)
    assert result["outcome"] == "inconclusive"
    assert gate(result, "touchdown_terrain")["code"] == "outside_coverage"


def test_navigation_bias_moves_the_touchdown_point():
    terrain = safe_terrain()
    result = run(
        MissionRunRequest(
            faults=[{"kind": "navigation_bias", "offset_m": 600, "bearing_deg": 90}]
        ),
        terrain=terrain,
    )
    site = result["touchdown_site"]
    assert site["offset_m"] == pytest.approx(600, abs=5)
    assert site["bearing_deg"] == pytest.approx(90, abs=1)
    expected = destination_point(
        SITE["latitude_deg"],
        SITE["longitude_deg_east"],
        site["offset_m"],
        site["bearing_deg"],
        R,
    )
    assert terrain.calls[0] == pytest.approx(expected)
    assert result["outcome"] == "success_with_warnings"
    assert "target_miss" in {w["code"] for w in result["warnings"]}


def test_landing_delay_shifts_the_surface_timeline():
    result = run(MissionRunRequest(faults=[{"kind": "landing_delay", "delay_h": 24}]))
    assert utc(result["landing_utc"]) == T0 + 24 * H
    assert utc(result["power"]["trace"][0]["utc"]) == pytest.approx(
        T0 + 24 * H, abs=timedelta(seconds=1)
    )
    with pytest.raises(MissionRunError):
        run(MissionRunRequest(faults=[{"kind": "landing_delay", "delay_h": 240}]))


def test_darkness_drains_the_battery():
    result = run(data=analysis(sun=[iv(0, 240, "dark")]))
    assert result["outcome"] == "failure"
    assert result["failure"]["gate"] == "power"
    assert result["failure"]["code"] == "battery_depleted"
    expected = T0 + timedelta(hours=3600 / 350)
    assert abs(utc(result["failure"]["at_utc"]) - expected) <= timedelta(seconds=2)
    assert gate(result, "communication")["status"] == "pass"
    assert utc(gate(result, "communication")["evaluated_until_utc"]) == pytest.approx(
        utc(result["failure"]["at_utc"]), abs=timedelta(seconds=1)
    )


def test_earliest_surface_failure_wins():
    result = run(
        data=analysis(
            sun=[iv(0, 240, "dark")],
            earth=[iv(0, 5, "blocked"), iv(5, 240, "visible")],
        )
    )
    assert result["failure"]["gate"] == "communication"
    assert result["failure"]["code"] == "first_contact_deadline"
    assert utc(result["failure"]["at_utc"]) == pytest.approx(
        T0 + H, abs=timedelta(seconds=1)
    )
    assert gate(result, "power")["status"] == "pass"


def test_unknown_sunlight_makes_the_run_inconclusive():
    result = run(data=analysis(sun=[iv(0, 1, "full"), iv(1, 240, "unknown")]))
    assert result["outcome"] == "inconclusive"
    assert gate(result, "power")["status"] == "unknown"
    assert result["failure"] is None


def test_curated_scenario_and_unknown_scenario():
    result = run(MissionRunRequest(scenario_id="battery-cell-loss"))
    assert result["scenario_id"] == "battery-cell-loss"
    assert result["faults"][0]["kind"] == "battery_capacity_loss"
    with pytest.raises(MissionRunError):
        run(MissionRunRequest(scenario_id="missing"))


def test_custom_faults_can_fail_a_mission_that_succeeds_nominally():
    data = analysis(sun=[iv(0, 240, "full")])
    assert run(data=data)["outcome"] == "success"
    result = run(
        MissionRunRequest(
            faults=[
                {
                    "kind": "solar_array_degradation",
                    "start_after_landing_h": 0,
                    "output_fraction": 0.2,
                }
            ]
        ),
        data=data,
    )
    assert result["failure"]["code"] == "battery_depleted"


# ------------------------------------------------------------- API


def write_dem(root, value=1234.0, size=201):
    folder = root / "terrain/raw" / ("a" * 64)
    folder.mkdir(parents=True)
    path = folder / "source.tif"
    half = size * 80 / 2
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=size,
        height=size,
        count=1,
        dtype="float32",
        crs=f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={R} +units=m",
        transform=from_origin(-half, half, 80, 80),
        nodata=np.nan,
    ) as ds:
        ds.write(np.full((size, size), value, dtype="float32"), 1)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "product_id": "LDEM_80S_80MPP_ADJ",
                "sha256": {"source.tif": hashlib.sha256(path.read_bytes()).hexdigest()},
            }
        )
    )


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    with TestClient(app) as c:
        c.store = app.state.store
        yield c


POLE = dict(latitude_deg=-89.99, longitude_deg_east=0.0)


def stored_analysis(client, status="ready"):
    job = client.store.create(Submission(**POLE, start_utc=T0, end_utc=T0 + 240 * H))
    if status != "pending":
        client.store.update(job["id"], status, result={"analysis": analysis(site=POLE)})
    return job["id"]


def test_fault_catalog_route(client):
    body = client.get("/mission-faults").json()
    ids = [s["scenario_id"] for s in body["scenarios"]]
    assert "nominal" in ids
    kinds = {k["kind"] for k in body["fault_kinds"]}
    assert "propellant_leak" in kinds and "communication_outage" in kinds
    leak = next(k for k in body["fault_kinds"] if k["kind"] == "propellant_leak")
    assert leak["phase"] == "descent"
    assert "leak_rate_kg_s" in leak["schema"]["properties"]
    outage = next(k for k in body["fault_kinds"] if k["kind"] == "communication_outage")
    assert outage["phase"] == "surface"
    assert body["defaults"]["power"]["battery_capacity_wh"] == 4000


def test_mission_run_route(client, tmp_path):
    assert client.post("/analyses/missing/mission-run", json={}).status_code == 404
    pending = stored_analysis(client, "pending")
    assert client.post(f"/analyses/{pending}/mission-run", json={}).status_code == 409
    ready = stored_analysis(client)
    url = f"/analyses/{ready}/mission-run"
    assert client.post(url, json={}).status_code == 409  # terrain not cached
    write_dem(tmp_path / "data")
    response = client.post(url, json={})
    assert response.status_code == 200
    result = response.json()
    assert result["outcome"] == "success"
    assert result["analysis_id"] == ready
    assert gate(result, "touchdown_terrain")["metrics"][
        "site_slope_deg"
    ] == pytest.approx(0)
    assert client.post(url, json={"scenario_id": "missing"}).status_code == 422
    assert (
        client.post(
            url, json={"faults": [{"kind": "landing_delay", "delay_h": 500}]}
        ).status_code
        == 422
    )
    assert client.post(url, json={"faults": [{"kind": "x"}]}).status_code == 422
    crash = client.post(url, json={"scenario_id": "major-propellant-leak"}).json()
    assert crash["outcome"] == "failure" and crash["failure"]["gate"] == "descent"
    insufficient = stored_analysis(client, "insufficient_data")
    assert (
        client.post(f"/analyses/{insufficient}/mission-run", json={}).status_code == 200
    )
