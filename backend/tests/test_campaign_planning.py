"""R11 multi-mission campaign planning: contracts, generation, MILP and validator."""

import copy
import json
import math
import random
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from itertools import product
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analysis_api import Submission, create_app
from app.campaign_planning import (
    MAX_VARIABLES,
    CampaignPlan,
    LinkAllocation,
    campaign_timeline,
    explain_infeasibility,
    generate_candidates,
    plan_campaign,
    plan_independently,
    site_evidence_from_results,
    solve_joint,
    validate_campaign_plan,
)
from app.communication import communication_windows
from app.descent import DescentRequest, plan_descent
from app.terrain_safety import SafetySettings, site_safety
from shared.campaign import (
    CampaignProblem,
    SiteEvidence,
    campaign_fingerprint,
    load_campaign,
)
from shared.constants import MOON_REFERENCE_RADIUS_M

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "shared" / "examples" / "r11"
T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)
RATE = 1e6
SLOT_BITS = RATE * 3600


def iso(hours):
    return (T0 + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def win(a, b):
    return {"start_utc": iso(a), "end_utc": iso(b)}


def rows(states, a=0):
    """Hourly state list -> merged contiguous intervals starting at hour a."""
    out = []
    for i, state in enumerate(states):
        if out and out[-1]["state"] == state:
            out[-1]["window"]["end_utc"] = iso(a + i + 1)
        else:
            out.append({"window": win(a + i, a + i + 1), "state": state})
    return out


SOURCES = [
    {"source_id": "syn", "kind": "synthetic", "description": "Test evidence."},
    {"source_id": "assume", "kind": "assumption", "description": "Test assumptions."},
]
PREFS = {
    "priority_weights": [5, 4, 3, 2, 1],
    "science_weight": 1,
    "science_scale_score": 1,
    "fuel_weight": 0.5,
    "fuel_scale_kg": 1000,
    "wait_weight": 1,
    "wait_scale_s": 3600,
    "reserve_weight": 0,
    "reserve_scale_kg": 1000,
}


def vehicle(vid, **kw):
    data = {
        "vehicle_id": vid,
        "usable_propellant_kg": 1000,
        "propellant_reserve_kg": 50,
        "max_landing_slope_deg": 8,
        "max_unknown_landing_fraction": 0.1,
        "solar_power_w": 100,
        "initial_energy_j": 1e6,
        "energy_reserve_j": 0,
        "source_ids": ["assume"],
    }
    data.update(kw)
    return data


def policy(cancel=False, pen=0, defer_s=0, defer_pen=0, per_h=0):
    return {
        "cancel_allowed": cancel,
        "cancel_penalty_score": pen,
        "defer_allowed": defer_s > 0,
        "max_defer_s": defer_s,
        "defer_penalty_score": defer_pen,
        "defer_penalty_score_per_h": per_h,
    }


def mission(
    mid,
    vid,
    *,
    sites=None,
    lw=(0, 2),
    deadline=12,
    surface_h=2,
    slots=2,
    energy=0,
    pol=None,
    deps=(),
    exclusive=False,
    terminal=False,
    relay=None,
    priority=1,
    mtype="robotic_lander",
):
    sites = sites or {"s1": 10}
    return {
        "mission_id": mid,
        "vehicle_id": vid,
        "mission_type": mtype,
        "priority": priority,
        "landing_window": win(*lw),
        "deadline_utc": iso(deadline),
        "site_ids": sorted(sites),
        "science_score_by_site": dict(sites),
        "requirements": {
            "surface_duration_s": surface_h * 3600,
            "energy_j": energy,
            "downlink_bits": slots * SLOT_BITS,
        },
        "policy": pol or policy(),
        "dependencies": [
            {
                "predecessor_mission_id": p,
                "kind": "landed_before_landing",
                "min_lag_s": lag,
            }
            for p, lag in deps
        ],
        "requires_exclusive_link": exclusive,
        "relay_terminal": terminal,
        "relay_service": relay,
        "source_ids": ["assume"],
    }


def site(
    sid,
    hours=12,
    *,
    lat=-89.0,
    lon=0.0,
    slope=3.0,
    unknown=0.0,
    sun=None,
    geometry=None,
    descents=None,
):
    return {
        "site": {"site_id": sid, "latitude_deg": lat, "longitude_deg_east": lon},
        "evidence_window": win(0, hours),
        "terrain": {
            "max_landing_slope_deg": slope,
            "unknown_landing_fraction": unknown,
            "hazard_distance_m": None,
            "source_ids": ["syn"],
        },
        "sunlight": sun if sun is not None else rows(["full"] * hours),
        "station_geometry": [
            {"station_id": st, "intervals": r}
            for st, r in (geometry or {"st": rows(["available"] * hours)}).items()
        ],
        "descents": [
            {
                "vehicle_id": v,
                "feasible": True,
                "propellant_used_kg": u,
                "source_ids": ["syn"],
            }
            for v, u in (descents or {"va": 500, "vb": 500, "vc": 500}).items()
        ],
        "source_ids": ["syn"],
    }


def station(sid="st", cap=1, avail=((0, 12),)):
    return {
        "station_id": sid,
        "capacity_links": cap,
        "availability": [win(a, b) for a, b in avail],
        "source_ids": ["assume"],
    }


def rate(mid, sid="st", bps=RATE):
    return {
        "mission_id": mid,
        "station_id": sid,
        "rate_bps": bps,
        "source_ids": ["assume"],
    }


def raw(
    missions=None,
    vehicles=None,
    sites=None,
    stations=None,
    rates=None,
    relays=(),
    hours=12,
    **kw,
):
    missions = missions or [mission("a", "va")]
    data = {
        "schema_version": 1,
        "campaign_id": "test-campaign",
        "revision": 0,
        "description": "Unit-test campaign.",
        "window": win(0, hours),
        "slot_s": 3600,
        "landing_step_s": 3600,
        "missions": missions,
        "vehicles": vehicles or [vehicle(m["vehicle_id"]) for m in missions],
        "sites": sites or [site("s1", hours)],
        "stations": stations
        if stations is not None
        else [station(avail=((0, hours),))],
        "link_rates": rates
        if rates is not None
        else [rate(m["mission_id"]) for m in missions],
        "relay_geometry": list(relays),
        "preferences": PREFS,
        "sources": SOURCES,
    }
    data.update(kw)
    return data


def build(**kw):
    return CampaignProblem.model_validate(raw(**kw))


def q(x):
    return Fraction(str(x))


def option(mid, sid, hours):
    return f"{mid}.{sid}.{(T0 + timedelta(hours=hours)).strftime('%Y%m%dT%H%M%SZ')}"


def alloc(mid, k, sid="st", via=None, bits=SLOT_BITS):
    return LinkAllocation(
        mission_id=mid,
        station_id=sid,
        slot_index=k,
        window=win(k, k + 1),
        via_relay_mission_id=via,
        bits=bits,
    )


def make_plan(problem, selections, allocations=(), plan_id="manual"):
    return CampaignPlan(
        plan_id=plan_id,
        campaign_id=problem.campaign_id,
        campaign_revision=problem.revision,
        input_fingerprint=campaign_fingerprint(problem),
        selections=selections,
        allocations=tuple(allocations),
        solver_name="manual",
    )


def codes(validation):
    return {issue.code for issue in validation.issues}


def example(name):
    return load_campaign(EXAMPLES / f"{name}.json")


# ---------------------------------------------------------------- contracts


def test_examples_load_and_fingerprint_is_canonical(tmp_path):
    for path in sorted(EXAMPLES.glob("*.json")):
        problem = load_campaign(path)
        digest = campaign_fingerprint(problem)
        assert len(digest) == 64 and digest == campaign_fingerprint(problem)
        data = json.loads(path.read_text())
        shuffled = dict(reversed(list(data.items())))
        shuffled["missions"] = list(reversed(shuffled["missions"]))
        shuffled["sources"] = list(reversed(shuffled["sources"]))
        copy_path = tmp_path / path.name
        copy_path.write_text(json.dumps(shuffled))
        assert campaign_fingerprint(load_campaign(copy_path)) == digest
        data["revision"] += 1
        assert campaign_fingerprint(CampaignProblem.model_validate(data)) != digest


def test_load_rejects_duplicate_keys_and_non_finite(tmp_path):
    path = tmp_path / "dup.json"
    path.write_text('{"campaign_id": "a", "campaign_id": "b"}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_campaign(path)
    data = raw()
    text = json.dumps(data).replace('"slot_s": 3600', '"slot_s": NaN')
    path.write_text(text)
    with pytest.raises(ValueError):
        load_campaign(path)


def mutate(path, value):
    data = raw()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    "data",
    [
        mutate(("unexpected",), 1),
        mutate(("window", "start_utc"), "2026-12-01T00:00:00"),
        mutate(("slot_s",), 7 * 3600),  # 12 h window not divisible
        mutate(("missions", 0, "vehicle_id"), "unknown"),
        mutate(("missions", 0, "site_ids"), ["nowhere"]),
        mutate(("missions", 0, "science_score_by_site"), {"other": 1}),
        mutate(("missions", 0, "deadline_utc"), iso(0)),
        mutate(("missions", 0, "deadline_utc"), iso(13)),
        mutate(("missions", 0, "landing_window"), win(11, 13)),
        mutate(("missions", 0, "source_ids"), ["missing-source"]),
        mutate(
            ("missions", 0, "dependencies"),
            [
                {
                    "predecessor_mission_id": "a",
                    "kind": "landed_before_landing",
                    "min_lag_s": 0,
                }
            ],
        ),
        mutate(
            ("missions", 0, "dependencies"),
            [
                {
                    "predecessor_mission_id": "ghost",
                    "kind": "landed_before_landing",
                    "min_lag_s": 0,
                }
            ],
        ),
        mutate(("missions", 0, "policy"), policy(cancel=False, pen=1)),
        mutate(("missions", 0, "policy"), {**policy(), "defer_allowed": True}),
        mutate(("missions", 0, "policy"), {**policy(), "max_defer_s": 3600}),
        mutate(("missions", 0, "priority"), 6),
        mutate(("missions", 0, "mission_type"), "orbiter"),
        mutate(("link_rates", 0, "station_id"), "ghost"),
        mutate(("link_rates",), [rate("a"), rate("a")]),
        mutate(("stations", 0, "availability"), [win(0, 6), win(5, 8)]),
        mutate(("stations", 0, "availability"), [win(0, 13)]),
        mutate(("sites", 0, "sunlight"), rows(["full"] * 13)),
        mutate(("sites", 0, "sunlight"), list(reversed(rows(["full", "dark"])))),
        mutate(
            ("sites", 0, "sunlight"),
            [
                {"window": win(0, 3), "state": "full"},
                {"window": win(2, 4), "state": "dark"},
            ],
        ),
        mutate(
            ("sites", 0, "station_geometry"),
            [{"station_id": "ghost", "intervals": rows(["available"])}],
        ),
        mutate(("sites", 0, "terrain", "unknown_landing_fraction"), 1.5),
        mutate(("vehicles", 0, "usable_propellant_kg"), "1000"),
        mutate(
            ("relay_geometry",),
            [
                {
                    "client_site_id": "s1",
                    "relay_site_id": "ghost",
                    "intervals": [],
                    "source_ids": ["syn"],
                }
            ],
        ),
        mutate(
            ("sources",), [{"source_id": "syn", "kind": "observed", "description": "x"}]
        ),
    ],
)
def test_invalid_problems_fail_loudly(data):
    with pytest.raises(ValueError):
        CampaignProblem.model_validate(data)


def test_structural_reference_rules():
    with pytest.raises(ValueError, match="vehicle"):
        build(
            missions=[mission("a", "va"), mission("b", "va")], vehicles=[vehicle("va")]
        )
    with pytest.raises(ValueError, match="[Cc]ycl"):
        build(
            missions=[
                mission("a", "va", deps=[("b", 0)]),
                mission("b", "vb", deps=[("a", 0)]),
            ]
        )
    with pytest.raises(ValueError):
        build(missions=[mission("a", "va"), mission("a", "vb")])
    # Gaps in evidence are allowed and mean unknown.
    build(sites=[site("s1", sun=rows(["full"] * 3) + rows(["full"] * 3, a=6))])


# ---------------------------------------------------------------- generation


def test_candidates_on_landing_grid_with_exact_metrics():
    problem = build()
    result = generate_candidates(problem)
    assert result.input_fingerprint == campaign_fingerprint(problem)
    assert [c.option_id for c in result.candidates] == [
        option("a", "s1", 0),
        option("a", "s1", 1),
    ]
    first, second = result.candidates
    assert first.landing_utc == T0 and not first.deferred and first.defer_s == 0
    assert first.surface_window.end_utc == T0 + timedelta(hours=2)
    assert first.comm_window.start_utc == T0
    assert first.comm_window.end_utc == T0 + timedelta(hours=12)
    assert first.fuel_kg == 500 and first.propellant_margin_kg == 450
    assert first.generated_energy_j == 100 * 7200
    assert first.energy_margin_j == 1e6 + 720000
    assert first.wait_s == 0 and second.wait_s == 3600
    expected = 5 * 10 - q(0.5) * 500 / 1000
    assert Fraction(first.exact_score) == expected
    assert Fraction(second.exact_score) == expected - 1
    assert first.score == pytest.approx(float(expected))
    assert set(first.source_ids) == {"assume", "syn"}
    assert result.rejections == ()


def test_deferred_candidates_only_when_policy_allows():
    plain = generate_candidates(build())
    assert not any(c.deferred for c in plain.candidates)
    pol = policy(defer_s=7200, defer_pen=0.5, per_h=0.25)
    problem = build(missions=[mission("a", "va", pol=pol)])
    result = generate_candidates(problem)
    deferred = [c for c in result.candidates if c.deferred]
    assert [c.landing_utc for c in deferred] == [
        T0 + timedelta(hours=2),
        T0 + timedelta(hours=3),
    ]
    late = deferred[1]
    assert late.defer_s == 3600 and late.wait_s == 3 * 3600
    assert late.comm_window.end_utc == T0 + timedelta(hours=12)  # clipped to campaign
    base = 50 - q(0.5) * 500 / 1000
    assert Fraction(late.exact_score) == base - 3 - q(0.5) - q(0.25)


def test_deferral_extends_deadline():
    pol = policy(defer_s=3 * 3600)
    problem = build(missions=[mission("a", "va", pol=pol, deadline=4, surface_h=3)])
    result = generate_candidates(problem)
    landings = {c.landing_utc: c for c in result.candidates}
    # nominal deadline 4 h: landing 0 and 1 fit; deferred deadline 7 h: 2, 3, 4 fit
    assert sorted(landings) == [T0 + timedelta(hours=h) for h in (0, 1, 2, 3, 4)]
    assert landings[T0 + timedelta(hours=4)].comm_window.end_utc == T0 + timedelta(
        hours=7
    )
    assert landings[T0 + timedelta(hours=1)].comm_window.end_utc == T0 + timedelta(
        hours=4
    )


def rejection_codes(problem):
    return [set(r.codes) for r in generate_candidates(problem).rejections]


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"sites": [site("s1", slope=None)]}, "terrain_unknown"),
        ({"sites": [site("s1", slope=9.0)]}, "terrain_slope"),
        ({"sites": [site("s1", unknown=0.2)]}, "terrain_unknown_fraction"),
        ({"sites": [site("s1", descents={"vb": 1})]}, "descent_unknown"),
        ({"sites": [site("s1", descents={"va": None})]}, "descent_unknown"),
        ({"sites": [site("s1", descents={"va": 960})]}, "propellant_reserve"),
        (
            {"sites": [site("s1", sun=rows(["full", "unknown"] + ["full"] * 10))]},
            "sunlight_unknown",
        ),
        (
            {"sites": [site("s1", sun=rows(["full"]) + rows(["full"] * 10, a=2))]},
            "sunlight_unknown",
        ),
        ({"missions": [mission("a", "va", energy=2e6)]}, "energy"),
        ({"missions": [mission("a", "va", deadline=2, surface_h=3)]}, "deadline"),
        (
            {
                "sites": [site("s1", hours=12, sun=rows(["full"] * 12))],
                "missions": [mission("a", "va", surface_h=11, lw=(0, 3))],
            },
            "outside_campaign_window",
        ),
    ],
)
def test_rejection_codes(kwargs, code):
    found = rejection_codes(build(**kwargs))
    assert found and any(code in c for c in found)


