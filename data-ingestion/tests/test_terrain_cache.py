"""Synthetic lunar rasters exercise cache/coverage, not scientific accuracy."""
import hashlib
import json
from pathlib import Path

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shared.contracts import ObserverSite
from app import terrain

CRS='+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R=1737400 +units=m +no_defs'


@pytest.fixture
def site():
    return ObserverSite(site_id='pole',longitude_deg_east=0.0,latitude_deg=-90.0,altitude_km=0.0)


def raster_bytes(nodata=False,crs=CRS):
    data=np.full((20,20),123.0,dtype='float32')
    if nodata:data[10,10]=np.nan
    with MemoryFile() as memory:
        with memory.open(driver='GTiff',width=20,height=20,count=1,dtype='float32',
                         crs=crs,transform=from_origin(-800,800,80,80),nodata=float('nan')) as ds:
            ds.write(data,1)
        return memory.read()


def http_client(content,calls):
    def handler(request):
        calls.append(request)
        return httpx.Response(200,content=content,headers={'content-length':str(len(content)),'etag':'fixture'})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_source_saved_once_with_checksum(tmp_path):
    raw=raster_bytes();calls=[]
    with http_client(raw,calls) as client:
        first=terrain.acquire_source(tmp_path,client)
        second=terrain.acquire_source(tmp_path,client)
    assert first==second and len(calls)==1
    assert first.read_bytes()==raw
    manifest=json.loads((first.parent/'manifest.json').read_text())
    assert manifest['source_url']==terrain.SOURCE_URL
    assert manifest['sha256'][first.name]==hashlib.sha256(raw).hexdigest()


def test_crop_and_persistent_reuse(site,tmp_path):
    calls=[]
    with http_client(raster_bytes(),calls) as client:
        first=terrain.prepare_terrain(site,80,tmp_path,client)
        second=terrain.prepare_terrain(site,80,tmp_path,client)
    assert len(calls)==1 and second['cache_hit'] is True
    path=Path(first['path'])
    with rasterio.open(path/'terrain.tif') as ds:
        assert ds.width==ds.height==3
        assert float(ds.read(1)[1,1])==pytest.approx(123.0,abs=1e-6)
        assert ds.res==(80,80)
    manifest=json.loads((path/'manifest.json').read_text())
    assert manifest['reference_frame']=='MOON_ME'
    assert manifest['source_sha256']
    assert manifest['valid_cells']==9


@pytest.mark.parametrize('kind',['nodata','wrong-crs','bounds','outside-latitude'])
def test_invalid_terrain_fails_without_ready_bundle(site,tmp_path,kind):
    raw=raster_bytes(nodata=kind=='nodata',crs='EPSG:4326' if kind=='wrong-crs' else CRS)
    if kind=='bounds':site.latitude_deg=-85.0
    if kind=='outside-latitude':site.latitude_deg=-70.0
    with http_client(raw,[]) as client:
        with pytest.raises(terrain.TerrainError):
            terrain.prepare_terrain(site,80,tmp_path,client)
    assert not list((tmp_path/'terrain'/'processed').glob('*/manifest.json'))


def test_corrupt_source_rejected(tmp_path):
    with http_client(raster_bytes(),[]) as client:
        path=terrain.acquire_source(tmp_path,client)
        path.write_bytes(b'corrupted')
        with pytest.raises(terrain.TerrainError,match='checksum'):
            terrain.acquire_source(tmp_path,client)


def test_corrupt_crop_rejected(site,tmp_path):
    with http_client(raster_bytes(),[]) as client:
        result=terrain.prepare_terrain(site,80,tmp_path,client)
        (Path(result['path'])/'terrain.tif').write_bytes(b'corrupted')
        with pytest.raises(terrain.TerrainError,match='checksum'):
            terrain.prepare_terrain(site,80,tmp_path,client)


def test_size_limit(tmp_path,monkeypatch):
    monkeypatch.setattr(terrain,'MAX_SOURCE_BYTES',10)
    with http_client(raster_bytes(),[]) as client:
        with pytest.raises(terrain.TerrainError):
            terrain.acquire_source(tmp_path,client)
    assert not list((tmp_path/'terrain'/'raw').glob('*/manifest.json'))


def test_distinct_radius_has_distinct_cache(site,tmp_path):
    with http_client(raster_bytes(),[]) as client:
        small=terrain.prepare_terrain(site,80,tmp_path,client)
        large=terrain.prepare_terrain(site,160,tmp_path,client)
    assert small['path']!=large['path']


def test_http_error_not_cached(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(503))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            terrain.acquire_source(tmp_path,client)
    assert not list((tmp_path/'terrain'/'raw').glob('*/manifest.json'))
