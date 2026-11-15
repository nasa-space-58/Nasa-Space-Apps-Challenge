"""Mission run surface outlook, fault-free baseline and window context.

The verdict (failure, gates, power, mission_end_utc) is covered by the frozen
tests in test_mission_run.py and must stay unchanged; these tests cover only
the additive fields.

Hand-computed values use the default vehicle: battery 4000 Wh at 90 % charge
(3600 Wh), 900 W array, 50 % output in partial sun, 350 W base load, first
contact within 1 h and gaps of at most 72 h. An ideal battery with constant
net power changes energy linearly, so depletion times are exact; timestamps
are compared with a 2 s tolerance for rounding and the touchdown time, which
is the R4 contact time and differs from the window start by under a second.
"""

from datetime import timedelta

import pytest

from app.mission_run import (
    LIMITATION_CODES,
    OUTLOOK_FINDING_KINDS,
    MissionRunRequest,
    run_mission,
)
from tests.test_mission_run import (
    H,
    T0,
    analysis,
    client,  # noqa: F401  (fixture)
    gate,
    iv,
    run,
    safe_terrain,
    stored_analysis,
    utc,
    write_dem,
)

TOL = timedelta(seconds=2)
HEATER = {
    "kind": "power_load_increase",
    "start_after_landing_h": 6,
    "duration_h": 48,
    "extra_load_w": 250,
}
DUST = {"kind": "solar_array_degradation", "start_after_landing_h": 0, "output_fraction": 0.4}
CELLS = {"kind": "battery_capacity_loss", "start_after_landing_h": 0, "capacity_fraction_lost": 0.5}
LEAK = {"kind": "propellant_leak", "start_after_pdi_s": 60, "leak_rate_kg_s": 0.5, "duration_s": 600}
CRASH = {"kind": "engine_thrust_loss", "start_after_pdi_s": 200, "thrust_fraction": 0.9}


def faults(*items):
    return MissionRunRequest(faults=list(items))


def landing(result):
    return utc(result["landing_utc"])


def kinds(outlook):
    return [f["kind"] for f in outlook["findings"]]


def codes(context, fault=None):
    return {
        item["code"]
        for item in context["limitations"]
        if fault is None or item.get("fault") == fault
    }


def blocked_earth(hours):
    return analysis(sun=[iv(0, hours, "partial")], earth=[iv(0, hours, "blocked")], hours=hours)


# ------------------------------------------------------------- surface outlook


def test_outlook_keeps_running_after_a_communication_verdict():
    # Partial sun: 450 W - 350 W = +100 W, full (4000 Wh) after 4 h. The heater
    # adds 250 W from 6 h: -150 W, so the battery is empty 4000/150 h later.
    data = blocked_earth(240)
    result = run(faults(HEATER), data=data)
    assert result["failure"]["code"] == "first_contact_deadline"
    outlook = result["surface_outlook"]
    t0 = landing(result)
    assert utc(outlook["landing_utc"]) == t0
    depleted = t0 + timedelta(hours=6 + 4000 / 150)
    assert abs(utc(outlook["power"]["depleted_at_utc"]) - depleted) <= TOL
    assert abs(utc(outlook["evaluated_until_utc"]) - depleted) <= TOL
    assert outlook["power"]["min_energy_wh"] == pytest.approx(0, abs=1e-6)
    assert outlook["power"]["trace"][0]["energy_wh"] == pytest.approx(3600)
    assert max(r["energy_wh"] for r in outlook["power"]["trace"]) == pytest.approx(4000)
    assert kinds(outlook) == [
        "first_contact_deadline",
        "fault_activated",
        "battery_depleted",
    ]
    heater = outlook["findings"][1]
    assert heater["fault"] == "power_load_increase"
    assert abs(utc(heater["utc"]) - (t0 + 6 * H)) <= TOL


