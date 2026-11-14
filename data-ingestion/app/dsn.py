"""Moon ephemerides seen from DSN stations: JPL Horizons download and cache.

Query conventions: https://ssd.jpl.nasa.gov/horizons/manual.html. Observer is a
WGS84 geodetic site on Earth (coord@399); target is the Moon (301). Airless
apparent azimuth/elevation only; no horizon masks or link budget are applied.
"""

import asyncio
import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aio_pika
import httpx
from pydantic import ValidationError
from shared.dsn import DsnEvent, DsnRequest, DsnStation, STATIONS_FILE, load_stations

from app.cache import locked, write_json
from app.horizons import ENDPOINT, _fetch

CACHE_VERSION = 1
PARSER_VERSION = 1
# WGS84 equatorial and polar radii as printed by Horizons for Earth sites (km).
EARTH_RADII_KM = (6378.137, 6378.137, 6356.752)
SITE_ANGLE_TOL_DEG = 1e-6  # Horizons echoes the site with 7 decimals.
SITE_HEIGHT_TOL_KM = 1e-5
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_MESSAGE_BYTES = 65536


class DsnSourceError(ValueError):
    """Invalid Horizons response or corrupt cached DSN data."""


class DsnRequestConflict(ValueError):
    """A DSN request UUID was reused with different parameters."""


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _site_coord(station: DsnStation) -> tuple[float, float, float]:
    return station.longitude_deg_east, station.latitude_deg, station.height_m / 1000


def build_station_params(request: DsnRequest, station: DsnStation) -> dict[str, str]:
    """Build the Horizons OBSERVER query for the Moon from one station."""
    if station.station_id not in request.station_ids:
        raise ValueError(f"{station.station_id} is not part of this request")
    coord = ",".join(format(v, ".15g") for v in _site_coord(station))
    return {
        "format": "json",
        "COMMAND": "'301'",
        "MAKE_EPHEM": "YES",
        "OBJ_DATA": "NO",
        "EPHEM_TYPE": "OBSERVER",
        "CENTER": "coord@399",
        "COORD_TYPE": "GEODETIC",
        "SITE_COORD": f"'{coord}'",
        "START_TIME": request.start_utc.strftime(f"'{TIME_FORMAT}'"),
        "STOP_TIME": request.end_utc.strftime(f"'{TIME_FORMAT}'"),
        "STEP_SIZE": f"'{request.step_s // 60} m'",
        "QUANTITIES": "'4'",
        "APPARENT": "AIRLESS",
        "CSV_FORMAT": "YES",
        "EXTRA_PREC": "YES",
        "ELEV_CUT": "-90",
        "SKIP_DAYLT": "NO",
        "TIME_TYPE": "UT",
        "TIME_DIGITS": "SECONDS",
    }


def _identity(request: DsnRequest) -> dict:
    return request.model_dump(mode="json", exclude={"request_id"})