def test_outside_evidence_rejection():
    short = site("s1", hours=12)
    short["evidence_window"] = win(0, 3)
    short["sunlight"] = rows(["full"] * 3)
    short["station_geometry"] = [
        {"station_id": "st", "intervals": rows(["available"] * 3)}
    ]
    result = generate_candidates(
        build(sites=[short], missions=[mission("a", "va", surface_h=2, lw=(0, 3))])
    )
    assert [c.landing_utc for c in result.candidates] == [T0, T0 + timedelta(hours=1)]
    assert {"outside_evidence"} <= set(result.rejections[0].codes)


def test_descent_infeasible_and_multiple_codes_are_collected():
    data = site("s1", slope=20.0, descents={"va": 500})
    data["descents"][0]["feasible"] = False
    result = generate_candidates(build(sites=[data]))
    assert len(result.rejections) == 2 and not result.candidates
    assert {"terrain_slope", "descent_infeasible"} <= set(result.rejections[0].codes)
    assert len(result.rejections[0].details) == len(result.rejections[0].codes)


def test_partial_and_boundary_sun_generate_no_energy():
    sun = rows(["partial", "boundary"] + ["full"] * 10)
    result = generate_candidates(build(sites=[site("s1", sun=sun)]))
    first = result.candidates[0]
    assert first.generated_energy_j == 0
    assert result.candidates[1].generated_energy_j == 100 * 3600


