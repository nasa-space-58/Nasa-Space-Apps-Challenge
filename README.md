# Lunar Landing Site Navigator

Team 2gbram's NASA Space Apps Challenge project. The first MVP compares lunar site and date options by Sun/Earth visibility.

## Current status

Task 0 is complete: service setup, dependency locks, a clean install and the initial terrain/site sources were verified. A backend health endpoint, shared data models and an initial interface are available. Task 1 was implemented on this branch: RabbitMQ requests produce Sun/Earth data and a persistent LOLA terrain tile. Task 2 computes the terrain horizon, and Task 3 computes visibility intervals and duration metrics. Task 4 was also completed on this branch: a persistent analysis API, a scroll-driven 3D introduction with NASA models, site/date selection and a time-controlled interface for real results.

## Setup

Docker Desktop and Docker Compose are required. In the project root, copy `.env.example` to `.env` and set a local RabbitMQ username/password. Choose characters that are safe inside a URL.

```bash
cp .env.example .env
docker compose up --build
```

- Interface — http://localhost:5173
- API documentation — http://localhost:8000/docs
- Health check — http://localhost:8000/health
- RabbitMQ management — http://localhost:15672

The worker consumes the durable RabbitMQ queue; it acknowledges a request after validating the files and publishing the result message. The backend reloads on source changes. After worker changes, use `docker compose restart data-ingestion`.

## Checks

```bash
docker compose --env-file .env.example config --quiet
docker compose run --rm backend pytest
docker compose run --rm data-ingestion pytest
docker compose run --rm frontend npm test
docker compose run --rm frontend npm run build
docker compose run --rm backend ruff check app tests /workspace/shared
docker compose run --rm data-ingestion ruff check app tests
```

Direct dependencies are pinned. The frontend uses Vite 7.3.8 and Vitest 4.1.12; the lock file was updated and verified with `npm ci`. In the 14 November 2026 check, `npm audit` reported zero vulnerabilities; at the end of Task 4, 192 tests (112 backend, 75 data ingestion, 5 interface), the Python lint checks and the frontend build passed. Python transitive dependencies are locked with versions and SHA-256 hashes in the `requirements.lock` files; the Docker install uses `--require-hashes` and `pip check`. [Updating the locks](docs/python-dependencies.md).

```bash
docker compose run --rm --no-deps frontend npm ci
```

On 14 November 2026, an uncached Docker build, startup with new data volumes and 70 tests were verified in a separate, clean Git clone. [Clean install report](docs/clean-install-verification.md).

## Downloading Horizons data

```bash
docker compose --env-file .env.example run --rm --no-deps data-ingestion \
  python -m app.download_horizons examples/horizons-reference.json
```

At the reference site, 25 hourly samples were downloaded for 14–15 November 2026; a repeated run used the cache. Raw responses, provenance and the matched Sun/Earth data are stored in the shared `/data/horizons/` directory. The standalone Horizons command can be used on its own; the end-to-end queue command below also prepares the terrain. [Details and verification](docs/horizons-ingestion.md).

The initial comparison sites are sourced as the Mons Mouton center and Malapert Massif R23. Data coverage was verified in the DEM windows around both sites. They are not safe-landing-site certifications; in-app site selection, the terrain horizon computation and the visibility analysis were added in later tasks. [Terrain and coordinate sources](docs/terrain-and-sites.md).

## Task 1 — Data preparation through the queue

To start a separate Docker project without touching another branch's running environment:

```bash
docker compose -p nasa-task1 --env-file .env.example \
  -f docker-compose.yml -f docker-compose.isolated.yml up -d --build --wait backend data-ingestion

docker compose -p nasa-task1 --env-file .env.example \
  -f docker-compose.yml -f docker-compose.isolated.yml exec -T backend \
  python -m app.submit_ingestion /workspace/shared/examples/ingestion-malapert.json
```

The first request downloads the roughly 189 MB NASA terrain source. Reusing the same request ID and parameters returns the verified result; a new scenario needs a new UUID. Data is kept in the project's persistent `lunar-data` volume. `down --volumes` deletes this data; use plain `down` to keep it. The isolated configuration exposes no host ports.

