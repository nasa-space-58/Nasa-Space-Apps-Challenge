"""C3 protocol acceptance; no assertion of a quantum performance advantage."""
from copy import deepcopy

import numpy as np
import pytest

from app.mission_resilience import MissionScenario, replay_mission
from app.stress_discovery import (
    CLASSICAL, FAMILIES, METHODS, Budget, born_probabilities, fit_born,
    make_scenario, paired_summary, run_search, summarize_replay,
)


def fake_result(scenario):
    failed = bool(scenario.events)
    return {
        'status': 'partial' if failed else 'completed', 'reasons': [],
        'unfinished_tasks': [{'task_id': 'science'}] if failed else [],
        'inputs': scenario.model_dump(mode='json'),
        'frames': [], 'decisions': [], 'completed_tasks': [] if failed else ['science', 'transmit'],
    }


@pytest.mark.parametrize('kwargs', [dict(calls=1), dict(calls=66), dict(calls=True), dict(calls=2.5),
                                         dict(seconds=0), dict(seconds=float('nan')), dict(seconds=-1)])
def test_invalid_budget(kwargs):
    with pytest.raises(ValueError):
        Budget(**kwargs)


def test_template_splits_and_parameter_bounds():
    assert set(FAMILIES.values()) == {'development', 'validation', 'test'}
    assert all(list(FAMILIES.values()).count(s) == 2 for s in set(FAMILIES.values()))
    event_roots = []
    for family in FAMILIES:
        baseline = make_scenario(family, None)
        assert not baseline.events
        assert replay_mission(baseline)['status'] == 'completed'
        hashes = set()
        for index in range(64):
            s = make_scenario(family, index)
            assert MissionScenario.model_validate(s.model_dump()) == s
            assert all(e.at_s > 0 for e in s.events)
            assert s.config.initial_phase == 'surface'
            hashes.add(s.model_dump_json())
        assert len(hashes) == 64
        event_roots.append(tuple((e.kind, e.subject) for e in make_scenario(family, 0).events))
    assert len(set(event_roots)) == len(FAMILIES)


@pytest.mark.parametrize('family,index', [('unknown', 0), ('shade_link', -1), ('shade_link', 64),
                                         ('shade_link', True), ('shade_link', 1.5)])
def test_invalid_candidate(family, index):
    with pytest.raises(ValueError):
        make_scenario(family, index)


def test_cluster_ignores_time_and_input_identity_and_distinguishes_shortfall():
    raw = fake_result(make_scenario('shade_link', 0))
    a = summarize_replay(raw)
    changed = deepcopy(raw)
    changed['inputs']['config']['scenario_id'] = 'another'
    changed['met_s'] = 999
    b = summarize_replay(changed)
    assert a['cluster'] == b['cluster']
    assert a['failed'] and 0 < a['score'] <= 3
    assert not a['resource_breach']
    changed['unfinished_tasks'] = [{'task_id': 'transmit'}]
    assert summarize_replay(changed)['cluster'] != a['cluster']
    clean = summarize_replay(fake_result(make_scenario('shade_link', None)))
    assert not clean['failed'] and clean['cluster'] is None


def test_born_reference_and_normalization():
    zero = born_probabilities(np.zeros(12))
    assert zero[0] == pytest.approx(1.0, abs=1e-12)
    angles = np.zeros(12)
    angles[:6] = np.pi / 2
    assert np.allclose(born_probabilities(angles), np.full(64, 1/64), atol=1e-12)
    angles = np.random.default_rng(77).normal(size=12)
    p = born_probabilities(angles)
    assert p.shape == (64,) and np.all(p >= 0)
    assert p.sum() == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize('angles', [np.zeros(11), np.zeros((2, 6)), np.full(12, np.nan)])
def test_invalid_angles(angles):
    with pytest.raises(ValueError):
        born_probabilities(angles)


