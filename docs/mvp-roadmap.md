# Lunar Landing Navigator — MVP and Implementation Roadmap

## Product goal

Help mission planners, educators and the public quickly compare lunar landing locations and dates by showing the Sun and Earth relative to the local terrain horizon. Make changes in sunlight availability and potential direct-to-Earth visibility understandable when a landing location or schedule changes.

This plan derives the first release from the NASA challenge description supplied by the team. The selected deliverables below are team implementation choices. NASA presents many of its suggested features as optional.

## Decisions and current status

- JPL Horizons supplies Sun and Earth observer ephemerides.
- LOLA elevation data supplies the terrain horizon, with explicit coordinate and height references.
- React/TypeScript displays results. Python performs scientific processing.
- The ingestion worker retrieves external data through HTTPX, communicates with the backend through RabbitMQ, and writes the shared data volume. The backend reads it.
- Taha leads implementation. Teammates review feature PRs according to coding-conventions.md.
- Step 0 service skeletons and contracts are implemented locally. Local container execution was verified on 2026-11-14; frontend and Python transitive dependency locks are verified, including a fresh-clone Docker run. See assumptions.md for selected defaults and remaining data checks.
- The full product scenarios remain in user-scenarios.md with release assignments. This document defines delivery order and MVP acceptance.

## First-release user journey

1. Choose a curated lunar site or enter coordinates within the supported terrain coverage.
2. Select a UTC landing date/time and analysis duration.
3. Inspect the local terrain horizon with the Sun and Earth positioned above or below it.
4. Scrub or play time and inspect sunlight, darkness and Earth-visibility intervals.
5. Read Sun elevation, sunlight duration, longest darkness, Earth-visible duration and time until the next sunset or Earthset.
6. Duplicate the scenario and change its location or landing date, such as a 48-hour delay.
7. Compare the two scenarios and read a plain-language explanation of the measured differences.

## MVP deliverables and acceptance

| Deliverable | Acceptance condition |
|---|---|
| Site selection | A sourced preset list, map selection and coordinate entry use the same coordinate convention. Unsupported terrain coverage is reported explicitly. |
| Flexible dates | Landing time and duration are editable in UTC. The same site at different dates and different sites at the same date can be compared. |
| Terrain-relative sky view | Sun/Earth azimuth and elevation are overlaid on a terrain horizon derived from LOLA. Displayed geometry and charts use the same results. |
| Time controls | Playback, pause and scrubbing update the visible timestamp, sky positions and metric cursor together. |
| Energy potential | Sun elevation, illuminated duration, longest darkness and partial solar visibility are shown with the observation interval and model assumptions. |
| Potential communication | Earth-center visibility intervals are clearly labeled with the ground-station and link-budget limitations. |
| Next setting event | The UI shows the next sunset/Earthset inside the computed interval. If there is no event or insufficient data, it shows that state explicitly. |
| Comparison | At least two scenarios show comparable metrics. Site comparisons share dates/settings. Date comparisons share duration/settings and clearly display their different UTC dates. |
| Accessibility for newcomers | One ready-to-open example, short term explanations, readable units and text labels alongside colors support public and educator use. |
| Traceability | Results identify sources, request parameters, acquisition time, terrain coverage/resolution and calculation assumptions. Missing intervals remain visible. |

A 3D map uses the existing React Three Fiber choice. The horizon plot and time-based comparison are the primary decision views. The first release reports energy potential through visibility and solar elevation. A power model in watts requires separately documented panel area, efficiency, orientation and illumination assumptions.

## Ordered MVP checklist

Follow the non-UI skeleton → frozen test commit → implementation workflow in coding-conventions.md for each applicable task. A completed task includes its relevant verification and a demonstrable result.

### Step 0 — Confirm inputs and development setup

