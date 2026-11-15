# Mission visuals — 2026-11-15

- Recommendation candidates and the classical descent grid now share their API assessments with the mission scene. Feasible, rejected, failed and insufficient-data sites have distinct colors; selecting a candidate shows its score (when available), reasons and metrics. Approach corridors are explicitly illustrative, not solver trajectories.
- The surface panel keeps API routes and provides an explicit crater-terrain demo (also available when the API fails). A* and sampled-cost A* use the displayed crater height field; sampled costs are not quantum computation. The demo randomizes reachable targets and labels its illustrative distance scale.
- Other surface routes remain visible in teal. The selected route is gold. Antenna sweep and illustrative telemetry pulses follow playback; wheels and body follow the ground. The existing GLB assets were not modified. The current rover GLB contains named mechanical parts, no skin or animation clips; skeleton-safe cloning also supports future rigged assets.
- Launch camera lens easing and scene fades were refined. The collapsed surface panel includes the target and playback state.

Validation: TypeScript + Vite build passed. Two Vitest tests verify reproducibility, variation, both route methods across 20 seeds, traversability, diagonal corner avoidance and correspondence between displayed heights and the crater field. Browser verification on localhost:5174 used the real preview API: the default descent optimization returned 27 feasible candidates; a 1600 fps reserve exercise produced rejected candidates, including r01 with a −27.7 kg reserve margin and its backend explanation. No rendering exceptions were observed. The old preview API rejects the optional scene fields with HTTP 422; the client now retries only explicitly unsupported scene fields, preserving its real route calculation. The loaded GLB rover and DEM-backed demo were visually inspected.

## Local preview

The existing `nasa-task4` frontend on port 5174 now mounts this checkout's frontend. Backend and ingestion were subsequently rebuilt from this checkout as well (see backend update below). Existing named data/state volumes were preserved. To recreate just that frontend from the original preview directory, set `NASA_UI_FRONTEND` to this checkout's absolute `frontend` path and add this checkout's `docker-compose.ui-preview.yml` after the original `docker-compose.yml` and `docker-compose.preview.yml`, then run `up -d --no-deps frontend` with project name `nasa-task4` and the original environment file.

No Git push or public deployment was performed.

## Scan regression fix

Legacy API route responses without a scene now automatically activate the labelled crater-aware demo instead of draping an unrelated API route over illustrative terrain. Returning to API routes requires a scene payload. Hazard cells remain red and readable after the scan finishes. A component regression test checks that the legacy response never emits a rover state without a scan map and that scan playback starts from zero. Three tests and the production build passed; the persistent hazard overlay was visually checked on port 5174.

## Backend and queue update

The `nasa-task4` backend and ingestion service now run the current checkout, rebuilt with the locked dependencies, using the existing RabbitMQ, lunar-data and analysis-state volumes. Port 5174 and API port 8001 are unchanged. Use this checkout’s compose files and the existing preview environment file with project `nasa-task4`; the UI override is optional when all services use this checkout.

The old ingestion process had no consumers for `lunar.flight-ephemeris.requests.v1` and `lunar.space-weather.requests.v1` (14 and 5 ready messages respectively). The updated process starts both consumers; both request queues drained to zero with one consumer each. Ingestion and DSN request queues also have consumers and no pending messages. Historical event/audit queues were not purged.

The user's analysis `e2126ddf-3cdd-4108-8e9d-f8670f011325` was already ready when checked; it was not permanently stuck. Its preserved 30-day result contains 1,005 frames, 37.6% Sun visibility, 38.5% Earth visibility and a longest darkness interval of 176.0 hours. The user's open Chrome page was refreshed and these results were verified in the Results tab.

A live `/surface/routes` request with `include_scene: true` now returns a 99×82 LOLA grid at 80 m resolution with a 15° slope limit. North rim (32 path cells) and East slope (39) are reachable; South is unreachable. Sky evaluation is enabled using the completed analysis. The user's browser shows North rim, the terrain scan legend and real waypoint Sun/Earth states, without automatically selecting Demo. JPL flight readouts and current NOAA weather also loaded.

Validation after update: 66 backend surface/analysis tests and 2 ingestion lifecycle tests passed. No analysis/result volumes were deleted, no historical failed jobs were relabelled, and no public deployment or Git push was performed.
