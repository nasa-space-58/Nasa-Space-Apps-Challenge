"""JPL Horizons geocentric vector tables for the flight scene, cached.

Query conventions: https://ssd.jpl.nasa.gov/horizons/manual.html. Targets are
the Moon (301) and the Sun (10) from Earth's centre (500@399), ecliptic of
J2000.0, position only, km, UTC times (TIME_TYPE=UT, JDUT column).
"""

import asyncio
import hashlib
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
from shared.flight_ephemeris import BODIES, FlightEphemerisEvent, FlightEphemerisRequest

from app.cache import identity, locked, write_json
from app.horizons import ENDPOINT, _fetch

CACHE_VERSION = 1
PARSER_VERSION = 1
MAX_MESSAGE_BYTES = 65536
QUERY_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
ROW_TIME_FORMAT = "%Y-%b-%d %H:%M:%S.%f"


class FlightEphemerisSourceError(ValueError):
    """Invalid Horizons response or corrupt cached flight ephemeris."""


class FlightEphemerisRequestConflict(ValueError):
    """A request UUID was reused with different parameters."""


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_vector_params(request: FlightEphemerisRequest, target: str) -> dict[str, str]:
    """Horizons VECTORS query for one body."""
    if target not in BODIES:
        raise ValueError("Only the Moon (301) and the Sun (10) are supported")
    return {
        "format": "json",
        "COMMAND": f"'{target}'",
        "MAKE_EPHEM": "YES",
        "OBJ_DATA": "NO",
        "EPHEM_TYPE": "VECTORS",
        "CENTER": "'500@399'",
        "REF_PLANE": "ECLIPTIC",
        "REF_SYSTEM": "ICRF",
        "VEC_TABLE": "1",
        "VEC_LABELS": "NO",
        "OUT_UNITS": "KM-S",
        "CSV_FORMAT": "YES",
        "TIME_TYPE": "UT",
        "START_TIME": request.start_utc.strftime(f"'{QUERY_TIME_FORMAT}'"),
        "STOP_TIME": request.end_utc.strftime(f"'{QUERY_TIME_FORMAT}'"),
        "STEP_SIZE": f"'{request.step_s // 60} m'",
    }


def _identity(request: FlightEphemerisRequest) -> dict:
    return {
        "start_utc": _utc(request.start_utc),
        "end_utc": _utc(request.end_utc),
        "step_s": request.step_s,
        "endpoint": ENDPOINT,
        "cache_version": CACHE_VERSION,
        "parser_version": PARSER_VERSION,
        "params": {t: build_vector_params(request, t) for t in sorted(BODIES)},
    }


def flight_cache_key(request: FlightEphemerisRequest) -> str:
    """Stable sha256 over window, step, query and parser versions (not request_id)."""
    return identity(_identity(request))


def _require(text: str, pattern: str, what: str) -> None:
    if not re.search(pattern, text, re.MULTILINE):
        raise FlightEphemerisSourceError(f"Unexpected Horizons {what}")


