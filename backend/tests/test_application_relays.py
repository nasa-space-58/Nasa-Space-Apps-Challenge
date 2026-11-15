"""R13 relay-location selection after geometry and capacity validation.

Geometry reference: in the site-centred local tangent frame, a relay at
horizontal distance d and height difference du has elevation
atan2(du - d^2/(2R), d), with R = 1737.4 km (IAU/IAG lunar reference radius,
shared.constants). Hand case: RA covers S1 (3 points), RC covers S1/S2 in slots
2-3 (4 points), RF covers all 6 points; with resilience weight 0.5 the
optimum pair is RC+RF = 6 + 0.5 * 4.
"""

import math
from datetime import datetime, timezone
from fractions import Fraction

import pytest
from pydantic import ValidationError

from app.application_relays import (
    APPLICATION,
    FRAME,
    HorizonMask,
    RelayCandidate,
    RelayProblem,
    RelaySite,
    build_relays,
    generate_relays,
    horizon_at,
    link_geometry,
    relay_metrics,
    validate_relay_geometry,
)
from app.quantum_applications import audit_qubo, solve_exact, solve_milp
from shared.constants import MOON_REFERENCE_RADIUS_M as R

START = datetime(2026, 11, 20, tzinfo=timezone.utc)


def mask(*elevations):
    return HorizonMask(
        azimuth_step_deg=360 / len(elevations), elevations_deg=elevations
    )


def relay(
    name,
    east,
    north,
    up=100.0,
    *,
    mast=10.0,
    equipment=True,
    capacity=2,
    max_range=5000.0,
    earth=(1, 2, 3),
):
    return RelayCandidate(
        relay_id=name,
        east_m=east,
        north_m=north,
        up_m=up,
        mast_height_m=mast,
        has_relay_equipment=equipment,
        capacity_links=capacity,
        max_range_m=max_range,
        earth_visible_slots=earth,
    )


SITES = (
    RelaySite(
        site_id="S1",
        east_m=0.0,
        north_m=0.0,
        up_m=0.0,
        horizon=mask(0.0, 0.0, 0.0, 0.0),
        demand_slots=(0, 1, 2, 3),
        direct_earth_slots=(0,),
    ),
    RelaySite(
        site_id="S2",
        east_m=2000.0,
        north_m=0.0,
        up_m=0.0,
        horizon=mask(5.0, 5.0, 5.0, 5.0),
        demand_slots=(1, 2, 3),
    ),
)
CANDIDATES = (
    relay("RA", 0.0, 1000.0),
    relay("RB", 2000.0, 1000.0, capacity=1, earth=(2, 3)),
    relay("RC", 2000.0, 1000.0, earth=(2, 3)),
    relay("RD", 1000.0, 500.0, equipment=False),
    relay("RE", 0.0, 50000.0, up=0.0, mast=0.0),
    relay("RF", 2000.0, -1000.0),
)


def problem(**changes):
    body = dict(
        problem_id="relay-test",
        start_utc=START,
        slot_s=3600,
        slot_count=4,
        sites=SITES,
        candidates=CANDIDATES,
        select_count=2,
        clearance_margin_deg=0.5,
        resilience_weight=0.5,
    )
    body.update(changes)
    return RelayProblem(**body)


# ------------------------------------------------------------ geometry


def test_horizon_interpolation_wraps_azimuth():
    horizon = mask(0.0, 10.0, 20.0, 30.0)
    assert horizon_at(horizon, 0.0) == pytest.approx(0.0)
    assert horizon_at(horizon, 45.0) == pytest.approx(5.0)
    assert horizon_at(horizon, 270.0) == pytest.approx(30.0)
    assert horizon_at(horizon, 315.0) == pytest.approx(15.0)
    assert horizon_at(horizon, 360.0) == pytest.approx(0.0)
    assert horizon_at(horizon, -45.0) == pytest.approx(15.0)
    assert horizon_at(horizon, 359.999) == pytest.approx(0.0, abs=1e-3)


def test_horizon_mask_must_close_the_azimuth_grid():
    with pytest.raises(ValidationError):
        HorizonMask(azimuth_step_deg=7.0, elevations_deg=(0.0,) * 51)
    with pytest.raises(ValidationError):
        HorizonMask(azimuth_step_deg=90.0, elevations_deg=(0.0, 0.0, 0.0))
    with pytest.raises(ValidationError):
        HorizonMask(azimuth_step_deg=90.0, elevations_deg=(0.0, 0.0, 0.0, 95.0))


def test_link_geometry_includes_the_spherical_drop():
    range_m, azimuth, elevation = link_geometry(SITES[0], CANDIDATES[0])
    du = 110.0 - 2.0 - 1000.0**2 / (2 * R)
    assert azimuth == pytest.approx(0.0, abs=1e-9)
    assert elevation == pytest.approx(math.degrees(math.atan2(du, 1000.0)), abs=1e-9)
    assert range_m == pytest.approx(math.hypot(1000.0, du), rel=1e-12)
    _, east_azimuth, _ = link_geometry(SITES[0], relay("X", 500.0, 0.0))
    assert east_azimuth == pytest.approx(90.0)
    _, south_west, _ = link_geometry(SITES[0], relay("X", -500.0, -500.0))
    assert south_west == pytest.approx(225.0)


