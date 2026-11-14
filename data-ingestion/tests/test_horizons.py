"""Frozen tests using NASA/JPL responses captured on 2026-11-14.

Reference query and precision justification: fixtures/README.md.
"""
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app import horizons as h
from shared.contracts import AnalysisRequest

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def request_model():
    return AnalysisRequest.model_validate({
        'site': {'site_id': 'reference', 'longitude_deg_east': 0.0,
                 'latitude_deg': -89.5, 'altitude_km': 0.0},
        'start_utc': '2026-11-14T00:00:00Z', 'end_utc': '2026-11-14T02:00:00Z',
        'step_s': 3600,
    })


def payload(target='10'):
    return json.loads((FIXTURES / f'horizons_{target}.json').read_text())


def client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def good_response(req):
    return httpx.Response(200, json=payload(req.url.params['COMMAND'].strip("'")))


def test_query_contract(request_model):
    p = h.build_params(request_model, '10')
    assert p['CENTER'] == 'coord@301'
    assert p['SITE_COORD'] == "'0,-89.5,0'"
    assert p['QUANTITIES'] == "'4,13'"
    assert p['TIME_TYPE'] == 'UT'
    assert p['APPARENT'] == 'AIRLESS'
    assert p['ELEV_CUT'] == '-90'
    assert p['TIME_DIGITS'] == 'SECONDS'
    assert p['STEP_SIZE'] == "'60 m'"
    assert p['START_TIME'] == "'2026-11-14 00:00:00'"


@pytest.mark.parametrize('step', [60, 300, 3600])
def test_sampling(request_model, step):
    assert h.build_params(request_model.model_copy(update={'step_s': step}), '399')['STEP_SIZE'] == f"'{step // 60} m'"


def test_reject_unsupported_target_and_fractional_time(request_model):
    with pytest.raises(ValueError):
        h.build_params(request_model, '499')
    request_model.start_utc += timedelta(microseconds=1)
    with pytest.raises(ValueError, match='second'):
        h.build_params(request_model, '10')


@pytest.mark.parametrize('target,az,el', [('10',126.627333,1.258335), ('399',358.773423,-4.148545)])
def test_live_reference_values(request_model, target, az, el):
    result = h.parse_response(payload(target), request_model, target)
    assert len(result['samples']) == 3
    first = result['samples'][0]
    assert first['sampled_at_utc'] == '2026-11-14T00:00:00Z'
    assert first['azimuth_deg'] == pytest.approx(az, abs=0.000001)
    assert first['elevation_deg'] == pytest.approx(el, abs=0.000001)
    assert first['angular_diameter_arcsec'] > 0
    assert 'MEAN_ME' in result['metadata']['orientation']
    assert 'DE441' in result['metadata']['target']
    assert result['metadata']['api_signature']['source'] == 'NASA/JPL Horizons API'


def test_unavailable_values_preserved(request_model):
    p = payload()
    p['result'] = p['result'].replace('126.627332945', 'n.a.')
    assert h.parse_response(p, request_model, '10')['samples'][0]['azimuth_deg'] is None


@pytest.mark.parametrize('old,new', [
    ('$$EOE', '$$MISSING'), ('Sun (10)', 'Earth (399)'),
    ('Moon (301)', 'Earth (399)'), ('MEAN_ME', 'IAU_MOON'),
    ('1737.4, 1737.4, 1737.4', '1738.0, 1738.0, 1738.0'),
    ('Date__(UT)', 'Date__(TT)'), ('Azimuth_(a-app)', 'Wrong-column'),
    ('2026-Nov-14 01:00:00,', '2026-Nov-14 00:00:00,'),
    ('126.627332945', 'NaN'), ('126.627332945', '361.0'),
    ('1.258334640', '-91'), ('1942.008', '-1'),
    ('Center geodetic : 0.0, -89.5, 0.0', 'Center geodetic : 1.0, -89.5, 0.0'),
])
def test_invalid_response_rejected(request_model, old, new):
    p = payload()
    assert old in p['result']
    p['result'] = p['result'].replace(old, new)
    with pytest.raises(h.HorizonsError):
        h.parse_response(p, request_model, '10')


