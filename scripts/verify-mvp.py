"""Opt-in live MVP acceptance checks against an isolated running API.

Creates real jobs (may download NASA data); never changes existing jobs or files.
Usage: python scripts/verify-mvp.py http://localhost:8002 /tmp/task6-live.json
Run only against your explicitly selected local verification environment.
"""
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

base, output = sys.argv[1:]
report = {"api": base, "cases": {}, "validation": {}}


def call(path, body=None, expected=200):
    request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    try:
        response = urlopen(request, timeout=30)
    except HTTPError as error:
        response = error
    assert response.status == expected, (path, response.status, response.read().decode()[:300])
    return json.load(response)


def run(name, body):
    started = time.monotonic()
    job = call('/analyses', body, 202)
    phases = []
    while time.monotonic() - started < 900:
        job = call('/analyses/' + job['id'])
        if not phases or phases[-1] != job['status']:
            phases.append(job['status'])
            print(name, job['status'], round(time.monotonic()-started, 2), flush=True)
        if job['status'] in ('ready', 'insufficient_data', 'failed'):
            break
        time.sleep(1)
    assert job['status'] == 'ready', job
    result = call('/analyses/' + job['id'] + '/result')
    for target in ('sun', 'earth'):
        metrics = result['analysis'][target]['metrics']
        accounted = sum(metrics[key] for key in ('visible_duration_s', 'blocked_duration_s', 'unknown_duration_s'))
        assert abs(accounted - 86400) < 1e-5
        assert 0 <= metrics['visible_percent_of_interval'] <= 100
        intervals = result['analysis'][target]['intervals']
        assert all(a['end_utc'] == b['start_utc'] for a, b in zip(intervals, intervals[1:]))
    assert len(result['horizon']['directions']) == 360
    assert result['horizon']['source_sha256']
    report['cases'][name] = dict(job_id=job['id'], elapsed_s=round(time.monotonic()-started, 3), phases=phases,
        analysis_id=result['analysis']['analysis_id'], frames=len(result['frames']), request=result['analysis']['request'],
        sun=result['analysis']['sun']['metrics'], earth=result['analysis']['earth']['metrics'])
    Path(output).write_text(json.dumps(report, indent=2)+'\n')
    return job['id'], result


body = dict(site_id='malapert-massif-r23', latitude_deg=-85.9948, longitude_deg_east=359.76422,
            observer_height_m=2, start_utc='2026-11-04T00:00:00Z', end_utc='2026-11-05T00:00:00Z', refine=True)
call('/observers/resolve', body, 409)
report['validation']['empty_terrain_resolver'] = 409
for name, patch in [('outside_coverage', {'latitude_deg': -70}), ('outside_date', {'start_utc':'2025-11-04T00:00:00Z'}), ('zero_duration', {'end_utc':body['start_utc']})]:
    call('/analyses', body | patch, 422)
    report['validation'][name] = 422
first_id, first = run('cold', body)
warm_id, warm = run('warm', body)
assert first == warm, 'Cached replay changed result bytes/content'
report['cached_replay_identical'] = True
later_id, _ = run('delay48', body | dict(start_utc='2026-11-06T00:00:00Z', end_utc='2026-11-07T00:00:00Z'))
site_id, _ = run('other_site', body | dict(site_id='mons-mouton-feature-center', latitude_deg=-84.6, longitude_deg_east=31))
report['comparisons'] = {name: call('/comparisons', dict(a_id=first_id, b_id=job_id))
                         for name, job_id in [('dates', later_id), ('sites', site_id)]}
assert report['comparisons']['dates']['start_offset_s'] == 172800
assert report['comparisons']['sites']['alignment'] == 'shared_utc'
Path(output).write_text(json.dumps(report, indent=2)+'\n')
print('Live acceptance checks passed', output, flush=True)