- [x] Record supported south-pole terrain coverage, initial preset sites and coordinate sources.
- [x] Define default analysis duration, supported date range, sampling limits and observer height.
- [x] Record coordinate frames, vertical reference, units and UTC handling in shared data contracts.
- [x] Define the meaning of partial/full sunlight and the handling of boundary and unknown states.
- [x] Set up Graphify and generate the initial graph according to CLAUDE.md.
- [x] Create the four-service Docker Compose setup, health checks and .env.example.
- [x] Pin documented dependencies and provide container-based run, lint and test commands.

**Verification evidence (2026-11-14)** — A fresh local Git clone of `2707be2` passed no-cache Docker builds, startup with new volumes, 30 backend tests, 39 ingestion tests and 1 frontend test. Both Python Ruff checks and pip check, frontend npm ci, production build and npm audit (zero findings) passed. Python locks pin 22 backend and 23 ingestion packages with SHA-256 hashes; regeneration produced no diff. Backend/frontend returned HTTP 200 and the worker connected to RabbitMQ. See [clean-install evidence](clean-install-verification.md). Terrain product, coordinate/height references, two initial observers and two finite native-resolution DEM windows were verified against NASA/USGS sources; see [terrain and site evidence](terrain-and-sites.md). All seven Step 0 checklist items are complete. Operational terrain ingestion and horizon calculations remain subsequent steps.

### Step 1 — Obtain and cache real data

Depends on Step 0.

- [x] Define shared Pydantic ingestion request and ready/failed event schemas with request identifiers.
- [x] Implement durable queue processing, retry behavior and idempotent requests.
- [x] Query Horizons for Sun (`10`) and Earth (`399`) from a lunar surface observer (`coord@301`).
- [x] Preserve raw queries/responses and metadata, parse named CSV columns, and validate semantic errors even on HTTP 200.
- [x] Retrieve angular diameter for partial solar disk evaluation.
- [x] Download a bounded LOLA terrain product, retaining source/product identifiers and integrity information.
- [x] Keep raw and processed data separate and publish completed files atomically before ready events.
- [x] Cache by all result-affecting site, time, sampling, source and model parameters.
- [x] Verify a documented Horizons reference query and coordinate conventions.

**Completion evidence** — One selected site and interval have reproducible Sun/Earth data and terrain coverage. Retried requests leave consistent cached results.

Step 1 implementation and local verification completed on 2026-11-14 in `feature/task1-ingestion-cache`. A backend RabbitMQ request produced a 501 × 501 Malapert terrain crop and 25 Sun/Earth samples. Queued requests survived broker restart; replay used existing verified files with the job attempt count still one. See [pipeline details](ingestion-pipeline.md) and [live evidence](verification/task1-ingestion-2026-11-14.json). Peer review/branch integration remain separate; no merge to tahakrcc or main was performed.

### Step 2 — Compute the terrain horizon

Depends on Step 1.

- [x] Align DEM coordinates and height reference with the observer definition.
- [x] Compute a 360-degree horizon with lunar curvature and observer height.
- [x] Document azimuth spacing, search distance, terrain resolution and numerical approximations.
- [x] Handle azimuth wrap-around and missing/out-of-coverage terrain explicitly.
- [x] Verify reference geometry, boundary conditions and at least one real terrain profile.

**Completion evidence** — A site has a reproducible horizon profile with coverage and quality metadata.

Step 2 implementation and local verification completed on 2026-11-14 in `feature/task2-terrain-horizon`. The full cached source produces a bounded 360-degree Malapert horizon with explicit coverage and approximation metadata. Radius and sampling sensitivity, independent Cartesian checks and reproducible commands are documented in [terrain horizon](terrain-horizon.md). This does not certify global horizon accuracy or landing safety. Peer review and manual branch integration remain separate.

### Step 3 — Derive visibility and metrics

Depends on Step 2.

- [x] Compare Horizons Sun/Earth angles with the terrain horizon at each timestamp.
- [x] Calculate the documented partial/full solar disk conditions.
- [x] Refine transition intervals and retain known sampling limitations.
- [x] Produce sunlight, darkness and Earth-center visibility intervals.
- [x] Calculate total visible duration, percentage, longest gap and longest continuous window.
- [x] Calculate the next sunset and Earthset relative to the selected instant.
- [x] Preserve unknown durations and distinguish no event in range from a confirmed setting event.
- [x] Validate time windows, clipped analysis boundaries and polar cases against documented references.