def test_energy_balance_boundary_is_inclusive():
    # 1e6 J initial, 0 generated in the dark, 1e6 J consumed, reserve 0 -> feasible.
    dark = rows(["dark"] * 12)
    ok = build(sites=[site("s1", sun=dark)], missions=[mission("a", "va", energy=1e6)])
    assert len(generate_candidates(ok).candidates) == 2
    tight = build(
        sites=[site("s1", sun=dark)],
        missions=[mission("a", "va", energy=1e6)],
        vehicles=[vehicle("va", energy_reserve_j=1)],
    )
    assert not generate_candidates(tight).candidates


def test_generation_is_deterministic_under_input_permutation():
    missions = [mission("b", "vb", sites={"s1": 3, "s2": 4}), mission("a", "va")]
    sites = [site("s2"), site("s1", slope=None)]
    one = generate_candidates(build(missions=missions, sites=sites))
    two = generate_candidates(
        build(missions=list(reversed(missions)), sites=list(reversed(sites)))
    )
    assert one == two
    assert [r.mission_id for r in one.rejections] == sorted(
        r.mission_id for r in one.rejections
    )


# ---------------------------------------------------------------- validator


def two_missions(cap=1, **kw):
    missions = kw.pop(
        "missions", [mission("a", "va"), mission("b", "vb", sites={"s1": 4})]
    )
    return build(missions=missions, stations=[station(cap=cap)], **kw)


def test_validator_accepts_manual_feasible_plan_and_scores_exactly():
    problem = two_missions()
    a0, b0 = option("a", "s1", 0), option("b", "s1", 0)
    plan = make_plan(
        problem,
        {"a": a0, "b": b0},
        [alloc("a", 0), alloc("a", 1), alloc("b", 2), alloc("b", 3)],
    )
    report = validate_campaign_plan(plan, problem)
    assert report.status == "feasible" and report.issues == ()
    cands = {c.option_id: c for c in generate_candidates(problem).candidates}
    assert Fraction(report.exact_score) == q(cands[a0].exact_score) + q(
        cands[b0].exact_score
    )
    outcome = {o.mission_id: o for o in report.outcomes}
    assert (
        outcome["a"].status == "nominal"
        and outcome["a"].delivered_bits == 2 * SLOT_BITS
    )
    assert outcome["b"].relayed_bits == 0


def test_validator_rejects_malformed_or_stale_plans():
    problem = two_missions()
    a0, b0 = option("a", "s1", 0), option("b", "s1", 0)
    with pytest.raises(ValueError):
        validate_campaign_plan(
            make_plan(problem, {"a": a0, "b": b0}).model_copy(
                update={"input_fingerprint": "0" * 64}
            ),
            problem,
        )
    with pytest.raises(ValueError):
        validate_campaign_plan(
            make_plan(problem, {"a": a0, "b": b0, "ghost": None}), problem
        )
    with pytest.raises(ValueError):
        validate_campaign_plan(make_plan(problem, {"a": "nope", "b": b0}), problem)
    with pytest.raises(ValueError):
        validate_campaign_plan(make_plan(problem, {"a": b0, "b": b0}), problem)
    with pytest.raises(ValueError):
        validate_campaign_plan(
            make_plan(problem, {"a": a0, "b": b0}, [alloc("ghost", 0)]), problem
        )


def test_missing_cancelled_and_inactive_allocations():
    problem = two_missions(
        missions=[
            mission("a", "va"),
            mission("b", "vb", sites={"s1": 4}, pol=policy(cancel=True, pen=2)),
        ]
    )
    a0 = option("a", "s1", 0)
    report = validate_campaign_plan(
        make_plan(problem, {"a": a0}, [alloc("a", 0), alloc("a", 1)]), problem
    )
    assert "missing_mission" in codes(report) and report.status == "infeasible"
    assert report.score is None
    cancelled = validate_campaign_plan(
        make_plan(problem, {"a": a0, "b": None}, [alloc("a", 0), alloc("a", 1)]),
        problem,
    )
    assert cancelled.status == "feasible"
    cands = {c.option_id: c for c in generate_candidates(problem).candidates}
    assert Fraction(cancelled.exact_score) == q(cands[a0].exact_score) - 2
    assert {o.mission_id: o.status for o in cancelled.outcomes}["b"] == "cancelled"
    bad = validate_campaign_plan(
        make_plan(problem, {"a": None, "b": None}, [alloc("b", 0)]), problem
    )
    assert {"cancel_not_allowed", "allocation_for_inactive_mission"} <= codes(bad)


def test_slot_window_and_bit_checks():
    problem = two_missions(cap=2)
    a0, b1 = option("a", "s1", 0), option("b", "s1", 1)
    bad_slot = LinkAllocation(
        mission_id="a", station_id="st", slot_index=0, window=win(1, 2), bits=SLOT_BITS
    )
    report = validate_campaign_plan(
        make_plan(
            problem,
            {"a": a0, "b": b1},
            [
                alloc("a", 99),
                bad_slot,
                alloc("b", 0),
                alloc("b", 2, bits=1.0),
                alloc("b", 3),
            ],
        ),
        problem,
    )
    found = codes(report)
    assert {
        "invalid_slot",
        "outside_comm_window",
        "bits_mismatch",
        "downlink_shortfall",
    } <= found
    outcome = {o.mission_id: o for o in report.outcomes}
    assert outcome["a"].delivered_bits == 0  # invalid allocations deliver nothing
    assert outcome["b"].delivered_bits == SLOT_BITS


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"stations": [station(avail=((2, 12),))]}, "station_unavailable"),
        ({"stations": [station(cap=None)]}, "station_capacity_unknown"),
        (
            {
                "sites": [
                    site(
                        "s1",
                        geometry={"st": rows(["unavailable"] + ["available"] * 11)},
                    )
                ]
            },
            "geometry_unavailable",
        ),
        (
            {
                "sites": [
                    site("s1", geometry={"st": rows(["unknown"] + ["available"] * 11)})
                ]
            },
            "geometry_unavailable",
        ),
        ({"rates": []}, "rate_unknown"),
    ],
)
def test_link_preconditions(kwargs, code):
    problem = build(**kwargs)
    report = validate_campaign_plan(
        make_plan(problem, {"a": option("a", "s1", 0)}, [alloc("a", 0), alloc("a", 1)]),
        problem,
    )
    assert code in codes(report) and report.status == "infeasible"


