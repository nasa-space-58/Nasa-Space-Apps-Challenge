# R1 — Observation search, exports, sharing and teaching

Branch: `feature/r1-exports-sharing-classroom`, based on Task 7 `28554ff`.
Skeleton `0b18549`; frozen backend test baseline `f534817`. Existing and frozen
backend/ingestion tests remain unchanged. No new runtime dependencies.
Only this branch is delivered; tahakrcc/main integration belongs to the user.

## Search: closest evaluated candidates

Task 7's candidate-grid search remains unchanged. When the search finishes without
a match, the UI shows the three closest fully evaluated candidates and every unmet
threshold. `GET /searches/{id}/ranking` returns all ranked/excluded candidates.

The score is the arithmetic mean of three nonnegative deficits:

- sunlight shortfall / 100 percentage points;
- excess longest darkness / mission duration in hours;
- Earth-visibility shortfall / mission duration in hours.

Lower is closer; exact matches score zero. These are explicit equal weights, not
scientific risk weights or a landing-safety score. Scores need not be bounded by
one (a threshold may exceed mission duration). Ties sort by UTC start then job ID.
Unknown, failed and unfinished candidates are excluded rather than presented as
close matches. Each deficit remains visible in percentage points or hours.

## Export / Share

Available with completed analysis, comparison and classroom results.

- **CSV:** exact sampled UTC timestamps, coordinates/frame, height in metres,
  Sun/Earth azimuth/elevation/horizon in degrees and labeled visibility states.
  Irregular sample intervals remain irregular. Null numbers are empty cells;
  unknown remains an explicit state. Each scenario's first row carries JSON
  columns for the original submission, complete ephemeris provenance, horizon
  metadata, summary metrics and visibility intervals. Subsequent metadata cells
  are empty. A/B exports have a `scenario` column. Durations in the metadata use
  seconds, as indicated by their field names. Spreadsheet formula-leading text
  cells receive an apostrophe; the unmodified input remains in submission JSON.
- **Chart PNG:** the displayed horizon sample(s), sampled UTC date, both visibility
  timelines, units, legend, analysis identifiers and NASA attribution. A/B charts
  use a common elevation scale and keep their separate UTC windows. PNG is rendered
  locally from SVG, without an external screenshot service.
- **Printable report:** standalone HTML containing inputs, metrics, interval lists,
  provenance, model versions, result fingerprints and, for a pair, B-minus-A
  differences. Open the downloaded HTML and use the browser's Print / Save as PDF.
  No additional PDF engine or external scripts are loaded. User text is escaped.
- **Scenario JSON:** a versioned, bounded manifest containing one or two submissions
  plus expected model/source metadata and a SHA-256 of each canonical result.
  It does not bundle terrain or ephemeris files and does not import claimed results.

Backend endpoints:

```text
GET /analyses/{id}/export/csv
GET /analyses/{id}/export/report
GET /analyses/{id}/export/scenario
# Add ?b_id={other-id} for an existing compatible A/B pair.
POST /scenarios/validate    # JSON manifest; validates only, queues nothing
POST /scenarios/check      # {"manifest": ..., "job_ids": ["A", "B"]}
```

Missing analyses return 404, unfinished/failed analyses 409, incompatible pairs or
invalid manifests 422. Request documents are limited to 1 MB and at most two
scenarios. Unknown schema versions/extra fields and unsupported analysis inputs
are rejected. Export responses set download filenames, no-sniff and a restrictive
content policy. Files never supply executable commands or URLs to fetch.

## Sharing and replay

The saved-result link uses `?analysis={job-id}&frame={sample-index}`. A/B adds
`compare={B-id}&cursor={comparison-index}`. Reload restores the immutable saved
result and the selected sample/cursor, taking precedence over device history.
Invalid indices are clamped; a missing shared result produces a visible error.
Explicitly choosing/running another analysis clears stale shared-link parameters.

A link works on the **same accessible server**, while its state volume exists.
A localhost URL is not a public share. No public deployment, user account system,
or new access-control model is introduced in R1.

For another installation, open **Mission control → Import a shared scenario file**.
Choose JSON and inspect its dates/model versions. Validation does not run jobs.
**Calculate imported scenario** explicitly submits its one/two original inputs to
the current pipeline. Progress/IDs persist in browser storage. When finished,
expected and actual result hashes are checked. The UI distinguishes exact content
match from changed data/model/acquisition metadata. It never promises that a future
recalculation on different data will reproduce old results. The shared snapshot
remains available through its original server link. A hash proves content equality,
not the authenticity of an untrusted imported file.

## Classroom mode

**Classroom → Start lesson** takes two clicks from the main navigation. Two
source-backed Malapert examples use 4 and 6 November 2026 (24 hours, 2 m above
terrain, refined transitions). Requests reuse identical saved or active analyses;
a failed job can be retried. First acquisition still needs NASA connectivity and
may take minutes; use the existing cache/prewarm before a presentation.

The simplified view has a horizon/sky chart, current UTC time, sample slider,
play/pause and three playback speeds. Time advances over actual computed samples,
whose UTC gaps may differ. Text explains full/partial/blocked/boundary/unknown
Sun states and Earth-center visibility. Technical details start collapsed; sources,
full analysis and export remain accessible. Playback starts only when requested.

**Teaching scope:** this release demonstrates visibility at a single observer and
the effect of a 48-hour schedule change. It does not compute terrain-wide moving
shadows, a crater-rim/floor map, or certify permanent illumination/shadow from a
24-hour sample. Those broader visual ideas in S15 require a separately validated
surface shadow model. The R1 guided presets/cards and two-click/speed/pause
acceptance are implemented; the broader surface-shadow visualization is not claimed.

```text
GET /classroom                 # bounded lessons, sources, explanatory cards
POST /classroom/{lesson-id}     # reuse or queue one analysis, return job ID
```

## Verification and remaining release gates

263 passing tests: 169 backend, 75 ingestion, 19 frontend. Ruff, pip dependency
compatibility, TypeScript/production build and npm audit passed (zero npm findings).
The existing large lazy 3D bundle advisory and Starlette/AnyIO deprecation remain.
Python vulnerability scanning and physical-device testing are not claimed.

[Live evidence](verification/r1-release-2026-11-14.json) records exact CSV/frame
values, single/pair manifest validation, identical cached recalculation fingerprints,
classroom reuse, closest-candidate order and exclusion of unknown coverage.
Browser checks cover real downloads (including PNG), shared sample/cursor restoration,
A/B import, classroom playback and a 390-pixel mobile layout.

R2–R10 remain subsequent work. Task 6's human teammate planner/public-user acceptance
is still open; these checks do not close that gate or claim a deployment/merge.
