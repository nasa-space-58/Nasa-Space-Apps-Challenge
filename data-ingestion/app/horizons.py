"""Validated JPL Horizons downloads and immutable local cache.

Source conventions: https://ssd.jpl.nasa.gov/horizons/manual.html.
No terrain obstruction or visibility calculations are performed here.
"""
import csv
import fcntl
import hashlib
import io
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from shared.contracts import AnalysisRequest

ENDPOINT = "https://ssd.jpl.nasa.gov/api/horizons.api"
CACHE_VERSION = 1
# Horizons reports a spherical lunar reference surface (km), not terrain height.
MOON_REFERENCE_RADIUS_KM = 1737.4
TARGETS = {'10': 'sun', '399': 'earth'}


class HorizonsError(ValueError):
    """Invalid source response or corrupt cached data."""


def build_params(request: AnalysisRequest, target: str) -> dict[str, str]:
    """Build a MOON_ME observer query; times must resolve to whole UTC seconds."""
    if target not in TARGETS:
        raise ValueError('Only Sun (10) and Earth (399) are supported')
    if request.start_utc.microsecond or request.end_utc.microsecond:
        raise ValueError('Horizons ingestion requires whole-second timestamps')
    site = request.site
    coord = ','.join(format(v, '.15g') for v in (
        site.longitude_deg_east, site.latitude_deg, site.altitude_km))
    return {
        'format': 'json', 'COMMAND': f"'{target}'", 'MAKE_EPHEM': 'YES',
        'OBJ_DATA': 'NO', 'EPHEM_TYPE': 'OBSERVER', 'CENTER': 'coord@301',
        'COORD_TYPE': 'GEODETIC', 'SITE_COORD': f"'{coord}'",
        'START_TIME': request.start_utc.strftime("'%Y-%m-%d %H:%M:%S'"),
        'STOP_TIME': request.end_utc.strftime("'%Y-%m-%d %H:%M:%S'"),
        'STEP_SIZE': f"'{request.step_s // 60} m'", 'QUANTITIES': "'4,13'",
        'APPARENT': 'AIRLESS', 'CSV_FORMAT': 'YES', 'EXTRA_PREC': 'YES',
        'ELEV_CUT': '-90', 'SKIP_DAYLT': 'NO', 'TIME_TYPE': 'UT',
        'TIME_DIGITS': 'SECONDS',
    }


def cache_key(request: AnalysisRequest) -> str:
    """Hash request identity, endpoint, all query options and parser version."""
    identity = {'request': request.model_dump(mode='json'), 'endpoint': ENDPOINT,
                'version': CACHE_VERSION,
                'params': {t: build_params(request, t) for t in TARGETS}}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _utc(value: datetime) -> str:
    return value.isoformat().replace('+00:00', 'Z')


def _header(text: str, name: str) -> str:
    match = re.search(r'^' + re.escape(name) + r'\s*:\s*(.+)$', text, re.MULTILINE)
    if not match:
        raise HorizonsError(f'Missing Horizons metadata: {name}')
    return match.group(1).strip()


def _number(value: str, lower: float, upper: float) -> float | None:
    if value.strip() == 'n.a.':
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise HorizonsError(f'Invalid numeric value: {value}') from exc
    if not math.isfinite(number) or not lower <= number <= upper:
        raise HorizonsError(f'Out-of-range numeric value: {value}')
    return number