For Malapert, 25 time samples and a 501 × 501 terrain tile were verified through the real queue flow. A pending request survived a RabbitMQ restart. [Flow, failure behavior and evidence](docs/ingestion-pipeline.md).

This work is pushed only to the `feature/task1-ingestion-cache` branch; it is not merged into `tahakrcc` or `main`.

## Plan and data contracts

- [MVP and phased task list](docs/mvp-roadmap.md)
- [Initial assumptions](docs/assumptions.md)
- [Technologies](docs/technologies.md)
- [Coding conventions](docs/coding-conventions.md)

Graphify output is kept locally under `graphify-out/` and is ignored by Git.

## Task 2: Terrain horizon

Task 2 computes a 360° horizon profile, including lunar curvature and observer
height, from the full cached NASA terrain file. The backend uses the data read-only.
Missing azimuths are reported as `null`; terrain beyond the search distance is not evaluated.

```bash
docker compose --env-file .env.example build backend
docker compose --env-file .env.example run --rm --no-deps backend \
  python -m app.compute_horizon \
  terrain/processed/2d16e4a91f72903dc821c84342efe7350bb308b479b41e2b7a6ae62669ecbd29 \
  > horizon.json
```

The reference in the example belongs to Task 1's Malapert request; if yours differs, use
the `terrain_path` value from your own `ready` response. The defaults are a 1° azimuth step, a 100 km
search distance and 40 m sampling far away; the first 1 km is sampled every meter.
The 80 m terrain resolution does not change. This profile does not yet produce Sun/Earth visibility durations
or a safe-landing decision. Visibility is in Task 3's scope and the interface in Task 4's.

[Computation details, limitations and verification](docs/terrain-horizon.md).
Task 2 is pushed only to the `feature/task2-terrain-horizon` branch;
the user performs the merge into `tahakrcc` and `main`.

## Task 3: Visibility and duration metrics

Run it with the two references from Task 1's `ready` response:

```bash
docker compose --env-file .env.example run --rm --no-deps backend \
  python -m app.analyze_visibility TERRAIN_REFERENCE HORIZONS_REFERENCE --refine \
  > visibility.json
```

For the Sun, full/partial visibility and darkness are computed; for Earth, center visibility;
plus total duration/percentage, the longest outage, continuous visibility and the next setting.
Missing data is reported separately. `--refine` requests 5-minute and then 1-minute data
through RabbitMQ for the transitions found; the services must be
running. Without this option, only local data is read.

Durations are estimates based on the model between samples. The time bracket of each transition and
the terrain uncertainty are preserved in the result. Earth being visible is not a guarantee of a real
communication link. [Model, commands and verification](docs/visibility-metrics.md).
Task 3 is pushed only to the `feature/task3-visibility-metrics` branch;
the user performs the merge.


## Task 4: Analysis API and 3D interface

A new preview that does not touch the team's environment on port 5173:

```bash
docker compose -p nasa-task4 --env-file .env.example \
  -f docker-compose.yml -f docker-compose.preview.yml up -d --build --wait
```

- Interface: http://localhost:5174
- API: http://localhost:8001/docs
- `Plan your observation` goes straight to the analysis panel. `Run the Malapert example` starts an example analysis with real data.
- The scroll-driven introduction with NASA LRO and Apollo models is an illustrative animation; it is not a physical descent-trajectory computation.
- You choose a site/coordinates, the observer height above the terrain, a UTC start and a duration. The result shows the horizon, Sun/Earth positions, visibility intervals and the next setting.
- The time slider and playback step between samples. Refined samples are not evenly spaced in time.
- Jobs are kept in a separate `analysis-state` volume; the browser stores the last job ID. When the backend restarts, completed results are preserved and unfinished jobs are re-queued.
- A run with an initially empty data volume downloads terrain from NASA; a connection is required. The backend does not write to the data volume; downloads go to the ingestion service through RabbitMQ.

[API contracts, sources and usage](docs/analysis-api-and-interface.md) · [Task 4 verification report](docs/verification/task4-interface-2026-11-14.json).

Task 4 is pushed only to the `feature/task4-mission-interface` branch; merging into tahakrcc/main is up to the user. Comparing two scenarios is in Task 5's scope.


## Task 5: A/B site and date comparison

