"""R6/S10 seeded Monte Carlo risk over the R4 descent.

References for expected values:
- Wilson score interval: E. B. Wilson (1927), J. Am. Stat. Assoc. 22(158).
- Sample covariance (ddof = 1) of +-a u, +-b v is (2 a^2 u u^T + 2 b^2 v v^T) / 3.
- Free fall: v^2 = v0^2 + 2 g h.
- Apollo 11 nominal at a zero-height site (R4 model, 0.5 s step): about
  7 396 kg of propellant used from 15 151 kg at PDI; the default R5 reserve
  (145 fps) needs about 112 kg at the landing mass.
"""

import hashlib
import json
import math

import numpy as np
import pytest
import rasterio
from affine import Affine
from fastapi.testclient import TestClient
from pydantic import ValidationError
from rasterio.transform import from_origin

from app.analysis_api import create_app
from app.descent import load_profiles
from app.risk import (
    FAILURE_CAUSES,
    MAX_REPORTED_POINTS,
    SOURCES,
    LocalSlopeMap,
    RiskError,
    RiskRequest,
    UncertaintySettings,
    dispersion_ellipse,
    load_slope_map,
    rank_correlation,
    run_monte_carlo,
    sample_standard_normals,
    touchdown_with_terrain_error,
    wilson_interval,
)
from shared.constants import MOON_GM_M3_S2, MOON_REFERENCE_RADIUS_M as R

SITE = dict(
    site_id="malapert-massif-r23", latitude_deg=-85.9948, longitude_deg_east=359.76422
)
ZERO = dict(
    thrust_sigma_fraction=0.0,
    isp_sigma_fraction=0.0,
    pdi_downrange_sigma_m=0.0,
    pdi_speed_sigma_mps=0.0,
    terrain_height_sigma_m=0.0,
    navigation_sigma_m=0.0,
)
GRAVITY = MOON_GM_M3_S2 / R**2


def request(**changes):
    body = dict(SITE, runs=10, seed=1)
    body.update(changes)
    return RiskRequest(**body)


def only(**sigmas):
    return UncertaintySettings(**(ZERO | sigmas))


def flat_map(value_deg=2.0, size=101):
    half = size * 80 / 2
    return LocalSlopeMap(
        slope_deg=np.full((size, size), value_deg),
        transform=from_origin(-half, half, 80, 80),
        site_xy_m=(0.0, 0.0),
        north_unit_xy=(0.0, 1.0),
        east_unit_xy=(1.0, 0.0),
        pixel_m=80.0,
    )


# ------------------------------------------------------------ validation


def test_defaults_are_bounded_planning_assumptions():
    settings = UncertaintySettings()
    assert 0 < settings.thrust_sigma_fraction <= 0.2
    assert 0 < settings.isp_sigma_fraction <= 0.1
    assert settings.navigation_sigma_m > 0
    assert settings.terrain_height_sigma_m > 0
    body = request()
    assert body.runs == 10 and body.seed == 1
    assert RiskRequest(**SITE, seed=3).runs == 200
    assert len(SOURCES) == 7 and len(set(SOURCES)) == 7
    assert "hazard_unknown" in FAILURE_CAUSES


@pytest.mark.parametrize(
    "changes",
    [
        {"runs": 5},
        {"runs": 1001},
        {"runs": 10.0},
        {"seed": -1},
        {"seed": 2**32},
        {"latitude_deg": -70.0},
        {"approach_azimuth_deg": 360.0},
        {"uncertainty": {"navigation_sigma_m": -1.0}},
        {"uncertainty": {"thrust_sigma_fraction": 0.5}},
        {"uncertainty": {"unknown": 1.0}},
        {"extra": True},
    ],
)
def test_invalid_requests(changes):
    with pytest.raises(ValidationError):
        RiskRequest(**(dict(SITE, seed=1) | changes))


def test_seed_is_required():
    with pytest.raises(ValidationError):
        RiskRequest(**SITE)


# ------------------------------------------------------------ statistics


def test_standard_normals_are_seeded():
    a = sample_standard_normals(50, 42)
    b = sample_standard_normals(50, 42)
    c = sample_standard_normals(50, 43)
    assert a.shape == (50, len(SOURCES))
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)
    assert np.array_equal(
        a, np.random.default_rng(42).standard_normal((50, len(SOURCES)))
    )


