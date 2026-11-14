# Task 3 — sampled visibility and metrics

This backend feature combines the Task 1 Horizons cache and Task 2 terrain
horizon. It reports terrain-relative solar disk states, Earth-center visibility,
UTC intervals, estimated durations and bracketed transitions. It does not add
REST endpoints or UI (Task 4), radio link budgets, eclipse modeling or solar power
in watts. Delivery is only to `feature/task3-visibility-metrics`, based on Task 2;
the user performs branch merges.

## Run and refine

Use the two references returned by a ready ingestion event:

```bash
docker compose --env-file .env.example run --rm --no-deps backend \
  python -m app.analyze_visibility TERRAIN_REFERENCE HORIZONS_REFERENCE \
  --selected-utc 2026-11-14T00:00:00Z > visibility.json
```

The selected instant defaults to the request start and must be inside its UTC
interval. `DATA_DIR` defaults to `/data`. The backend verifies checksums and reads
files only; JSON goes to stdout. A reference is a relative `terrain/processed/<hash>`
or `horizons/<hash>` path, never a download URL. Missing/corrupt files and mismatched
observer positions or heights fail explicitly.

`--refine` adds two passes: 300 seconds, then 60 seconds. It submits deterministic
RabbitMQ requests covering detected transition brackets, waits for ready events,
validates the returned site/interval/step, merges finer samples, then recalculates.
The ingestion service performs every external request and cache write. Its normal
retry/failure policy applies. Failed ingestion stops the CLI with an error; it
never reports that refinement succeeded. Existing cached results are reusable.

Adjacent/overlapping windows are merged and aligned to the finer time grid.
There are at most 32 windows per pass; excessive work fails with instructions to
shorten the analysis. This is refinement of **detected** crossings, not a full
fine-grid scan. A rise and set between two same-sign coarse samples can be missed.
A pass with no windows sends no messages. Exact zero samples may retain a bracket
of two time steps because evidence on both sides is required to establish a
crossing. Report the bracket, not only the interpolated timestamp.

For a real crossing demonstration, submit the included month envelope:

```bash
docker compose --env-file .env.example exec -T backend \
  python -m app.submit_ingestion /workspace/shared/examples/visibility-malapert-month.json
# Then use the returned terrain_path and horizons_path:
docker compose --env-file .env.example run --rm --no-deps backend \
  python -m app.analyze_visibility TERRAIN_REFERENCE HORIZONS_REFERENCE --refine \
  > visibility-month.json
```

The envelope covers 2026-11-01 through 2026-12-01 UTC, hourly, at the documented
Malapert R23 observer. It reuses Task 1 terrain. In the isolated local environment,
replace the Compose options with `-p nasa-task1 --env-file .env.example
-f docker-compose.yml -f docker-compose.isolated.yml` to use its independent cache
without publishing ports. Full result JSON is generated locally, not committed.

## Model and boundary semantics

Clearance is apparent airless target elevation minus the circularly interpolated
terrain horizon at its azimuth. Solar angular radius in degrees is the Horizons
full angular diameter in arcseconds divided by 7200. Under the locally constant
horizon approximation across the solar disk:

- `full`: center clearance minus radius is positive.
- `partial`: upper limb is positive and lower limb is negative.
- `dark`: center clearance plus radius is negative.
- `boundary`: an exact limb contact. A persistent boundary contributes to unknown
  duration, not guaranteed sunlight or darkness.
- `unknown`: required ephemeris or terrain is unavailable. Missing/nonpositive
  solar diameter makes solar disk evaluation unknown without erasing Earth data.

Earth uses its **center** clearance: positive is `visible`, negative `blocked`,
zero `boundary`. This does not claim DSN contact, a usable radio link, station
availability or visibility of the entire Earth disk.

The temporal model is piecewise-linear **clearance**, not interpolated azimuth
with a claim of exact dynamics. Within each pair of valid samples, zero crossings
split intervals into states. Missing endpoints, gaps larger than the original
requested step, or missing horizon sectors swept by the shortest azimuth path
produce unknown intervals. An exactly 180-degree azimuth jump is ambiguous and
also unknown. Longitude/azimuth wrap-around does not create a false discontinuity.
Leading/trailing uncovered time is unknown; no extrapolation fills the request.
Adjacent equal states are merged, and intervals partition the entire requested
[start, end) period. UTC timestamps and every duration carry explicit units.

Events require opposite signs on both sides. A touch followed by return to the
same side is not a setting. A run of zero samples preserves the surrounding
sign bracket; a boundary at the observation edge cannot prove a crossing beyond
that edge. Event types are sunrise, sunset (last solar limb), full-sun start/end,
and Earthrise/Earthset (center). Estimated root times are not new observations.
The solar disk calculation does not compute fractional visible area or energy.

## Metrics and next events

For both targets, report visible seconds, percentage of the entire requested
interval, blocked seconds, unknown seconds, longest continuous visible period
and longest continuous blocked period. Solar results additionally separate full
and partial durations. Full and partial solar intervals join into one continuous
sunlight window; unknown periods break every continuous window. Unknown time is
not included in darkness and is not removed from the percentage denominator.
The visible, blocked and unknown durations sum to the observation duration.