def test_far_relay_sinks_below_the_local_horizontal():
    _, _, elevation = link_geometry(SITES[0], CANDIDATES[4])
    # 50 km away the surface drops 50000^2 / (2R) = 719 m.
    assert elevation < -0.8


def test_validation_credits_links_and_rejects_with_codes():
    validation = validate_relay_geometry(problem())
    assert validation.accepted_relay_ids == ("RA", "RC", "RF")
    codes = {r.relay_id: r.code for r in validation.rejections}
    assert codes == {
        "RB": "capacity_exceeded",
        "RD": "no_relay_equipment",
        "RE": "no_credited_link",
    }
    assert validation.demand_points == (
        ("S1", 1),
        ("S1", 2),
        ("S1", 3),
        ("S2", 1),
        ("S2", 2),
        ("S2", 3),
    )
    links = {(k.relay_id, k.site_id): k for k in validation.links}
    assert links[("RA", "S1")].credited_slots == (1, 2, 3)
    assert links[("RA", "S2")].line_of_sight is False
    assert links[("RA", "S2")].credited_slots == ()
    assert links[("RC", "S2")].credited_slots == (2, 3)
    assert links[("RE", "S1")].in_range is False
    assert links[("RF", "S2")].elevation_deg > links[("RF", "S2")].horizon_deg + 0.5


def test_direct_earth_slots_need_no_relay():
    validation = validate_relay_geometry(problem())
    assert ("S1", 0) not in validation.demand_points


def test_higher_clearance_margin_removes_marginal_links():
    validation = validate_relay_geometry(problem(clearance_margin_deg=3.0))
    links = {(k.relay_id, k.site_id): k for k in validation.links}
    # RF is 2.73 deg above S1's flat horizon and 6.15 deg over S2's 5 deg
    # horizon: both blocked by a 3 deg margin; RA at 6.15 deg over S1 is kept.
    assert links[("RF", "S1")].line_of_sight is False
    assert links[("RF", "S2")].line_of_sight is False
    assert links[("RA", "S1")].line_of_sight is True


# ------------------------------------------------------------ metrics and selection


def test_coverage_resilience_and_gap_metrics():
    p = problem()
    validation = validate_relay_geometry(p)
    best = relay_metrics(p, validation, ("RC", "RF"))
    assert best["coverage"] == pytest.approx(1.0)
    assert best["resilient_points"] == 4
    assert best["resilience"] == pytest.approx(4 / 6)
    assert best["double_coverage"] == pytest.approx(4 / 6)
    assert best["longest_gap_s"] == 0
    weak = relay_metrics(p, validation, ("RA", "RC"))
    assert weak["covered_points"] == 5 and weak["coverage"] == pytest.approx(5 / 6)
    assert weak["resilient_points"] == 3
    assert weak["double_coverage"] == pytest.approx(2 / 6)
    assert weak["longest_gap_s"] == pytest.approx(3600.0)
    single = relay_metrics(p, validation, ("RF",))
    assert single["coverage"] == pytest.approx(1.0) and single["resilient_points"] == 0
    with pytest.raises(ValueError):
        relay_metrics(p, validation, ("RB",))


def test_hand_optimum_and_select_count_rule():
    instance = build_relays(problem())
    assert instance.application == APPLICATION == "relay_selection"
    assert instance.variable_labels == ("RA", "RC", "RF")
    assert any("capacity_exceeded" in r for r in instance.rejections)
    exact = solve_exact(instance)
    assert exact.bits == (0, 1, 1)
    assert Fraction(exact.objective) == 8
    assert instance.evaluate((1, 0, 1)).objective == Fraction("7.5")
    assert instance.evaluate((1, 1, 0)).objective == Fraction("6.5")
    too_many = instance.evaluate((1, 1, 1))
    assert not too_many.feasible
    assert any(i.startswith("select_count") for i in too_many.issues)
    assert not instance.evaluate((0, 0, 1)).feasible
    milp = solve_milp(instance)
    assert Fraction(milp.objective) == 8


def test_qubo_is_a_declared_heuristic():
    instance = build_relays(problem())
    assert instance.qubo.exact is False
    audit = audit_qubo(instance)
    assert audit.assignments == 8 and audit.feasible_assignments == 3


def test_too_few_validated_candidates():
    with pytest.raises(ValueError):
        build_relays(problem(select_count=4))


def test_generator_and_milp_agree_with_exact():
    for seed in (1, 2, 3, 4):
        p = generate_relays(seed)
        assert p == generate_relays(seed)
        assert p.frame == FRAME
        assert len(p.sites) == 3 and len(p.candidates) == 8 and p.select_count == 2
        instance = build_relays(p)
        assert len(instance.variable_labels) >= p.select_count
        exact = solve_exact(instance)
        milp = solve_milp(instance)
        assert exact.status == milp.status == "optimal"
        assert Fraction(exact.objective) == Fraction(milp.objective)
        metrics = instance.evaluate(exact.bits).metrics
        assert 0 <= metrics["resilience"] <= metrics["coverage"] <= 1
    assert generate_relays(1) != generate_relays(2)


@pytest.mark.parametrize(
    "changes",
    [
        {"select_count": 7},
        {"slot_count": 3},
        {"sites": (SITES[0], SITES[0])},
        {"candidates": (CANDIDATES[0], CANDIDATES[0])},
        {"start_utc": datetime(2026, 11, 20)},
    ],
)
def test_invalid_problems_are_rejected(changes):
    with pytest.raises(ValidationError):
        problem(**changes)
