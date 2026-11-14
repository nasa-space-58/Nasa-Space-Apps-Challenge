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

- [x] Task 7: implement bounded S3 candidate-start searches using sunlight and Earth-visibility constraints (see [window search](window-search.md)).
- [x] Task 7: show satisfied/unmet/unknown constraints for every evaluated candidate.
- [x] R1: rank closest fully evaluated results with explicit normalized deficits; exclude unknown/failed candidates.
- [x] R1: implement S14 CSV, chart PNG and printable HTML reports with units and provenance.
- [x] R1: add saved-result links and portable scenario manifests retaining inputs and dataset/model fingerprints.
- [x] R1: add S15 guided observer-based teaching presets, state explanations, two-click start and controllable playback.

**R1 delivery** — See [release details](release-r1.md). The original S15 terrain-wide moving-shadow/crater-floor visualization is not implemented; the delivered classroom mode uses validated single-observer horizon/visibility results. No permanent-shadow claim is made. Human teammate acceptance from Step 6 remains open.

**Completion evidence** — Search results satisfy their stated constraints and a shared/exported scenario can be reproduced.

### R2 — Ground-station communication analysis

- [x] Source DSN station coordinates and define station elevation thresholds.
- [x] Implement S4 geometry for the landing site and individual ground stations.
- [x] Display station-specific and combined visibility intervals.
- [x] Document antenna, station availability and link-budget assumptions required for operational interpretation.

**R2 delivery** — See [ground-station communication](ground-station-communication.md). Stations DSS-14/43/63 come from DSN 810-005 module 301 Rev. P, Table 5. The generic 6° receive and 10° low-power transmit limits apply; station horizon masks, keyhole, scheduling and link budget are documented as not modeled. Frozen tests: `233a9b8`.

**Completion evidence** — Each reported geometric window satisfies the modeled line-of-sight conditions at both ends.

### R3 — Explained recommendations and priorities

- [x] Define terrain constraints, hazard data and visibility scoring rules.
- [x] Implement S8 ranking with available terrain, sunlight and communication criteria.
- [x] Implement S9 weight controls using cached metrics.
- [x] Explain rejected candidates and each criterion's contribution.
- [x] Suggest alternate locations/dates for changed schedules.

**R3 delivery** — See [explained recommendations](recommendations.md). The terrain screen uses Horn slope on the cached LOLA 80 m DEM with a configurable 10° landing-area limit (planning assumption; NASA publishes no numeric value) and steep-terrain distance; it cannot resolve lander-scale craters or boulders. Fuel and science value are listed as not modeled. Frozen tests: `cee01e4`.

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

### R9 — Mission simulation and digital-twin prototype

- [ ] Extend S13 with position, velocity, mass, remaining propellant, available thrust, battery state and mission duration, with explicit units and coordinate frames.
- [ ] Define versioned contracts for vehicle state, mission requirements, resource availability, uncertainty, events and plans.
- [ ] Separate simulated ground truth from sensor observations and the planner's estimated state.
- [ ] Model sensor health, measurement uncertainty and state-estimation updates.
- [ ] Implement a simulation clock, deterministic event log and replay with fixed seeds.
- [ ] Track input dependencies and invalidate affected plans/results when state, observations or resources change.
- [ ] Compare modified plans against a preserved baseline with numerical differences.
- [ ] Label simulated telemetry and synthetic hazards separately from sourced observations.
- [ ] Display predicted versus observed state and explain why a plan was invalidated.

### R10 — Hybrid quantum-classical optimization

- [ ] Define a common solver interface receiving the same candidate options, objectives, constraints and state/version identifiers.
- [ ] Keep physical modeling, candidate generation, state estimation and final feasibility validation classical.
- [ ] Implement a classical exact reference for small problems and select a CP-SAT or MILP baseline for larger problems.
- [ ] Define binary mission-option variables and encode selection, incompatibility and resource constraints as a QUBO.
- [ ] Verify that the QUBO corresponds to the original constrained problem; document penalty scales, coefficient normalization and any auxiliary variables.
- [ ] Implement an initial QAOA experiment using three missions with four options each; verify all feasible assignments against classical enumeration.
- [ ] Add a classical outer loop for quantum circuit parameters and record circuit depth, shots, seeds and optimization settings.
- [ ] Scale experiments across problem size, constraint density and resource contention; measure logical-variable and circuit resource growth.
- [ ] Add bounded subproblem selection so larger mission plans can use a small quantum optimization step inside a classical search.
- [ ] Validate every solver result against the original constraints independently of the QUBO objective value.
- [ ] Record raw quantum feasibility separately from any classical repair, including repair cost and changes.
- [ ] Compare quantum-generated candidates with classical and random candidates using the same repair/local-improvement procedure.
- [ ] Run noiseless simulation, noise-model experiments and a bounded hardware experiment when access is available; label each execution mode explicitly.
- [ ] Benchmark feasible-solution rate, objective quality, optimum gap where known, variability and end-to-end runtime, including preparation, tuning, queueing and validation.
- [ ] Use multiple reproducible instances and held-out evaluation cases with documented compute budgets.
- [ ] Define go/no-go criteria for each quantum application and retain the classical solver when measured benefit is absent.
- [ ] Publish reproducible classical-versus-hybrid results without assuming or claiming an unmeasured quantum advantage.

