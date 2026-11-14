"""Offline queue/job tests: stable identity, retry bounds and publish-before-ack."""
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from shared.ingestion import IngestionRequest, IngestionEvent, declare_topology, REQUEST_QUEUE, DEAD_QUEUE
from app import consumer, jobs
from app.terrain import TerrainError


@pytest.fixture
def request():
    return IngestionRequest.model_validate({'request_id':str(uuid4()), 'analysis':{
        'site':{'site_id':'reference','latitude_deg':-85.9948,'longitude_deg_east':359.76422,'altitude_km':4.78662451171875},
        'start_utc':'2026-11-14T00:00:00Z','end_utc':'2026-11-14T02:00:00Z','step_s':3600},
        'terrain_radius_m':80})


def event(request):
    return IngestionEvent(request_id=request.request_id,status='ready',
        horizons_path='horizons/'+'a'*64,terrain_path='terrain/processed/'+'b'*64)


@pytest.mark.parametrize('change', [{'request_id':'../../bad'},{'schema_version':2},
    {'terrain_radius_m':0},{'terrain_radius_m':20001},{'terrain_radius_m':True},{'unexpected':1}])
def test_bad_envelope(request, change):
    with pytest.raises(ValidationError):
        IngestionRequest.model_validate(request.model_dump(mode='json')|change)


@pytest.mark.parametrize('change', [{'horizons_path':'/etc/passwd'}, {'terrain_path':'../escape'},
    {'horizons_path':None}, {'status':'failed'}, {'error_code':'wrong-for-ready'}])
def test_event_consistency(request, change):
    with pytest.raises(ValidationError):
        IngestionEvent.model_validate(event(request).model_dump(mode='json')|change)


def test_topology():
    channel=AsyncMock()
    asyncio.run(declare_topology(channel))
    declarations={c.args[0]:c.kwargs for c in channel.declare_queue.await_args_list}
    assert declarations[REQUEST_QUEUE]['durable']
    assert declarations[REQUEST_QUEUE]['arguments']['x-dead-letter-routing-key']==DEAD_QUEUE
    assert declarations[DEAD_QUEUE]['durable']


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    def bundle(name):
        path=tmp_path/name
        path.mkdir(parents=True,exist_ok=True)
        (path/'data.bin').write_bytes(b'verified')
        (path/'manifest.json').write_text(json.dumps({'sha256':{'data.bin':hashlib.sha256(b'verified').hexdigest()}}))
        return {'path':str(path),'cache_hit':False}
    h=Mock(side_effect=lambda *a:bundle('horizons/'+'a'*64))
    t=Mock(side_effect=lambda *a:bundle('terrain/processed/'+'b'*64))
    monkeypatch.setattr(jobs,'download',h)
    monkeypatch.setattr(jobs,'prepare_terrain',t)
    monkeypatch.setattr('time.sleep',lambda _:None)
    return h,t


def test_success_and_duplicate(request,tmp_path,downloads):
    first=jobs.process_job(request,tmp_path)
    second=jobs.process_job(request,tmp_path)
    assert first==second==event(request)
    assert downloads[0].call_count==downloads[1].call_count==1
    assert json.loads((tmp_path/'jobs'/str(request.request_id)/'result.json').read_text())['status']=='ready'


def test_reused_id_rejects_changed_request(request,tmp_path,downloads):
    jobs.process_job(request,tmp_path)
    request.terrain_radius_m=160
    with pytest.raises(jobs.RequestConflict):
        jobs.process_job(request,tmp_path)


def test_corrupt_ready_data_not_replayed(request,tmp_path,downloads):
    ready=jobs.process_job(request,tmp_path)
    (tmp_path/ready.terrain_path/'data.bin').write_bytes(b'corrupt')
    result=jobs.process_job(request,tmp_path)
    assert result.status=='failed'
    assert result.error_code=='cache_corrupt'


def test_transient_retry_then_success(request,tmp_path,downloads):
    original=downloads[1].side_effect
    count=0
    def flaky(*args):
        nonlocal count
        count+=1
        if count==1: raise httpx.ConnectError('offline')
        return original(*args)
    downloads[1].side_effect=flaky
    assert jobs.process_job(request,tmp_path).status=='ready'
    assert count==2


def test_retry_budget_survives_replay(request,tmp_path,downloads):
    downloads[1].side_effect=httpx.ConnectError('offline')
    first=jobs.process_job(request,tmp_path)
    second=jobs.process_job(request,tmp_path)
    assert first==second
    assert first.status=='failed'
    assert downloads[1].call_count==3


def test_permanent_coverage_failure_not_retried(request,tmp_path,downloads):
    downloads[1].side_effect=TerrainError('NoData')
    assert jobs.process_job(request,tmp_path).status=='failed'
    assert downloads[1].call_count==1


def test_concurrent_duplicates(request,tmp_path,downloads):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:jobs.process_job(request,tmp_path),range(2)))
    assert results[0]==results[1]
    assert downloads[1].call_count==1


def test_ack_after_event_confirmation(request,tmp_path,monkeypatch):
    order=[]
    message=Mock(body=request.model_dump_json().encode())
    message.ack=AsyncMock(side_effect=lambda:order.append('ack'))
    message.reject=AsyncMock()
    exchange=Mock(publish=AsyncMock(side_effect=lambda *a,**k:order.append('publish')))
    monkeypatch.setattr(consumer,'process_job',lambda *a:event(request))
    asyncio.run(consumer.handle_message(message,exchange,tmp_path))
    assert order==['publish','ack']
    sent=exchange.publish.await_args.args[0]
    assert int(sent.delivery_mode)==2
    assert sent.correlation_id==str(request.request_id)
    assert exchange.publish.await_args.kwargs['mandatory'] is True


def test_broken_publication_keeps_request_unacked(request,tmp_path,monkeypatch):
    message=Mock(body=request.model_dump_json().encode(),ack=AsyncMock(),nack=AsyncMock(),reject=AsyncMock())
    exchange=Mock(publish=AsyncMock(side_effect=ConnectionError('broker down')))
    monkeypatch.setattr(consumer,'process_job',lambda *a:event(request))
    with pytest.raises(ConnectionError):
        asyncio.run(consumer.handle_message(message,exchange,tmp_path))
    message.ack.assert_not_awaited()


def test_invalid_message_dead_lettered(tmp_path):
    message=Mock(body=b'not JSON',ack=AsyncMock(),reject=AsyncMock())
    exchange=Mock(publish=AsyncMock())
    asyncio.run(consumer.handle_message(message,exchange,tmp_path))
    message.reject.assert_awaited_once_with(requeue=False)
    exchange.publish.assert_not_awaited()
