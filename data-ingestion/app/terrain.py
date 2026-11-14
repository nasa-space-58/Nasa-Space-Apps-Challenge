"""Persistent, validated LOLA source and cropped terrain bundles."""
from pathlib import Path
import httpx
from shared.contracts import ObserverSite

SOURCE_URL = 'https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_80MPP_ADJ.TIF'
PRODUCT_ID = 'LDEM_80S_80MPP_ADJ'
MAX_SOURCE_BYTES = 200_000_000
VERSION = 1


class TerrainError(ValueError):
    """Invalid terrain source, incomplete coverage or corrupt cache."""


def acquire_source(data_dir: Path, client: httpx.Client) -> Path:
    """Download a bounded, immutable source with provenance and SHA-256."""
    raise NotImplementedError


def prepare_terrain(site: ObserverSite, radius_m: int, data_dir: Path,
                    client: httpx.Client) -> dict:
    """Produce a validated atomic crop; preserve original source separately."""
    raise NotImplementedError