### R11 — Multi-mission planning and shared resources

- [ ] Define mission types, priorities, deadlines, dependencies and required energy/communication/science resources.
- [ ] Build an initial three-mission scenario with multiple site/start-time options and shared communication capacity.
- [ ] Generate candidate options from validated terrain, illumination, communication and vehicle constraints.
- [ ] Separate mandatory feasibility constraints from weighted preferences such as science value, fuel use, waiting time and reserve margins.
- [ ] Implement classical joint site/time selection and compare it with independently planned missions.
- [ ] Schedule communication demand within visibility windows while modeling station availability, capacity, data rates and exclusivity as separate inputs.
- [ ] Implement energy and science-activity scheduling with battery limits, task durations, precedence and deadlines.
- [ ] Add explicit defer/cancel alternatives where mission policy permits, and explain infeasible mission sets.
- [ ] Model inter-vehicle relay links and their equipment/geometry constraints before crediting one mission with communication support for another.
- [ ] Evaluate the downstream value of an early mission's location and schedule for subsequent missions.
- [ ] Connect the same candidate problem to the R10 hybrid solver and independent classical validator.
- [ ] Show a shared mission timeline, resource conflicts, dependencies and the reasons for selected/rejected options.
- [ ] Verify cases where the best individual choices conflict but a joint feasible plan exists, and cases where no joint plan is feasible.

### R12 — Self-healing landing and event-driven replanning

- [ ] Define a bounded terminal-descent scenario with an explicit initial position/velocity, vehicle model and touchdown tolerances, building on R4/R5.
- [ ] Validate classical gravity, thrust, mass-flow and propellant calculations with reference cases, limiting cases and time-step convergence checks.
- [ ] Generate reachable alternative landing targets and trajectories under current thrust, fuel reserve and maneuver constraints.
- [ ] Independently validate candidate trajectories at finer time resolution, including touchdown speed, terrain clearance and reserve limits.
- [ ] Inject timestamped sensor degradation, thrust loss, newly observed obstacles, communication outages and propellant shortfalls.
- [ ] Make each event alter the relevant physical/state-estimation/resource model rather than only the visualization.
- [ ] Propagate sensor uncertainty into landing dispersion and candidate acceptance margins.
- [ ] Implement event-triggered classical replanning with an explicit deadline and updated state/version identifiers.
- [ ] Define policies for continuing, retargeting, deferring or aborting where the vehicle and mission model permit those actions.
- [ ] Revalidate fallback plans against the current state; reject stale solver outputs and report when no feasible continuation exists.
- [ ] Evaluate preservation of reachable backup targets during descent, alongside fuel and primary-goal performance.
- [ ] Use hybrid optimization for discrete candidate-plan selection and downstream mission rescheduling, while keeping immediate flight control classical.
- [ ] Run the quantum path asynchronously with a time budget; use a currently validated classical plan when no valid quantum result arrives in time.
- [ ] Propagate changed landing time/location and lost resources into the R11 multi-mission schedule.
- [ ] Replay identical events under the original plan, classical replanning and hybrid replanning, comparing feasibility, reserves, delays and mission value.
- [ ] Add a visual event panel, original/replanned trajectories, uncertainty display, decision explanations and solver timing.
- [ ] Validate both recoverable and unrecoverable scenarios, including combinations of faults, without assuming every event has a safe solution.

### R13 — Additional quantum application benchmarks

