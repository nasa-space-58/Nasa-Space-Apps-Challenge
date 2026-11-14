# Direct-to-Earth communication through DSN stations — R2 (scenario S4)

Start from tahakrcc `a37e83c`; developed on `feature/r2-ground-station-communication`.
Skeleton: `12d272c`. Frozen test baseline: `233a9b8` (unchanged; `git diff 233a9b8 -- backend/tests data-ingestion/tests` is empty).
Implementation: `3b04ad3`. Interface: `53cae9f`.

## What it answers

A direct-to-Earth (DTE) window needs two conditions at the same time:

1. The **Earth center is above the local terrain horizon** at the lunar site. This is the existing analysis result (Task 3).
2. The **Moon is above the elevation limit of at least one NASA Deep Space Network (DSN) antenna** on Earth.

The interface shows one band per station, one combined band (`ANY`) and a list of windows naming the covering stations. Results are **geometric line-of-sight estimates**, not a guaranteed radio link.

## User flow

Open or run an analysis in mission control. In **Direct-to-Earth communication**, choose the station limit and select **Check DSN communication**:

- **6°:** generic DSN receive limit (default).
- **10°:** generic low-power transmit limit.

The job runs in the background. Its ID is remembered in this browser per analysis, and the result persists in the `analysis-state` volume. The bands follow the playback cursor. **Recalculate windows** submits a new job with the selected limit.

## Sources

| Input | Source |
|---|---|
| Station coordinates | DSN Telecommunications Link Design Handbook 810-005, module 301 *Coverage and Geometry*, **Rev. P (2026-05-22)**, Table 5. Geodetic coordinates with respect to the WGS84 ellipsoid. Stated accuracy ±0.001″ and ±3 cm (1σ). [301P.pdf](https://deepspace.jpl.nasa.gov/dsndocs/810-005/301/301P.pdf) |
| Elevation limits | Same module, §2.2.3: generic limits of 6.0° receive, 10.0° low-power transmit and 13.5° high-power transmit. §2.2.2 gives a mechanical limit of about 6° for az-el antennas. |
| Moon position from each station | JPL Horizons API: `COMMAND='301'`, `CENTER=coord@399`, `COORD_TYPE=GEODETIC`, `QUANTITIES='4'`, `APPARENT=AIRLESS`, UTC, 5-minute steps (1-minute steps for one-minute analyses). Horizons reports the Earth site in ITRF93 with the 6378.137/6356.752 km ellipsoid. The worker verifies the echoed site within 1e-6° and 1e-5 km. |
| Earth visibility at the lunar site | Existing Task 3 intervals (Earth center vs. LOLA terrain horizon). |

The modeled stations are the three 70 m antennas:

| Station | Complex | Latitude | East longitude | WGS84 height |
|---|---|---|---|---|
| DSS-14 | Goldstone | 35°25′33.24312″ | 243°06′37.66244″ | 1001.390 m |
| DSS-43 | Canberra | −35°24′08.72724″ | 148°58′52.56231″ | 688.867 m |
| DSS-63 | Madrid | 40°25′52.35510″ | 355°45′07.16924″ | 864.816 m |

The 34 m antennas at each complex are within a few kilometres of these stations; the module notes that visibility differences within a complex are insignificant. Runtime data: `shared/dsn_stations.json`.

## Method

- **Station state:** Moon elevation is linearly interpolated between Horizons samples. The crossing of the limit splits a sample interval; reaching the limit counts as *above*. A sample with an unavailable elevation makes its adjacent intervals *unknown*.
- **Per station:** *available* only when Earth is visible and the station is above its limit. *Unavailable* when either condition is definitively false (an Earth state of `boundary` counts as not visible). Otherwise *unknown*.
- **Combined:** *available* when any station is available, listing the stations. *Unknown* when none is available but some are unknown. Otherwise *unavailable*.
- **Metrics:** available, unavailable and unknown durations, available percentage, window count and longest window. The window count and longest window use contiguous available time even when the covering station changes.
- **Partial results:** a job with unknown combined time finishes as `insufficient_data`.

Reference check: the 5-minute crossings for DSS-14 (rising), DSS-43 (setting) and DSS-63 (rising) through 6° on 2026-11-04 agree with 1-minute Horizons data to better than 0.1 s. The frozen tests use a 10 s tolerance.

## Architecture

- `shared/dsn.py`: station loading and the `DsnRequest`/`DsnEvent` JSON contracts. There is a separate durable queue `lunar.dsn.requests.v1` with dead-lettering to `lunar.dsn.dead.v1`. Events use the existing event exchange. Existing ingestion messages and caches are unchanged.
- `data-ingestion/app/dsn.py`: the worker downloads one Horizons table per station into an atomic, checksummed `dsn/<sha256>` bundle (raw responses, `samples.json`, manifest with sources). The cache key excludes the request UUID. Jobs under `dsn-jobs/<uuid>` are idempotent and reject reused UUIDs with other parameters. Outcomes, including failures, are stored per UUID.
- `backend/app/communication.py`: `CommunicationStore` (same SQLite database, at most 4 active jobs), `execute_communication` and the routes below. The backend never contacts NASA. It reads verified bundles from the read-only data volume.

### API

`POST /analyses/{analysis_id}/communication` with body `{"min_elevation_deg": 6.0}` (6 ≤ value ≤ 90) returns `202` with the job. Errors:

- `404`: the analysis is missing.
- `409`: the analysis is not `ready` or `insufficient_data`.
- `422`: invalid limit.
- `429`: the queue is full.

`GET /communications/{id}` returns `{id, analysis_id, status, request, result, error}`.

- **Statuses:** `pending`, `acquiring`, `computing`, `ready`, `insufficient_data`, `failed`.
- **Result fields:** limit, UTC window, station metadata, `windows.stations[id]` and `windows.combined` (intervals and metrics), sources and limitations.

## Limitations (shown in the interface)

- Geometric line of sight only; not a guaranteed radio link.
- Generic elevation limit. The station-specific horizon masks (810-005 §2.2.4) are **not** applied, so real low-elevation coverage can be shorter in some azimuths.
- The 70 m antenna keyhole near zenith (about 89°), station scheduling, maintenance, availability, antenna pointing, frequency band, transmitter power and link budget are not modeled. Operational planning requires DSN scheduling and a link-budget analysis (810-005 modules 101 onward).
- Earth visibility refers to the Earth **center** above the lunar terrain horizon, with the terrain and sampling limits of the Task 3 analysis.
- Interpolated station rise/set times carry the sampling uncertainty of the 5-minute grid. At lunar rates this is far below one minute, but it is not zero.

## Verification (2026-11-14, local Docker)

- **Tests:** backend 275, ingestion 112 and frontend 21. Ruff and the frontend production build pass.
- **Malapert R23, 2026-11-04, 24 h:** Earth is terrain-blocked all day, so there are 0 communication windows, as expected.
- **Mons Mouton, 2026-11-04, 24 h (Earth visible 00:00–06:14 UTC):**
  - At **6°** there is 1 continuous window, 6.2 h (26.0%). Canberra covers it first, then Canberra and Madrid together from 02:21:42, then Madrid only from 03:02:55. Goldstone sees the Moon only from 10:13:42, after Earthset.
  - At **10°** the same period splits into 2 windows (25.9%). There is a 2-minute Canberra-to-Madrid handover gap from 02:41:43 to 02:43:40.