def parse_response(payload: dict, request: AnalysisRequest, target: str) -> dict:
    """Validate source, Moon frame/sphere/site and exact UTC grid.

    Missing quantities remain null; missing rows, wrong metadata and malformed
    numeric values raise HorizonsError. Angular diameters are in arcseconds.
    """
    build_params(request, target)
    if not isinstance(payload, dict) or payload.get('error'):
        raise HorizonsError(f'Horizons error: {payload}')
    text = payload.get('result', '')
    signature = payload.get('signature', {})
    if not isinstance(text, str) or not isinstance(signature, dict):
        raise HorizonsError('Malformed Horizons response')
    if signature.get('source') != 'NASA/JPL Horizons API' or not signature.get('version'):
        raise HorizonsError('Missing or unexpected API signature')
    if text.count('$$SOE') != 1 or text.count('$$EOE') != 1:
        raise HorizonsError('Missing or ambiguous ephemeris table')
    header, body = text.split('$$SOE')
    table, _ = body.split('$$EOE')
    target_header = _header(header, 'Target body name')
    center = _header(header, 'Center body name')
    orientation = _header(header, 'Center pole/equ')
    if f'({target})' not in target_header or 'Moon (301)' not in center:
        raise HorizonsError('Unexpected target or observer center')
    if 'MEAN_ME (high precision)' not in orientation:
        raise HorizonsError('Unsupported lunar orientation frame')
    try:
        radii = [float(v) for v in _header(header, 'Center radii').split('km')[0].split(',')]
        coords = [float(v) for v in _header(header, 'Center geodetic').split('{')[0].split(',')]
    except ValueError as exc:
        raise HorizonsError('Invalid lunar coordinate metadata') from exc
    if len(radii) != 3 or any(v != MOON_REFERENCE_RADIUS_KM for v in radii):
        raise HorizonsError('Unexpected lunar reference sphere')
    expected = [request.site.longitude_deg_east, request.site.latitude_deg, request.site.altitude_km]
    # Horizons rounds site metadata to a few decimal places; do not claim a finer check.
    if len(coords) != 3 or any(not math.isclose(a, b, rel_tol=0, abs_tol=0.00001)
                               for a, b in zip(coords, expected)):
        raise HorizonsError('Observer site differs from request')
    column_lines = [line for line in header.splitlines() if line.strip().startswith('Date__')]
    if len(column_lines) != 1:
        raise HorizonsError('Missing CSV column header')
    columns = [c.strip() for c in next(csv.reader(column_lines))]
    names = ['Date__(UT)__HR:MN:SS', 'Azimuth_(a-app)', 'Elevation_(a-app)', 'Ang-diam']
    if any(columns.count(name) != 1 for name in names):
        raise HorizonsError('Missing or ambiguous CSV columns / UTC time scale')
    indexes = [columns.index(name) for name in names]
    rows = list(csv.reader(io.StringIO(table.strip())))
    count = int((request.end_utc - request.start_utc).total_seconds() // request.step_s) + 1
    if len(rows) != count:
        raise HorizonsError(f'Incomplete sample grid: expected {count}, got {len(rows)}')
    samples = []
    for i, row in enumerate(rows):
        if len(row) != len(columns):
            raise HorizonsError('Malformed CSV row')
        try:
            stamp = datetime.strptime(row[indexes[0]].strip(), '%Y-%b-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HorizonsError('Invalid UTC sample timestamp') from exc
        if stamp != request.start_utc + timedelta(seconds=i * request.step_s):
            raise HorizonsError('Duplicate, missing or unexpected UTC sample')
        azimuth = _number(row[indexes[1]], 0, 360)
        samples.append({
            'sampled_at_utc': _utc(stamp),
            'azimuth_deg': 0.0 if azimuth == 360 else azimuth,
            'elevation_deg': _number(row[indexes[2]], -90, 90),
            'angular_diameter_arcsec': _number(row[indexes[3]], 0, 1296000),
        })
    return {'samples': samples, 'metadata': {
        'target': target_header, 'center': center, 'orientation': orientation,
        'reference_radius_km': MOON_REFERENCE_RADIUS_KM, 'api_signature': signature,
        'header': header, 'time_scale': 'UTC',
    }}


def _fetch(client: httpx.Client, params: dict) -> httpx.Response:
    for attempt in range(3):
        try:
            response = client.get(ENDPOINT, params=params, timeout=60)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except httpx.TransportError:
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise AssertionError('Retry loop exhausted without result')


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def _verify_cache(path: Path, key: str) -> None:
    try:
        manifest = json.loads((path / 'manifest.json').read_text())
        hashes = manifest['sha256']
        if manifest['cache_key'] != key or set(hashes) != {'raw/10.json', 'raw/399.json', 'samples.json'}:
            raise HorizonsError('Invalid cache manifest')
        for name, digest in hashes.items():
            if hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
                raise HorizonsError(f'Cache checksum mismatch: {name}')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HorizonsError(f'Cache checksum/manifest validation failed: {path}: {exc}') from exc


def download(request: AnalysisRequest, data_dir: Path, client: httpx.Client) -> dict:
    """Download Sun/Earth sequentially into an atomic, checksummed cache bundle.

    Uses an advisory file lock per request to prevent duplicate concurrent writes.
    Retries transient network failures at most three times. Corrupt existing cache
    raises HorizonsError; it is never silently replaced. No backend network access.
    """
    key = cache_key(request)
    root = Path(data_dir) / 'horizons'
    root.mkdir(parents=True, exist_ok=True)
    destination = root / key
    with (root / f'{key}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists():
            _verify_cache(destination, key)
            return {'cache_key': key, 'path': str(destination), 'cache_hit': True}
        with tempfile.TemporaryDirectory(prefix='.pending-', dir=root) as temporary:
            stage = Path(temporary) / 'bundle'
            (stage / 'raw').mkdir(parents=True)
            sources, targets = {}, {}
            for target in TARGETS:
                params = build_params(request, target)
                response = _fetch(client, params)
                try:
                    decoded = response.json()
                except ValueError as exc:
                    raise HorizonsError('Horizons returned invalid JSON') from exc
                parsed = parse_response(decoded, request, target)
                (stage / 'raw' / f'{target}.json').write_bytes(response.content)
                targets[target] = parsed['samples']
                sources[target] = {'url': str(response.url), 'params': params,
                                   'acquired_at_utc': _utc(datetime.now(timezone.utc)),
                                   **parsed['metadata']}
            combined = []
            for sun, earth in zip(targets['10'], targets['399'], strict=True):
                row = {'site_id': request.site.site_id, 'sampled_at_utc': sun['sampled_at_utc']}
                for prefix, sample in [('sun', sun), ('earth', earth)]:
                    row.update({f'{prefix}_{k}': v for k, v in sample.items() if k != 'sampled_at_utc'})
                combined.append(row)
            _write_json(stage / 'samples.json', combined)
            hashes = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
                      for name in ['raw/10.json', 'raw/399.json', 'samples.json']}
            _write_json(stage / 'manifest.json', {
                'cache_key': key, 'cache_version': CACHE_VERSION,
                'request': request.model_dump(mode='json'), 'sources': sources,
                'acquired_at_utc': _utc(datetime.now(timezone.utc)), 'sha256': hashes,
                'sample_count': len(combined), 'terrain_applied': False,
            })
            os.rename(stage, destination)
        return {'cache_key': key, 'path': str(destination), 'cache_hit': False}
