# Horizons ingestion

## Run

From the repository root, with Docker available:

```bash
docker compose --env-file .env.example run --rm --no-deps data-ingestion \
  python -m app.download_horizons examples/horizons-reference.json
```

For a new scenario, copy the example inside `data-ingestion/examples/` and edit
its site, UTC start/end and sampling. Existing AnalysisRequest constraints apply;
timestamps must additionally have whole-second precision. The documented
reference observer has no certified landing-site or DEM coverage status.

The command runs in the ingestion service, which alone writes the shared data
volume. Backend reads that volume without contacting NASA. This is an explicit
CLI acquisition path; the long-running worker still waits on the broker. Queue
request/event schemas and consumers, UI/API submission and terrain ingestion
remain subsequent work.

## Data and validation

Both Sun (10) and Earth center (399) use `coord@301`, GEODETIC coordinates,
AIRLESS apparent angles, UTC, and quantities 4 and 13. Angular diameter is stored
in arcseconds; azimuth/elevation in degrees. Missing quantities remain JSON null.
No terrain visibility, power, DSN availability or communication budget is inferred.

The parser verifies target, Moon center, MEAN_ME orientation, 1737.4 km sphere,
observer coordinates, named CSV columns and the complete requested UTC grid.
Missing rows fail explicitly. Output coordinate metadata is rounded by Horizons;
its comparison tolerance is 1e-5 degree/km and does not imply terrain accuracy.
Future UTC results inherit Horizons' leap-second assumptions.

Each request produces `/data/horizons/<sha256>/`:

- `raw/10.json` and `raw/399.json`: original API response bytes.
- `samples.json`: Sun and Earth quantities joined on the validated UTC grid.
- `manifest.json`: full request URLs/options, acquisition timestamps, API signature,
  source headers, original request and SHA-256 checksums for the three data files.

The cache key includes coordinates, site identifier, date interval, sampling,
endpoint, query options and parser/model version. Parser/model changes must bump
CACHE_VERSION. Cached source results are immutable snapshots, not automatic
refreshes when JPL updates its models. Cached files are checked before reuse;
corruption fails loudly. Per-key file locks serialize concurrent local downloads.
The completed directory is renamed atomically only after both targets validate.
Failed requests leave no ready bundle. HTTP transport failures, 429 and selected
5xx errors retry at most three times with bounded exponential delays. Other HTTP
and semantic errors stop immediately. The client issues requests sequentially.

## Verification — 2026-11-14

Live query: 0° E, 89.5° S, 0 km; 2026-11-14 00:00 UTC to
2026-11-15 00:00 UTC; hourly sampling. Each target returned 25 samples.
Both responses reported DE441, MEAN_ME and API signature version 1.2.
First samples: Sun azimuth 126.627332945°, elevation +1.258334640°;
Earth azimuth 358.773422998°, elevation −4.148545320°.
The manifest/raw responses are authoritative for full precision.

The first acquisition reported `cache_hit: false`; repeating the command reported
`cache_hit: true`. Backend successfully read the completed bundle from its
read-only mount. A local ignored copy is under `data/horizons/`.

Frozen test baseline: `accef1e`. 37 new cases cover reference data, parsing,
metadata, unknown values, invalid time grids, retries, atomic completion,
cache identity and checksum corruption. Existing tests were not modified.
Full validation: 39 ingestion tests, 30 backend tests, 1 frontend test,
Python Ruff checks, frontend build and npm audit (zero findings) passed.

## Sources

- [JPL Horizons API](https://ssd-api.jpl.nasa.gov/doc/horizons.html)
- [Horizons manual: observer coordinates and quantities](https://ssd.jpl.nasa.gov/horizons/manual.html)
- [Captured fixture provenance](../data-ingestion/tests/fixtures/README.md)