def test_nominal_outlook_survives_the_whole_window():
    data = blocked_earth(240)
    result = run(data=data)
    outlook = result["surface_outlook"]
    assert outlook["power"]["depleted_at_utc"] is None
    assert outlook["power"]["min_energy_wh"] == pytest.approx(3600)
    assert utc(outlook["evaluated_until_utc"]) == T0 + 240 * H
    assert outlook["communication"]["windows"] == []
    assert outlook["communication"]["first_contact_utc"] is None
    assert kinds(outlook) == ["first_contact_deadline"]


def test_surface_faults_differ_in_the_outlook_even_with_the_same_verdict():
    data = blocked_earth(24)
    results = {
        name: run(request, data=data)
        for name, request in {
            "nominal": MissionRunRequest(),
            "heater": faults(HEATER),
            "dust": faults(DUST),
            "cells": faults(CELLS),
        }.items()
    }
    assert {r["failure"]["code"] for r in results.values()} == {"first_contact_deadline"}
    minima = {k: r["surface_outlook"]["power"]["min_energy_wh"] for k, r in results.items()}
    assert minima["nominal"] == pytest.approx(3600)
    # Heater: 4000 Wh at 6 h, then -150 W for the remaining 18 h.
    assert minima["heater"] == pytest.approx(4000 - 150 * 18, abs=1)
    # Dust: 900*0.5*0.4 - 350 = -170 W; empty after 3600/170 h < 24 h.
    assert minima["dust"] == pytest.approx(0, abs=1e-6)
    dust = results["dust"]
    assert abs(
        utc(dust["surface_outlook"]["power"]["depleted_at_utc"])
        - (landing(dust) + timedelta(hours=3600 / 170))
    ) <= TOL
    # Cells: half capacity at touchdown, 1800 Wh, then charging.
    assert minima["cells"] == pytest.approx(1800)
    assert len(set(round(v) for v in minima.values())) == 4


def test_a_dead_lander_makes_no_contact():
    # Darkness: empty after 3600/350 h; Earth rises only at 20 h.
    data = analysis(
        sun=[iv(0, 240, "dark")],
        earth=[iv(0, 20, "blocked"), iv(20, 240, "visible")],
    )
    result = run(data=data)
    outlook = result["surface_outlook"]
    depleted = landing(result) + timedelta(hours=3600 / 350)
    assert abs(utc(outlook["evaluated_until_utc"]) - depleted) <= TOL
    assert outlook["communication"]["first_contact_utc"] is None
    assert outlook["communication"]["windows"] == []
    assert kinds(outlook) == ["first_contact_deadline", "battery_depleted"]


def test_every_contact_gap_and_the_first_contact_are_findings():
    data = analysis(
        earth=[
            iv(0, 2, "visible"),
            iv(2, 80, "blocked"),
            iv(80, 90, "visible"),
            iv(90, 170, "blocked"),
            iv(170, 240, "visible"),
        ]
    )
    result = run(data=data)
    assert result["failure"]["code"] == "contact_gap"
    outlook = result["surface_outlook"]
    assert kinds(outlook) == ["first_contact", "contact_gap", "contact_gap"]
    first, gap1, gap2 = outlook["findings"]
    assert abs(utc(first["utc"]) - landing(result)) <= TOL
    assert abs(utc(gap1["utc"]) - (T0 + 74 * H)) <= TOL
    assert abs(utc(gap2["utc"]) - (T0 + 162 * H)) <= TOL
    assert [w["end_utc"] for w in outlook["communication"]["windows"]][-1] == (
        (T0 + 240 * H).isoformat().replace("+00:00", "Z")
    )
    assert outlook["communication"]["longest_gap_h"] == pytest.approx(80)


def test_outages_and_their_end_are_findings():
    outage = {"kind": "communication_outage", "start_after_landing_h": 2, "duration_h": 10}
    result = run(faults(outage))
    outlook = result["surface_outlook"]
    assert kinds(outlook) == ["first_contact", "fault_activated", "fault_cleared"]
    t0 = landing(result)
    assert abs(utc(outlook["findings"][1]["utc"]) - (t0 + 2 * H)) <= TOL
    assert abs(utc(outlook["findings"][2]["utc"]) - (t0 + 12 * H)) <= TOL
    assert outlook["findings"][2]["fault"] == "communication_outage"


