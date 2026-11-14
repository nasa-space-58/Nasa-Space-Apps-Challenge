# Solar activity: NOAA SWPC flares and proton storms

Developed on `feature/space-weather-noaa`.
Skeleton: `4861ec7`. Frozen test baseline: `be593c9` (unchanged; `git diff be593c9 -- backend/tests data-ingestion/tests` is empty).
Implementation: `6d5a4c4`. Source entry: [data-sources.md §12](data-sources.md#12-noaa-swpc-space-weather-json--).

## What it answers

For a saved analysis, the **Solar activity** panel answers two questions:

1. **What is the Sun doing now?** The panel shows the latest GOES X-ray flare class and the ≥10 MeV proton flux, each with its NOAA scale level and its peak over the last 24 hours.
2. **Does the SWPC forecast cover my analysis window, and what does it say?** The panel shows the daily C/M/X flare and proton event probabilities for every forecast day that overlaps the window.

It also lists the five most flare-prone numbered active regions from the newest SWPC region report.

**It does not predict flares.** SWPC gives probabilities for at most three UTC days. For a window outside those days, the panel says *No forecast* and shows only current conditions. Nothing is extrapolated.

## User flow

Open or run an analysis in mission control. In **Solar activity**, select **Check solar activity**. The job runs in the background. Its ID is remembered in this browser per analysis, and the result persists in the `analysis-state` volume. **Fetch a new snapshot** starts a new job, which downloads current data again.

## Sources

| Input | Product |
|---|---|
| Daily flare and proton probabilities | `https://services.swpc.noaa.gov/json/solar_probabilities.json` |
| GOES 0.1–0.8 nm X-ray flux (W/m²) | `https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json` |
| GOES ≥10 MeV integral proton flux (pfu) | `https://services.swpc.noaa.gov/json/goes/primary/integral-protons-1-day.json` |
| Active regions and per-region probabilities | `https://services.swpc.noaa.gov/json/solar_regions.json` |
| Scale thresholds | [NOAA Space Weather Scales](https://www.swpc.noaa.gov/noaa-scales-explanation) |

**Forecast day convention.** The `date` of a probability entry is its first forecast UTC day: `*_1_day` covers that day, and `*_2_day` and `*_3_day` cover the next two. This was checked against the SWPC 3-day forecast text issued on 2026 Nov 14 at 12:30 UTC. That text lists R1–R2 25 %, R3+ 5 % and S1+ 5 % for Nov 14–16, which matches the entry dated 2026-11-14 (M 25 %, X 5 %, proton 5 %). The newest entry is used.

## Method

- **Flare class:** from the 0.1–0.8 nm flux, using the lower bounds A 10⁻⁸, B 10⁻⁷, C 10⁻⁶, M 10⁻⁵ and X 10⁻⁴ W/m². The class is printed with one decimal and **floored**, so a value at a class boundary is never rounded up into the next class.
- **Radio blackout (R):** R1–R5 at M1, M5, X1, X10 and X20 (10⁻⁵, 5×10⁻⁵, 10⁻⁴, 10⁻³, 2×10⁻³ W/m²).
- **Solar radiation storm (S):** S1–S5 at 10, 10², 10³, 10⁴ and 10⁵ pfu at ≥10 MeV.
- **Current and peak:** *current* is the latest sample with a measured value; *peak* is the maximum over the 1-day product. A null GOES sample is a data gap, never zero.
- **Forecast overlap:** each SWPC day is a half-open UTC interval [00:00, 24:00). Coverage is
  - `full` when the whole analysis window lies inside the forecast days,
  - `partial` when only part of it does,
  - `none` otherwise.

  The `max_daily_*` values are the largest daily percentages among the overlapping days. They are **not** the probability of a flare over the whole window.
- **Active regions:** the newest report day, ranked by X, then M, then C probability (all descending), then region number. Unknown probabilities rank last.

## Architecture

- `shared/space_weather.py`: product list and the `SpaceWeatherRequest`/`SpaceWeatherEvent` JSON contracts. The durable queue is `lunar.space-weather.requests.v1`, with dead-lettering to `lunar.space-weather.dead.v1`. Events use the existing event exchange. Messages carry only a request UUID or a bundle reference.
- `data-ingestion/app/space_weather.py`: the worker fetches the four products and validates them. Validation covers explicit UTC timestamps, strictly increasing samples, finite non-negative flux, integer percentages from 0 to 100 and unique dates and regions; anything else fails loudly. The worker writes an atomic, checksummed `space-weather/<sha256>` bundle containing the raw responses, a normalized `snapshot.json` and a manifest with URLs, headers and the acquisition time. The key is content-addressed: identical content reuses the verified bundle, and a corrupt bundle raises and is never replaced. Jobs under `space-weather-jobs/<uuid>` are idempotent, and outcomes (including failures) are stored per UUID.
- `backend/app/space_weather.py`: `SpaceWeatherStore` (same SQLite database, at most 4 active jobs), `execute_space_weather`, the assessment and the routes below. The request UUID derives from the job ID, so a recovered job replays the worker's stored result. The backend never contacts NOAA. It reads verified bundles from the read-only data volume.
- `frontend/src/components/SpaceWeather.tsx`: display only.

### API

`POST /analyses/{analysis_id}/space-weather` (no body) returns `202` with the job. Errors:

- `404`: the analysis is missing.
- `409`: the analysis is not `ready` or `insufficient_data`.
- `429`: the queue is full.

`GET /space-weather/{id}` returns `{id, analysis_id, status, created, result, error}`.

- **Statuses:** `pending`, `acquiring`, `computing`, `ready`, `failed`. A window outside the forecast is still `ready`, with `forecast.coverage = "none"`.
- **Result fields:** `analysis_id`, `start_utc`, `end_utc`, `acquired_at_utc`, `current`, `forecast`, `active_regions`, `limitations` and `source`.

## Limitations (shown in the interface)

- Solar flares cannot be predicted with certainty. SWPC gives daily probabilities for at most 3 days.
- Analysis time outside the SWPC forecast days has no forecast. Nothing is extrapolated.
- Current conditions describe the Sun when the snapshot was fetched, not at the landing time.
- GOES measures near Earth. Proton flux is a proxy for the lunar surface, not a dose estimate.
- The radio blackout scale describes HF radio on Earth's sunlit side, not DSN links.
- The space weather result does not change the visibility metrics, recommendations or risk scores.

## Verification (2026-11-14, local Docker)

- **Tests:** backend 1041 (including 71 new) and ingestion 185 (including 73 new). Ruff passes. The frontend production build passes; per project rules there are no frontend tests.
- **Live end-to-end run** on an isolated stack: backend `submit_space_weather` → RabbitMQ → worker → live SWPC → bundle `space-weather/dd6547cd…`. `load_snapshot` and `assess_space_weather` then gave:
  - **Acquired:** 21:01:28 UTC.
  - **X-ray:** B9.4 now (R0), 24 h peak C4.4 at 06:06 UTC.
  - **Protons:** 0.25 pfu at ≥10 MeV (S0).
  - **Forecast:** the 2026-11-14 to 11-17 forecast gives a 12 h window starting now `full` coverage with M 25 % and X 5 %. The same window 5 days later gives `none`, with no values.
  - **Active regions:** 12 regions reported.
