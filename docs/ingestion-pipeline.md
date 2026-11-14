# Task 1: durable ingestion and terrain cache

## Run

Start the services with the repository's Docker Compose configuration, then:

```bash
docker compose --env-file .env.example exec -T backend \
  python -m app.submit_ingestion /workspace/shared/examples/ingestion-malapert.json
```

The example requests Malapert Massif R23, 2026-11-14 to 2026-11-15 UTC, hourly
samples and a 20,000 m projected terrain half-width. It uses the documented
provisional containing-pixel ground height plus 2 m. A new logical request needs
a new UUID; resubmit an identical envelope with the same UUID to replay it.
Edit a copy under `shared/examples/` for another request. Supported input dates,
sampling and count limits are inherited from AnalysisRequest. The terrain stage
accepts nominal 80–90° S and validates the requested square against actual data.

The backend CLI contacts RabbitMQ only. It never downloads NASA data or writes
the shared `/data` volume. The long-running ingestion worker owns downloads and
writes. This task adds no analysis REST endpoints or UI; those remain Step 4.

## Message contracts and delivery

`shared/ingestion.py` defines version 1 request and ready/failed envelopes with
UUID request identifiers. Request size is limited to 64 KiB. Result messages
contain data-volume-relative paths and error metadata, never raster/time-series
payloads. Ready events require both paths; failed events require an error and
cannot claim ready paths. Paths are constrained to known cache namespaces.

Durable topology:

- Requests: `lunar.ingestion.requests.v1`.
- Invalid input dead letters: `lunar.ingestion.dead.v1`.
- Topic result exchange and audit queue: `lunar.ingestion.events.v1`.
- CLI reply queues: `lunar.ingestion.reply.<request-uuid>.<submission-uuid>`,
  bound to the request UUID; inactive queues expire after 24 hours.

Requests and events use persistent delivery and publisher confirms. One worker
process handles one delivery at a time (`prefetch_count=1`). Blocking acquisition
runs off the event loop so heartbeats continue. The worker acknowledges a request
only after complete files and a persistent job result exist and its completion
publication is confirmed. Broker/process failures before ACK cause redelivery;
local records and file locks prevent duplicate writes. Consumer errors propagate,
and Compose restarts failed workers. A hard kill can leave ignored `.pending-*`
directories; they are never treated as complete datasets.

Delivery is **at least once**, not exactly once. Consumers deduplicate completion
messages by request UUID/status. Do not reuse a request UUID for different inputs;
that produces `request_conflict` without overwriting the original job. Use distinct
UUIDs for concurrent logical submissions. A CLI timeout stops waiting, not the job;
resubmitting the same envelope creates a fresh reply queue and safely replays it.
The audit and dead-letter queues are retained for inspection; production retention
and a dashboard are outside this local MVP.

## Persistent layout and retries

```text
/data/
  jobs/<request-uuid>/
    request.json        # immutable identity
    attempts.json       # written before each attempt; survives restart
    result.json         # atomic ready/failed envelope
  horizons/<key>/       # raw API responses, normalized samples, manifest
  terrain/raw/<key>/
    source.tif          # original HTTP bytes
    manifest.json      # URL, product, timestamps, headers, size and SHA-256
  terrain/processed/<key>/
    terrain.tif         # native-grid crop
    manifest.json      # source checksum/reference, site, grid, bounds, SHA-256
```

Jobs have at most three attempts, including interrupted attempts. Transport errors,
HTTP 429 and selected 5xx responses retry after 1 and 2 seconds. Other known HTTP,
semantic, coverage and cache errors produce terminal failures immediately.
The existing Horizons client also has at most three HTTP attempts per call;
therefore nested job retries can make at most nine HTTP attempts for a failing
Horizons target. Terminal results are replayed, not silently retried. To retry a
terminal failure intentionally, create a new UUID; corrupt immutable bundles need
explicit investigation/replacement, not automatic overwriting.