def _wilson(s, n, z=1.96):
    p = s / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z / (1 + z * z / n) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return centre - half, centre + half


@pytest.mark.parametrize("s,n", [(0, 10), (5, 10), (10, 10), (197, 200), (1, 1000)])
def test_wilson_interval(s, n):
    lo, hi = wilson_interval(s, n)
    exp_lo, exp_hi = _wilson(s, n)
    assert lo == pytest.approx(max(exp_lo, 0.0), abs=1e-12)
    assert hi == pytest.approx(min(exp_hi, 1.0), abs=1e-12)
    assert 0.0 <= lo <= s / n <= hi <= 1.0


def test_wilson_reference_values():
    assert wilson_interval(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-4)
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == 1.0
    with pytest.raises(ValueError):
        wilson_interval(3, 0)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_dispersion_ellipse_known_covariance():
    a, b, theta = 30.0, 10.0, math.radians(30)
    u = np.array([math.cos(theta), math.sin(theta)])
    v = np.array([-math.sin(theta), math.cos(theta)])
    centre = np.array([5.0, -7.0])
    points = np.array([centre + a * u, centre - a * u, centre + b * v, centre - b * v])
    one = dispersion_ellipse(points, 1.0)
    three = dispersion_ellipse(points, 3.0)
    assert one["center_m"] == pytest.approx(list(centre))
    assert one["semi_major_m"] == pytest.approx(math.sqrt(2 * a * a / 3))
    assert one["semi_minor_m"] == pytest.approx(math.sqrt(2 * b * b / 3))
    assert three["semi_major_m"] == pytest.approx(3 * one["semi_major_m"])
    assert one["orientation_deg"] % 180 == pytest.approx(30.0)
    expected = (2 * a * a * np.outer(u, u) + 2 * b * b * np.outer(v, v)) / 3
    assert np.allclose(one["covariance_m2"], expected)


def test_dispersion_ellipse_degenerate():
    same = dispersion_ellipse(np.array([[1.0, 2.0]] * 5), 3.0)
    assert same["semi_major_m"] == 0.0 and same["semi_minor_m"] == 0.0
    assert same["center_m"] == [1.0, 2.0]
    single = dispersion_ellipse(np.array([[1.0, 2.0]]), 3.0)
    assert single["semi_major_m"] == 0.0


def test_rank_correlation():
    x = [1, 2, 3, 4, 5]
    assert rank_correlation(x, [10, 20, 30, 40, 100]) == pytest.approx(1.0)
    assert rank_correlation(x, [5, 4, 3, 2, 1]) == pytest.approx(-1.0)
    assert rank_correlation(x, [7, 7, 7, 7, 7]) is None
    assert rank_correlation([1, 1, 1], [1, 2, 3]) is None
    assert -1.0 <= rank_correlation(x, [2, 1, 4, 3, 5]) <= 1.0


# ------------------------------------------------------------ terrain error


SAMPLES = [
    {"altitude_m": 30.0, "downrange_m": -40.0, "vertical_speed_mps": -3.0,
     "horizontal_speed_mps": 2.0},
    {"altitude_m": 10.0, "downrange_m": -10.0, "vertical_speed_mps": -2.0,
     "horizontal_speed_mps": 1.0},
    {"altitude_m": 2.0, "downrange_m": -1.0, "vertical_speed_mps": -1.2,
     "horizontal_speed_mps": 0.3},
    {"altitude_m": 0.0, "downrange_m": 0.2, "vertical_speed_mps": -0.9,
     "horizontal_speed_mps": 0.1},
]  # fmt: skip
FINAL = {"vertical_speed_mps": -0.9, "horizontal_speed_mps": 0.1}


def test_no_terrain_error_uses_the_believed_touchdown():
    state = touchdown_with_terrain_error(SAMPLES, FINAL, 0.0, GRAVITY)
    assert state == pytest.approx(
        {"downrange_m": 0.2, "vertical_speed_mps": -0.9, "horizontal_speed_mps": 0.1}
    )