`next_setting` is relative to the selected UTC instant:

| Status | Meaning |
|---|---|
| `found` | The sampled model has a candidate setting with no preceding unknown interval. |
| `candidate_after_unknown` | A setting is modeled, but an earlier event may be hidden by missing data. |
| `transition_overlaps_selected_time` | The estimated root is past, but its sample bracket still overlaps the selected instant. |
| `no_event_in_range` | No setting was found in the remaining modeled observation period. This is not a prediction beyond it. |
| `insufficient_data` | No candidate can be established and the remaining period includes unknown/boundary duration. |

For an event, the output retains estimated UTC time, bracket start/end, bracket
width in seconds and estimated/bracketed time until it. All timings remain
conditional on the terrain and temporal model. Subsecond JSON arithmetic must
not be displayed as subsecond physical accuracy.

## Spatial limitations and provenance

Task 2's finite radius, 80 m terrain cells, unresolved subpixel features and
azimuth-interpolation limitations remain in `horizon_quality`. Fine time sampling
does not fix terrain error. `--sensitivity-deg` (default 0.75, range 0–5) recomputes
visible durations after uniformly raising/lowering the horizon. These are
illustrative sensitivity scenarios, **not confidence intervals or guaranteed
error bounds**. The default is a development choice motivated by the roughly
0.73-degree interpolation sensitivity in the Task 2 Malapert experiment; it is
not a globally validated uncertainty estimate. Surface slope across the disk,
eclipses, atmospheric effects and unresolved narrow peaks are not modeled.

The result identifies the request, selected UTC time, horizon profile ID, every
coarse/refined ephemeris reference and its file hashes, model version and a
deterministic `analysis_id`. Source URLs/acquisition details remain available in
the referenced manifests. No random result IDs or current-time-dependent output
are introduced. This CLI does not persist analysis results in the shared volume.

## Live API regression and test history

The real one-minute Malapert query for 2026-11-02 15:55–16:00 UTC returned
`Date__(UT)__HR:MN`, even though the existing request sends `TIME_DIGITS=SECONDS`.
The ingestion parser now accepts either named UTC time column, and still checks
every timestamp against the exact requested grid. It does not round second-offset
requests or accept TT. The numeric output schema remains cache version 1;
`parser_version=2` now enters new cache identities and manifests. Previously
validated version-1 bundles remain readable. Refinement request identity uses v2,
so the terminal failed v1 job is preserved rather than overwritten or silently
retried under the same UUID.

Visibility tests were frozen in `2d6d067`; the new minute-format regression tests
were added separately in `09e8719`, before fixing the parser. No pre-existing test
was edited. The latest combined frozen test baseline is `09e8719`.

Tests exercise analytic disk transitions and duration conservation, exact contacts
and tangencies, clipped bounds, missing values/gaps, unknown swept terrain,
wrap-around, observer mismatch, cache corruption, deterministic refinement plans
and broker orchestration/failure. Real-data evidence and final check counts are
recorded below after execution.

## Sources

- [JPL Horizons manual](https://ssd.jpl.nasa.gov/horizons/manual.html): apparent
  AZ/EL (quantity 4), full angular diameter (quantity 13) and UTC observer output.
- [Terrain horizon implementation and verification](terrain-horizon.md): spherical
  geometry, provenance, sampling and measured spatial sensitivity.
- [Input/visibility assumptions](assumptions.md): original MVP boundary semantics.

## Live verification — 2026-11-14

[Machine-readable evidence](verification/task3-visibility-2026-11-14.json) records
checksummed references and the measured results for the November Malapert case.
The hourly source had 721 samples. Refinement submitted 18 five-minute windows
and 19 one-minute windows through the existing broker/worker, producing 1,006
merged samples and 19 transition events. Every final event bracket is 60 seconds
wide. The largest matched event-time shift from the hourly estimate is about
602 seconds, demonstrating why the coarse interpolated timestamp alone is not
sufficient evidence of event timing.

Within this bounded terrain/linear-clearance model, estimated sunlight (including
partial disk visibility) is about 37.10% of the 30-day interval, and Earth-center
visibility about 44.03%. These are model results, not an operational communication
or measured illumination certification. The default spatial sensitivity scenarios
remain alongside the nominal metrics. Replaying the refined analysis from cached
jobs produced identical JSON and analysis ID. Both targets' intervals are
contiguous and their visible/blocked/unknown durations conserve the requested
interval within 1e-5 seconds of floating-point arithmetic.

The verification script is reproducible after generating the three reports:

```bash
python scripts/verify-visibility.py coarse.json refined.json replay.json > evidence.json
```

Final validation: 96 backend tests, 75 ingestion tests and 1 frontend test
(172 total); Python lint, new-code formatting, pip check, frontend clean install,
production build and npm audit. The existing upstream Starlette/AnyIO deprecation
warning is non-failing. These are local checks; teammate approval and manual
branch integration remain separate.