def test_findings_are_in_time_order_with_known_kinds():
    data = analysis(
        sun=[iv(0, 240, "partial")],
        earth=[iv(0, 3, "blocked"), iv(3, 240, "visible")],
    )
    result = run(faults(DUST, HEATER, CELLS), data=data)
    findings = result["surface_outlook"]["findings"]
    times = [utc(f["utc"]) for f in findings]
    assert times == sorted(times)
    assert set(kinds(result["surface_outlook"])) <= set(OUTLOOK_FINDING_KINDS)
    assert all(f["detail"] for f in findings)


def test_outlook_is_absent_without_a_surface_phase():
    crash = run(faults(CRASH))
    assert crash["failure"]["gate"] == "descent"
    assert crash["surface_outlook"] is None
    tipped = run(terrain=safe_terrain(slope=14.0, max_slope=16.0))
    assert tipped["failure"]["code"] == "tip_over"
    assert tipped["surface_outlook"] is None


def test_verdict_fields_are_unchanged_by_the_outlook():
    data = blocked_earth(24)
    result = run(faults(HEATER), data=data)
    # The verdict still ends at the communication failure, 1 h after touchdown.
    assert abs(utc(result["mission_end_utc"]) - (landing(result) + H)) <= TOL
    assert gate(result, "power")["status"] == "pass"
    assert result["power"]["depleted_at_utc"] is None


# ------------------------------------------------------------- baseline


def test_baseline_is_the_fault_free_run():
    data = analysis(sun=[iv(0, 240, "full")])
    result = run(faults(LEAK), data=data)
    base = result["baseline"]
    assert utc(base["landing_utc"]) == T0
    assert base["propellant_margin_kg"] == pytest.approx(
        result["descent"]["nominal_propellant_margin_kg"]
    )
    assert base["touchdown_offset_m"] == 0
    assert base["min_energy_wh"] == pytest.approx(3600)
    assert base["depleted_at_utc"] is None
    assert abs(utc(base["first_contact_utc"]) - T0) <= TOL


def test_leak_reduces_the_propellant_delta():
    result = run(faults(LEAK))
    delta = result["baseline"]["delta"]
    expected = (
        result["descent"]["propellant_margin_kg"]
        - result["descent"]["nominal_propellant_margin_kg"]
    )
    assert expected < 0
    assert delta["propellant_margin_kg"] == pytest.approx(expected)


def test_navigation_bias_shows_as_touchdown_offset_delta():
    result = run(faults({"kind": "navigation_bias", "offset_m": 600, "bearing_deg": 90}))
    assert result["baseline"]["delta"]["touchdown_offset_m"] == pytest.approx(600, abs=5)


def test_battery_loss_shows_as_minimum_energy_delta():
    result = run(faults(CELLS))
    assert result["baseline"]["delta"]["min_energy_wh"] == pytest.approx(-1800)


def test_landing_delay_and_first_contact_shift():
    delayed = run(faults({"kind": "landing_delay", "delay_h": 2}))
    assert delayed["baseline"]["delta"]["landing_delay_h"] == pytest.approx(2, abs=1e-3)
    data = analysis(earth=[iv(0, 3, "blocked"), iv(3, 240, "visible")])
    outage = {"kind": "communication_outage", "start_after_landing_h": 0, "duration_h": 5}
    result = run(faults(outage), data=data)
    # Baseline first contact at 3 h; the outage keeps the radio off until 5 h.
    assert result["baseline"]["delta"]["first_contact_shift_h"] == pytest.approx(2, abs=1e-3)


def test_nominal_run_has_zero_deltas():
    delta = run()["baseline"]["delta"]
    assert delta["propellant_margin_kg"] == pytest.approx(0, abs=1e-6)
    assert delta["touchdown_offset_m"] == pytest.approx(0, abs=0.1)
    assert delta["landing_delay_h"] == pytest.approx(0, abs=1e-3)
    assert delta["min_energy_wh"] == pytest.approx(0, abs=1e-6)
    assert delta["first_contact_shift_h"] == pytest.approx(0, abs=1e-3)


