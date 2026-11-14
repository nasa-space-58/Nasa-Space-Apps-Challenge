"""R4 powered descent: sourced profile, orbital mechanics, throttle rule,
ZEM/ZEV guidance, Apollo 11 reference comparison, reachability and API.

References:
- NASA TN D-6846 (1972), Table I: Apollo 11 premission powered descent.
  PDI inertial velocity 5560 fps, DOI ~75 fps from 60 n.mi., total powered
  descent delta-v 6775 fps; gates at 506 s / 606 s / 714 s.
- Apollo 11 Mission Report, Table A-I: LM 33 401.6 lb after DOI cutoff.
- JPL astrodynamic parameters: GM(Moon) = 4902.800118 km^3/s^2.
Tolerances: vis-viva quantities are analytic (0.1 %); DOI is "approximately"
75 fps in the source (10 %); the guidance law differs from Apollo P63/P64 and
omits hover/redesignation, so total powered delta-v is compared within 5 %.
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
    DEFAULT_ORBIT_ALTITUDE_KM,
    DescentError,
    DescentRequest,
    OrbitSettings,
    Tolerances,
    VehicleProfile,
    apollo11_reference,
    engine_thrust,
    gate_states,
    hohmann_deorbit,
    load_profiles,
    plan_descent,
    plane_change,
    simulate_powered_descent,
    terrain_height_m,
    zem_zev_acceleration,
)
from shared.constants import (
    FOOT_M,
    MOON_GM_M3_S2,
    MOON_REFERENCE_RADIUS_M as R,
    NAUTICAL_MILE_M,
    POUND_FORCE_N,
    POUND_MASS_KG,
    STANDARD_GRAVITY_M_S2,
)

FPS = FOOT_M
MALAPERT = dict(
    site_id="malapert-massif-r23", latitude_deg=-85.9948, longitude_deg_east=359.76422
)


@pytest.fixture(scope="module")
def apollo():
    return load_profiles()["apollo-lm-11"]


@pytest.fixture(scope="module")
def nominal():
    return plan_descent(DescentRequest(**MALAPERT), terrain_height_m=0.0)


# ------------------------------------------------------------ sourced inputs


def test_constants_are_sourced_values():
    assert MOON_GM_M3_S2 == 4902.800118e9
    assert STANDARD_GRAVITY_M_S2 == 9.80665
    assert FOOT_M == 0.3048 and NAUTICAL_MILE_M == 1852.0
    assert POUND_FORCE_N == pytest.approx(4.4482216152605, rel=1e-15)
    assert POUND_MASS_KG == 0.45359237
    assert DEFAULT_ORBIT_ALTITUDE_KM == pytest.approx(60 * NAUTICAL_MILE_M / 1000)


def test_apollo_profile_converts_document_units(apollo):
    assert isinstance(apollo, VehicleProfile)
    assert apollo.profile_id == "apollo-lm-11"
    assert apollo.mass_at_pdi_kg == pytest.approx(33401.6 * POUND_MASS_KG, rel=1e-12)
    assert apollo.usable_propellant_kg == pytest.approx(
        17992.5 * POUND_MASS_KG, rel=1e-12
    )
    assert apollo.max_thrust_n == pytest.approx(10000 * POUND_FORCE_N, rel=1e-12)
    assert apollo.design_thrust_n == pytest.approx(10500 * POUND_FORCE_N, rel=1e-12)
    assert (apollo.min_throttle_fraction, apollo.max_throttle_fraction) == (0.10, 0.60)
    assert apollo.isp_s == 309.7
    # Physical sanity: dry mass stays positive; maximum thrust below design.
    assert apollo.usable_propellant_kg < apollo.mass_at_pdi_kg
    assert apollo.max_thrust_n < apollo.design_thrust_n


def test_reference_table_is_recorded():
    ref = apollo11_reference()
    assert ref["pdi_inertial_velocity_fps"] == 5560
    assert ref["total_powered_delta_v_fps"] == 6775
    assert [ref[g]["time_s"] for g in ("high_gate", "low_gate", "touchdown")] == [
        506,
        606,
        714,
    ]
    assert "TN D-6846" in ref["source"]


@pytest.mark.parametrize(
    "changes",
    [
        {"latitude_deg": -70},
        {"longitude_deg_east": 360},
        {"orbit": {"altitude_km": 10}},
        {"orbit": {"inclination_deg": 181}},
        {"tolerances": {"position_m": 0}},
        {"unexpected": 1},
    ],
)
def test_invalid_requests(changes):
    with pytest.raises(ValidationError):
        DescentRequest(**(MALAPERT | changes))


def test_invalid_custom_profile():
    with pytest.raises(ValidationError):
        VehicleProfile(
            mass_at_pdi_kg=5000,
            usable_propellant_kg=1000,
            max_thrust_n=-1,
            design_thrust_n=10000,
            min_throttle_fraction=0.1,
            max_throttle_fraction=0.6,
            isp_s=300,
        )


# ----------------------------------------------------------- orbital mechanics


def test_hohmann_deorbit_matches_apollo_reference():
    r1 = R + 60 * NAUTICAL_MILE_M
    rp = R + 50000 * FOOT_M
    result = hohmann_deorbit(r1, rp)
    assert result["periapsis_speed_mps"] / FPS == pytest.approx(5560, rel=1e-3)
    assert result["delta_v_mps"] / FPS == pytest.approx(75, rel=0.10)
    a = (r1 + rp) / 2
    assert result["coast_s"] == pytest.approx(
        math.pi * math.sqrt(a**3 / MOON_GM_M3_S2), rel=1e-12
    )
    # Energy check: vis-viva at periapsis.
    assert result["periapsis_speed_mps"] ** 2 == pytest.approx(
        MOON_GM_M3_S2 * (2 / rp - 1 / a), rel=1e-12
    )


@pytest.mark.parametrize(
    "r1, rp", [(R + 15000, R + 20000), (R + 20000, R + 20000), (R + 20000, 0)]
)
def test_hohmann_rejects_raising_or_degenerate_transfers(r1, rp):
    with pytest.raises(ValueError):
        hohmann_deorbit(r1, rp)


@pytest.mark.parametrize(
    "latitude, inclination, required",
    [
        (-85.9948, 90.0, 0.0),
        (-85.9948, 100.0, 5.9948),
        (-85.9948, 80.0, 5.9948),
        (-85.0, 0.0, 85.0),
        (-80.0, 85.0, 0.0),
    ],
)
def test_plane_change(latitude, inclination, required):
    result = plane_change(latitude, inclination, 1600.0)
    assert result["required_deg"] == pytest.approx(required, abs=1e-9)
    expected = 2 * 1600.0 * math.sin(math.radians(required) / 2)
    assert result["delta_v_mps"] == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------- engine model


@pytest.mark.parametrize(
    "fraction, expected_fraction, saturated",
    [
        (0.50, 0.50, False),
        (0.10, 0.10, False),
        (0.05, 0.10, False),
        (0.00, 0.10, False),
        (0.70, None, False),  # above 60 % of design: fixed throttle position
        (1.20, None, True),  # beyond maximum thrust: saturated
    ],
)
def test_throttle_rule(apollo, fraction, expected_fraction, saturated):
    thrust, flag = engine_thrust(fraction * apollo.design_thrust_n, apollo)
    expected = (
        apollo.max_thrust_n
        if expected_fraction is None
        else expected_fraction * apollo.design_thrust_n
    )
    assert thrust == pytest.approx(expected, rel=1e-12)
    assert flag is saturated


def test_negative_thrust_command_is_invalid(apollo):
    with pytest.raises(ValueError):
        engine_thrust(-1.0, apollo)


# --------------------------------------------------------------------- guidance


def test_zem_zev_formula():
    r, v = np.array([0.0, 1000.0]), np.array([100.0, -10.0])
    rf, vf, g = np.array([3000.0, 0.0]), np.array([0.0, -1.0]), np.array([0.0, -1.62])
    t = 40.0
    zem = rf - (r + v * t + 0.5 * g * t**2)
    zev = vf - (v + g * t)
    assert np.allclose(
        zem_zev_acceleration(r, v, rf, vf, t, g), 6 * zem / t**2 - 2 * zev / t
    )


def test_zem_zev_reaches_target_under_constant_gravity():
    # Closed loop on a double integrator with uniform gravity must hit the target.
    r, v = np.array([-5000.0, 2000.0]), np.array([150.0, -40.0])
    rf, vf, g = np.array([0.0, 150.0]), np.array([15.0, -5.0]), np.array([0.0, -1.62])
    dt, t = 0.01, 60.0
    a = np.zeros(2)
    while t > 1e-9:
        # Hold the last command for the final 0.5 s, as t_go -> 0 is singular.
        if t > 0.5:
            a = zem_zev_acceleration(r, v, rf, vf, t, g)
        v = v + (a + g) * dt
        r = r + v * dt
        t -= dt
    assert np.linalg.norm(r - rf) < 1.0
    assert np.linalg.norm(v - vf) < 0.1


@pytest.mark.parametrize("t_go", [0.0, -1.0])
def test_zem_zev_requires_positive_time_to_go(t_go):
    z = np.zeros(2)
    with pytest.raises(ValueError):
        zem_zev_acceleration(z, z, z, z, t_go, z)


def test_gate_states_follow_table_i():
    states = gate_states(R)
    assert [s["name"] for s in states] == ["braking", "approach", "terminal"]
    assert [s["duration_s"] for s in states] == [506.0, 100.0, 108.0]
    up = np.array([0.0, 1.0])
    high, low, touch = states
    assert np.linalg.norm(high["position_m"]) - R == pytest.approx(
        7515 * FOOT_M, abs=1e-6
    )
    assert np.linalg.norm(low["position_m"]) - R == pytest.approx(
        512 * FOOT_M, abs=1e-6
    )
    assert np.allclose(touch["position_m"], R * up, atol=1e-6)
    assert np.allclose(touch["velocity_mps"], -3 * FOOT_M * up, atol=1e-9)
    # High gate: 4.5 n.mi. uprange, -145 fps altitude rate, 506 fps total speed.
    angle = math.atan2(high["position_m"][0], high["position_m"][1])
    assert angle * R == pytest.approx(-4.5 * NAUTICAL_MILE_M, rel=1e-9)
    radial = high["position_m"] / np.linalg.norm(high["position_m"])
    assert high["velocity_mps"] @ radial == pytest.approx(-145 * FOOT_M, abs=1e-9)
    assert np.linalg.norm(high["velocity_mps"]) == pytest.approx(
        506 * FOOT_M, rel=1e-12
    )
    # Low gate on a 16 deg glide slope with 55 fps horizontal and -16 fps vertical.
    low_angle = math.atan2(low["position_m"][0], low["position_m"][1])
    assert -low_angle * R == pytest.approx(
        512 * FOOT_M / math.tan(math.radians(16)), rel=1e-9
    )
    radial = low["position_m"] / np.linalg.norm(low["position_m"])
    assert low["velocity_mps"] @ radial == pytest.approx(-16 * FOOT_M, abs=1e-9)
    horizontal = low["velocity_mps"] - (low["velocity_mps"] @ radial) * radial
    assert np.linalg.norm(horizontal) == pytest.approx(55 * FOOT_M, abs=1e-9)


# ------------------------------------------------------------------ simulation


def test_simulation_lands_on_the_site(apollo):
    vp = hohmann_deorbit(R + 60 * NAUTICAL_MILE_M, R + 50000 * FOOT_M)[
        "periapsis_speed_mps"
    ]
    result = simulate_powered_descent(apollo, R, 520e3, vp, dt_s=0.2)
    assert [p["name"] for p in result["phases"]] == ["braking", "approach", "terminal"]
    assert result["phases"][-1]["end_s"] == pytest.approx(714.0)
    final = result["final"]
    assert final["position_error_m"] < 5
    assert abs(final["vertical_speed_mps"]) <= 1.0
    assert abs(final["horizontal_speed_mps"]) <= 0.5
    assert result["propellant_exhausted"] is False
    masses = [s["mass_kg"] for s in result["samples"]]
    assert all(b <= a + 1e-9 for a, b in zip(masses, masses[1:]))
    for sample in result["samples"]:
        assert apollo.min_throttle_fraction - 1e-9 <= sample["throttle_fraction"]
        assert (
            sample["throttle_fraction"]
            <= apollo.max_thrust_n / apollo.design_thrust_n + 1e-9
        )
        assert sample["altitude_m"] > -1.0


def test_nominal_plan_matches_apollo_11(nominal, apollo):
    assert nominal["feasible"] is True
    assert nominal["limiting_factors"] == []
    powered = nominal["totals"]["powered_delta_v_mps"]
    assert powered / FPS == pytest.approx(6775, rel=0.05)
    assert nominal["deorbit"]["delta_v_mps"] / FPS == pytest.approx(75, rel=0.10)
    assert nominal["deorbit"]["periapsis_speed_mps"] / FPS == pytest.approx(
        5560, rel=1e-3
    )
    assert 400e3 <= nominal["pdi_range_m"] <= 650e3
    names = [p["name"] for p in nominal["phases"]]
    assert names == ["coast", "braking", "approach", "terminal"]
    assert [p["delta_v_mps"] for p in nominal["phases"]][0] == 0
    assert sum(p["delta_v_mps"] for p in nominal["phases"]) == pytest.approx(
        powered, rel=1e-9
    )
    assert nominal["totals"]["duration_s"] == pytest.approx(
        nominal["deorbit"]["coast_s"] + 714.0
    )
    assert 0 < nominal["totals"]["propellant_used_kg"] < apollo.usable_propellant_kg
    assert nominal["totals"]["propellant_margin_kg"] == pytest.approx(
        apollo.usable_propellant_kg - nominal["totals"]["propellant_used_kg"], rel=1e-9
    )
    touchdown = nominal["touchdown"]
    assert touchdown["position_error_m"] <= 5
    assert abs(touchdown["vertical_speed_mps"]) <= 1.0
    assert abs(touchdown["horizontal_speed_mps"]) <= 0.5
    comparison = nominal["reference_comparison"]
    assert comparison["apollo11_total_powered_delta_v_fps"] == 6775
    assert abs(comparison["difference_percent"]) <= 5
    assert nominal["assumptions"] and all(
        isinstance(a, str) for a in nominal["assumptions"]
    )
    assert 0 < len(nominal["samples"]) <= 400


def test_plan_is_deterministic(nominal):
    assert plan_descent(DescentRequest(**MALAPERT), terrain_height_m=0.0) == nominal


def test_site_terrain_height_moves_the_target():
    high = plan_descent(DescentRequest(**MALAPERT), terrain_height_m=4786.6)
    assert high["site"]["radius_m"] == pytest.approx(R + 4786.6)
    assert high["deorbit"]["pdi_altitude_m"] == pytest.approx(4786.6 + 50000 * FOOT_M)
    assert high["feasible"] is True


def test_plane_change_is_a_limiting_factor():
    blocked = plan_descent(
        DescentRequest(**MALAPERT, orbit=OrbitSettings(inclination_deg=80)),
        terrain_height_m=0.0,
    )
    assert blocked["feasible"] is False
    factor = blocked["limiting_factors"][0]
    assert factor["factor"] == "plane_change"
    assert factor["value"] == pytest.approx(5.9948, abs=1e-6)
    allowed = plan_descent(
        DescentRequest(
            **MALAPERT, orbit=OrbitSettings(inclination_deg=80), allow_plane_change=True
        ),
        terrain_height_m=0.0,
    )
    assert allowed["feasible"] is True
    assert allowed["plane_change"]["delta_v_mps"] == pytest.approx(170.3, abs=1.0)
    assert allowed["totals"]["propellant_used_kg"] > 0


def test_large_plane_change_exhausts_propellant():
    result = plan_descent(
        DescentRequest(
            **MALAPERT, orbit=OrbitSettings(inclination_deg=70), allow_plane_change=True
        ),
        terrain_height_m=0.0,
    )
    assert result["feasible"] is False
    assert "propellant" in [f["factor"] for f in result["limiting_factors"]]


def test_underpowered_vehicle_is_infeasible():
    weak = VehicleProfile(
        name="weak",
        mass_at_pdi_kg=15000,
        usable_propellant_kg=8000,
        max_thrust_n=12000,
        design_thrust_n=12500,
        min_throttle_fraction=0.1,
        max_throttle_fraction=0.6,
        isp_s=310,
    )
    result = plan_descent(
        DescentRequest(**MALAPERT, custom_profile=weak), terrain_height_m=0.0
    )
    assert result["feasible"] is False
    assert {f["factor"] for f in result["limiting_factors"]} & {
        "touchdown_tolerance",
        "propellant",
    }


def test_unknown_profile_and_too_low_orbit():
    with pytest.raises(DescentError):
        plan_descent(
            DescentRequest(**MALAPERT, profile_id="missing"), terrain_height_m=0.0
        )
    with pytest.raises(DescentError):
        plan_descent(
            DescentRequest(**MALAPERT, orbit=OrbitSettings(altitude_km=30)),
            terrain_height_m=20000.0,
        )


def test_custom_tolerances_are_applied():
    strict = plan_descent(
        DescentRequest(**MALAPERT, tolerances=Tolerances(vertical_speed_mps=0.5)),
        terrain_height_m=0.0,
    )
    # The terminal target descends at 3 fps (0.914 m/s), above a 0.5 m/s limit.
    assert strict["feasible"] is False
    assert strict["limiting_factors"][0]["factor"] == "touchdown_tolerance"


# ------------------------------------------------------------- terrain and API


def write_dem(root, value=1234.0, size=21):
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


def test_terrain_height_from_cached_dem(tmp_path):
    with pytest.raises(FileNotFoundError):
        terrain_height_m(tmp_path, -90.0, 0.0)
    write_dem(tmp_path)
    assert terrain_height_m(tmp_path, -90.0, 0.0) == pytest.approx(1234.0)
    with pytest.raises(DescentError):
        terrain_height_m(tmp_path, -85.0, 0.0)


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as c:
        yield c


def test_routes(client, tmp_path):
    profiles = client.get("/vehicle-profiles").json()
    assert profiles["profiles"][0]["profile_id"] == "apollo-lm-11"
    assert profiles["profiles"][0]["sources"]
    assert profiles["apollo11_reference"]["total_powered_delta_v_fps"] == 6775
    body = dict(site_id="pole", latitude_deg=-90.0, longitude_deg_east=0.0)
    assert client.post("/descents", json=body).status_code == 409
    write_dem(tmp_path / "data")
    response = client.post("/descents", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["feasible"] is True
    assert result["site"]["terrain_height_m"] == pytest.approx(1234.0)
    assert (
        client.post("/descents", json=body | {"latitude_deg": -85.0}).status_code == 422
    )
    assert (
        client.post("/descents", json=body | {"profile_id": "missing"}).status_code
        == 422
    )
    assert (
        client.post("/descents", json=body | {"latitude_deg": -70}).status_code == 422
    )