def test_lower_ground_adds_a_free_fall():
    state = touchdown_with_terrain_error(SAMPLES, FINAL, -2.0, GRAVITY)
    assert state["vertical_speed_mps"] == pytest.approx(
        -math.sqrt(0.81 + 2 * GRAVITY * 2.0)
    )
    assert state["horizontal_speed_mps"] == pytest.approx(0.1)
    assert state["downrange_m"] == pytest.approx(0.2)


def test_higher_ground_contacts_earlier():
    state = touchdown_with_terrain_error(SAMPLES, FINAL, 5.0, GRAVITY)
    assert state == pytest.approx(
        {"downrange_m": -1.0, "vertical_speed_mps": -1.2, "horizontal_speed_mps": 0.3}
    )
    state = touchdown_with_terrain_error(SAMPLES, FINAL, 10.0, GRAVITY)
    assert state["vertical_speed_mps"] == pytest.approx(-2.0)
    with pytest.raises(RiskError):
        touchdown_with_terrain_error(SAMPLES, FINAL, 50.0, GRAVITY)


# ------------------------------------------------------------ slope map


def test_slope_map_lookup_follows_the_approach_frame():
    grid = np.tile(np.arange(101, dtype=float), (101, 1))  # slope = column index
    slope_map = flat_map()
    slope_map = LocalSlopeMap(
        slope_deg=grid,
        transform=slope_map.transform,
        site_xy_m=(0.0, 0.0),
        north_unit_xy=(0.0, 1.0),
        east_unit_xy=(1.0, 0.0),
        pixel_m=80.0,
    )
    # Travel north: crossrange is east (+x). Travel east: downrange is +x.
    assert slope_map.slopes_at([0.0], [800.0], 0.0)[0] == 60
    assert slope_map.slopes_at([800.0], [0.0], 90.0)[0] == 60
    # Travel south: crossrange (to the right) is west (-x).
    assert slope_map.slopes_at([0.0], [800.0], 180.0)[0] == 40
    assert np.isnan(slope_map.slopes_at([10000.0], [0.0], 0.0)[0])
    down, cross, slope = slope_map.cell_offsets(0.0)
    assert down.shape == cross.shape == slope.shape == (101 * 101,)
    index = 50 * 101 + 60
    assert cross[index] == pytest.approx(800.0)
    assert down[index] == pytest.approx(0.0)
    assert slope[index] == 60


def write_dem(root, value=0.0, size=21):
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


