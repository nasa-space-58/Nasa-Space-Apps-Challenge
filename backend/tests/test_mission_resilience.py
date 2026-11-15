"""Frozen acceptance of causal synthetic mission replay, not flight certification."""
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.mission_resilience import (
    MissionConfig, MissionEvent, MissionScenario, PlannerView, ReplayCancelled,
    plan_mission, replay_mission, resilience_examples,
)

ROOT = Path(__file__).resolve().parents[2]


def config(**updates):
    base = json.loads((ROOT / 'shared/examples/recovery/combined_recovery.json').read_text())
    raw = dict(
        scenario_id='acceptance', source=base['uncertainty']['source'],
        terminal=base['initial_request'], targets={'primary': 0.0, 'backup': 40.0, 'spare': -40.0, 'remote': 5000.0},
        routes=[dict(route_id='direct', duration_s=10.0, fuel_kg=3.0, min_thrust_n=2000.0, requires_link=True),
                dict(route_id='autonomous', duration_s=16.0, fuel_kg=2.0, min_thrust_n=1000.0)],
        tasks=[dict(task_id='science', duration_s=6, power_w=40.0, priority=3, hardware='camera'),
               dict(task_id='transmit', duration_s=3, power_w=20.0, priority=2, hardware='radio', requires_link=True)],
        horizon_s=80,
    )
    raw.update(updates)
    return MissionConfig.model_validate(raw)


def view(c, **updates):
    raw = dict(
        phase=c.initial_phase, met_s=0.0, phase_elapsed_s=0.0, revision=0,
        fuel_kg=c.terminal.initial_state.propellant_kg, energy_wh=c.initial_energy_wh,
        temperature_c=c.initial_temperature_c, available_thrust_n=c.terminal.vehicle.max_thrust_n,
        solar_power_w=c.solar_power_w, thermal_drift_cps=c.thermal_drift_cps,
        link=True, weather_ok=True, hardware=('camera', 'radio', 'heater', 'cooler'),
        landing_state=c.terminal.initial_state if c.initial_phase == 'descent' else None,
    )
    raw.update(updates)
    return PlannerView.model_validate(raw)


def event(name, phase, at, kind, value, **kw):
    return MissionEvent(event_id=name, phase=phase, at_s=at, kind=kind, value=float(value), **kw)


def scenario(c=None, events=()):
    return MissionScenario(config=c or config(), events=events)