Keep the current result as A with the **Add comparison** button below the single-site analysis. For B, choose a different date at the same place or a different site on the same date. **Use +48-hour delay** sets up the ready-made delay example; **Run scenario B** starts a real analysis.

Shared duration/height/computation settings are kept. Differences in sunlight, longest darkness and Earth visibility are shown as B−A. The shared time control states both UTC dates and the times of the actually computed samples explicitly. It is used side by side on desktop and with A/B tabs on mobile. The B draft and job ID are stored in the browser; A does not change.

`POST /comparisons` takes two completed job IDs. It rejects incompatible conditions and does not interpret missing coverage as zero visibility. In the Task 5 verification, **212 tests** (128 backend, 75 ingestion, 9 interface), lint, the build and npm audit passed.

[Usage, API and model limits](docs/scenario-comparison.md) · [Real-data verification](docs/verification/task5-comparison-2026-11-14.json).

This work is pushed only to the `feature/task5-scenario-comparison` branch; it is not merged into tahakrcc or main.


## Task 6: Verification and demo preparation

The technical checks are complete: **222 tests** (136 backend, 75 ingestion, 11 interface), an uncached Docker install, a real first-download/cache comparison, failure/recovery and the production interface were checked.

Fixed: the ready-made example starting with an empty date field, saved analysis inputs returning to the form after a page reload, and a bounded retry for a backend connection that starts before RabbitMQ is ready.

In this session, the first 24-hour analysis took about **268 s**, the same analysis from cache **4 s**, and a refined 30-day analysis **58 s**. These are local measurements; prepare the examples before presenting.

[Verification report](docs/mvp-verification.md) · [Demo flow and teammate checklist](docs/demo-runbook.md).

**The teammate's usability review is still pending; the overall MVP release gate is not considered closed.** Task 6 is pushed only to the `feature/task6-demo-verification` branch; it is not merged into tahakrcc/main.

## Task 7: Searching for suitable start times

Choose **Time search** in the top menu. Enter the site, the UTC start range, the mission duration and
the hour spacing between candidates; then the minimum sunlight percentage, the maximum
continuous darkness and the minimum Earth visibility. At most 16 candidates are computed.

The conditions each candidate meets/fails are shown; missing data never counts as a suitable result.
**Open analysis** opens the result, **Use as comparison A** makes it the comparison's A
scenario. Search progress is preserved across restarts.

The ready-made sunlight example explicitly lowers the Earth requirement to 0 hours; among the 4–6 November 2026 Malapert
starts, only 6 November meets the sunlight conditions. With a 4-hour Earth requirement,
none of these three candidates is suitable. Not every moment in between is scanned; short opportunities may be missed.

**243 tests**, Ruff, the build and npm audit passed. [Usage and API](docs/window-search.md).
Task 7 is pushed only to the `feature/task7-window-search` branch; it is not merged into tahakrcc/main.

## R1: Exports, sharing and classroom mode

- From the **Export / Share** panel next to the results you can download CSV, a chart PNG, a printable HTML report and a scenario JSON file. Open the HTML report and choose **Print / Save as PDF** in the browser.
- A link opens the saved analysis and selected time on the same server; A/B comparisons can be shared too. A `localhost` link does not give another computer access to your server.
- **Mission control → Import a shared scenario file** loads the inputs and the expected data/model identifiers. **Calculate imported scenario** recomputes; if the result does not match the old fingerprint, a clear warning is shown. The JSON file does not contain the NASA data cache.
- When a time search has no suitable result, the three closest evaluable candidates and the shortfall/excess for each condition are shown. Candidates with missing data are not ranked.
- **Classroom → Start lesson** opens the Malapert example in two clicks. It has the 4 and 6 November examples, a speed selector, play/pause, a time slider and explanation cards. This mode teaches one observer's horizon/visibility; it does not perform a surface-wide moving-shadow simulation or permanent-shadow verification.

**263 tests** and the build checks passed. [R1 usage, API and scope](docs/release-r1.md).
Pushed only to the `feature/r1-exports-sharing-classroom` branch; merging into tahakrcc/main is up to the user. Teammate MVP acceptance is still pending.

## R2: Direct-to-Earth communication with DSN ground stations

The **Direct-to-Earth communication** panel in mission control shows, for a saved analysis, when direct communication with Earth is geometrically possible. A window requires two conditions at the same time:

