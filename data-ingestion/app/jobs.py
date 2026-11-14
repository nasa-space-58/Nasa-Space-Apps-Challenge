"""Idempotent persistent ingestion jobs with a restart-safe retry budget."""

import json
from pathlib import Path
import time

import httpx
from shared.ingestion import IngestionRequest, IngestionEvent
from app.cache import digest, locked, verify_bundle, write_json
from app.horizons import download, HorizonsError
from app.terrain import prepare_terrain, TerrainError

MAX_ATTEMPTS = 3


class RequestConflict(ValueError):
    """A request UUID was reused with different parameters."""


def _failure(request, code, detail):
    return IngestionEvent(
        request_id=request.request_id,
        status="failed",
        error_code=code,
        error_message=str(detail)[:1000],
    )


def _verify_ready(event, data_dir):
    for ref in [event.horizons_path, event.terrain_path]:
        manifest = verify_bundle(data_dir / ref)
        if "source_path" in manifest:
            raw = (data_dir / manifest["source_path"]).resolve()
            if (
                not raw.is_relative_to(data_dir.resolve())
                or digest(raw) != manifest["source_sha256"]
            ):
                raise ValueError("Raw terrain checksum mismatch")


def process_job(request: IngestionRequest, data_dir: Path) -> IngestionEvent:
    """Replay stable results, reject UUID conflicts and persist at most 3 attempts.

    Known permanent source/coverage failures finish immediately. HTTP transport,
    429 and selected 5xx failures retry with bounded delays. File locks serialize
    duplicates; a crash consumes the in-progress attempt instead of resetting it.
    """
    data_dir = Path(data_dir)
    root = data_dir / "jobs" / str(request.request_id)
    with locked(root / "job.lock"):
        request_file = root / "request.json"
        params = request.model_dump(mode="json")
        if request_file.exists():
            if json.loads(request_file.read_text()) != params:
                raise RequestConflict(
                    "request_id already belongs to different parameters"
                )
        else:
            write_json(request_file, params)
        result_file = root / "result.json"
        if result_file.exists():
            event = IngestionEvent.model_validate_json(result_file.read_text())
            if event.status == "ready":
                try:
                    _verify_ready(event, data_dir)
                except (ValueError, OSError, KeyError) as exc:
                    event = _failure(request, "cache_corrupt", exc)
                    write_json(result_file, event.model_dump(mode="json"))
            return event
        attempt_file = root / "attempts.json"
        attempts = (
            json.loads(attempt_file.read_text())["attempts"]
            if attempt_file.exists()
            else 0
        )
        event = _failure(
            request,
            "retry_exhausted",
            "Previous attempts were interrupted; submit a new request_id to retry",
        )
        with httpx.Client() as client:
            while attempts < MAX_ATTEMPTS:
                attempts += 1
                write_json(attempt_file, {"attempts": attempts})
                try:
                    terrain = prepare_terrain(
                        request.analysis.site,
                        request.terrain_radius_m,
                        data_dir,
                        client,
                    )
                    horizons = download(request.analysis, data_dir, client)
                    event = IngestionEvent(
                        request_id=request.request_id,
                        status="ready",
                        horizons_path=str(Path(horizons["path"]).relative_to(data_dir)),
                        terrain_path=str(Path(terrain["path"]).relative_to(data_dir)),
                    )
                    _verify_ready(event, data_dir)
                    break
                except (
                    httpx.HTTPError,
                    TerrainError,
                    HorizonsError,
                    ValueError,
                    OSError,
                ) as exc:
                    transient = isinstance(exc, httpx.TransportError) or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code in (429, 500, 502, 503, 504)
                    )
                    event = _failure(request, type(exc).__name__, exc)
                    if not transient or attempts >= MAX_ATTEMPTS:
                        break
                    time.sleep(2 ** (attempts - 1))
        write_json(result_file, event.model_dump(mode="json"))
        return event
