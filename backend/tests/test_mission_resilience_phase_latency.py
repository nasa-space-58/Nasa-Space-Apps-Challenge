"""Phase changes do not create free planning time or future knowledge."""
from app.mission_resilience import replay_mission
from test_mission_resilience import config, scenario


def test_only_initial_preflight_is_instantaneous():
    c = config(planning_delay_s=1)
    result = replay_mission(scenario(c))
    assert result['status'] == 'completed'
    for transition in result['transitions'][1:]:
        accepted = [d for d in result['decisions'] if d['status'] == 'accepted' and d['phase'] == transition['phase']]
        assert accepted
        assert accepted[0]['met_s'] >= transition['met_s'] + c.planning_delay_s
        assert accepted[0]['met_s'] > accepted[0]['based_on_met_s']
    assert result['decisions'][0]['met_s'] == 0