def test_crash_has_no_surface_deltas():
    delta = run(faults(CRASH))["baseline"]["delta"]
    assert delta["touchdown_offset_m"] is None
    assert delta["landing_delay_h"] is None
    assert delta["min_energy_wh"] is None
    assert delta["first_contact_shift_h"] is None


def test_missing_first_contact_gives_no_shift():
    result = run(data=blocked_earth(24))
    assert result["baseline"]["first_contact_utc"] is None
    assert result["baseline"]["delta"]["first_contact_shift_h"] is None


# ------------------------------------------------------------- window context


def test_earth_never_visible_is_reported():
    context = run(data=blocked_earth(24))["context"]
    assert "earth_never_visible" in codes(context)
    assert context["earth_visible_h"] == 0
    assert context["sun_visible_h"] == pytest.approx(24)
    assert context["surface_window_h"] == pytest.approx(24, abs=1e-3)


def test_unknown_earth_is_not_never_visible():
    data = analysis(earth=[iv(0, 10, "blocked"), iv(10, 240, "unknown")])
    assert "earth_never_visible" not in codes(run(data=data)["context"])


def test_successful_run_has_no_limitations():
    result = run()
    assert result["outcome"] == "success"
    assert result["context"]["limitations"] == []
    assert utc(result["context"]["verdict_utc"]) == T0 + 240 * H
    assert result["context"]["earth_visible_h"] == pytest.approx(240)


def test_surface_faults_after_the_verdict_are_reported():
    result = run(faults(HEATER), data=blocked_earth(24))
    context = result["context"]
    assert context["verdict_utc"] == result["failure"]["at_utc"]
    assert codes(context, "power_load_increase") == {
        "fault_after_mission_end",
        "fault_exceeds_window",
    }


def test_transmitter_fault_longer_than_the_window():
    outage = {"kind": "communication_outage", "start_after_landing_h": 2, "duration_h": 80}
    context = run(faults(outage), data=analysis(hours=24))["context"]
    assert codes(context, "communication_outage") == {"fault_exceeds_window"}


def test_fault_after_the_window_end():
    late = {**HEATER, "start_after_landing_h": 30, "duration_h": 5}
    context = run(faults(late), data=analysis(hours=24))["context"]
    assert codes(context, "power_load_increase") == {"fault_after_window"}


def test_surface_fault_is_not_reached_after_a_crash():
    context = run(faults(CRASH, CELLS))["context"]
    assert codes(context, "battery_capacity_loss") == {"fault_not_reached"}
    assert codes(context, "engine_thrust_loss") == set()
    assert context["surface_window_h"] is None


def test_limitations_use_known_codes_with_details():
    result = run(faults(HEATER, DUST, CELLS), data=blocked_earth(24))
    items = result["context"]["limitations"]
    assert items
    assert {i["code"] for i in items} <= set(LIMITATION_CODES)
    assert all(i["detail"] for i in items)


# ------------------------------------------------------------- API


def test_route_returns_the_additive_fields(client, tmp_path):  # noqa: F811
    ready = stored_analysis(client)
    write_dem(tmp_path / "data")
    url = f"/analyses/{ready}/mission-run"
    result = client.post(url, json={"scenario_id": "stuck-heater"}).json()
    for key in ("outcome", "failure", "gates", "power", "communication", "events"):
        assert key in result
    assert result["surface_outlook"]["findings"]
    assert "delta" in result["baseline"]
    assert "limitations" in result["context"]
    crash = client.post(url, json={"scenario_id": "major-propellant-leak"}).json()
    assert crash["surface_outlook"] is None
    assert crash["baseline"]["delta"]["min_energy_wh"] is None


def test_run_mission_signature_is_unchanged():
    result = run_mission(MissionRunRequest(), analysis(), 0.0, safe_terrain())
    assert {"surface_outlook", "baseline", "context"} <= set(result)
