"""Isolated loopback HTTP verification; never starts shared Compose services.

The test-only factory adds diagnostics to its own server, not to app.main.
"""

import argparse
import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter, sleep
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shared.constants import MOON_REFERENCE_RADIUS_M

from .analysis_api import create_app


def verification_app():
    """Test-only local server, with counters and a post-shutdown snapshot."""
    app = create_app(start_worker=False)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            yield
        Path(os.environ["RISK_VERIFY_SHUTDOWN"]).write_text(
            json.dumps(app.state.risk_runtime.snapshot(), indent=2) + "\n"
        )

    app.router.lifespan_context = lifespan

    @app.get("/_verify/cache")
    def counters():
        return app.state.risk_runtime.snapshot()

    return app


def _terrain(root):
    folder = root / "terrain/raw" / ("c" * 64)
    folder.mkdir(parents=True)
    size = 81
    path = folder / "source.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=size,
        height=size,
        count=1,
        dtype="float32",
        nodata=np.nan,
        transform=from_origin(-size * 40, size * 40, 80, 80),
        crs=f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R={MOON_REFERENCE_RADIUS_M} +units=m",
    ) as dataset:
        dataset.write(np.zeros((size, size), dtype="float32"), 1)
    (folder / "manifest.json").write_text(
        json.dumps(
            dict(
                product_id="LDEM_80S_80MPP_ADJ",
                sha256={"source.tif": hashlib.sha256(path.read_bytes()).hexdigest()},
            )
        )
    )


def _get(base, path, body=None):
    request = Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={} if body is None else {"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def run_check(output):
    """Measure fixed cold/warm/off cases on real local HTTP, then disconnect."""
    rows, reference = [], {}
    for seed, runs in ((831, 24), (832, 24), (833, 200)):
        for enabled in (False, True):
            with tempfile.TemporaryDirectory(prefix="risk-http-") as directory:
                root = Path(directory)
                _terrain(root / "data")
                with socket.socket() as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    port = reservation.getsockname()[1]
                env = os.environ | dict(
                    STATE_DIR=str(root / "state"),
                    DATA_DIR=str(root / "data"),
                    RISK_PHYSICS_CACHE_ENABLED=str(int(enabled)),
                    RISK_PHYSICS_CACHE_BYTES=str(32 * 1024 * 1024),
                    RISK_PHYSICS_CACHE_ENTRIES="128",
                    RISK_VERIFY_SHUTDOWN=str(root / "shutdown.json"),
                )
                log = (root / "server.log").open("w")
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.verify_risk_runtime:verification_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--no-access-log",
                    ],
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                base = f"http://127.0.0.1:{port}"
                row = dict(seed=seed, runs=runs, cache_enabled=enabled)
                try:
                    deadline = perf_counter() + 15
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError(
                                "Verification server exited during startup"
                            )
                        try:
                            _get(base, "/health")
                            break
                        except URLError:
                            if perf_counter() > deadline:
                                raise RuntimeError(
                                    "Verification server startup timed out"
                                ) from None
                            sleep(0.05)
                    body = dict(
                        site_id="verified-synthetic-pole",
                        latitude_deg=-90,
                        longitude_deg_east=0,
                        runs=runs,
                        seed=seed,
                    )
                    outputs = []
                    for phase in ("cold", "warm"):
                        mark = perf_counter()
                        result = _get(base, "/descents/risk", body)
                        elapsed = perf_counter() - mark
                        outputs.append(result)
                        row[phase] = dict(
                            elapsed_s=elapsed,
                            result_id=result["result_id"],
                            diagnostics=_get(base, "/_verify/cache"),
                        )
                    if outputs[0] != outputs[1]:
                        raise RuntimeError("Cold/warm API results differ")
                    if not enabled:
                        reference[seed] = outputs[0]
                    elif outputs[0] != reference[seed]:
                        raise RuntimeError("Cache on/off API results differ")
                    row["outcomes_identical"] = True
                    if enabled:
                        # A real client abandons a long request after admission.
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", port, timeout=10
                        )
                        connection.request(
                            "POST",
                            "/descents/risk",
                            json.dumps(body | {"runs": 1000, "seed": seed + 100}),
                            {"Content-Type": "application/json"},
                        )
                        deadline = perf_counter() + 10
                        while _get(base, "/_verify/cache")["pending"] == 0:
                            if perf_counter() > deadline:
                                raise RuntimeError("Long request was not admitted")
                            sleep(0.05)
                        mark = perf_counter()
                        connection.close()
                        while _get(base, "/_verify/cache")["pending"]:
                            if perf_counter() - mark > 10:
                                raise RuntimeError(
                                    "Disconnect did not release the risk worker"
                                )
                            sleep(0.05)
                        row["disconnect_release_s"] = perf_counter() - mark
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        raise RuntimeError(
                            "Owned verification server failed to shut down"
                        ) from None
                    log.close()
                # Uvicorn re-raises the captured SIGTERM after graceful shutdown.
                if process.returncode not in (0, -signal.SIGTERM):
                    raise RuntimeError((root / "server.log").read_text())
                row["server_exit_code"] = process.returncode
                row["after_shutdown"] = json.loads((root / "shutdown.json").read_text())
                if (
                    not row["after_shutdown"]["closed"]
                    or row["after_shutdown"]["cache"]["entries"]
                ):
                    raise RuntimeError("Shutdown leaked cache ownership")
                rows.append(row)
                print(
                    json.dumps(
                        {
                            k: row[k]
                            for k in ("seed", "cache_enabled", "outcomes_identical")
                        }
                    ),
                    flush=True,
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(dict(complete=False, rows=rows), indent=2) + "\n"
                )
    sources = [
        "backend/app/risk.py",
        "backend/app/risk_runtime.py",
        "backend/app/analysis_api.py",
        "backend/app/verify_risk_runtime.py",
    ]
    root = Path(__file__).resolve().parents[2]
    report = dict(
        complete=True,
        endpoint="POST /descents/risk",
        mode="real_local_HTTP_synthetic_verified_DEM",
        rows=rows,
        source_sha256={
            p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in sources
        },
        hardware_jobs=0,
        spark_spent=0,
        quantum_advantage=False,
    )
    output.write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run_check(parser.parse_args().output)
