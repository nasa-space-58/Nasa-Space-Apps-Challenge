"""Explicit offline JPL catalog acquisition; not executed inside backend routes."""
from pathlib import Path

CATALOG_URL = 'https://ssd-api.jpl.nasa.gov/periodic_orbits.api?sys=earth-moon&family=halo&libr=2&branch=S&periodmin=6&periodmax=9&periodunits=d'


def save_catalog(output_dir: Path) -> dict:
    """Fetch one fixed public catalog; verify schema and save raw response plus hash."""
    raise NotImplementedError