def digest(s):
    return hashlib.sha256(json.dumps(s.model_dump(mode='json'), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def test_complete_four_phase_replay_is_reproducible_and_keeps_units():
    s = scenario()
    a, b = replay_mission(s), replay_mission(s)
    assert a == b
    assert a['input_sha256'] == digest(s)
    assert a['status'] == 'completed'
    assert [row['phase'] for row in a['transitions']] == ['launch', 'cruise', 'descent', 'surface']
    assert a['completed_tasks'] == ['science', 'transmit']
    assert a['quantum_calls'] == 0
    assert a['limitations'] and a['source']['kind'] == 'synthetic'
    assert all(f['truth']['fuel_kg'] >= 0 and f['truth']['energy_wh'] >= 0 for f in a['frames'])
    assert a['frames'][-1]['truth']['fuel_kg'] < a['frames'][0]['truth']['fuel_kg']


@pytest.mark.parametrize('updates', [
    {'initial_energy_wh': 30.0}, {'reserve_energy_wh': 30.0},
    {'launch_windows_s': ((8, 4),)}, {'temperature_limits_c': (50.0, -20.0)},
    {'targets': {'bad': float('nan')}}, {'horizon_s': 301},
    {'planning_delay_s': 11}, {'navigation_bound_m': -1.0},
])
def test_invalid_configuration_rejected(updates):
    with pytest.raises(ValidationError):
        config(**updates)


@pytest.mark.parametrize('kind,value,subject', [
    ('fuel_loss', -1, 'vehicle'), ('thrust', 10001, 'vehicle'),
    ('link', 2, 'vehicle'), ('weather', -1, 'vehicle'),
    ('hazard', 1, 'missing'), ('hardware', 0, 'missing'),
    ('solar', -1, 'vehicle'), ('energy_loss', -1, 'vehicle'),
])
def test_invalid_events_rejected(kind, value, subject):
    with pytest.raises(ValidationError):
        scenario(events=(event('invalid', 'descent', 1, kind, value, subject=subject),))


def test_duplicate_event_and_route_identity_rejected():
    e = event('same', 'surface', 1, 'link', 0)
    with pytest.raises(ValidationError):
        scenario(events=(e, e))
    c = config()
    with pytest.raises(ValidationError):
        config(routes=[c.routes[0].model_dump(), c.routes[0].model_dump()])


def test_launch_weather_defers_and_consumes_time_energy():
    c = config(solar_power_w=0.0)
    s = scenario(c, (event('bad', 'launch', 0, 'weather', 0), event('clear', 'launch', 7, 'weather', 1)))
    result = replay_mission(s)
    launch_end = next(t for t in result['transitions'] if t['phase'] == 'cruise')
    assert launch_end['met_s'] >= 12
    assert launch_end['energy_wh'] < c.initial_energy_wh
    assert any('weather' in reason for d in result['decisions'] for cand in d['candidates'] for reason in cand['reasons'])


def test_exhausted_launch_windows_do_not_invent_safe_wait():
    c = config(launch_windows_s=((0, 3),))
    result = replay_mission(scenario(c, (event('bad', 'launch', 0, 'weather', 0),)))
    assert result['status'] == 'no_feasible_continuation'
    assert result['frames'][-1]['met_s'] < c.horizon_s


def test_cruise_thrust_and_link_force_replanning_and_preserve_progress():
    c = config(initial_phase='cruise')
    result = replay_mission(scenario(c, (
        event('lost', 'cruise', 2, 'link', 0),
        event('thrust', 'cruise', 3, 'thrust', 1500),
        event('back', 'cruise', 7, 'link', 1),
    )))
    assert any(d['selected'] and d['selected']['id'] == 'autonomous' for d in result['decisions'])
    progress = [f['estimate']['cruise_progress'] for f in result['frames'] if f['phase'] == 'cruise']
    assert progress == sorted(progress)
    assert any('link_required' in cand['reasons'] for d in result['decisions'] for cand in d['candidates'])


def test_arrival_deadline_and_fuel_are_hard_constraints():
    c = config(initial_phase='cruise', arrival_deadline_met_s=1)
    assert plan_mission(c, view(c))['selected'] is None
    c = config(initial_phase='cruise')
    assert plan_mission(c, view(c, fuel_kg=1.0))['selected'] is None


def test_terminal_candidates_use_physics_reject_remote_and_hazard():
    c = config(initial_phase='descent')
    p = plan_mission(c, view(c, blocked_targets=('primary',)))
    rows = {r['id']: r for r in p['candidates']}
    assert 'landing_hazard' in rows['primary']['reasons']
    assert not rows['remote']['feasible']
    assert p['selected'] is not None and p['selected']['id'] in ('backup', 'spare')
    assert rows[p['selected']['id']]['physics']['numerically_consistent']


def test_sequential_target_failures_are_all_reconsidered():
    c = config(initial_phase='descent', planning_delay_s=0)
    result = replay_mission(scenario(c, (
        event('primary', 'descent', 1, 'hazard', 1, subject='primary'),
        event('backup', 'descent', 3, 'hazard', 1, subject='backup'),
        event('spare', 'descent', 5, 'hazard', 1, subject='spare'),
    )))
    assert len(result['observations']) >= 3
    assert result['status'] == 'no_feasible_continuation'
    assert any(d['selected'] and d['selected']['id'] != 'primary' for d in result['decisions'])
    assert any(set(f['estimate']['blocked_targets']) >= {'primary', 'backup', 'spare'} for f in result['frames'])


def test_no_solution_with_empty_tank_or_zero_thrust():
    c = config(initial_phase='descent')
    for updates in ({'fuel_kg': 0.0}, {'available_thrust_n': 0.0}):
        assert plan_mission(c, view(c, **updates))['selected'] is None


def test_sensor_disagreement_is_not_perfect_truth_navigation():
    c = config(initial_phase='descent')
    result = replay_mission(scenario(c, (event('bias', 'descent', 1, 'sensor_bias', 30, notice_delay_s=4),)))
    f = next(f for f in result['frames'] if f['met_s'] == 1)
    assert f['estimate']['sensor_conflict']
    assert f['estimate']['navigation_bound_m'] >= 15
    assert f['estimate']['landing_state']['position_m'] != f['truth']['landing_state']['position_m']
    assert 'bias' not in [o['event_id'] for o in result['observations'] if o['met_s'] <= 1]
    assert result['status'] == 'no_feasible_continuation'


def test_future_hidden_events_do_not_change_earlier_decisions():
    c = config(initial_phase='surface')
    clean = replay_mission(scenario(c))
    altered = replay_mission(scenario(c, (event('future', 'surface', 5, 'hardware', 0, subject='camera', notice_delay_s=2),)))
    assert [d for d in clean['decisions'] if d['met_s'] < 5] == [d for d in altered['decisions'] if d['met_s'] < 5]
    assert all(o['met_s'] >= 7 for o in altered['observations'] if o['event_id'] == 'future')


def test_surface_link_restoration_allows_delayed_transmission():
    c = config(initial_phase='surface')
    result = replay_mission(scenario(c, (event('off', 'surface', 0, 'link', 0), event('on', 'surface', 9, 'link', 1))))
    assert result['status'] == 'completed'
    assert 'transmit' in result['completed_tasks']
    assert any('link_required' in x['reasons'] for d in result['decisions'] for x in d['candidates'])
    assert result['frames'][-1]['met_s'] >= 12


def test_surface_hardware_loss_cancels_unavailable_work():
    c = config(initial_phase='surface', horizon_s=15)
    result = replay_mission(scenario(c, (event('broken', 'surface', 1, 'hardware', 0, subject='camera'),)))
    assert 'science' not in result['completed_tasks']
    assert any(x['task_id'] == 'science' and x['reason'] for x in result['unfinished_tasks'])
    assert result['status'] != 'completed'


def test_thermal_control_costs_energy_and_is_not_free_wait():
    c = config(initial_phase='surface', initial_temperature_c=-19.0, thermal_drift_cps=-0.2, solar_power_w=0.0)
    result = replay_mission(scenario(c))
    assert any(d['selected'] and d['selected']['id'] == 'heat' for d in result['decisions'])
    assert result['frames'][-1]['truth']['energy_wh'] < c.initial_energy_wh
    assert all(-20 <= f['truth']['temperature_c'] <= 50 for f in result['frames'])


def test_battery_exhaustion_is_failure_not_safe_idle():
    c = config(initial_phase='surface', initial_energy_wh=0.1, reserve_energy_wh=0.1, solar_power_w=0.0)
    result = replay_mission(scenario(c))
    assert result['status'] == 'no_feasible_continuation'
    assert result['frames'][-1]['met_s'] < 2


def test_fuel_and_energy_faults_do_not_create_negative_resources():
    c = config(initial_phase='surface')
    result = replay_mission(scenario(c, (event('empty', 'surface', 1, 'energy_loss', 1000),)))
    assert result['status'] == 'no_feasible_continuation'
    assert all(f['truth']['energy_wh'] >= 0 for f in result['frames'])
    assert any('depleted' in r for r in result['reasons'])


def test_planning_latency_ages_state_and_fences_old_revision():
    c = config(initial_phase='surface', planning_delay_s=3)
    result = replay_mission(scenario(c, (
        event('lost', 'surface', 1, 'link', 0),
        event('back', 'surface', 2, 'link', 1),
    )))
    assert any(d['status'] == 'stale' for d in result['decisions'])
    assert all(d['met_s'] >= d['based_on_met_s'] for d in result['decisions'])
    assert any(d['met_s'] > d['based_on_met_s'] for d in result['decisions'])


def test_expired_proposals_are_not_applied():
    c = config(initial_phase='surface', planning_delay_s=4, plan_ttl_s=1)
    result = replay_mission(scenario(c, (event('lost', 'surface', 1, 'link', 0),)))
    assert any(d['status'] == 'expired' for d in result['decisions'])
    assert all(d['met_s'] - d['based_on_met_s'] < c.plan_ttl_s for d in result['decisions'] if d['status'] == 'accepted')


def test_replay_cancellation_does_not_return_success():
    with pytest.raises(ReplayCancelled):
        replay_mission(scenario(), cancelled=lambda: True)


def test_examples_cover_all_phases_and_are_valid():
    cases = resilience_examples()
    assert len(cases) >= 5
    phases = set()
    for body in cases.values():
        s = MissionScenario.model_validate(body)
        phases.add(s.config.initial_phase)
    assert phases == {'launch', 'cruise', 'descent', 'surface'}