def test_unknown_station_reference():
    problem = build()
    report = validate_campaign_plan(
        make_plan(
            problem,
            {"a": option("a", "s1", 0)},
            [alloc("a", 0), alloc("a", 1, sid="ghost")],
        ),
        problem,
    )
    assert "station_unknown" in codes(report)


def test_radio_capacity_and_exclusivity():
    problem = two_missions(cap=2)
    a0, b0 = option("a", "s1", 0), option("b", "s1", 0)
    two_station = build(
        missions=[mission("a", "va"), mission("b", "vb", sites={"s1": 4})],
        stations=[station(cap=2), station("st2", cap=1)],
        sites=[
            site(
                "s1",
                geometry={
                    "st": rows(["available"] * 12),
                    "st2": rows(["available"] * 12),
                },
            )
        ],
        rates=[rate("a"), rate("b"), rate("a", "st2"), rate("b", "st2")],
    )
    radio = validate_campaign_plan(
        make_plan(
            two_station,
            {"a": a0, "b": b0},
            [alloc("a", 0), alloc("a", 0, sid="st2"), alloc("b", 2), alloc("b", 3)],
        ),
        two_station,
    )
    assert "radio_conflict" in codes(radio)
    shared = validate_campaign_plan(
        make_plan(
            problem,
            {"a": a0, "b": b0},
            [alloc("a", 0), alloc("a", 1), alloc("b", 0), alloc("b", 1)],
        ),
        problem,
    )
    assert shared.status == "feasible"
    single = two_missions(cap=1)
    over = validate_campaign_plan(
        make_plan(
            single,
            {"a": a0, "b": b0},
            [alloc("a", 0), alloc("a", 1), alloc("b", 1), alloc("b", 2)],
        ),
        single,
    )
    assert "station_capacity" in codes(over)
    exclusive = two_missions(
        cap=2,
        missions=[
            mission("a", "va", exclusive=True),
            mission("b", "vb", sites={"s1": 4}),
        ],
    )
    report = validate_campaign_plan(
        make_plan(
            exclusive,
            {"a": a0, "b": b0},
            [alloc("a", 0), alloc("a", 1), alloc("b", 1), alloc("b", 2)],
        ),
        exclusive,
    )
    assert codes(report) == {"exclusive_link"}
    assert report.issues[0].window.start_utc == T0 + timedelta(hours=1)


def test_dependency_rules():
    problem = two_missions(
        cap=2,
        missions=[
            mission("a", "va", pol=policy(cancel=True)),
            mission(
                "b", "vb", sites={"s1": 4}, deps=[("a", 3600)], pol=policy(cancel=True)
            ),
        ],
    )
    a0, a1, b0, b1 = (
        option(m, "s1", h) for m, h in (("a", 0), ("a", 1), ("b", 0), ("b", 1))
    )
    links = [alloc("a", 2), alloc("a", 3), alloc("b", 2), alloc("b", 3)]
    assert (
        validate_campaign_plan(
            make_plan(problem, {"a": a0, "b": b1}, links), problem
        ).status
        == "feasible"
    )
    lag = validate_campaign_plan(make_plan(problem, {"a": a1, "b": b1}, links), problem)
    assert codes(lag) == {"dependency_lag"}
    gone = validate_campaign_plan(
        make_plan(problem, {"a": None, "b": b0}, links[2:]), problem
    )
    assert codes(gone) == {"dependency_cancelled"}
    both = validate_campaign_plan(make_plan(problem, {"a": None, "b": None}), problem)
    assert both.status == "feasible"


def test_zero_downlink_needs_no_allocation():
    problem = build(missions=[mission("a", "va", slots=0)])
    report = validate_campaign_plan(
        make_plan(problem, {"a": option("a", "s1", 0)}), problem
    )
    assert report.status == "feasible"


# ---------------------------------------------------------------- relays


def relay_problem(
    *,
    terminal=True,
    service=True,
    los="available",
    hop=RATE,
    max_range=60000.0,
    relay_cancel=False,
    client_geometry="unavailable",
    relay_cap=1,
    client_slots=2,
    extra_client=False,
):
    relay = (
        {"capacity_links": relay_cap, "max_range_m": max_range, "hop_rate_bps": hop}
        if service
        else None
    )
    missions = [
        mission(
            "r",
            "va",
            sites={"hub": 1},
            slots=1,
            relay=relay,
            pol=policy(cancel=relay_cancel),
            mtype="relay_lander",
        ),
        mission(
            "c",
            "vb",
            sites={"pit": 8},
            slots=client_slots,
            terminal=terminal,
            pol=policy(cancel=True, pen=1),
        ),
    ]
    rates_ = [rate("r"), rate("c")]
    if extra_client:
        missions.append(
            mission(
                "d",
                "vc",
                sites={"pit": 6},
                slots=1,
                terminal=True,
                pol=policy(cancel=True, pen=1),
            )
        )
        rates_.append(rate("d"))
    return build(
        missions=missions,
        sites=[
            site("hub", lat=-88.8, lon=10.0),
            site(
                "pit",
                lat=-89.3,
                lon=25.0,
                geometry={"st": rows([client_geometry] * 12)},
            ),
        ],
        stations=[station(cap=3)],
        rates=rates_,
        relays=[
            {
                "client_site_id": "pit",
                "relay_site_id": "hub",
                "intervals": rows([los] * 12),
                "source_ids": ["syn"],
            }
        ],
    )


def relay_plan(problem, extra=(), selections=None):
    sel = selections or {"r": option("r", "hub", 0), "c": option("c", "pit", 0)}
    links = [alloc("r", 0), alloc("c", 1, via="r"), alloc("c", 2, via="r"), *extra]
    return make_plan(problem, sel, links)


def test_relay_allocation_feasible_and_counted_as_relayed():
    problem = relay_problem()
    report = validate_campaign_plan(relay_plan(problem), problem)
    assert report.status == "feasible"
    outcome = {o.mission_id: o for o in report.outcomes}["c"]
    assert outcome.relayed_bits == 2 * SLOT_BITS == outcome.delivered_bits


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"terminal": False}, "relay_equipment"),
        ({"service": False}, "relay_equipment"),
        ({"los": "unknown"}, "relay_geometry"),
        ({"los": "unavailable"}, "relay_geometry"),
        ({"max_range": 1000.0}, "relay_range"),
    ],
)
def test_relay_preconditions(kwargs, code):
    problem = relay_problem(**kwargs)
    assert code in codes(validate_campaign_plan(relay_plan(problem), problem))


def test_relay_hop_rate_limits_bits_and_capacity():
    slow = relay_problem(hop=RATE / 2)
    report = validate_campaign_plan(relay_plan(slow), slow)
    assert "bits_mismatch" in codes(report)
    half = make_plan(
        slow,
        {"r": option("r", "hub", 0), "c": option("c", "pit", 0)},
        [
            alloc("r", 0),
            *[alloc("c", k, via="r", bits=SLOT_BITS / 2) for k in (1, 2, 3, 4)],
        ],
    )
    assert validate_campaign_plan(half, slow).status == "feasible"
    crowded = relay_problem(extra_client=True)
    sel = {
        "r": option("r", "hub", 0),
        "c": option("c", "pit", 0),
        "d": option("d", "pit", 0),
    }
    plan = make_plan(
        crowded,
        sel,
        [
            alloc("r", 0),
            alloc("c", 1, via="r"),
            alloc("c", 2, via="r"),
            alloc("d", 1, via="r"),
        ],
    )
    assert "relay_capacity" in codes(validate_campaign_plan(plan, crowded))


def test_relay_needs_active_relay_and_relay_station_geometry():
    problem = relay_problem(relay_cancel=True)
    plan = relay_plan(problem, selections={"r": None, "c": option("c", "pit", 0)})
    assert "relay_inactive" in codes(validate_campaign_plan(plan, problem))
    late = relay_plan(
        problem, selections={"r": option("r", "hub", 1), "c": option("c", "pit", 0)}
    )
    late = late.model_copy(
        update={
            "allocations": (
                alloc("r", 1),
                alloc("c", 0, via="r"),
                alloc("c", 2, via="r"),
            )
        }
    )
    assert "relay_inactive" in codes(validate_campaign_plan(late, problem))
    blind = relay_problem()
    data = blind.model_dump(mode="json")
    data["sites"][0]["station_geometry"][0]["intervals"] = rows(
        ["available"] + ["unavailable"] * 11
    )
    blind = CampaignProblem.model_validate(data)
    assert "geometry_unavailable" in codes(
        validate_campaign_plan(relay_plan(blind), blind)
    )