- The center of Earth must be above the terrain horizon at the landing site.
- The Moon must be above the elevation limit at at least one NASA DSN station (Goldstone DSS-14, Canberra DSS-43, Madrid DSS-63).

The default limit is 6° (receive); the alternative is 10° (low-power transmit). Station coordinates come from NASA DSN 810-005 module 301 Rev. P, and Moon positions from JPL Horizons. Station horizon masks, scheduling and the link budget are not modeled; the result is not a link guarantee. [Sources, method and API](docs/ground-station-communication.md).

## R3: Explained landing-site recommendations

The **Recommend** section in the navigation ranks 2–6 candidate sites over the same UTC time range. The criteria are sunlight, longest darkness, DSN communication (R2) and terrain safety. Each rank's criterion contributions and a short explanation are shown. For every eliminated site, it states which hard condition failed and by what value. Sites with missing data are not ranked and are shown separately.

When the weight sliders change, the ranking is recomputed without rerunning the analyses; the old and new rankings are shown side by side. When the schedule shifts (for example by 48 hours), the same candidates are re-ranked for the new date and the best alternative is recommended.

Slope is computed from the cached 80 m LOLA data with Horn's method. The default 10° landing-area limit is a planning assumption; NASA has not published a numeric value. The 80 m resolution cannot see small craters and boulders; fuel and science value are not modeled yet. [Method, sources and API](docs/recommendations.md).

## R4: Descent trajectory

The **Descent** section in the navigation computes the descent trajectory from lunar orbit to touchdown for the selected landing site. The phases are the descent orbit insertion (DOI), coast, braking, approach and terminal descent. Each phase's duration, Δv and propellant are shown; instantaneous values can be read by hovering over the altitude–range chart.

The default vehicle is the Apollo 11 Lunar Module. The values come from NASA TN D-6846 and the Apollo 11 mission report. The model matches the total Δv of the Apollo 11 descent plan (6775 fps) within 2%. If the orbit does not pass over the landing site, the required plane change is reported; if propellant runs out or touchdown tolerances are exceeded, the site is marked "unreachable" and the reason is given.

Terrain obstacles, the Moon's mass concentrations (mascons) and its rotation are not modeled. The visualization is 2D. [Sources, model and verification](docs/descent-trajectories.md).

## R5: Fuel optimization across routes

**Descent → Optimize routes** generates 27 routes for the same landing site. The routes vary along three variables: the starting orbit (40/60/80 nautical miles), the approach duration (60/100/140 s) and the terminal descent duration (60/108/150 s). The propellant each route uses from the descent orbit insertion (DOI) to touchdown is computed.

The default touchdown reserve is 145 fps. This is the sum of the landing-site redesignation (60 fps) and manual hover (85 fps) allowances in NASA TN D-6846 Table II. Routes that exceed the reserve, the propellant or the R4 limits are eliminated together with the reason. The chart shows propellant used against inspection time (approach + terminal descent); the Pareto front and the lowest-propellant route are marked.

If the "include descent" option is enabled in the **Recommend** section, the R4 descent is computed for each candidate. Unreachable sites are eliminated; the propellant margin is added to the ranking as a fifth criterion. With the option off, the R3 results do not change. [Method, sources and verification](docs/fuel-optimization.md).

## R11: Multi-mission campaign planning

The **Campaign** tab in mission control plans several missions together. The missions share landing sites, time and ground-station capacity. Each mission's type, priority, deadline, dependencies, energy and data needs, and cancel/defer rules are defined.

Landing options are generated from terrain slope, sunlight, descent propellant and vehicle evidence; the reason is given for every eliminated time. The planner (HiGHS MILP) chooses an option, a deferral or a cancellation for each mission and schedules every downlink slot. Station visibility, calendar, capacity, data rate and exclusive-link needs are separate inputs. Relay support counts only when relay equipment, line-of-sight evidence and range are provided.

Every plan is rechecked by a validator independent of the solver. The shared timeline shows why the best options the missions would choose on their own conflict when combined, and the rationale for every decision. If no feasible plan exists, the smallest set of missions that cannot be completed together is explained.

