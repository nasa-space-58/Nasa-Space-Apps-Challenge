"""A saved custom replay must include the complete input needed to reproduce it."""
from app.mission_resilience import MissionScenario, replay_mission
from test_mission_resilience import config, event, scenario


def test_exported_inputs_reproduce_custom_events_and_resources():
    original = scenario(config(initial_phase='surface', initial_energy_wh=13.0), (
        event('link-down', 'surface', 0, 'link', 0),
        event('link-back', 'surface', 10, 'link', 1),
    ))
    report = replay_mission(original)
    restored = MissionScenario.model_validate(report['inputs'])
    assert restored == original
    assert replay_mission(restored) == report