def test_relay_range_uses_reference_sphere_chord():
    a = (-88.8, 10.0)
    b = (-89.3, 25.0)

    def vec(lat, lon):
        la, lo = math.radians(lat), math.radians(lon)
        return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))

    chord = MOON_REFERENCE_RADIUS_M * math.dist(vec(*a), vec(*b))
    ok = relay_problem(max_range=chord + 1)
    far = relay_problem(max_range=chord - 1)
    assert validate_campaign_plan(relay_plan(ok), ok).status == "feasible"
    assert "relay_range" in codes(validate_campaign_plan(relay_plan(far), far))


# ---------------------------------------------------------------- solver


def test_solver_picks_best_option_with_minimal_early_links():
    problem = build()
    outcome = solve_joint(problem)
    assert outcome.status == "optimal"
    assert outcome.plan.selections == {"a": option("a", "s1", 0)}
    assert outcome.validation.status == "feasible"
    assert [a.slot_index for a in outcome.plan.allocations] == [0, 1]
    assert outcome.variables > 0 and outcome.constraints > 0
    assert outcome.plan.input_fingerprint == campaign_fingerprint(problem)


def test_shared_capacity_shifts_or_reschedules():
    problem = two_missions(
        missions=[
            mission("a", "va", deadline=2, surface_h=1),
            mission("b", "vb", sites={"s1": 4}, deadline=4, surface_h=1),
        ]
    )
    outcome = solve_joint(problem)
    assert outcome.status == "optimal" and outcome.validation.status == "feasible"
    slots = {
        m: sorted(a.slot_index for a in outcome.plan.allocations if a.mission_id == m)
        for m in "ab"
    }
    assert slots["a"] == [0, 1] and slots["b"] == [2, 3]


def test_defer_used_when_nominal_is_infeasible():
    pol = policy(defer_s=3 * 3600, defer_pen=1)
    problem = build(
        missions=[
            mission("a", "va", pol=pol, lw=(0, 2), deadline=4, surface_h=1, slots=2)
        ],
        sites=[
            site("s1", geometry={"st": rows(["unavailable"] * 4 + ["available"] * 8)})
        ],
    )
    outcome = solve_joint(problem)
    assert outcome.status == "optimal"
    chosen = outcome.plan.selections["a"]
    candidates = {c.option_id: c for c in generate_candidates(problem).candidates}
    assert candidates[chosen].deferred
    assert {o.mission_id: o.status for o in outcome.validation.outcomes}[
        "a"
    ] == "deferred"


def test_cancel_when_allowed_and_infeasible_when_mandatory():
    blocked = {"st": rows(["unavailable"] * 12)}
    optional = build(
        missions=[mission("a", "va", pol=policy(cancel=True, pen=4))],
        sites=[site("s1", geometry=blocked)],
    )
    outcome = solve_joint(optional)
    assert outcome.status == "optimal" and outcome.plan.selections == {"a": None}
    assert Fraction(outcome.validation.exact_score) == -4
    mandatory = build(sites=[site("s1", geometry=blocked)])
    hard = solve_joint(mandatory)
    assert hard.status == "infeasible" and hard.plan is None and hard.validation is None


def test_cancel_preferred_when_option_score_is_below_penalty():
    prefs = {**PREFS, "fuel_weight": 200}
    problem = build(
        missions=[mission("a", "va", pol=policy(cancel=True, pen=1))], preferences=prefs
    )
    outcome = solve_joint(problem)
    assert outcome.plan.selections == {"a": None}
    forced = solve_joint(problem, forbid_cancel_mission_ids=("a",))
    assert forced.plan.selections["a"] == option("a", "s1", 0)
    assert Fraction(forced.validation.exact_score) < Fraction(
        outcome.validation.exact_score
    )


def test_dependencies_in_solver():
    problem = two_missions(
        cap=2,
        missions=[
            mission("a", "va", lw=(0, 3)),
            mission("b", "vb", sites={"s1": 4}, lw=(0, 3), deps=[("a", 7200)]),
        ],
    )
    outcome = solve_joint(problem)
    sel = outcome.plan.selections
    assert sel == {"a": option("a", "s1", 0), "b": option("b", "s1", 2)}
    chain = two_missions(
        missions=[
            mission("a", "va", pol=policy(cancel=True, pen=1)),
            mission(
                "b",
                "vb",
                sites={"s1": 4},
                deps=[("a", 0)],
                pol=policy(cancel=True, pen=1),
            ),
        ],
        sites=[site("s1", geometry={"st": rows(["available"] * 12)})],
    )
    forced = solve_joint(
        chain,
        excluded_option_ids=tuple(
            c.option_id
            for c in generate_candidates(chain).candidates
            if c.mission_id == "a"
        ),
    )
    assert forced.plan.selections == {"a": None, "b": None}


def test_exclusive_mission_never_shares_slots():
    problem = two_missions(
        cap=2,
        missions=[
            mission("a", "va", exclusive=True, deadline=3, surface_h=1),
            mission("b", "vb", sites={"s1": 4}, deadline=3, surface_h=1),
        ],
    )
    outcome = solve_joint(problem)
    assert outcome.status == "infeasible"  # 4 exclusive-slot needs, 3 slots
    relaxed = two_missions(
        cap=2,
        missions=[
            mission("a", "va", deadline=3, surface_h=1),
            mission("b", "vb", sites={"s1": 4}, deadline=3, surface_h=1),
        ],
    )
    assert solve_joint(relaxed).status == "optimal"


def test_relay_credit_requires_equipment_and_geometry():
    ok = solve_joint(relay_problem())
    assert ok.plan.selections["c"] is not None
    outcome = {o.mission_id: o for o in ok.validation.outcomes}["c"]
    assert outcome.relayed_bits == 2 * SLOT_BITS
    for kwargs in (
        {"terminal": False},
        {"los": "unknown"},
        {"max_range": 1000.0},
        {"service": False},
    ):
        result = solve_joint(relay_problem(**kwargs))
        assert result.status == "optimal" and result.plan.selections["c"] is None, (
            kwargs
        )


def test_unknown_station_capacity_is_never_used():
    problem = build(
        missions=[mission("a", "va", pol=policy(cancel=True, pen=1))],
        stations=[station(cap=None)],
    )
    outcome = solve_joint(problem)
    assert outcome.plan.selections == {"a": None}


def test_solver_controls():
    problem = two_missions(cap=2)
    a1 = option("a", "s1", 1)
    forced = solve_joint(problem, forced_option_ids=(a1,))
    assert forced.plan.selections["a"] == a1
    excluded = solve_joint(problem, excluded_option_ids=(option("a", "s1", 0), a1))
    assert excluded.status == "infeasible"
    alone = solve_joint(problem, only_mission_ids=("b",))
    assert set(alone.plan.selections) == {"b"}
    relaxed = solve_joint(
        problem,
        required_mission_ids=("a",),
        excluded_option_ids=(option("b", "s1", 0), option("b", "s1", 1)),
    )
    assert relaxed.status == "optimal" and relaxed.plan.selections["b"] is None
    with pytest.raises(ValueError):
        solve_joint(problem, time_limit_s=0.0)
    with pytest.raises(ValueError):
        solve_joint(problem, forced_option_ids=("ghost",))


def test_variable_limit_fails_loudly():
    hours = 240
    big = build(
        hours=hours,
        missions=[
            mission(f"m{i}", f"v{i}", lw=(0, 200), deadline=hours, slots=1)
            for i in range(3)
        ],
        vehicles=[vehicle(f"v{i}") for i in range(3)],
        sites=[site("s1", hours, descents={f"v{i}": 500 for i in range(3)})],
    )
    # 3 missions x 200 landings x up to 240 slots exceeds the bound.
    assert 3 * 200 * 200 > MAX_VARIABLES
    with pytest.raises(ValueError, match="variables"):
        solve_joint(big)