def _grid(request: FlightEphemerisRequest) -> list[str]:
    count = int((request.end_utc - request.start_utc).total_seconds() // request.step_s)
    return [
        _utc(request.start_utc + timedelta(seconds=i * request.step_s))
        for i in range(count + 1)
    ]


def parse_vector_response(
    decoded: dict, request: FlightEphemerisRequest, target: str
) -> list[dict]:
    """Rows {utc, x_km, y_km, z_km} on the exact request grid; fail loudly otherwise."""
    if target not in BODIES:
        raise ValueError(f"Unsupported target {target}")
    if not isinstance(decoded, dict) or "error" in decoded:
        raise FlightEphemerisSourceError(f"Horizons error: {decoded.get('error') if isinstance(decoded, dict) else decoded}")
    text = decoded.get("result")
    if not isinstance(text, str):
        raise FlightEphemerisSourceError("Horizons response has no result text")
    _require(text, rf"^Target body name:.*\({re.escape(target)}\)", "target")
    _require(text, r"^Center body name: Earth \(399\)", "center")
    _require(text, r"^Reference frame : Ecliptic of J2000\.0\s*$", "reference frame")
    _require(text, r"^Output units\s*:\s*KM-S\s*$", "units")
    _require(text, r"^\s*JDUT\s*,", "time column")
    if "$$SOE" not in text or "$$EOE" not in text:
        raise FlightEphemerisSourceError("Horizons table markers are missing")
    body = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
    rows = []
    for line in body.strip().splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 5:
            raise FlightEphemerisSourceError(f"Malformed Horizons row: {line!r}")
        try:
            moment = datetime.strptime(
                cells[1].removeprefix("A.D.").strip(), ROW_TIME_FORMAT
            ).replace(tzinfo=timezone.utc)
            x, y, z = (float(v) for v in cells[2:5])
        except ValueError as exc:
            raise FlightEphemerisSourceError(f"Malformed Horizons row: {line!r}") from exc
        if not all(math.isfinite(v) for v in (x, y, z)):
            raise FlightEphemerisSourceError("Horizons returned a non-finite position")
        rows.append({"utc": _utc(moment), "x_km": x, "y_km": y, "z_km": z})
    if [r["utc"] for r in rows] != _grid(request):
        raise FlightEphemerisSourceError("Horizons samples do not match the request grid")
    return rows


def _manifest_files() -> set[str]:
    return {f"raw/{t}.json" for t in BODIES} | {"samples.json"}


def _verify_bundle(path: Path, key: str) -> None:
    try:
        manifest = json.loads((path / "manifest.json").read_text())
        hashes = manifest["sha256"]
        if manifest["cache_key"] != key or set(hashes) != _manifest_files():
            raise FlightEphemerisSourceError("Invalid flight ephemeris manifest")
        for name, expected in hashes.items():
            if hashlib.sha256((path / name).read_bytes()).hexdigest() != expected:
                raise FlightEphemerisSourceError(f"Flight ephemeris checksum mismatch: {name}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FlightEphemerisSourceError(
            f"Flight ephemeris cache validation failed: {path}: {exc}"
        ) from exc


def download_flight_ephemeris(
    request: FlightEphemerisRequest, data_dir: Path, client: httpx.Client
) -> dict:
    """Atomic checksummed flight_ephemeris/<key> bundle; verified reuse."""
    key = flight_cache_key(request)
    root = Path(data_dir) / "flight_ephemeris"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / key
    with locked(root / f"{key}.lock"):
        if destination.exists():
            _verify_bundle(destination, key)
            return {"cache_key": key, "path": str(destination), "cache_hit": True}
        with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
            stage = Path(temporary) / "bundle"
            (stage / "raw").mkdir(parents=True)
            samples, sources = {}, {}
            for target in sorted(BODIES):
                params = build_vector_params(request, target)
                response = _fetch(client, params)
                try:
                    decoded = response.json()
                except ValueError as exc:
                    raise FlightEphemerisSourceError("Horizons returned invalid JSON") from exc
                samples[BODIES[target]] = parse_vector_response(decoded, request, target)
                (stage / "raw" / f"{target}.json").write_bytes(response.content)
                sources[target] = {
                    "params": params,
                    "api_signature": decoded.get("signature"),
                }
            write_json(stage / "samples.json", samples)
            hashes = {
                name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
                for name in sorted(_manifest_files())
            }
            write_json(
                stage / "manifest.json",
                {
                    "cache_key": key,
                    "request": _identity(request),
                    "sources": sources,
                    "frame": "Earth-centred (500@399), ecliptic of J2000.0, km",
                    "time_scale": "UTC",
                    "acquired_at_utc": _utc(datetime.now(timezone.utc)),
                    "sha256": hashes,
                },
            )
            os.rename(stage, destination)
        return {"cache_key": key, "path": str(destination), "cache_hit": False}


def _failure(request: FlightEphemerisRequest, code: str, detail) -> FlightEphemerisEvent:
    return FlightEphemerisEvent(
        request_id=request.request_id,
        status="failed",
        error_code=code,
        error_message=str(detail)[:1000] or code,
    )


def process_flight_ephemeris_job(
    request: FlightEphemerisRequest, data_dir: Path, client: httpx.Client | None = None
) -> FlightEphemerisEvent:
    """Idempotent job keyed by request UUID; outcomes are persisted."""
    data_dir = Path(data_dir)
    root = data_dir / "flight-ephemeris-jobs" / str(request.request_id)
    with locked(root / "job.lock"):
        params = request.model_dump(mode="json")
        request_file = root / "request.json"
        if request_file.exists():
            if json.loads(request_file.read_text()) != params:
                raise FlightEphemerisRequestConflict(
                    "request_id already belongs to different parameters"
                )
        else:
            write_json(request_file, params)
        result_file = root / "result.json"
        if result_file.exists():
            return FlightEphemerisEvent.model_validate_json(result_file.read_text())
        owned = client is None
        client = client or httpx.Client()
        try:
            result = download_flight_ephemeris(request, data_dir, client)
            event = FlightEphemerisEvent(
                request_id=request.request_id,
                status="ready",
                ephemeris_path=f"flight_ephemeris/{result['cache_key']}",
            )
        except FlightEphemerisSourceError as exc:
            event = _failure(request, "source_invalid", exc)
        except httpx.HTTPError as exc:
            event = _failure(request, "source_unavailable", exc)
        finally:
            if owned:
                client.close()
        write_json(result_file, event.model_dump(mode="json"))
        return event


async def handle_flight_ephemeris_message(message, exchange, data_dir: Path) -> None:
    """Dead-letter invalid messages; publish the event before acknowledging."""
    try:
        if len(message.body) > MAX_MESSAGE_BYTES:
            raise ValueError("Flight ephemeris message exceeds 64 KiB")
        request = FlightEphemerisRequest.model_validate_json(message.body)
    except (ValidationError, ValueError):
        logging.warning("Rejecting invalid flight ephemeris message to dead-letter queue")
        await message.reject(requeue=False)
        return
    try:
        event = await asyncio.to_thread(process_flight_ephemeris_job, request, data_dir)
    except FlightEphemerisRequestConflict as exc:
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
    logging.info("Flight ephemeris %s: %s", request.request_id, event.status)