def dsn_cache_key(request: DsnRequest) -> str:
    """Hash every result-affecting parameter; the request UUID is excluded."""
    stations = load_stations()
    identity = {
        "request": _identity(request),
        "endpoint": ENDPOINT,
        "cache_version": CACHE_VERSION,
        "parser_version": PARSER_VERSION,
        "params": {
            sid: build_station_params(request, stations[sid])
            for sid in request.station_ids
        },
    }
    encoded = json.dumps(identity, sort_keys=True, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _header(text: str, name: str) -> str:
    match = re.search(r"^" + re.escape(name) + r"\s*:\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise DsnSourceError(f"Missing Horizons metadata: {name}")
    return match.group(1).strip()


def _floats(text: str) -> list[float]:
    try:
        return [float(v) for v in text.split(",")]
    except ValueError as exc:
        raise DsnSourceError(f"Invalid numeric metadata: {text}") from exc


def _angle(value: str, lower: float, upper: float) -> float | None:
    if value.strip() == "n.a.":
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise DsnSourceError(f"Invalid numeric value: {value}") from exc
    if not math.isfinite(number) or not lower <= number <= upper:
        raise DsnSourceError(f"Out-of-range numeric value: {value}")
    return number


def parse_station_response(
    payload: dict, request: DsnRequest, station: DsnStation
) -> list[dict]:
    """Validate metadata and the exact UTC grid; return Moon az/el samples.

    Elevations reported as n.a. stay None. Wrong target/center/site/ellipsoid,
    refraction, columns, timestamps or values raise DsnSourceError.
    """
    build_station_params(request, station)
    if not isinstance(payload, dict) or payload.get("error"):
        raise DsnSourceError(f"Horizons error: {payload!r}"[:500])
    text = payload.get("result")
    signature = payload.get("signature")
    if not isinstance(text, str) or not isinstance(signature, dict):
        raise DsnSourceError("Malformed Horizons response")
    if signature.get("source") != "NASA/JPL Horizons API" or not signature.get(
        "version"
    ):
        raise DsnSourceError("Missing or unexpected API signature")
    if text.count("$$SOE") != 1 or text.count("$$EOE") != 1:
        raise DsnSourceError("Missing or ambiguous ephemeris table")
    header, body = text.split("$$SOE")
    table, _ = body.split("$$EOE")
    if "Moon (301)" not in _header(header, "Target body name"):
        raise DsnSourceError("Unexpected target; expected Moon (301)")
    if "Earth (399)" not in _header(header, "Center body name"):
        raise DsnSourceError("Unexpected observer center; expected Earth (399)")
    if "AIRLESS" not in _header(header, "Atmos refraction"):
        raise DsnSourceError("Expected airless (no refraction) apparent angles")
    radii = _floats(_header(header, "Center radii").split("km")[0])
    if len(radii) != 3 or any(
        not math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
        for a, b in zip(radii, EARTH_RADII_KM)
    ):
        raise DsnSourceError("Unexpected Earth reference ellipsoid")
    coords = _floats(_header(header, "Center geodetic").split("{")[0])
    expected = _site_coord(station)
    tolerances = (SITE_ANGLE_TOL_DEG, SITE_ANGLE_TOL_DEG, SITE_HEIGHT_TOL_KM)
    if len(coords) != 3 or any(
        not math.isclose(a, b, rel_tol=0, abs_tol=t)
        for a, b, t in zip(coords, expected, tolerances)
    ):
        raise DsnSourceError("Observer site differs from the DSN station")
    column_lines = [
        line for line in header.splitlines() if line.strip().startswith("Date__")
    ]
    if len(column_lines) != 1:
        raise DsnSourceError("Missing CSV column header")
    columns = [c.strip() for c in next(csv.reader(column_lines))]
    names = ["Date__(UT)__HR:MN:SS", "Azimuth_(a-app)", "Elevation_(a-app)"]
    if any(columns.count(name) != 1 for name in names):
        raise DsnSourceError("Missing or ambiguous CSV columns / UTC time scale")
    indexes = [columns.index(name) for name in names]
    rows = list(csv.reader(io.StringIO(table.strip())))
    span_s = (request.end_utc - request.start_utc).total_seconds()
    count = int(span_s // request.step_s) + 1
    if len(rows) != count:
        raise DsnSourceError(
            f"Incomplete sample grid: expected {count}, got {len(rows)}"
        )
    samples = []
    for index, row in enumerate(rows):
        if len(row) != len(columns):
            raise DsnSourceError("Malformed CSV row")
        try:
            stamp = datetime.strptime(row[indexes[0]].strip(), "%Y-%b-%d %H:%M:%S")
        except ValueError as exc:
            raise DsnSourceError("Invalid UTC sample timestamp") from exc
        stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp != request.start_utc + timedelta(seconds=index * request.step_s):
            raise DsnSourceError("Duplicate, missing or unexpected UTC sample")
        azimuth = _angle(row[indexes[1]], 0, 360)
        samples.append(
            {
                "sampled_at_utc": _utc(stamp),
                "azimuth_deg": 0.0 if azimuth == 360 else azimuth,
                "elevation_deg": _angle(row[indexes[2]], -90, 90),
            }
        )
    return samples


def _manifest_files(stations) -> set[str]:
    return {f"raw/{sid}.json" for sid in stations} | {"samples.json"}


def _verify_bundle(path: Path, key: str, station_ids) -> None:
    try:
        manifest = json.loads((path / "manifest.json").read_text())
        hashes = manifest["sha256"]
        if manifest["cache_key"] != key or set(hashes) != _manifest_files(station_ids):
            raise DsnSourceError("Invalid DSN cache manifest")
        for name, expected in hashes.items():
            if hashlib.sha256((path / name).read_bytes()).hexdigest() != expected:
                raise DsnSourceError(f"DSN cache checksum mismatch: {name}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DsnSourceError(f"DSN cache validation failed: {path}: {exc}") from exc


def download_dsn(request: DsnRequest, data_dir: Path, client: httpx.Client) -> dict:
    """Download all stations into an atomic checksummed dsn/<key> bundle.

    An advisory lock serializes duplicate work. Existing bundles are verified and
    reused without network access; corrupt bundles raise and are never replaced.
    Source errors raise DsnSourceError; HTTP failures propagate after retries.
    """
    key = dsn_cache_key(request)
    stations = load_stations()
    root = Path(data_dir) / "dsn"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / key
    with locked(root / f"{key}.lock"):
        if destination.exists():
            _verify_bundle(destination, key, request.station_ids)
            return {"cache_key": key, "path": str(destination), "cache_hit": True}
        with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
            stage = Path(temporary) / "bundle"
            (stage / "raw").mkdir(parents=True)
            sources, samples = {}, {}
            for sid in request.station_ids:
                station = stations[sid]
                params = build_station_params(request, station)
                response = _fetch(client, params)
                try:
                    decoded = response.json()
                except ValueError as exc:
                    raise DsnSourceError("Horizons returned invalid JSON") from exc
                samples[sid] = parse_station_response(decoded, request, station)
                (stage / "raw" / f"{sid}.json").write_bytes(response.content)
                sources[sid] = {
                    "url": str(response.url),
                    "params": params,
                    "acquired_at_utc": _utc(datetime.now(timezone.utc)),
                    "api_signature": decoded.get("signature"),
                }
            write_json(stage / "samples.json", samples)
            hashes = {
                name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
                for name in sorted(_manifest_files(request.station_ids))
            }
            document = json.loads(STATIONS_FILE.read_text())
            write_json(
                stage / "manifest.json",
                {
                    "cache_key": key,
                    "cache_version": CACHE_VERSION,
                    "parser_version": PARSER_VERSION,
                    "request": _identity(request),
                    "stations": {
                        sid: stations[sid].model_dump(mode="json")
                        for sid in request.station_ids
                    },
                    "station_source": document["source"],
                    "sources": sources,
                    "time_scale": "UTC",
                    "acquired_at_utc": _utc(datetime.now(timezone.utc)),
                    "sha256": hashes,
                },
            )
            os.rename(stage, destination)
        return {"cache_key": key, "path": str(destination), "cache_hit": False}


def _failure(request: DsnRequest, code: str, detail) -> DsnEvent:
    return DsnEvent(
        request_id=request.request_id,
        status="failed",
        error_code=code,
        error_message=str(detail)[:1000] or code,
    )


def process_dsn_job(
    request: DsnRequest, data_dir: Path, client: httpx.Client | None = None
) -> DsnEvent:
    """Idempotent job: replay stored results and reject reused UUIDs.

    The outcome (ready or failed) is persisted per request UUID; a retry after a
    failure therefore needs a new UUID. Unexpected errors propagate unhandled.
    """
    data_dir = Path(data_dir)
    root = data_dir / "dsn-jobs" / str(request.request_id)
    with locked(root / "job.lock"):
        params = request.model_dump(mode="json")
        request_file = root / "request.json"
        if request_file.exists():
            if json.loads(request_file.read_text()) != params:
                raise DsnRequestConflict(
                    "request_id already belongs to different parameters"
                )
        else:
            write_json(request_file, params)
        result_file = root / "result.json"
        if result_file.exists():
            return DsnEvent.model_validate_json(result_file.read_text())
        owned = client is None
        client = client or httpx.Client()
        try:
            result = download_dsn(request, data_dir, client)
            event = DsnEvent(
                request_id=request.request_id,
                status="ready",
                dsn_path=f"dsn/{result['cache_key']}",
            )
        except DsnSourceError as exc:
            event = _failure(request, "source_invalid", exc)
        except httpx.HTTPError as exc:
            event = _failure(request, "source_unavailable", exc)
        finally:
            if owned:
                client.close()
        write_json(result_file, event.model_dump(mode="json"))
        return event


async def handle_dsn_message(message, exchange, data_dir: Path) -> None:
    """Dead-letter invalid messages; publish the event before acknowledging."""
    try:
        if len(message.body) > MAX_MESSAGE_BYTES:
            raise ValueError("DSN request message exceeds 64 KiB")
        request = DsnRequest.model_validate_json(message.body)
    except (ValidationError, ValueError):
        logging.warning("Rejecting invalid DSN message to dead-letter queue")
        await message.reject(requeue=False)
        return
    try:
        event = await asyncio.to_thread(process_dsn_job, request, data_dir)
    except DsnRequestConflict as exc:
        event = _failure(request, "request_conflict", exc)
    await exchange.publish(
        aio_pika.Message(
            body=event.model_dump_json().encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            correlation_id=str(request.request_id),
            message_id=f"{request.request_id}:{event.status}",
        ),
        routing_key=str(request.request_id),
        mandatory=True,
    )
    await message.ack()
    logging.info("DSN request %s: %s", request.request_id, event.status)
