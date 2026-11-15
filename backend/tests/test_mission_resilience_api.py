"""Durable replay ownership, expiry and cancellation; no hardware calls."""
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.mission_resilience import ReplayCancelled
from app.mission_resilience_api import (
    MissionReplayStore, ReplayCommand, ReplayConflict, register_resilience_routes,
)
from test_mission_resilience import config, digest, scenario


def command(name='one', **kw):
    return ReplayCommand(request_id=name, scenario=scenario(config(initial_phase='surface')), **kw)


def result(s, **_):
    return {'input_sha256': digest(s), 'status': 'completed'}


def test_persistence_and_identical_command_idempotency(tmp_path):
    store = MissionReplayStore(tmp_path / 'r.sqlite')
    c = command()
    a = store.submit(c)
    assert store.submit(c)['id'] == a['id']
    assert MissionReplayStore(store.path).get(a['id'])['status'] == 'queued'
    assert store.tick(runner=result)
    assert store.get(a['id'])['status'] == 'completed'
    assert len(store.recent()) == 1
    assert store.submit(c)['id'] == a['id']
    with pytest.raises(ReplayConflict):
        store.submit(command(ttl_s=1.0))


def test_queue_is_bounded(tmp_path):
    store = MissionReplayStore(tmp_path / 'r.sqlite')
    for i in range(8):
        store.submit(command(str(i)))
    with pytest.raises(ReplayConflict):
        store.submit(command('overflow'))


def test_cancel_queued_never_runs(tmp_path):
    store = MissionReplayStore(tmp_path / 'r.sqlite')
    job = store.submit(command())
    assert store.cancel(job['id'])['status'] == 'cancelled'
    assert not store.tick(runner=lambda *_a, **_k: pytest.fail('cancelled work ran'))
    assert store.cancel(job['id'])['status'] == 'cancelled'


def test_cancel_running_fences_late_result(tmp_path):
    store = MissionReplayStore(tmp_path / 'r.sqlite')
    job = store.submit(command())
    entered, resume = threading.Event(), threading.Event()

    def delayed(s, *, cancelled):
        entered.set()
        assert resume.wait(5)
        assert cancelled()
        return result(s)

    worker = threading.Thread(target=lambda: store.tick(runner=delayed))
    worker.start()
    assert entered.wait(5)
    store.cancel(job['id'])
    resume.set()
    worker.join(5)
    assert not worker.is_alive()
    assert store.get(job['id'])['status'] == 'cancelled'
    assert store.get(job['id'])['result'] is None


@pytest.mark.parametrize('during', [False, True])
def test_timeout_before_or_during_work_cannot_publish(tmp_path, during):
    now = [0.0]
    store = MissionReplayStore(tmp_path / 'r.sqlite', clock=lambda: now[0])
    job = store.submit(command(ttl_s=1.0))

    def late(s, **_):
        now[0] = 2.0
        return result(s)

    if not during:
        now[0] = 2.0
    store.tick(runner=late)
    assert store.get(job['id'])['status'] == 'expired'
    assert store.get(job['id'])['result'] is None


def test_input_fingerprint_and_exception_fail_closed(tmp_path):
    store = MissionReplayStore(tmp_path / 'r.sqlite')
    a = store.submit(command('wrong'))
    store.tick(runner=lambda *_a, **_k: {'input_sha256': 'wrong'})
    assert store.get(a['id'])['status'] == 'failed'
    b = store.submit(command('error'))

    def fail(*_a, **_k):
        raise RuntimeError('internal error')

    store.tick(runner=fail)
    assert store.get(b['id'])['status'] == 'failed'
    c = store.submit(command('ok'))
    store.tick(runner=result)
    assert store.get(c['id'])['status'] == 'completed'


def test_interrupted_running_job_cannot_publish(tmp_path):
    store = MissionReplayStore(tmp_path / 'r.sqlite')
    job = store.submit(command())

    def stop(s, **_):
        store.recover_interrupted()
        return result(s)

    store.tick(runner=stop)
    assert store.get(job['id'])['status'] == 'interrupted'
    assert store.get(job['id'])['result'] is None


def test_exclusive_worker_claim(tmp_path):
    store = MissionReplayStore(tmp_path / 'r.sqlite')
    store.submit(command())
    entered, resume = threading.Event(), threading.Event()

    def wait(s, **_):
        entered.set()
        assert resume.wait(5)
        return result(s)

    worker = threading.Thread(target=lambda: store.tick(runner=wait))
    worker.start()
    assert entered.wait(5)
    assert not store.tick(runner=result)
    resume.set()
    worker.join(5)
    assert not worker.is_alive()


def test_cooperative_stop_status_is_not_success(tmp_path):
    store = MissionReplayStore(tmp_path / 'r.sqlite')
    job = store.submit(command())

    def stop(*_a, **_k):
        raise ReplayCancelled('stopped')

    store.tick(runner=stop)
    assert store.get(job['id'])['status'] == 'cancelled'


def test_http_create_read_cancel_validate_and_missing(tmp_path):
    store = MissionReplayStore(tmp_path / 'r.sqlite')
    app = FastAPI()
    register_resilience_routes(app, store)
    with TestClient(app) as client:
        assert len(client.get('/resilience/examples').json()) >= 5
        response = client.post('/resilience/runs', json=command().model_dump(mode='json'))
        assert response.status_code == 202
        job_id = response.json()['id']
        assert client.get('/resilience/runs/' + job_id).status_code == 200
        assert client.post('/resilience/runs/' + job_id + '/cancel').json()['status'] == 'cancelled'
        assert client.get('/resilience/runs/missing').status_code == 404
        assert client.post('/resilience/runs', json={'request_id': 'bad'}).status_code == 422
        assert client.post('/resilience/runs', json=command(ttl_s=1.0).model_dump(mode='json')).status_code == 409
