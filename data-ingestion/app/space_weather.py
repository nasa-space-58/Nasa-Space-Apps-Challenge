"""NOAA SWPC space weather snapshot: download, validate and cache.

Products and field meanings: https://services.swpc.noaa.gov/ and
https://www.swpc.noaa.gov/products-and-data. Every timestamp in the stored
snapshot is explicit UTC. Probabilities are percent; X-ray flux is W/m² in the
GOES 0.1-0.8 nm channel; proton flux is pfu (cm^-2 s^-1 sr^-1) at >=10 MeV.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aio_pika
import httpx
from pydantic import ValidationError
from shared.space_weather import (
    PRODUCTS,
    SWPC_BASE_URL,
    SpaceWeatherEvent,
    SpaceWeatherRequest,
)

from app.cache import locked, write_json

SNAPSHOT_VERSION = 1
MAX_MESSAGE_BYTES = 65536
MAX_PRODUCT_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT_S = 60.0
XRAY_CHANNEL = "0.1-0.8nm"
PROTON_CHANNEL = ">=10 MeV"
FORECAST_DAYS = 3
PROBABILITY_FIELDS = (
    ("c_class_percent", "c_class_{}_day"),
    ("m_class_percent", "m_class_{}_day"),
    ("x_class_percent", "x_class_{}_day"),
    ("proton_10mev_percent", "10mev_protons_{}_day"),
)
REGION_PERCENT_FIELDS = (
    ("c_flare_percent", "c_flare_probability"),
    ("m_flare_percent", "m_flare_probability"),
    ("x_flare_percent", "x_flare_probability"),
    ("proton_percent", "proton_probability"),
)


class SpaceWeatherSourceError(ValueError):
    """Invalid SWPC response or corrupt cached snapshot."""


class SpaceWeatherRequestConflict(ValueError):
    """A request UUID was reused with different parameters."""


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _instant_utc(value, label: str) -> datetime:
    """Parse a timestamp that must carry an explicit zero UTC offset."""
    if not isinstance(value, str):
        raise SpaceWeatherSourceError(f"Missing or non-text {label}: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpaceWeatherSourceError(f"Invalid {label}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise SpaceWeatherSourceError(f"{label} must be explicit UTC: {value!r}")
    return parsed.astimezone(timezone.utc)


def _day_utc(value, label: str) -> datetime:
    """Parse an SWPC UTC day; SWPC prints these without an offset."""
    if not isinstance(value, str):
        raise SpaceWeatherSourceError(f"Missing or non-text {label}: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpaceWeatherSourceError(f"Invalid {label}: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    elif parsed.utcoffset() != timedelta(0):
        raise SpaceWeatherSourceError(f"{label} must be a UTC day: {value!r}")
    if (parsed.hour, parsed.minute, parsed.second, parsed.microsecond) != (0, 0, 0, 0):
        raise SpaceWeatherSourceError(f"{label} must start at 00:00 UTC: {value!r}")
    return parsed


def _percent(value, label: str, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise SpaceWeatherSourceError(f"{label} must be an integer percent: {value!r}")
    return value


def _optional_text(value, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SpaceWeatherSourceError(f"{label} must be text: {value!r}")
    return value.strip() or None


def product_url(name: str) -> str:
    """Absolute SWPC URL of one named product; KeyError for unknown names."""
    return SWPC_BASE_URL + PRODUCTS[name]


def parse_probabilities(payload) -> dict:
    """Return the newest 3-day flare/proton forecast in explicit UTC days.

    The entry ``date`` is the first forecast UTC day; ``*_1_day`` covers that
    day, ``*_2_day`` and ``*_3_day`` the next two. Result::

        {"forecast_start_utc", "polar_cap_absorption",
         "days": [{"start_utc", "end_utc", "c_class_percent", "m_class_percent",
                   "x_class_percent", "proton_10mev_percent"}, ...]}
    """
    if not isinstance(payload, list) or not payload:
        raise SpaceWeatherSourceError("Probability product must be a non-empty list")
    issues: dict[datetime, dict] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            raise SpaceWeatherSourceError("Probability entries must be objects")
        start = _day_utc(entry.get("date"), "probability date")
        if start in issues:
            raise SpaceWeatherSourceError(f"Duplicate probability date {_iso(start)}")
        issues[start] = entry
    start = max(issues)
    entry = issues[start]
    days = []
    for index in range(1, FORECAST_DAYS + 1):
        day = {
            "start_utc": _iso(start + timedelta(days=index - 1)),
            "end_utc": _iso(start + timedelta(days=index)),
        }
        for name, source in PROBABILITY_FIELDS:
            key = source.format(index)
            day[name] = _percent(entry.get(key), key)
        days.append(day)
    return {
        "forecast_start_utc": _iso(start),
        "polar_cap_absorption": _optional_text(
            entry.get("polar_cap_absorption"), "polar_cap_absorption"
        ),
        "days": days,
    }


def _series(payload, channel: str, field: str) -> list[dict]:
    if not isinstance(payload, list):
        raise SpaceWeatherSourceError("GOES product must be a list")
    samples, previous = [], None
    for row in payload:
        if not isinstance(row, dict):
            raise SpaceWeatherSourceError("GOES rows must be objects")
        if row.get("energy") != channel:
            continue
        stamp = _instant_utc(row.get("time_tag"), "GOES time_tag")
        if previous is not None and stamp <= previous:
            raise SpaceWeatherSourceError(
                f"Duplicate or decreasing GOES time {_iso(stamp)} ({channel})"
            )
        previous = stamp
        flux = row.get("flux")
        if flux is not None:
            if (
                isinstance(flux, bool)
                or not isinstance(flux, (int, float))
                or not math.isfinite(flux)
                or flux < 0
            ):
                raise SpaceWeatherSourceError(f"Invalid GOES flux: {flux!r}")
            flux = float(flux)
        samples.append({"time_utc": _iso(stamp), field: flux})
    if not any(sample[field] is not None for sample in samples):
        raise SpaceWeatherSourceError(f"No measured GOES values for {channel}")
    return samples


def parse_xrays(payload) -> list[dict]:
    """Return [{"time_utc", "flux_w_m2"}] for the 0.1-0.8 nm channel, in time order.

    A null flux stays None (data gap). Duplicate or decreasing times, negative or
    non-finite flux, or a channel without any measured value raise.
    """
    return _series(payload, XRAY_CHANNEL, "flux_w_m2")


def parse_protons(payload) -> list[dict]:
    """Return [{"time_utc", "flux_pfu"}] for the >=10 MeV channel, in time order.

    Same validation rules as parse_xrays.
    """
    return _series(payload, PROTON_CHANNEL, "flux_pfu")


def parse_regions(payload) -> dict:
    """Return the newest daily active-region report.

    Result: {"observed_date_utc", "regions": [{"region", "location", "spot_class",
    "area_msh", "c_flare_percent", "m_flare_percent", "x_flare_percent",
    "proton_percent"}]} sorted by region number. Missing probabilities stay None.
    """
    if not isinstance(payload, list) or not payload:
        raise SpaceWeatherSourceError("Region product must be a non-empty list")
    by_day: dict[datetime, list[dict]] = {}
    for row in payload:
        if not isinstance(row, dict):
            raise SpaceWeatherSourceError("Region rows must be objects")
        observed = _day_utc(row.get("observed_date"), "observed_date")
        by_day.setdefault(observed, []).append(row)
    latest = max(by_day)
    regions = {}
    for row in by_day[latest]:
        number = row.get("region")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise SpaceWeatherSourceError(f"Invalid region number: {number!r}")
        if number in regions:
            raise SpaceWeatherSourceError(f"Duplicate region {number} on one day")
        area = row.get("area")
        if area is not None and (
            isinstance(area, bool) or not isinstance(area, int) or area < 0
        ):
            raise SpaceWeatherSourceError(f"Invalid region area: {area!r}")
        entry = {
            "region": number,
            "location": _optional_text(row.get("location"), "location"),
            "spot_class": _optional_text(row.get("spot_class"), "spot_class"),
            "area_msh": area,
        }
        for name, source in REGION_PERCENT_FIELDS:
            entry[name] = _percent(row.get(source), source, nullable=True)
        regions[number] = entry
    return {
        "observed_date_utc": _iso(latest),
        "regions": [regions[number] for number in sorted(regions)],
    }


def _decode(raw: dict[str, bytes], name: str):
    if name not in raw:
        raise SpaceWeatherSourceError(f"Missing SWPC product: {name}")
    try:
        return json.loads(raw[name])
    except (ValueError, UnicodeDecodeError) as exc:
        raise SpaceWeatherSourceError(f"SWPC {name} is not valid JSON") from exc


def build_snapshot(raw: dict[str, bytes], acquired_at_utc: str) -> dict:
    """Decode and parse all products into one normalized snapshot document."""
    acquired = _instant_utc(acquired_at_utc, "acquired_at_utc")
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "acquired_at_utc": _iso(acquired),
        "flare_probabilities": parse_probabilities(_decode(raw, "solar_probabilities")),
        "xrays_w_m2": parse_xrays(_decode(raw, "xrays")),
        "protons_10mev_pfu": parse_protons(_decode(raw, "protons")),
        "active_regions": parse_regions(_decode(raw, "solar_regions")),
    }


def snapshot_key(raw: dict[str, bytes]) -> str:
    """SHA-256 over product names, raw content digests and the snapshot version."""
    identity = {
        "snapshot_version": SNAPSHOT_VERSION,
        "products": {
            name: {
                "url": product_url(name),
                "sha256": hashlib.sha256(raw[name]).hexdigest(),
            }
            for name in sorted(raw)
        },
    }
    encoded = json.dumps(identity, sort_keys=True, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _bundle_files() -> set[str]:
    return {f"raw/{name}.json" for name in PRODUCTS} | {"snapshot.json"}


def _verify_bundle(path: Path, key: str) -> None:
    try:
        manifest = json.loads((path / "manifest.json").read_text())
        hashes = manifest["sha256"]
        if manifest["cache_key"] != key or set(hashes) != _bundle_files():
            raise SpaceWeatherSourceError("Invalid space weather cache manifest")
        for name, expected in hashes.items():
            if hashlib.sha256((path / name).read_bytes()).hexdigest() != expected:
                raise SpaceWeatherSourceError(
                    f"Space weather checksum mismatch: {name}"
                )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SpaceWeatherSourceError(
            f"Space weather cache validation failed: {path}: {exc}"
        ) from exc


def download_snapshot(data_dir: Path, client: httpx.Client) -> dict:
    """Fetch every product into an atomic checksummed space-weather/<key> bundle.

    Identical content reuses the verified existing bundle (cache_hit True); a
    corrupt existing bundle raises and is never replaced. HTTP errors propagate.
    Returns {"cache_key", "path", "cache_hit"}.
    """
    raw, sources = {}, {}
    for name in PRODUCTS:
        url = product_url(name)
        response = client.get(url, timeout=HTTP_TIMEOUT_S)
        response.raise_for_status()
        if len(response.content) > MAX_PRODUCT_BYTES:
            raise SpaceWeatherSourceError(
                f"SWPC {name} exceeds {MAX_PRODUCT_BYTES} bytes"
            )
        raw[name] = response.content
        sources[name] = {
            "url": url,
            "content_type": response.headers.get("content-type"),
            "last_modified": response.headers.get("last-modified"),
        }
    acquired = _iso(datetime.now(timezone.utc).replace(microsecond=0))
    snapshot = build_snapshot(raw, acquired)
    key = snapshot_key(raw)
    root = Path(data_dir) / "space-weather"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / key
    with locked(root / f"{key}.lock"):
        if destination.exists():
            _verify_bundle(destination, key)
            return {"cache_key": key, "path": str(destination), "cache_hit": True}
        with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
            stage = Path(temporary) / "bundle"
            (stage / "raw").mkdir(parents=True)
            for name, content in raw.items():
                (stage / "raw" / f"{name}.json").write_bytes(content)
            write_json(stage / "snapshot.json", snapshot)
            hashes = {
                name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
                for name in sorted(_bundle_files())
            }
            write_json(
                stage / "manifest.json",
                {
                    "cache_key": key,
                    "snapshot_version": SNAPSHOT_VERSION,
                    "provider": "NOAA Space Weather Prediction Center",
                    "sources": sources,
                    "time_scale": "UTC",
                    "acquired_at_utc": acquired,
                    "sha256": hashes,
                },
            )
            os.rename(stage, destination)
    return {"cache_key": key, "path": str(destination), "cache_hit": False}


def _failure(request: SpaceWeatherRequest, code: str, detail) -> SpaceWeatherEvent:
    return SpaceWeatherEvent(
        request_id=request.request_id,
        status="failed",
        error_code=code,
        error_message=str(detail)[:1000] or code,
    )


def process_space_weather_job(
    request: SpaceWeatherRequest, data_dir: Path, client: httpx.Client | None = None
) -> SpaceWeatherEvent:
    """Idempotent job: replay the stored outcome and reject reused UUIDs.

    The outcome (ready or failed) is persisted per request UUID; a retry after a
    failure therefore needs a new UUID. Unexpected errors propagate unhandled.
    """
    data_dir = Path(data_dir)
    root = data_dir / "space-weather-jobs" / str(request.request_id)
    with locked(root / "job.lock"):
        params = request.model_dump(mode="json")
        request_file = root / "request.json"
        if request_file.exists():
            if json.loads(request_file.read_text()) != params:
                raise SpaceWeatherRequestConflict(
                    "request_id already belongs to different parameters"
                )
        else:
            write_json(request_file, params)
        result_file = root / "result.json"
        if result_file.exists():
            return SpaceWeatherEvent.model_validate_json(result_file.read_text())
        owned = client is None
        client = client or httpx.Client()
        try:
            result = download_snapshot(data_dir, client)
            event = SpaceWeatherEvent(
                request_id=request.request_id,
                status="ready",
                snapshot_path=f"space-weather/{result['cache_key']}",
            )
        except SpaceWeatherSourceError as exc:
            event = _failure(request, "source_invalid", exc)
        except httpx.HTTPError as exc:
            event = _failure(request, "source_unavailable", exc)
        finally:
            if owned:
                client.close()
        write_json(result_file, event.model_dump(mode="json"))
        return event


async def handle_space_weather_message(message, exchange, data_dir: Path) -> None:
    """Dead-letter invalid messages; publish the event before acknowledging."""
    try:
        if len(message.body) > MAX_MESSAGE_BYTES:
            raise ValueError("Space weather request message exceeds 64 KiB")
        request = SpaceWeatherRequest.model_validate_json(message.body)
    except (ValidationError, ValueError):
        logging.warning("Rejecting invalid space weather message to dead-letter queue")
        await message.reject(requeue=False)
        return
    try:
        event = await asyncio.to_thread(process_space_weather_job, request, data_dir)
    except SpaceWeatherRequestConflict as exc:
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
    logging.info("Space weather request %s: %s", request.request_id, event.status)