def test_semantic_error_and_missing_sample(request_model):
    with pytest.raises(h.HorizonsError):
        h.parse_response({'error': 'No ephemeris available'}, request_model, '10')
    p = payload()
    p['result'] = '\n'.join(line for line in p['result'].splitlines() if not line.startswith(' 2026-Nov-14 01:'))
    with pytest.raises(h.HorizonsError):
        h.parse_response(p, request_model, '10')


def test_reordered_columns(request_model):
    p = payload()
    lines = p['result'].splitlines()
    for i, line in enumerate(lines):
        if 'Date__(UT)' in line or line.startswith(' 2026-Nov-14 '):
            cells = line.split(',')
            cells[3], cells[5] = cells[5], cells[3]
            lines[i] = ','.join(cells)
    p['result'] = '\n'.join(lines)
    assert h.parse_response(p, request_model, '10')['samples'][0]['azimuth_deg'] == pytest.approx(126.627333, abs=0.000001)


@pytest.mark.parametrize('field,value', [('step_s',300), ('start_utc','2026-11-14T01:00:00Z'), ('end_utc','2026-11-14T03:00:00Z')])
def test_cache_identity_changes(request_model, field, value):
    d = request_model.model_dump(mode='json')
    d[field] = value
    assert h.cache_key(AnalysisRequest.model_validate(d)) != h.cache_key(request_model)


@pytest.mark.parametrize('field,value', [('site_id','other'),('longitude_deg_east',1.0),('latitude_deg',-90.0),('altitude_km',0.002)])
def test_site_cache_identity(request_model, field, value):
    d = request_model.model_dump(mode='json')
    d['site'][field] = value
    assert h.cache_key(AnalysisRequest.model_validate(d)) != h.cache_key(request_model)


def test_atomic_cache_and_reuse(request_model, tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        return good_response(req)
    with client_with(handler) as client:
        first = h.download(request_model, tmp_path, client)
        second = h.download(request_model, tmp_path, client)
    assert len(calls) == 2
    assert first['cache_hit'] is False
    assert second['cache_hit'] is True
    path = Path(first['path'])
    assert path == Path(second['path'])
    manifest = json.loads((path / 'manifest.json').read_text())
    assert len(manifest['sha256']) == 3
    assert manifest['request']['site']['site_id'] == 'reference'
    assert manifest['acquired_at_utc'].endswith('Z')
    rows = json.loads((path / 'samples.json').read_text())
    assert len(rows) == 3
    assert rows[0]['earth_elevation_deg'] < 0
    assert rows[0]['sun_angular_diameter_arcsec'] > 0
    assert (path / 'raw' / '10.json').is_file()
    assert (path / 'raw' / '399.json').is_file()
    assert all(item['url'].startswith(h.ENDPOINT) for item in manifest['sources'].values())


def test_corrupt_cache_fails_loudly(request_model, tmp_path):
    with client_with(good_response) as client:
        result = h.download(request_model, tmp_path, client)
        (Path(result['path']) / 'samples.json').write_text('[]')
        with pytest.raises(h.HorizonsError, match='checksum'):
            h.download(request_model, tmp_path, client)


@pytest.mark.parametrize('status', [400, 429, 503])
def test_failed_download_not_published(request_model, tmp_path, monkeypatch, status):
    monkeypatch.setattr('time.sleep', lambda _: None)
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status)
    with client_with(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            h.download(request_model, tmp_path, client)
    assert len(calls) == (1 if status == 400 else 3)
    assert not (tmp_path / 'horizons' / h.cache_key(request_model)).exists()


def test_transient_failure_retried(request_model, tmp_path, monkeypatch):
    monkeypatch.setattr('time.sleep', lambda _: None)
    calls = []
    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            raise httpx.ConnectError('temporary', request=req)
        return good_response(req)
    with client_with(handler) as client:
        assert h.download(request_model, tmp_path, client)['cache_hit'] is False
    assert len(calls) == 3


def test_second_target_semantic_failure_does_not_publish(request_model, tmp_path):
    def handler(req):
        if req.url.params['COMMAND'] == "'399'":
            return httpx.Response(200, json={'error':'no data'})
        return good_response(req)
    with client_with(handler) as client:
        with pytest.raises(h.HorizonsError):
            h.download(request_model, tmp_path, client)
    assert not (tmp_path / 'horizons' / h.cache_key(request_model)).exists()