- [ ] Benchmark communication-slot allocation using shared-station conflicts, unmet demand, delivered data and latency.
- [ ] Benchmark science-activity selection and ordering under energy/time constraints using completed science value and missed deadlines.
- [ ] Benchmark relay-location selection after validating relay geometry/capacity, measuring coverage and resilience to one relay failure.
- [ ] Benchmark rover target/route selection after R8, measuring science return, energy use and communication gaps.
- [ ] Benchmark robust plan selection across reproducible uncertainty/fault scenarios after R6, measuring success rates and worst-case costs.
- [ ] For each application, define the concrete decision variables, classical baseline, quantum subproblem, validation rules and measurable benefit before implementation.
- [ ] Record negative as well as positive results and enable quantum execution only where its role and observed contribution are demonstrated.

## Hybrid development sequence

- [ ] **H0 — Shared contracts:** define the R9 state/event/plan model, constraint semantics, data provenance and benchmark scenario formats.
- [ ] **H1 — Classical multi-mission baseline:** deliver R11 candidate generation, joint planning, small exact solutions and an independent validator.
- [ ] **H2 — Quantum mapping:** deliver the small R10 QUBO/QAOA experiment and prove mapping consistency against enumeration.
- [ ] **H3 — Resource integration:** connect communication windows and energy/activity models, then benchmark classical and hybrid scheduling on identical inputs.
- [ ] **H4 — Descent foundation:** deliver the validated R4/R5 terminal-descent, fuel and reachability model; this can progress alongside H1–H3.
- [ ] **H5 — Classical self-healing:** add R6/R9 uncertainty, event replay and R12 classical replanning with feasible and infeasible recovery cases.
- [ ] **H6 — Hybrid recovery:** connect candidate-plan selection to downstream multi-mission scheduling, with deadlines, stale-result rejection and validated fallback behavior.
- [ ] **H7 — Evidence and demonstration:** run hardware experiments where available, publish reproducible benchmarks, and demonstrate a fault changing both one vehicle's landing plan and subsequent mission schedules.

### Proposed hybrid demonstration features

These are planned concepts, not implemented features. Each one reuses the H1–H3 problem, solvers and validator, so it adds presentation and measurement rather than a new scientific model.

- [ ] **H8 — Side-by-side solver arena:** run the classical solver and QAOA on the same multi-mission instance in two synchronized panels.
  - Animate the QAOA measurement distribution across optimization iterations, highlighting feasible plans and the known optimum.
  - Finish with a shared scoreboard: feasible-solution rate, objective value, optimum gap and end-to-end runtime, including tuning and validation.
  - Label the execution mode (noiseless simulator, noise model or hardware) and never declare a winner the measurements do not support.
- [ ] **H9 — Artemis-style shared-antenna scenario:** an illustrative campaign with one crewed lander and two robotic CLPS-class missions sharing limited DSN capacity.
  - Use candidate sites and start times from the existing sunlight and R2 communication analyses, each with a deadline and communication demand.
  - Show a shared timeline in which moving one mission's landing date shifts the others' antenna windows and resolves or creates conflicts.
  - Contrast independently chosen "best" options that conflict with a jointly feasible plan, and explain mission sets with no feasible plan.
  - Label the scenario as illustrative; it is not a NASA mission plan or a DSN scheduling commitment.
- [ ] **H10 — Problem-scaling outlook:** a chart showing where exhaustive classical search stops being practical as the mission count grows.
  - Plot the number of candidate plans, measured exact/CP-SAT/MILP runtime and required logical qubits against mission and option counts.
  - Mark measured points separately from extrapolated trends, and record the hardware and compute budget behind each measurement.
  - State plainly that classical methods suffice at the current three-mission scale; the chart identifies the problem sizes worth benchmarking, not a demonstrated quantum advantage.
- [ ] **H11 — Three-body relay-orbit selection:** choose lunar relay orbits that restore communication for south-pole sites without direct Earth visibility.
  - Motivation: the Task 4 Malapert verification case (2026-11-04, 24 hours) reports 0% Earth-center visibility, so direct-to-Earth links cannot be assumed.
  - Classical physics: propagate candidate relay orbits in the Earth–Moon circular restricted three-body problem (for example, L2 halo and near-rectilinear halo orbit families) with documented initial conditions, integrator tolerances and reference checks.
  - Classical geometry: compute each candidate's line-of-sight windows to the terrain-relative sites and to Earth, reusing the existing horizon and R2 visibility rules.
  - Hybrid decision: encode "select k relay orbits from n candidates" with coverage, gap and per-mission communication-demand constraints as a QUBO, solved by the H8 classical/QAOA pair.
  - Independent validation: check every selected set against the original coverage constraints, independent of the QUBO objective value.
  - Scope: quantum computation selects among classically computed orbits; it does not propagate trajectories. Relay link budgets, station keeping and launch constraints remain outside this concept unless separately modeled.
