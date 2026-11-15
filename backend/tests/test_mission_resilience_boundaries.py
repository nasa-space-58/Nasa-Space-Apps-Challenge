"""Independent current-control and delayed observation guards."""
from app.mission_resilience import replay_mission, validate_active_action
from test_mission_resilience import config, event, scenario, view


def action(kind='task', **kw):
    return dict(id='science', kind=kind, power_w=40.0, duration_s=6.0, fuel_kg=0.0, **kw)


def test_task_deadline_cannot_be_exceeded_by_an_old_command():
    c = config(initial_phase='surface')
    v = view(c, met_s=240.0)
    assert 'task_deadline' in validate_active_action(c, v, action())


def test_known_link_and_hardware_gate_execution_during_planning():
    c = config(initial_phase='surface')
    a = action()
    assert 'hardware_unavailable' in validate_active_action(c, view(c, hardware=('radio',)), a)
    a['id'] = 'transmit'
    assert 'link_required' in validate_active_action(c, view(c, link=False), a)


def test_current_energy_and_temperature_must_allow_one_more_tick():
    c = config(initial_phase='surface', solar_power_w=0.0)
    assert 'energy_reserve' in validate_active_action(c, view(c, energy_wh=0.1), action())
    assert 'temperature_limit' in validate_active_action(c, view(c, temperature_c=-20.0), action())


def test_old_fault_notice_cannot_overwrite_newer_measurement():
    c = config(initial_phase='surface', horizon_s=25)
    result = replay_mission(scenario(c, (
        event('new-link-down', 'surface', 0, 'link', 0),
        event('old-link-up', 'surface', 1, 'link', 1, notice_delay_s=9),
        event('new-link-down-again', 'surface', 2, 'link', 0),
        event('camera', 'surface', 0, 'hardware', 0, subject='camera'),
    )))
    at_ten = next(f for f in result['frames'] if f['met_s'] == 10)
    assert not at_ten['estimate']['link']
    delayed = next(o for o in result['observations'] if o['event_id'] == 'old-link-up')
    assert delayed['status'] == 'ignored_older_observation'
    assert 'transmit' not in result['completed_tasks']


def test_expired_task_is_never_completed_after_hidden_equipment_downtime():
    c = config(initial_phase='surface', horizon_s=20)
    raw = c.model_dump()
    raw['tasks'][0]['deadline_met_s'] = 7
    c = type(c).model_validate(raw)
    result = replay_mission(scenario(c, (
        event('camera-off', 'surface', 1, 'hardware', 0, subject='camera', notice_delay_s=8),
        event('camera-on', 'surface', 8, 'hardware', 1, subject='camera'),
    )))
    assert 'science' not in result['completed_tasks']
    assert max(f['truth']['task_progress_s']['science'] for f in result['frames']) <= 1


def test_current_landing_hazard_is_reported_as_unvalidated_control():
    c = config(initial_phase='descent', planning_delay_s=2)
    result = replay_mission(scenario(c, (event('blocked', 'descent', 1, 'hazard', 1, subject='primary'),)))
    affected = next(f for f in result['frames'] if f['met_s'] == 1)
    assert not affected['control_validated']
    assert 'landing_hazard' in affected['control_reasons']


def test_route_elapsed_delay_does_not_add_fuel_or_reset_progress():
    c = config(initial_phase='cruise', planning_delay_s=2)
    result = replay_mission(scenario(c, (event('lost', 'cruise', 2, 'link', 0),)))
    frames = [f for f in result['frames'] if f['phase'] == 'cruise']
    assert all(b['truth']['fuel_kg'] <= a['truth']['fuel_kg'] for a, b in zip(frames, frames[1:]))
    assert all(b['estimate']['cruise_progress'] >= a['estimate']['cruise_progress'] for a, b in zip(frames, frames[1:]))