Per-job, source and crop advisory file locks serialize writers sharing this Linux
filesystem. This supports local Docker volumes, not arbitrary distributed NFS
locking semantics. Atomic directory renames publish bundles; manifests/checksums
are checked on reuse. A ready replay also verifies referenced files and raw terrain.
Hash verification detects local corruption; a self-computed hash is not an
independent publisher authenticity signature.

## Terrain acquisition decision

The only allowed terrain URL is the fixed NASA PGDA product 90 asset
`LDEM_80S_80MPP_ADJ.TIF`; requests cannot inject URLs or local file paths. A streaming
HTTP download is bounded to 200,000,000 bytes. The current source is approximately
189 MB. It is cached once in full, then reused across sites. This bounded full-product
approach retains exact original bytes and enables offline reuse; unlike research
range reads, first acquisition transfers the whole product. Sources are immutable
snapshots, not automatically refreshed when NASA changes an asset.

Rasterio validates the spherical lunar south-polar stereographic projection,
80 m resolution, float32 band, unit scale/offset and north-up grid. Equivalent
GDAL polar scale encodings (`k=1` / `lat_ts=-90`) are accepted. A square centered
on the containing pixel is cropped without interpolation. Missing cells,
non-finite values and truncated coverage fail the request. The maximum 20 km
projected half-width gives a 501 × 501 crop. This is an acquisition bound, not a
scientifically justified horizon-search cutoff. It does not establish landing
safety, full distant-terrain visibility or native measurement density.

The crop key includes source SHA-256, site parameters, requested radius, processing
version and Rasterio version. Horizons retains its independently parameterized
cache. No terrain-derived altitude is silently substituted into the input request.
The manifest records measured pixel height so later analysis can check the chosen
observer altitude. Sources/coordinates are detailed in [terrain-and-sites.md](terrain-and-sites.md).

## Dependencies and review

The planned Rasterio/GDAL and NumPy stack is now installed in the ingestion
container (Rasterio 1.5.2); Debian libexpat1 is needed by its wheels. The backend
now installs the already-selected aio-pika 9.5.7 client. Both Python locks were
regenerated with distribution hashes; builds still enforce `--require-hashes`
and `pip check`. Application scientific/UI dependencies were not otherwise expanded.

Task branch: `feature/task1-ingestion-cache`. Delivery is to this branch only;
`tahakrcc` and `main` are not merge targets for this task.
Frozen test baseline: `37a8d3f`. The initial new fixture's reserved pytest name
was corrected with user approval; subsequent new-test style cleanup changed no
assertions. Existing baseline tests were preserved.

## References

- [aio-pika work queue durability and acknowledgement](https://docs.aio-pika.com/rabbitmq-tutorial/2-work-queues.html)
- [Rasterio windowed reads/writes](https://rasterio.readthedocs.io/en/stable/topics/windowed-rw.html)
- [NASA PGDA product 90](https://pgda.gsfc.nasa.gov/products/90)

## Live verification — 2026-11-14

[Machine-readable evidence](verification/task1-ingestion-2026-11-14.json) records
the acquired full source checksum, artifact references and dimensions. In isolated
Compose project `nasa-task1`, the backend submitted the documented Malapert request;
the worker acquired 189,158,392 raw bytes, saved a 251,001-cell crop and cached 25
Sun/Earth samples. All resulting file checksums were independently checked from
the backend's read-only mount.

With the worker stopped, another copy of the request remained pending (one ready
queue message). RabbitMQ was restarted; the same pending message remained. Starting
the worker completed it with identical references and job attempts still equal to
one. The waiting backend reconnected and received the result. aio-pika logged
transient connection/channel errors during forced broker shutdown but recovered;
these were not hidden. No new source download was logged on replay.

A separate out-of-coverage request returned `failed/TerrainError` after one attempt.
A malformed JSON message reached the dead-letter queue and was acknowledged there
by the verification command. Unit tests also cover retry exhaustion, corrupt
sources/crops, concurrent duplicates and publication-before-ACK.

Validation: 72 ingestion tests, 30 backend tests, 1 frontend test; both Python
Ruff checks, pip check, frontend build and npm audit passed. The existing upstream
Starlette/AnyIO deprecation warning remains non-failing. These are local tests,
not a production deployment or peer-review approval.