**Completion evidence** — One site's analysis can be reproduced from cached data and exposes its uncertainty and coverage.

Step 3 implementation and local verification completed on 2026-11-14 in `feature/task3-visibility-metrics`. The November Malapert case has real queue-driven transition refinement and explicit temporal/spatial limitations. See [visibility metrics](visibility-metrics.md) and its reproducible verification report. Teammate review and manual merge remain separate.

### Step 4 — Deliver the analysis API and single-site experience

Depends on Step 3.

- [x] Define endpoints/contracts for presets, analysis requests, processing status and result retrieval.
- [x] Provide pending, ready, failed and insufficient-data responses.
- [x] Add site selection, coordinate input and UTC date/duration controls.
- [x] Render the lunar map, terrain horizon and Sun/Earth sky positions.
- [x] Add synchronized playback, pause and time scrubbing.
- [x] Show summary metrics, next setting events and source/model information.
- [x] Add loading/error/empty states, readable units, keyboard controls and text equivalents for color states.

**Completion evidence** — A user completes the first five steps of the MVP journey using real data through the normal interface.

Step 4 implementation and local verification completed on 2026-11-14 in `feature/task4-mission-interface`. Persistent API jobs, real-data playback, sourced presets and a NASA-model scroll introduction are available. See [API and interface details](analysis-api-and-interface.md) and [verification evidence](verification/task4-interface-2026-11-14.json). Site/date comparison remains Task 5; teammate review and manual integration remain separate.

### Step 5 — Compare locations and dates

Depends on Step 4.

- [x] Add a second scenario with its own site and landing time.
- [x] Support same-date site comparison and same-site date comparison.
- [x] Display aligned timelines with explicit UTC dates and shared analysis settings.
- [x] Show numerical differences in sunlight, darkness and Earth-visible duration.
- [x] Provide a 48-hour-delay example and explain which observed intervals change.
- [x] Provide an accessible preset and brief explanations of horizon, elevation and visibility.
- [x] Check that switching scenarios preserves the original scenario's parameters.

**Completion evidence** — A user can compare a baseline and a delayed landing and identify the changes from the interface.

Task 5 implementation and local verification completed on 2026-11-14 in `feature/task5-scenario-comparison`. Real same-site/date-delay and same-date/site comparisons, synchronized samples, unknown coverage and preserved scenarios are documented in [scenario comparison](scenario-comparison.md). Teammate review and manual integration remain separate.

### Step 6 — Verify and prepare the MVP demonstration

Depends on Step 5.

- [x] Run all required tests and lint checks in containers.
- [x] Test the complete user journey with supported coordinates and dates.
- [x] Exercise missing terrain, upstream failure, cached data and requests outside the supported interval.
- [x] Verify scientific reference cases and reproducibility metadata.
- [x] Compare with independent NASA reference products only where time period, model and resolution are comparable.
- [x] Measure response times for cached and uncached analyses and document results.
- [ ] Check the interface with a teammate using the planner and public-user workflows.
- [x] Update README setup/run instructions and prepare the short site/date comparison demo.
- [x] Record the NASA resources actually used and the assumptions shown in the submission.

**MVP release gate** — All first-release acceptance conditions above are demonstrated and the required checks pass.

Technical verification and fixes completed on 2026-11-14 in `feature/task6-demo-verification`: 222 tests, no-cache clean builds, real cold/warm data, outage/restart recovery, numerical regression and production UI checks. See [verification report](mvp-verification.md) and [demo/human acceptance checklist](demo-runbook.md). NASA long-cycle illumination maps were assessed and found unsuitable as short-window numerical targets. **Teammate acceptance remains pending; the full MVP release gate is not closed.**

## Subsequent releases in order

Each release builds on the preceding delivered functionality. Existing scenario details describe the intended behavior. These are future implementation tasks.

