"""Explicit offline JPL catalog acquisition; not executed inside backend routes."""

from pathlib import Path
import argparse
from datetime import datetime, timezone
import hashlib
import json
import urllib.request

CATALOG_URL = "https://ssd-api.jpl.nasa.gov/periodic_orbits.api?sys=earth-moon&family=halo&libr=2&branch=S&periodmin=6&periodmax=9&periodunits=d"


def save_catalog(output_dir: Path) -> dict:
    """Fetch one fixed public catalog; verify schema and save raw response plus hash."""
    with urllib.request.urlopen(CATALOG_URL, timeout=30) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Catalog exceeds bounded response size")
    data = json.loads(raw)
    expected = ["x", "y", "z", "vx", "vy", "vz", "jacobi", "period", "stability"]
    if (
        data.get("signature", {}).get("version") != "1.0"
        or data.get("system", {}).get("name") != "Earth-Moon"
        or data.get("family") != "halo"
        or data.get("libration_point") != "2"
        or data.get("branch") != "S"
        or data.get("fields") != expected
        or not data.get("data")
        or int(data.get("count", 0)) != len(data["data"])
        or any(len(row) != len(expected) for row in data["data"])
    ):
        raise ValueError("Unexpected or empty JPL periodic-orbit catalog")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = dict(
        url=CATALOG_URL,
        sha256=hashlib.sha256(raw).hexdigest(),
        acquired_at_utc=datetime.now(timezone.utc).isoformat(),
        signature=data["signature"],
    )
    temporary = output_dir / "catalog.json.tmp"
    temporary.write_bytes(raw)
    temporary.replace(output_dir / "catalog.json")
    temporary = output_dir / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(output_dir / "manifest.json")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(save_catalog(args.output_dir)))
