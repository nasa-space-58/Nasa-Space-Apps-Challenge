"""Comparison arithmetic and alignment; physics is covered by frozen visibility tests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from app.analysis_api import create_app
from app.comparison import compare_results


def view(delay=0, latitude=-86, visible=1800, unknown=0):
    start = datetime(2026, 11, 4, tzinfo=timezone.utc) + timedelta(hours=delay)
    stamp = lambda seconds: (start + timedelta(seconds=seconds)).isoformat()
    metrics = dict(visible_duration_s=visible, visible_percent_of_interval=visible/36,
                   longest_blocked_s=3600-visible-unknown, longest_visible_s=visible,
                   unknown_duration_s=unknown)
    intervals = [dict(start_utc=stamp(0), end_utc=stamp(visible), state='full', duration_s=visible),
                 dict(start_utc=stamp(visible), end_utc=stamp(3600), state='dark', duration_s=3600-visible)]
    return dict(analysis=dict(analysis_id=f'{delay}-{latitude}-{visible}', model_version='v1',
                request=dict(site=dict(latitude_deg=latitude, longitude_deg_east=0, altitude_km=1, reference_frame='MOON_ME'),
                             start_utc=stamp(0), end_utc=stamp(3600), step_s=1800),
                refinement=dict(status='completed'), spatial_sensitivity=dict(offset_deg=.75),
                sun=dict(metrics=metrics, intervals=intervals), earth=dict(metrics=deepcopy(metrics), intervals=deepcopy(intervals))),
                horizon=dict(model_version='h1', settings=dict(search_distance_m=100000), observer_height_above_terrain_m=2),
                frames=[dict(utc=stamp(0)),dict(utc=stamp(1800))])


def test_date_delta_and_exact_48_hour_offset():
    result = compare_results(view(), view(delay=48, visible=900))
    assert result['mode'] == 'dates'
    assert result['start_offset_s'] == pytest.approx(172800)
    assert result['deltas']['sun']['visible_duration_s'] == pytest.approx(-900)
    assert result['deltas']['earth']['visible_percent_of_interval'] == pytest.approx(-25)
    assert result['deltas']['sun']['longest_blocked_s'] == pytest.approx(900)
    assert result['alignment'] == 'elapsed_since_start'
    assert result['changes']['sun'][0]['start_elapsed_s'] == pytest.approx(900)
    assert result['changes']['sun'][0]['end_elapsed_s'] == pytest.approx(1800)
    assert result['changes']['sun'][0]['a_state'] == 'full'
    assert result['changes']['sun'][0]['b_state'] == 'dark'
    assert result['explanations']


def test_site_comparison_uses_shared_utc():
    assert compare_results(view(), view(latitude=-89))['mode'] == 'sites'
    assert compare_results(view(), view(latitude=-89))['alignment'] == 'shared_utc'


def test_identity_determinism_and_no_mutation():
    a=view(); original=deepcopy(a)
    result=compare_results(a,a)
    assert result==compare_results(a,a)
    assert a==original
    assert result['mode']=='identical'
    assert result['changes']=={'sun':[], 'earth':[]}
    assert all(value==pytest.approx(0) for target in result['deltas'].values() for value in target.values())


def test_unknown_is_not_ranked_as_better():
    result=compare_results(view(),view(delay=48,visible=0,unknown=3600))
    assert result['status']=='insufficient_data'
    assert result['deltas']['sun']['unknown_duration_s']==pytest.approx(3600)
    assert any('unknown' in text.lower() for text in result['explanations'])
    assert 'winner' not in result


def test_irregular_samples_keep_actual_timestamps_and_indices():
    b=view(delay=48)
    b['frames'].insert(1,dict(utc='2026-11-06T00:15:00+00:00'))
    result=compare_results(view(),b)
    assert [x['elapsed_s'] for x in result['cursor']] == [0,900,1800]
    assert result['cursor'][1]['a_index']==0
    assert result['cursor'][1]['b_index']==1
    assert result['cursor'][1]['a_sample_age_s']==pytest.approx(900)
    assert result['cursor'][1]['b_sample_age_s']==pytest.approx(0)


@pytest.mark.parametrize('change', ['duration','mixed','step','horizon','height','refinement','model','empty','unsorted'])
def test_reject_incompatible_or_unusable_results(change):
    a=view(); b=view(delay=48)
    if change=='duration': b['analysis']['request']['end_utc']='2026-11-06T02:00:00Z'
    if change=='mixed': b['analysis']['request']['site']['latitude_deg']=-89
    if change=='step': b['analysis']['request']['step_s']=3600
    if change=='horizon': b['horizon']['settings']['search_distance_m']=200000
    if change=='height': b['horizon']['observer_height_above_terrain_m']=5
    if change=='refinement': b['analysis']['refinement']['status']='not_requested'
    if change=='model': b['analysis']['model_version']='v2'
    if change=='empty': b['frames']=[]
    if change=='unsorted': b['frames'].reverse()
    with pytest.raises(ValueError): compare_results(a,b)


def test_missing_initial_sample_does_not_imply_known_state():
    b=view(delay=48); b['frames']=b['frames'][1:]
    result=compare_results(view(),b)
    assert result['cursor'][0]['b_index'] is None
    assert result['cursor'][0]['b_sample_age_s'] is None


def test_api_status_validation_and_persisted_results(tmp_path):
    with TestClient(create_app(tmp_path/'state',tmp_path/'data',start_worker=False)) as client:
        body=dict(site_id='test',latitude_deg=-86,longitude_deg_east=0,start_utc='2026-11-04T00:00:00Z',end_utc='2026-11-04T01:00:00Z')
        a=client.post('/analyses',json=body).json()['id']
        b=client.post('/analyses',json=body).json()['id']
        pair=dict(a_id=a,b_id=b)
        assert client.post('/comparisons',json=pair).status_code==409
        assert client.post('/comparisons',json=dict(a_id='missing',b_id=b)).status_code==404
        assert client.post('/comparisons',json=dict(a_id=a)).status_code==422
        client.app.state.store.update(a,'ready',result=view())
        client.app.state.store.update(b,'ready',result=view(delay=48,visible=900))
        response=client.post('/comparisons',json=pair)
        assert response.status_code==200
        assert response.json()['job_ids']==pair
        assert response.json()['mode']=='dates'
        client.app.state.store.update(b,'ready',result=view(delay=48,latitude=-89))
        assert client.post('/comparisons',json=pair).status_code==422
        client.app.state.store.update(b,'failed',error='Source unavailable')
        assert client.post('/comparisons',json=pair).status_code==409
