"""Idempotent persistent ingestion jobs."""
from pathlib import Path
from shared.ingestion import IngestionRequest, IngestionEvent
from app.horizons import download
from app.terrain import prepare_terrain


class RequestConflict(ValueError):
    """A request UUID was reused with different parameters."""


def process_job(request: IngestionRequest, data_dir: Path) -> IngestionEvent:
    """Persist bounded attempts and result; return a replayable ready/failed event."""
    raise NotImplementedError