def test_fit_uses_observed_elites_and_is_reproducible():
    history = [{'index': x, 'score': float(x)} for x in (0, 3, 12, 63)]
    a, b = fit_born(history, 17), fit_born(history, 17)
    assert np.allclose(a['probabilities'], b['probabilities'], atol=1e-12)
    assert a['evaluations'] <= 65  # 64 optimizer calls plus final statevector
    assert a['evaluations'] > 0
    assert a['probabilities'].sum() == pytest.approx(1, abs=1e-12)
    assert set(a['training_indices']).issubset({0, 3, 12, 63})
    assert 63 in a['training_indices']


@pytest.mark.parametrize('method', METHODS)
def test_budget_no_duplicate_replay_and_history_reproducibility(method):
    seen = []
    def oracle(s):
        seen.append(s.model_dump_json())
        return fake_result(s)
    a = run_search('shade_link', method, 42, Budget(calls=10), oracle=oracle)
    assert len(seen) == a['replay_calls'] == 10
    assert len(set(seen)) == 10
    assert a['qualified'] and len(a['observations']) == 9
    assert len(a['clusters']) == 1
    b = run_search('shade_link', method, 42, Budget(calls=10), oracle=fake_result)
    assert [x['index'] for x in a['observations']] == [x['index'] for x in b['observations']]
    assert all(x['calls_at_discovery'] >= 2 for x in a['observations'])
    assert a['provider_calls'] == 0
    assert a['timing_s']['total'] >= a['timing_s']['replay']


def test_shared_initial_candidates_without_free_results():
    rows = [run_search('shade_link', m, 11, Budget(calls=5), oracle=fake_result) for m in METHODS]
    assert len({tuple(x['index'] for x in r['observations']) for r in rows}) == 1
    assert all(r['replay_calls'] == 5 for r in rows)


def test_initially_impossible_is_excluded_and_not_counted_as_discovery():
    def impossible(s):
        r = fake_result(s)
        r['status'] = 'no_feasible_continuation'
        return r
    r = run_search('shade_link', 'random', 1, Budget(), oracle=impossible)
    assert not r['qualified'] and r['replay_calls'] == 1
    assert not r['observations'] and not r['clusters']


def test_time_budget_charges_but_excludes_late_results():
    elapsed = [0.0]
    def oracle(s):
        elapsed[0] += 0.4
        return fake_result(s)
    r = run_search('shade_link', 'random', 1, Budget(calls=65, seconds=1),
                   oracle=oracle, clock=lambda: elapsed[0])
    assert r['replay_calls'] == 3
    assert len(r['observations']) == 1
    assert r['late_results'] == 1 and r['overrun_s'] == pytest.approx(0.2, abs=1e-12)


def test_small_domain_can_be_exhausted_without_duplicates():
    r = run_search('shade_link', 'sobol', 1, Budget(calls=65), oracle=fake_result)
    assert {x['index'] for x in r['observations']} == set(range(64))


def test_paired_uncertainty_uses_all_roots_and_best_classical_envelope():
    rows = []
    for family in ('heater_shade', 'radio_energy'):
        for seed in (1, 2, 3):
            for method in (*CLASSICAL, 'qcbm'):
                n = 3 if method == 'qcbm' else 1
                rows.append(dict(family=family, seed=seed, method=method, clusters=list(range(n))))
    a = paired_summary(rows)
    assert a == paired_summary(rows)
    assert a['mean_gain'] == pytest.approx(2, abs=1e-12)
    assert a['interval_95'] == pytest.approx([2, 2], abs=1e-12)
    assert a['root_count'] == 2 and a['paired_count'] == 6
    assert a['screen_passed']
    rows[-1]['clusters'] = []
    assert paired_summary(rows)['mean_gain'] < a['mean_gain']


def test_missing_pairs_are_not_silently_dropped():
    with pytest.raises(ValueError):
        paired_summary([dict(family='heater_shade', seed=1, method='qcbm', clusters=[])])


def test_bad_search_method_is_rejected_before_oracle():
    with pytest.raises(ValueError):
        run_search('shade_link', 'not_a_method', 1, Budget(), oracle=lambda _: pytest.fail('oracle called'))