The example campaigns are synthetic; they are not NASA mission plans or DSN bookings. [Method, results and limits](docs/multi-mission-planning.md).

## H0: Shared data foundation for hybrid planning

Jupyter notebook: [`notebooks/H0_shared_contracts.ipynb`](notebooks/H0_shared_contracts.ipynb). It runs the shared models, five example scenarios, data validation and input fingerprint checks cell by cell. Setup and field descriptions: [H0 contracts](docs/hybrid-contracts.md). No quantum solver runs at this stage; the classical reference is developed in H1 and the QUBO/QAOA experiment in H2.


## H1: Classical multi-mission planning

[`notebooks/H1_classical_planning.ipynb`](notebooks/H1_classical_planning.ipynb) compares independent choices with joint planning using the same Jupyter kernel. It evaluates 64 combinations for three missions and four options and explains capacity, time and energy violations. In the single-channel example, it selects the plan with a raw science score of 26 among 24 valid plans. Missing information, infeasibility and an interrupted search are shown separately.

[Method, limits and tests](docs/classical-planning.md). This stage is a static classical reference; it does not run a quantum circuit or a real landing-safety assessment. Hybrid development continues on the `codex/quantum-hybrid` branch.


## H2: Small quantum simulator experiment

[`notebooks/H2_quantum_mapping.ipynb`](notebooks/H2_quantum_mapping.ipynb) validates the 12-decision-variable QUBO model against the classical reference and tries the same three-mission problem on a 6-qubit QAOA simulator. A single layer with a low gate count is the default; it compares the result and the gate cost with two layers. Raw invalid samples are not hidden and no classical repair is applied.

[Method, measurements and limits](docs/quantum-mapping.md). It does not submit jobs to a real quantum service; this small example is already easily solved classically. A higher per-sample probability in the simulator does not mean a speed or cost advantage over the classical solver.


## H3: Communication scheduling and energy integration

[`notebooks/H3_resource_integration.ipynb`](notebooks/H3_resource_integration.ipynb) separates geometric visibility from station allocation and evaluates activity consumption together with initial energy, capacity, reserve and generation. Generation that cannot be stored in a full battery, and energy that arrives later, are never wrongly counted as available. In the supported example, the classical and quantum solvers use the same input; constraints that do not fit the quantum encoding are reported explicitly.

[Assumptions, method and verification](docs/resource-integration.md). The notebook data is synthetic; it is not a real DSN allocation, a radio-link guarantee or a physical battery verification.

## H8 — Classical and quantum comparison

The **Solver arena** tab in mission control (`#/solver-arena`) compares classical exhaustive search with a local QAOA simulation on the same example scenario. You can replay the real computation steps, inspect raw measurements and validated plans, and download the results as JSON. H7's archived Rigetti experiment is shown separately; no new quantum job is submitted and no credit is spent.

- Description and limits: [H8 solver arena](docs/solver-arena.md)
- Executed notebook: [H8_solver_arena.ipynb](notebooks/H8_solver_arena.ipynb)

### H9 — Three missions, one shared antenna

H9 plans how one crewed and two robotic example missions share a single antenna. It shows, on the same timeline, the conflict between independent choices, the joint classical/QAOA plan and the effect of pinning the crewed mission's choice. Sourced visibility data is kept separate from the example capacity/priority assumptions; the result is not a NASA mission plan or a landing/energy safety approval.

- Interface: **Shared antenna** in mission control, `#/shared-antenna`.
- [Scope, sources and verification](docs/shared-antenna-campaign.md)
- [H9 Jupyter notebook](notebooks/H9_shared_antenna.ipynb): Run All does not submit a new quantum job or spend credit.


### H10–H11: Scaling and relay orbit selection

H10 measures exhaustive-search and MILP times on the same example problems and separates estimates from measurements. H11 computes JPL orbits classically and compares one/two-relay selection with a local QAOA. The model's frame, sampling and communication limits are stated on screen. These stages do not submit real quantum jobs or spend credit.

- [H10 measurements and verification](docs/problem-scaling.md), [notebook](notebooks/H10_problem_scaling.ipynb), interface: `#/scaling`.
- [H11 sources and results](docs/relay-orbit-selection.md), [notebook](notebooks/H11_relay_orbits.ipynb), interface: `#/relays`.
