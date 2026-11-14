"""Atomic local files and process-safe cache locks (Linux containers)."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def write_json(path: Path, value: object) -> None:
    """Replace one JSON file atomically and flush its contents before rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def verify_bundle(path: Path) -> dict:
    """Check each declared file; reject missing files and escaping manifest paths."""
    try:
        manifest = json.loads((path / "manifest.json").read_text())
        hashes = manifest["sha256"]
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError("Empty checksum manifest")
        for name, expected in hashes.items():
            target = (path / name).resolve()
            if not target.is_relative_to(path.resolve()) or digest(target) != expected:
                raise ValueError(f"checksum mismatch: {name}")
        return manifest
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid cache/checksum at {path}: {exc}") from exc