# --------------------------------------------- brute-force oracle comparison


def _covered(window_start, window_end, intervals, state="available"):
    cursor = window_start
    for row in sorted(intervals, key=lambda r: r["window"]["start_utc"]):
        a = datetime.fromisoformat(row["window"]["start_utc"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(row["window"]["end_utc"].replace("Z", "+00:00"))
        if row["state"] == state and a <= cursor < b:
            cursor = b
    return cursor >= window_end


def oracle(problem):
    """Exhaustive optimum: selections x per-slot link DP (uniform link bits)."""
    data = problem.model_dump(mode="json")
    cands = generate_candidates(problem).candidates
    missions = {m["mission_id"]: m for m in data["missions"]}
    sites = {s["site"]["site_id"]: s for s in data["sites"]}
    stations = {s["station_id"]: s for s in data["stations"]}
    rates_ = {(r["mission_id"], r["station_id"]) for r in data["link_rates"]}
    los = {
        (g["client_site_id"], g["relay_site_id"]): g["intervals"]
        for g in data["relay_geometry"]
    }
    n_slots = 6

    def slot(k):
        return T0 + timedelta(hours=k), T0 + timedelta(hours=k + 1)

    def direct(c, s, k):
        a, b = slot(k)
        st = stations[s]
        if not (c.comm_window.start_utc <= a and b <= c.comm_window.end_utc):
            return False
        if not st["capacity_links"] or (c.mission_id, s) not in rates_:
            return False
        if not any(
            datetime.fromisoformat(w["start_utc"].replace("Z", "+00:00")) <= a
            and b <= datetime.fromisoformat(w["end_utc"].replace("Z", "+00:00"))
            for w in st["availability"]
        ):
            return False
        geo = {
            g["station_id"]: g["intervals"]
            for g in sites[c.site_id]["station_geometry"]
        }
        return s in geo and _covered(a, b, geo[s])

    def relay_ok(c, r, s, k):
        mc, mr = missions[c.mission_id], missions[r.mission_id]
        if not mc["relay_terminal"] or not mr["relay_service"]:
            return False
        a, b = slot(k)
        if not (c.comm_window.start_utc <= a and b <= c.comm_window.end_utc):
            return False
        key = (c.site_id, r.site_id)
        return key in los and _covered(a, b, los[key]) and direct(r, s, k)

    order = sorted(missions)
    options = []
    for mid in order:
        opts = [c for c in cands if c.mission_id == mid]
        if missions[mid]["policy"]["cancel_allowed"]:
            opts.append(None)
        options.append(opts)
    best = None
    for combo in product(*options):
        sel = dict(zip(order, combo))
        ok = True
        for mid, c in sel.items():
            for dep in missions[mid]["dependencies"]:
                p = sel[dep["predecessor_mission_id"]]
                if c is not None and (
                    p is None
                    or c.landing_utc
                    < p.landing_utc + timedelta(seconds=dep["min_lag_s"])
                ):
                    ok = False
        if not ok:
            continue
        need = {
            mid: math.ceil(missions[mid]["requirements"]["downlink_bits"] / SLOT_BITS)
            for mid, c in sel.items()
            if c is not None
        }
        states = {tuple(0 for _ in need)}
        active = sorted(need)
        for k in range(n_slots):
            per = []
            for mid in active:
                c = sel[mid]
                choice = [None]
                choice += [("d", s) for s in sorted(stations) if direct(c, s, k)]
                for rid in active:
                    if rid != mid:
                        choice += [
                            ("r", rid, s)
                            for s in sorted(stations)
                            if relay_ok(c, sel[rid], s, k)
                        ]
                per.append(choice)
            moves = set()
            for joint in product(*per):
                use, relay_use, owners = {}, {}, {}
                for mid, ch in zip(active, joint):
                    if ch is None:
                        continue
                    s = ch[-1]
                    use[s] = use.get(s, 0) + 1
                    owners.setdefault(s, []).append(mid)
                    if ch[0] == "r":
                        relay_use[ch[1]] = relay_use.get(ch[1], 0) + 1
                if any(use[s] > stations[s]["capacity_links"] for s in use):
                    continue
                if any(
                    missions[m]["requires_exclusive_link"] and len(owners[s]) > 1
                    for s in owners
                    for m in owners[s]
                ):
                    continue
                if any(
                    relay_use[r] > missions[r]["relay_service"]["capacity_links"]
                    for r in relay_use
                ):
                    continue
                moves.add(tuple(0 if ch is None else 1 for ch in joint))
            states = {
                tuple(min(need[m], s + d) for m, s, d in zip(active, state, move))
                for state in states
                for move in moves
            }
        if tuple(need[m] for m in active) not in states:
            continue
        score = sum(Fraction(c.exact_score) for c in sel.values() if c is not None)
        score -= sum(
            q(missions[m]["policy"]["cancel_penalty_score"])
            for m, c in sel.items()
            if c is None
        )
        best = score if best is None or score > best else best
    return best


def random_problem(seed):
    rng = random.Random(seed)
    hours = 6
    states = ["available", "available", "unavailable", "unknown"]

    def pattern():
        return rows([rng.choice(states) for _ in range(hours)])

    use_relay = seed % 2 == 0
    n = rng.choice([2, 3])
    missions = []
    for i in range(n):
        cancel = rng.random() < 0.6
        defer = rng.random() < 0.4
        deps = (
            [(f"m{i - 1}", rng.choice([0, 3600]))] if i and rng.random() < 0.4 else []
        )
        relay = None
        if use_relay and i == 0:
            relay = {"capacity_links": 1, "max_range_m": 1e6, "hop_rate_bps": RATE}
        missions.append(
            mission(
                f"m{i}",
                f"v{i}",
                sites={"s1": rng.randint(1, 9), "s2": rng.randint(1, 9)}
                if i % 2
                else {"s1": rng.randint(1, 9)},
                lw=(0, 2),
                deadline=rng.choice([4, 5, 6]),
                surface_h=1,
                slots=rng.randint(1, 3),
                pol=policy(
                    cancel=cancel,
                    pen=rng.randint(0, 3) if cancel else 0,
                    defer_s=3600 if defer else 0,
                    defer_pen=1 if defer else 0,
                ),
                deps=deps,
                exclusive=rng.random() < 0.3,
                terminal=use_relay and i > 0,
                relay=relay,
                priority=rng.randint(1, 5),
            )
        )
    st_ids = ["st", "st2"]
    stations = [
        station(
            "st",
            cap=rng.choice([1, 1, 2]),
            avail=((rng.choice([0, 0, 1]), rng.choice([5, 6])),),
        ),
        station("st2", cap=rng.choice([0, 1]), avail=((0, 6),)),
    ]
    sites = [
        site(
            sid,
            hours,
            lat=-89.0,
            lon=lon,
            geometry={s: pattern() for s in st_ids},
            descents={f"v{i}": rng.choice([400, 600, 900]) for i in range(n)},
        )
        for sid, lon in (("s1", 0.0), ("s2", 5.0))
    ]
    rates_ = [
        rate(m["mission_id"], s)
        for m in missions
        for s in st_ids
        if rng.random() < 0.85
    ]
    relays = []
    if use_relay:
        relays = [
            {
                "client_site_id": c,
                "relay_site_id": "s1",
                "intervals": pattern(),
                "source_ids": ["syn"],
            }
            for c in ("s1", "s2")
        ]
    return build(
        missions=missions,
        vehicles=[vehicle(f"v{i}") for i in range(n)],
        sites=sites,
        stations=stations,
        rates=rates_,
        relays=relays,
        hours=hours,
    )


@pytest.mark.parametrize("seed", range(14))
def test_milp_matches_exhaustive_oracle(seed):
    problem = random_problem(seed)
    expected = oracle(problem)
    outcome = solve_joint(problem)
    if expected is None:
        assert outcome.status == "infeasible" and outcome.plan is None
    else:
        assert outcome.status == "optimal"
        assert outcome.validation.status == "feasible"
        assert Fraction(outcome.validation.exact_score) == expected


def test_oracle_agrees_on_known_feasible_and_infeasible_cases():
    feasible = build(
        hours=6,
        missions=[mission("a", "va", deadline=6)],
        sites=[site("s1", 6)],
        stations=[station(avail=((0, 6),))],
    )
    assert oracle(feasible) == Fraction(solve_joint(feasible).validation.exact_score)
    blocked = build(
        hours=6,
        missions=[mission("a", "va", deadline=6)],
        sites=[site("s1", 6, geometry={"st": rows(["unavailable"] * 6)})],
        stations=[station(avail=((0, 6),))],
    )
    assert oracle(blocked) is None and solve_joint(blocked).status == "infeasible"


# --------------------------------------------- independent, examples, explanations


def test_shared_conflict_example_independent_versus_joint():
    problem = example("shared-dsn-conflict")
    independent = plan_independently(problem)
    assert independent.selections == {
        "crew-1": "crew-1.ridge-a.20261201T000000Z",
        "clps-1": "clps-1.rim-b.20261201T000000Z",
        "rover-1": "rover-1.rim-b.20261201T000000Z",
    }
    report = validate_campaign_plan(independent, problem)
    assert report.status == "infeasible"
    assert {"station_capacity", "exclusive_link", "dependency_lag"} <= codes(report)
    for mid in independent.selections:  # each choice is fine on its own
        alone = {o.mission_id: o for o in report.outcomes}[mid]
        assert alone.delivered_bits >= alone.required_bits
    joint = solve_joint(problem)
    assert joint.status == "optimal" and joint.validation.status == "feasible"
    assert joint.plan.selections == {
        "crew-1": "crew-1.ridge-a.20261201T000000Z",
        "clps-1": "clps-1.rim-b.20261201T120000Z",
        "rover-1": "rover-1.rim-b.20261201T180000Z",
    }
    status = {o.mission_id: o.status for o in joint.validation.outcomes}
    assert status == {"crew-1": "nominal", "clps-1": "deferred", "rover-1": "nominal"}
    assert joint.validation.score == pytest.approx(28.122)
    # The independent proposal scores higher but is not achievable.
    assert Fraction(report.exact_score) > Fraction(joint.validation.exact_score)


def test_shared_conflict_rejections_are_explained():
    problem = example("shared-dsn-conflict")
    rejected = generate_candidates(problem).rejections
    shadow = [r for r in rejected if r.site_id == "shadow-c"]
    assert shadow and all("terrain_unknown" in r.codes for r in shadow)
    rover = [r for r in rejected if r.mission_id == "rover-1"]
    assert [r.landing_utc.hour for r in rover] == [6] and "energy" in rover[0].codes


def test_independent_plan_marks_mission_without_alone_schedule():
    problem = build(
        missions=[mission("a", "va"), mission("b", "vb", sites={"s1": 4}, slots=20)],
        stations=[station(cap=2)],
    )
    independent = plan_independently(problem)
    assert independent.selections["b"] is None
    assert "cancel_not_allowed" in codes(validate_campaign_plan(independent, problem))


def test_infeasible_example_has_minimal_conflict_set():
    problem = example("infeasible-mandatory")
    assert solve_joint(problem).status == "infeasible"
    sets = explain_infeasibility(problem)
    assert [s.mission_ids for s in sets] == [("crew-2", "science-2")]
    assert "crew-2" in sets[0].detail and "science-2" in sets[0].detail
    for mid in ("crew-2", "science-2"):
        assert solve_joint(problem, only_mission_ids=(mid,)).status == "optimal"


def test_single_mission_conflict_reports_rejections():
    problem = build(sites=[site("s1", slope=None)])
    sets = explain_infeasibility(problem)
    assert [s.mission_ids for s in sets] == [("a",)]
    assert "terrain_unknown" in sets[0].detail
    assert explain_infeasibility(build()) == ()


def test_plan_campaign_explanations_for_shared_conflict():
    result = plan_campaign(example("shared-dsn-conflict"))
    assert result.joint.status == "optimal" and result.conflict_sets == ()
    by_id = {e.mission_id: e for e in result.explanations}
    clps = by_id["clps-1"]
    assert (
        clps.status == "deferred"
        and clps.rejection_counts.get("terrain_unknown", 0) > 0
    )
    selected = [a for a in clps.alternatives if a.status == "selected"]
    assert [a.option_id for a in selected] == ["clps-1.rim-b.20261201T120000Z"]
    assert selected[0].reason == "best_joint_score" and selected[0].score_delta == 0
    nominal = [
        a for a in clps.alternatives if a.option_id == "clps-1.rim-b.20261201T000000Z"
    ]
    assert (
        nominal
        and nominal[0].reason == "forcing_infeasible"
        and nominal[0].score_delta is None
    )
    cancel = [a for a in clps.alternatives if a.status == "cancel_not_selected"]
    assert (
        cancel and cancel[0].reason == "lower_joint_score" and cancel[0].score_delta < 0
    )
    assert len([a for a in clps.alternatives if a.status == "not_selected"]) <= 3
    assert by_id["crew-1"].status == "nominal"
    assert not any(a.status.startswith("cancel") for a in by_id["crew-1"].alternatives)
    assert result.independent_validation.status == "infeasible"


def test_plan_campaign_for_infeasible_and_relay_examples():
    infeasible = plan_campaign(example("infeasible-mandatory"))
    assert infeasible.joint.status == "infeasible" and infeasible.joint.plan is None
    assert infeasible.conflict_sets[0].mission_ids == ("crew-2", "science-2")
    assert {e.status for e in infeasible.explanations} == {"unplanned"}
    relay = plan_campaign(example("relay-support"))
    outcome = {o.mission_id: o for o in relay.joint.validation.outcomes}
    assert outcome["crater-3"].status == "nominal"
    assert outcome["crater-3"].relayed_bits == outcome["crater-3"].required_bits > 0
    assert outcome["relay-3"].relayed_bits == 0


def test_relay_example_loses_credit_without_line_of_sight():
    data = example("relay-support").model_dump(mode="json")
    data["relay_geometry"] = []
    outcome = solve_joint(CampaignProblem.model_validate(data))
    assert outcome.plan.selections["crater-3"] is None
    data = example("relay-support").model_dump(mode="json")
    data["missions"] = [
        {**m, "relay_service": {**m["relay_service"], "max_range_m": 1000}}
        if m["relay_service"]
        else m
        for m in data["missions"]
    ]
    assert (
        solve_joint(CampaignProblem.model_validate(data)).plan.selections["crater-3"]
        is None
    )


def test_timeline_presents_plan_usage_conflicts_and_dependencies():
    problem = example("shared-dsn-conflict")
    result = plan_campaign(problem)
    timeline = result.timeline
    assert {m["mission_id"] for m in timeline["missions"]} == {
        "crew-1",
        "clps-1",
        "rover-1",
    }
    lanes = {m["mission_id"]: m for m in timeline["missions"]}
    assert (
        lanes["clps-1"]["status"] == "deferred" and lanes["clps-1"]["deferred"] is True
    )
    assert lanes["rover-1"]["landing_utc"] == "2026-12-01T18:00:00Z"
    for st in timeline["stations"]:
        for usage in st["slots"]:
            assert 0 < usage["used"] <= usage["capacity"]
    allocations = timeline["allocations"]
    assert len(allocations) == len(result.joint.plan.allocations)
    deps = timeline["dependencies"]
    assert deps == [
        {
            "predecessor_mission_id": "clps-1",
            "successor_mission_id": "rover-1",
            "min_lag_s": 21600,
            "satisfied": True,
        }
    ]
    conflict_codes = {c["code"] for c in timeline["conflicts"]}
    assert {"station_capacity", "exclusive_link", "dependency_lag"} <= conflict_codes
    assert {m["mission_id"] for m in timeline["independent"]} == {
        "crew-1",
        "clps-1",
        "rover-1",
    }
    json.dumps(timeline, allow_nan=False)
    empty = campaign_timeline(problem, result.candidate_set, None, None)
    assert all(m["status"] == "unplanned" for m in empty["missions"])


def test_plan_campaign_is_deterministic():
    problem = example("shared-dsn-conflict")
    one = plan_campaign(problem).model_dump(mode="json")
    two = plan_campaign(problem).model_dump(mode="json")
    one["joint"].pop("elapsed_s")
    two["joint"].pop("elapsed_s")
    assert one == two


def test_planning_does_not_mutate_input():
    problem = example("relay-support")
    before = copy.deepcopy(problem.model_dump(mode="json"))
    plan_campaign(problem)
    assert problem.model_dump(mode="json") == before


# --------------------------------------------------------------- adapters


def test_site_evidence_from_real_result_shapes():
    start = T0
    end = T0 + timedelta(hours=6)
    earth = [
        {
            "start_utc": iso(0),
            "end_utc": iso(3),
            "state": "visible",
            "duration_s": 10800,
        },
        {
            "start_utc": iso(3),
            "end_utc": iso(6),
            "state": "blocked",
            "duration_s": 10800,
        },
    ]
    stations = {
        "DSS-14": [
            {
                "start_utc": iso(0),
                "end_utc": iso(6),
                "state": "above",
                "duration_s": 21600,
            }
        ],
        "DSS-43": [
            {
                "start_utc": iso(0),
                "end_utc": iso(1),
                "state": "unknown",
                "duration_s": 3600,
            },
            {
                "start_utc": iso(1),
                "end_utc": iso(6),
                "state": "above",
                "duration_s": 18000,
            },
        ],
    }
    windows = communication_windows(earth, stations, start, end)
    visibility = {
        "request": {"start_utc": iso(0), "end_utc": iso(6)},
        "sun": {
            "intervals": [
                {
                    "start_utc": iso(0),
                    "end_utc": iso(4),
                    "state": "full",
                    "duration_s": 14400,
                },
                {
                    "start_utc": iso(4),
                    "end_utc": iso(6),
                    "state": "partial",
                    "duration_s": 7200,
                },
            ]
        },
        "earth": {"intervals": earth},
    }
    import numpy as np

    heights = np.zeros((61, 61))
    safety = site_safety(heights, 80.0, 30, 30, SafetySettings())
    descent = plan_descent(
        DescentRequest(
            site_id="malapert-massif-r23",
            latitude_deg=-85.9948,
            longitude_deg_east=359.76422,
        ),
        terrain_height_m=0.0,
    )
    evidence, sources = site_evidence_from_results(
        site_id="malapert-massif-r23",
        latitude_deg=-85.9948,
        longitude_deg_east=359.76422,
        analysis_id="job-1",
        visibility=visibility,
        terrain_safety=safety,
        descents={"apollo-lm-11": descent},
        communication_id="comm-1",
        communication_windows=windows,
    )
    assert isinstance(evidence, SiteEvidence)
    assert (
        evidence.evidence_window.start_utc == start
        and evidence.evidence_window.end_utc == end
    )
    assert [i.state for i in evidence.sunlight] == ["full", "partial"]
    geo = {
        g.station_id: [
            (i.window.start_utc.hour, i.window.end_utc.hour, i.state)
            for i in g.intervals
        ]
        for g in evidence.station_geometry
    }
    assert geo["DSS-14"] == [(0, 3, "available"), (3, 6, "unavailable")]
    assert geo["DSS-43"] == [
        (0, 1, "unknown"),
        (1, 3, "available"),
        (3, 6, "unavailable"),
    ]
    assert evidence.terrain.max_landing_slope_deg == pytest.approx(0.0)
    (dsc,) = evidence.descents
    assert dsc.vehicle_id == "apollo-lm-11" and dsc.feasible == descent["feasible"]
    assert dsc.propellant_used_kg == pytest.approx(
        descent["totals"]["propellant_used_kg"]
    )
    kinds = {s.source_id: s for s in sources}
    assert kinds["analysis:job-1"].kind == "observed"
    assert kinds["analysis:job-1"].analysis_id == "job-1"
    assert kinds["communication:comm-1"].kind == "observed"
    referenced = set(evidence.source_ids) | set(evidence.terrain.source_ids)
    referenced |= {s for d in evidence.descents for s in d.source_ids}
    assert referenced <= set(kinds)
    for source in sources:
        assert set(source.parent_source_ids) <= set(kinds)
    no_comm, _ = site_evidence_from_results(
        site_id="malapert-massif-r23",
        latitude_deg=-85.9948,
        longitude_deg_east=359.76422,
        analysis_id="job-1",
        visibility=visibility,
        terrain_safety=safety,
        descents={},
    )
    assert no_comm.station_geometry == () and no_comm.descents == ()


def test_adapter_rejects_communication_outside_analysis_window():
    visibility = {
        "request": {"start_utc": iso(0), "end_utc": iso(2)},
        "sun": {
            "intervals": [
                {
                    "start_utc": iso(0),
                    "end_utc": iso(2),
                    "state": "full",
                    "duration_s": 7200,
                }
            ]
        },
        "earth": {"intervals": []},
    }
    windows = {
        "stations": {
            "DSS-14": {
                "intervals": [
                    {
                        "start_utc": iso(0),
                        "end_utc": iso(5),
                        "state": "available",
                        "duration_s": 18000,
                    }
                ]
            }
        }
    }
    with pytest.raises(ValueError):
        site_evidence_from_results(
            site_id="x",
            latitude_deg=-89.0,
            longitude_deg_east=0.0,
            analysis_id="j",
            visibility=visibility,
            terrain_safety={
                "max_landing_slope_deg": 1.0,
                "unknown_landing_fraction": 0.0,
                "hazard_distance_m": None,
            },
            descents={},
            communication_id="c",
            communication_windows=windows,
        )


# ------------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "state", tmp_path / "data", start_worker=False)
    ) as c:
        yield c


def test_example_routes(client):
    listing = client.get("/campaigns/examples")
    assert listing.status_code == 200
    names = [row["name"] for row in listing.json()]
    assert names == ["infeasible-mandatory", "relay-support", "shared-dsn-conflict"]
    assert all(row["description"] for row in listing.json())
    body = client.get("/campaigns/examples/relay-support").json()
    assert CampaignProblem.model_validate(body) == example("relay-support")
    assert client.get("/campaigns/examples/../secret").status_code == 404
    assert client.get("/campaigns/examples/missing").status_code == 404


def test_plan_route(client):
    body = client.get("/campaigns/examples/shared-dsn-conflict").json()
    response = client.post("/campaigns/plan", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["joint"]["status"] == "optimal"
    assert result["independent_validation"]["status"] == "infeasible"
    assert result["timeline"]["missions"]
    body["slot_s"] = 7
    assert client.post("/campaigns/plan", json=body).status_code == 422


def test_evidence_route_errors(client):
    assert (
        client.post("/analyses/missing/campaign-evidence", json={}).status_code == 404
    )
    analyses = client.app.state.store
    submission = Submission(
        site_id="malapert-massif-r23",
        latitude_deg=-85.9948,
        longitude_deg_east=359.76422,
        start_utc="2026-12-01T00:00:00Z",
        end_utc="2026-12-01T06:00:00Z",
        step_s=3600,
        refine=False,
    )
    pending = analyses.create(submission)["id"]
    assert (
        client.post(f"/analyses/{pending}/campaign-evidence", json={}).status_code
        == 409
    )
    ready = analyses.create(submission)["id"]
    analyses.update(
        ready,
        "ready",
        result={
            "analysis": {
                "request": {"start_utc": iso(0), "end_utc": iso(6)},
                "sun": {
                    "intervals": [
                        {
                            "start_utc": iso(0),
                            "end_utc": iso(6),
                            "state": "full",
                            "duration_s": 21600,
                        }
                    ]
                },
                "earth": {"intervals": []},
            },
            "frames": [],
        },
    )
    missing_comm = client.post(
        f"/analyses/{ready}/campaign-evidence", json={"communication_id": "nope"}
    )
    assert missing_comm.status_code == 404
    unknown_profile = client.post(
        f"/analyses/{ready}/campaign-evidence", json={"vehicle_profile_ids": ["ghost"]}
    )
    assert unknown_profile.status_code == 422
    no_terrain = client.post(f"/analyses/{ready}/campaign-evidence", json={})
    assert no_terrain.status_code == 409