def test_load_slope_map_from_verified_dem(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_slope_map(tmp_path, -90.0, 0.0, 400.0)
    write_dem(tmp_path, size=41)
    slope_map = load_slope_map(tmp_path, -90.0, 0.0, 800.0)
    assert slope_map.pixel_m == 80.0
    assert isinstance(slope_map.transform, Affine)
    inner = slope_map.slope_deg[1:-1, 1:-1]
    assert inner.size and np.allclose(inner, 0.0)
    north = np.array(slope_map.north_unit_xy)
    east = np.array(slope_map.east_unit_xy)
    assert np.linalg.norm(north) == pytest.approx(1.0, abs=1e-6)
    assert np.linalg.norm(east) == pytest.approx(1.0, abs=1e-6)
    assert float(north @ east) == pytest.approx(0.0, abs=1e-6)
    # Window is clipped to the raster rather than failing.
    wide = load_slope_map(tmp_path, -90.0, 0.0, 50000.0)
    assert wide.slope_deg.shape[0] <= 41 and wide.slope_deg.shape[1] <= 41
    with pytest.raises(RiskError):
        load_slope_map(tmp_path, -85.0, 0.0, 400.0)


# ------------------------------------------------------------ Monte Carlo


@pytest.fixture(scope="module")
def deterministic():
    return run_monte_carlo(request(uncertainty=only()), 0.0)


def test_zero_uncertainty_reproduces_the_nominal_plan(deterministic):
    result = deterministic
    assert result["nominal"]["feasible"] is True
    assert result["runs"] == 10 and result["seed"] == 1
    assert result["success"]["count"] == 10
    assert result["success"]["rate"] == 1.0
    lo, hi = result["success"]["wilson95"]
    assert lo == pytest.approx(_wilson(10, 10)[0]) and hi == 1.0
    assert set(result["failure_causes"]) == set(FAILURE_CAUSES)
    assert all(count == 0 for count in result["failure_causes"].values())
    landing = result["landing"]
    assert abs(landing["mean_down_m"]) < 1.0
    assert landing["mean_cross_m"] == 0.0
    assert landing["ellipse_3sigma"]["semi_major_m"] == pytest.approx(0.0, abs=1e-6)
    assert landing["within_3sigma_fraction"] == 1.0
    assert result["hazard"]["evaluated"] is False
    assert result["hazard"]["ellipse_3sigma_cells"] is None
    assert result["dominant_failure_source"] is None
    assert all(not row["enabled"] for row in result["sensitivity"])
    assert result["assumptions"]


def test_result_structure(deterministic):
    result = deterministic
    assert len(result["result_id"]) == 64
    assert result["settings"]["seed"] == 1
    points = result["landing"]["points"]
    assert len(points) == 10
    assert sum(p["success"] for p in points) == result["success"]["count"]
    assert set(points[0]) == {"down_m", "cross_m", "success"}
    for key in ("ellipse_1sigma", "ellipse_3sigma"):
        assert set(result["landing"][key]) >= {
            "center_m",
            "semi_major_m",
            "semi_minor_m",
            "orientation_deg",
            "covariance_m2",
        }
    propellant = result["propellant"]
    for key in ("remaining_kg", "margin_above_reserve_kg"):
        stats = propellant[key]
        assert (
            stats["min"] <= stats["p05"] <= stats["p50"] <= stats["p95"] <= stats["max"]
        )
    histogram = propellant["histogram"]
    assert len(histogram["edges_kg"]) == len(histogram["counts"]) + 1
    assert sum(histogram["counts"]) == 10
    assert [row["source"] for row in result["sensitivity"]] == list(SOURCES)
    assert result["noise_threshold"] == pytest.approx(2 / math.sqrt(10))
    # 7 396 kg used of 15 151 kg: margin above the 145 fps reserve is ~650 kg.
    assert 500 < propellant["margin_above_reserve_kg"]["p50"] < 800


def test_same_seed_is_reproducible_and_seed_changes_results():
    first = run_monte_carlo(request(seed=11), 0.0)
    second = run_monte_carlo(request(seed=11), 0.0)
    other = run_monte_carlo(request(seed=12), 0.0)
    assert first == second
    assert first["result_id"] == second["result_id"]
    assert first["result_id"] != other["result_id"]
    assert first["landing"]["points"] != other["landing"]["points"]


def test_navigation_error_spreads_the_landing_points():
    result = run_monte_carlo(
        request(runs=60, uncertainty=only(navigation_sigma_m=200.0)), 0.0
    )
    ellipse = result["landing"]["ellipse_1sigma"]
    # Independent 200 m errors on both axes: both 1-sigma axes near 200 m.
    assert 120 < ellipse["semi_minor_m"] <= ellipse["semi_major_m"] < 300
    assert result["success"]["count"] == 60
    rows = {row["source"]: row for row in result["sensitivity"]}
    assert rows["navigation_down"]["enabled"] and rows["navigation_cross"]["enabled"]
    assert not rows["thrust"]["enabled"]
    assert 0.0 <= result["landing"]["within_3sigma_fraction"] <= 1.0


def test_insufficient_propellant_is_attributed():
    apollo = load_profiles()["apollo-lm-11"]
    short = apollo.model_copy(update={"usable_propellant_kg": 7000.0}).model_dump()
    result = run_monte_carlo(request(custom_profile=short, uncertainty=only()), 0.0)
    assert result["nominal"]["feasible"] is False
    assert result["success"]["count"] == 0
    assert result["failure_causes"]["propellant_exhausted"] == 10
    lo, hi = result["success"]["wilson95"]
    assert lo == 0.0 and hi == pytest.approx(_wilson(0, 10)[1])


def test_reserve_violation_is_separate_from_exhaustion():
    apollo = load_profiles()["apollo-lm-11"]
    tight = apollo.model_copy(update={"usable_propellant_kg": 7450.0}).model_dump()
    result = run_monte_carlo(request(custom_profile=tight, uncertainty=only()), 0.0)
    causes = result["failure_causes"]
    assert causes["propellant_reserve"] == 10
    assert causes["propellant_exhausted"] == 0
    assert result["propellant"]["margin_above_reserve_kg"]["max"] < 0
    relaxed = run_monte_carlo(
        request(custom_profile=tight, uncertainty=only(), reserve_delta_v_mps=0.0), 0.0
    )
    assert relaxed["failure_causes"]["propellant_reserve"] == 0


def test_terrain_error_dominates_when_it_is_the_only_source():
    result = run_monte_carlo(
        request(runs=40, seed=7, uncertainty=only(terrain_height_sigma_m=0.3)), 0.0
    )
    causes = result["failure_causes"]
    assert 0 < causes["vertical_speed"] < 40
    assert result["dominant_failure_source"] == "terrain_height"
    rows = {row["source"]: row for row in result["sensitivity"]}
    terrain = rows["terrain_height"]
    assert terrain["enabled"] and terrain["significant"]
    assert terrain["failure_association"] > result["noise_threshold"]
    # Lower true ground (negative error) causes the hard landings.
    assert terrain["failure_correlation"] < 0


def test_unknown_vehicle_and_rejected_orbit_raise():
    with pytest.raises(RiskError):
        run_monte_carlo(request(profile_id="missing"), 0.0)
    with pytest.raises(RiskError):
        run_monte_carlo(request(orbit={"altitude_km": 30.0}), 20000.0)


def test_flat_slope_map_keeps_successes():
    result = run_monte_carlo(
        request(runs=20, uncertainty=only(navigation_sigma_m=300.0)),
        0.0,
        flat_map(2.0),
    )
    assert result["hazard"]["evaluated"] is True
    assert result["failure_causes"]["hazard_slope"] == 0
    assert result["failure_causes"]["hazard_unknown"] == 0
    assert result["success"]["count"] == 20
    cells = result["hazard"]["ellipse_3sigma_cells"]
    assert cells["count"] > 0
    assert cells["steep_fraction"] == 0.0 and cells["unknown_fraction"] == 0.0


def test_steep_slope_map_fails_every_landing():
    result = run_monte_carlo(
        request(runs=20, uncertainty=only(navigation_sigma_m=300.0)),
        0.0,
        flat_map(30.0),
    )
    assert result["failure_causes"]["hazard_slope"] == 20
    assert result["success"]["count"] == 0
    assert result["hazard"]["ellipse_3sigma_cells"]["steep_fraction"] == 1.0


def test_landings_beyond_the_map_are_unknown_not_safe():
    result = run_monte_carlo(
        request(runs=30, uncertainty=only(navigation_sigma_m=2000.0)),
        0.0,
        flat_map(2.0, size=11),
    )
    assert result["failure_causes"]["hazard_unknown"] > 0
    assert result["success"]["count"] == 30 - result["failure_causes"]["hazard_unknown"]


def test_reported_points_are_capped():
    assert MAX_REPORTED_POINTS >= 100
    result = run_monte_carlo(request(runs=MAX_REPORTED_POINTS + 20), 0.0)
    assert len(result["landing"]["points"]) == MAX_REPORTED_POINTS
    assert sum(result["propellant"]["histogram"]["counts"]) == MAX_REPORTED_POINTS + 20


# ------------------------------------------------------------ API


def test_route(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as client:
        body = dict(
            site_id="pole", latitude_deg=-90.0, longitude_deg_east=0.0, runs=10, seed=5
        )
        assert client.post("/descents/risk", json=body).status_code == 409
        write_dem(tmp_path / "data", size=41)
        response = client.post("/descents/risk", json=body)
        assert response.status_code == 200
        result = response.json()
        assert result["hazard"]["evaluated"] is True
        assert result["runs"] == 10
        again = client.post("/descents/risk", json=body).json()
        assert again["result_id"] == result["result_id"]
        for change in ({"profile_id": "missing"}, {"runs": 5}, {"latitude_deg": -85.0}):
            assert client.post("/descents/risk", json=body | change).status_code == 422
        assert (
            client.post(
                "/descents/risk", json={k: v for k, v in body.items() if k != "seed"}
            ).status_code
            == 422
        )