### R1 — Window search, exports and classroom presets

- [ ] Implement S3 searches using user-defined sunlight and Earth-visibility constraints.
- [ ] Show satisfied constraints and explain the closest results when none qualify.
- [ ] Implement S14 CSV, chart export and reports with units and provenance.
- [ ] Add shareable scenarios that retain input parameters and dataset/model versions.
- [ ] Complete S15 guided teaching presets and explanation cards.

**Completion evidence** — Search results satisfy their stated constraints and a shared/exported scenario can be reproduced.

### R2 — Ground-station communication analysis

- [ ] Source DSN station coordinates and define station elevation thresholds.
- [ ] Implement S4 geometry for the landing site and individual ground stations.
- [ ] Display station-specific and combined visibility intervals.
- [ ] Document antenna, station availability and link-budget assumptions required for operational interpretation.

**Completion evidence** — Each reported geometric window satisfies the modeled line-of-sight conditions at both ends.

### R3 — Explained recommendations and priorities

- [ ] Define terrain constraints, hazard data and visibility scoring rules.
- [ ] Implement S8 ranking with available terrain, sunlight and communication criteria.
- [ ] Implement S9 weight controls using cached metrics.
- [ ] Explain rejected candidates and each criterion's contribution.
- [ ] Suggest alternate locations/dates for changed schedules.

**Completion evidence** — Every ranked candidate and rejection can be traced to its inputs and scoring rules.

### R4 — Descent trajectories

- [ ] Define spacecraft profiles, initial orbit, physical assumptions and tolerances.
- [ ] Implement and validate S6 descent phases and reachability limits.
- [ ] Display the trajectory, phase timing and delta-v.

**Completion evidence** — A feasible trajectory reaches its selected target within documented tolerances.

### R5 — Fuel optimization

- [ ] Implement S7 fuel estimation and reserve/engine constraints across feasible trajectories.
- [ ] Show fuel versus safety trade-offs and rejected-route reasons.
- [ ] Extend S8/S9 ranking with validated reachability and fuel metrics.

**Completion evidence** — Every proposed route respects its configured fuel reserve and vehicle limits.

### R6 — Risk and uncertainty

- [ ] Source or label sensor, engine, landing-position and terrain uncertainty assumptions.
- [ ] Implement seeded S10 Monte Carlo runs and failure-cause tracking.
- [ ] Display dispersion, fuel distributions and sensitivity results.

**Completion evidence** — Fixed inputs and seed reproduce results, with assumptions and success criteria visible.

### R7 — Alternative landing plans

- [ ] Implement S11 backup-site selection from the current approach state.
- [ ] Check remaining fuel and maneuver limits for every backup.
- [ ] Compare primary and backup plans using common metrics.

**Completion evidence** — Each offered backup is reachable under the stated state and limits.

### R8 — Post-landing exploration

- [ ] Implement S12 science-target selection and terrain-constrained surface routing.
- [ ] Evaluate sunlight and communication along time-dependent routes.
- [ ] Report distance, duration and shadowed segments with mobility assumptions.

**Completion evidence** — Generated routes satisfy documented slope and mobility constraints.

### R9 — Full mission what-if simulation

- [ ] Extend S13 to spacecraft mass, fuel, sensor/engine parameters and mission duration.
- [ ] Track dependencies and recompute affected outputs.
- [ ] Compare modified plans against a preserved baseline with numerical differences.

**Completion evidence** — Parameter changes invalidate the appropriate cached results and preserve the baseline.

### R10 — Optional hybrid quantum experiment

- [ ] Decide whether a concrete coupled planning problem warrants this experiment.
- [ ] Define its variables, objective and constraints from validated classical inputs.
- [ ] Establish a classical baseline and test datasets.
- [ ] Implement a hybrid solver and measure feasibility, solution quality and total runtime on the same inputs.
- [ ] Document hardware/simulator settings and the measured outcome.

**Completion evidence** — A reproducible comparison establishes what the experiment achieves. Starting this phase requires a separate team decision.
