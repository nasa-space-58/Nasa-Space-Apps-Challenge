# Task 4 — Analysis API and single-site interface

Implemented on `feature/task4-mission-interface`, based on Task 3 (`f25d945`). Only this branch is delivered; integration into tahakrcc/main is manual. The non-UI skeleton is `c4f13bc`; frozen API tests are `183d277`. Earlier scientific tests remain unchanged.

## Run and use

```bash
docker compose -p nasa-task4 --env-file .env.example \
  -f docker-compose.yml -f docker-compose.preview.yml up -d --build --wait
```

Open http://localhost:5174 and use **Plan your observation** to skip the introduction. **Run the Malapert example** selects 2026-11-04, 24 hours, 2 m above terrain, with transition refinement. The first fresh-volume request downloads the approximately 189 MB terrain product through the ingestion worker. Later requests verify/reuse cached files. Network or source failures are explicit failed jobs; no simulated science replaces them.

Default Compose still uses 5173/8000. The preview override uses a separate project, volumes and ports 5174/8001, and publishes no RabbitMQ port. Existing named frontend dependency volumes need `run --rm --no-deps frontend npm ci` after a lock update. Keep named volumes when stopping: `down` preserves data, `down --volumes` deletes it.

## API

The browser uses `/api/*`, proxied by Vite to FastAPI. Direct API docs: http://localhost:8001/docs.

| Method / endpoint | Behavior |
|---|---|
| GET `/health` | Existing liveness response |
| GET `/presets` | Sourced initial coordinates, conventions and height assumptions |
| POST `/observers/resolve` | Resolve requested AGL meters to reference-sphere altitude km from a verified native terrain pixel; 409 if cache absent, 422 for invalid coordinates/data |
| POST `/analyses` | Validate and persist request, return 202 plus UUID/status; 422 invalid input, 429 when eight jobs are active |
| GET `/analyses/{id}` | Status, submitted parameters, creation UTC and error; 404 unknown ID |
| GET `/analyses/{id}/result` | Analysis, horizon and playback frames for ready/insufficient data; 409 while unavailable |

Example POST body:

```json
{
  "site_id": "malapert-massif-r23",
  "latitude_deg": -85.9948,
  "longitude_deg_east": 359.76422,
  "observer_height_m": 2,
  "start_utc": "2026-11-04T00:00:00Z",
  "end_utc": "2026-11-05T00:00:00Z",
  "step_s": 3600,
  "refine": true
}
```

Coordinates use MOON_ME, latitude −90…−80°, east longitude [0,360). UTC interval must satisfy 2026-01-01 ≤ start < end ≤ 2028-01-01 and last at most 31 days. Shared sample/work limits remain enforced. Optional `horizon` uses the existing HorizonSettings contract (default 100 km search, 1° azimuth, 40 m radial step). The UI offers 24 h, 3 d, 7 d and 30 d intervals; invalid end dates are rejected by the API.

`observer_height_m` is height above the containing 80 m terrain pixel, not altitude above the reference sphere. Backend verifies terrain SHA-256, product/grid, coverage and finite height before constructing the Horizons observer. On an empty cache, a fixed documented baseline request obtains the raw terrain through RabbitMQ, then the actual observer is resolved and its own ephemerides requested. Baseline ephemerides never substitute for the submitted site.

## Persistence and processing

Job states: pending → preparing → analyzing/refining → ready, insufficient_data or failed. SQLite stores parameters, status, errors and complete JSON results in the separate `analysis-state` volume (`STATE_DIR=/state`). The acquired `/data` volume stays read-only in the backend. Downloads/writes remain exclusively in ingestion.

One in-process worker runs scientific work off the API event loop. Restart requeues interrupted phases, with stable ingestion UUIDs and existing cache validation making replay safe. Completed results persist. The browser saves the latest job ID in localStorage and resumes polling after reload. This is a bounded local single-process service: do not configure multiple Uvicorn workers or replicas against this database. Authentication, cancellation, retention policies and distributed job scheduling are outside Task 4. Host ports bind loopback.

## Interface and interpretation

- Scroll introduction: NASA LRO model, textured Moon, illustrative Apollo approach. Direct navigation to mission control is always available. This is neither a physical trajectory nor a depiction of a historical south-pole Apollo landing.
- South-pole schematic coordinate map, two sourced presets, manual coordinates, AGL height and UTC inputs. The map is a coordinate selector; it does not claim local terrain-image detail.
- Real horizon profile and Sun/Earth angles; display bounds include the supplied samples. Scientific classifications and event estimates are prepared in Python, not recomputed in React.
- Playback/pause/keyboard slider updates UTC, positions, timeline cursor and next-setting events. Refined time samples are irregular: playback advances one sample per tick, not at constant simulated speed.
- Full/partial/blocked/unknown labels accompany colors. Totals refer to the entire analysis interval, not the selected sample. Unknown coverage remains explicit; it is not counted as visibility.
- Sources, model assumptions, units, transition brackets and sensitivity limits are visible. Earth visibility means the center, not a ground station or a usable communication link. No eclipse, power, hazard or landing-safety certification is implied.
- Lazy-loaded 3D scene, capped pixel ratio, paused offscreen rendering, reduced-motion support, WebGL error fallback, responsive controls and a keyboard-trapped sources dialog.

## NASA assets

Small display assets are versioned under `frontend/public/nasa/`; source pages, credits, byte sizes and SHA-256 hashes are in `manifest.json`. Scientific terrain and ephemeris datasets remain outside Git.

- [LRO](https://science.nasa.gov/3d-resources/lunar-reconnaissance-orbiter-a/): NASA / Christopher R. Meaney.
- [Apollo Lunar Module](https://science.nasa.gov/3d-resources/apollo-lunar-module/): NASA / Michael D. Carbajal.
- [CGI Moon Kit](https://svs.gsfc.nasa.gov/4720/): NASA/Goddard/SVS, Ernie Wright. The 2K color image textures the illustrative sphere; no scientific heights derive from it.
- The supplied [Apollo 11 Moon view](https://science.nasa.gov/3d-resources/apollo-11-view-of-the-moon/) is a photograph, not a 3D model, and is not included.

Draco WASM decoding files come from the locked Three.js 0.187.0 package; its Apache-2.0 license is retained under `public/draco/`. Model loading uses [Three.js DRACOLoader](https://threejs.org/docs/#DRACOLoader). NASA attribution does not imply endorsement.

## Verification

See [machine-readable local evidence](verification/task4-interface-2026-11-14.json). A real UI-submitted Malapert case returned 39 frames, 360 horizon directions and a 60 s sunset bracket. Result bytes stayed identical after backend restart. A 300 km coverage probe returned insufficient_data with 24 h unknown Earth coverage. Tests cover validation, queue capacity, persisted recovery, source verification, failure and result delivery; UI tests cover request submission, error recovery, focus and playback against a real response excerpt.

Desktop 1440×1000 and mobile 390×844 were checked in Chromium, including actual model rendering, scrolling, playback, source dialog keyboard navigation, reduced motion and WebGL fallback. No page runtime errors remained. A Three.Clock deprecation warning originates in React Three Fiber; the lazy 3D bundle also triggers Vite's size advisory. Multi-device performance and teammate review remain release-gate work in Task 6.
