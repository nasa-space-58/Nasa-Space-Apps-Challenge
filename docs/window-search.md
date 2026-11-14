# Observation time search — Task 7

Start from Task 6 (`16d59d0`); deliver only `feature/task7-window-search`.
Skeleton: `3ffe4d0`. Frozen backend test baseline: `20fc132` (unchanged).

## User flow

Open **Time search** in the navigation. Select a location, terrain-relative observer
height, first/last candidate start in UTC, mission duration and start spacing.
Set minimum sunlight percentage, maximum continuous darkness and minimum Earth
visibility hours. Each candidate runs the existing scientific analysis pipeline.

The sunlight example uses Malapert, 4–6 November 2026, a 24-hour mission and 24-hour
start spacing. It explicitly sets the Earth minimum to **0 h**; the default is 4 h.
With current data/model, only the November 6 start meets the example conditions.
With a 4-hour Earth minimum none of these three starts qualifies.

Results remain chronological, with actual values and pass/fail/unknown for every
condition. Filter confirmed matches, open an individual analysis, or use it as
comparison A. The candidate's actual inputs are restored in mission control.
The browser remembers the last search and submitted form. Backend progress and
results persist across reloads/restarts in the existing `analysis-state` volume.

## API and bounds

`POST /searches` returns 202 with the persistent search representation:

```json
{
  "template": {
    "site_id": "malapert-massif-r23",
    "latitude_deg": -85.9948,
    "longitude_deg_east": 359.76422,
    "observer_height_m": 2,
    "start_utc": "2026-11-04T00:00:00Z",
    "end_utc": "2026-11-05T00:00:00Z",
    "step_s": 3600,
    "refine": true
  },
  "last_start_utc": "2026-11-06T00:00:00Z",
  "start_step_hours": 24,
  "min_sunlight_percent": 70,
  "max_darkness_hours": 6,
  "min_earth_hours": 0
}
```

Poll `GET /searches/{id}`. Invalid bounds return 422, full search queue 429,
unknown identifier 404. The template accepts existing analysis/horizon settings.

- At most 16 candidate starts, at most 31 days between first and last start.
- Starts are first + n × spacing (integer 1–168 hours), up to and including last
  **only when aligned**. A nonaligned last bound is not an extra candidate.
- Every mission retains the template duration (at most 31 days), and must fit
  the existing 2026-01-01 through 2028-01-01 UTC analysis interval.
- At most two active searches, at most one pending/running analysis per search,
  sharing the existing eight-job active queue with individual analyses.
- Closing the page does not cancel jobs. Cancellation and retention controls are
  not included in this task. These are the existing local/demo API access rules.

Search states: pending, running, ready, partial, failed. Candidate states: waiting,
processing, match, not_met, unknown, failed. A search is partial when any candidate
has unknown coverage or fails; all failures produce failed. A ready search may
have **zero** matches: it means all candidates were evaluated, not all were suitable.

## Persistence and scientific interpretation

Search state and candidate job insertion share a SQLite transaction. Stable UUIDs
link each candidate to its analysis. On restart, the existing worker recovers
unfinished jobs; the search scheduler consumes terminal results and advances.
Data download remains in ingestion through RabbitMQ; backend mounts data read-only.

Only Python evaluates thresholds. Sunlight includes full and partial solar disk
visibility; darkness uses the longest blocked interval; Earth uses visible-center
hours. Any unknown duration for a target makes its conditions unknown. Unknown
candidates never count as matches, including with permissive zero thresholds.

This is a grid of candidate starts, **not a continuous feasible-window solver**.
Short opportunities between starts can be missed. Existing terrain, temporal
sampling and horizon-coverage limitations apply; no landing-safety or operational
communications guarantee is made. No ranking of “closest” failures is introduced:
every failure is shown with its unmet conditions. Export/share/classroom work in
R1 and human teammate acceptance from Task 6 remain outstanding.

## Verification

243 tests passed: 153 backend, 75 ingestion, 15 frontend. Ruff, Python dependency
checks, TypeScript/production build and npm audit passed (zero vulnerabilities).
The build retains the pre-existing large 3D chunk advisory and tests retain the
Starlette/AnyIO deprecation warning.

Real-data checks cover one match among three starts, no match when Earth ≥4 h,
an interrupted search resuming with unchanged candidate IDs, preservation of an
older completed search, and unknown coverage excluded even at permissive thresholds.
See [recorded evidence](verification/task7-search-2026-11-14.json).
