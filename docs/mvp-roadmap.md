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

- [x] Define spacecraft profiles, initial orbit, physical assumptions and tolerances.
- [x] Implement and validate S6 descent phases and reachability limits.
- [x] Display the trajectory, phase timing and delta-v.

**R4 delivery** — See [descent trajectories](descent-trajectories.md). Apollo 11 LM profile from the Apollo 11 Mission Report and NASA TN D-6846; planar point-mass model with Hohmann DOI and ZEM/ZEV guidance through the TN D-6846 gates. Total powered delta-v is within 2 % of the Apollo 11 plan (6775 fps). Terrain clearance, mascons, lunar rotation and a 3D view are not included. Frozen tests: `9ed69c8`.

**Completion evidence** — A feasible trajectory reaches its selected target within documented tolerances.

### R5 — Fuel optimization

- [x] Implement S7 fuel estimation and reserve/engine constraints across feasible trajectories.
- [x] Show fuel versus safety trade-offs and rejected-route reasons.
- [x] Extend S8/S9 ranking with validated reachability and fuel metrics.

**R5 delivery** — See [fuel optimization](fuel-optimization.md). Route grid over orbit altitude and approach/terminal durations on the R4 model; propellant counted from a DOI mass backed out of the PDI mass; default touchdown reserve 145 fps from the NASA TN D-6846 Table II contingencies (redesignation 60 fps + manual hover 85 fps); every route rejected with reasons when it breaks the reserve or R4 limits; Pareto front of fuel versus assessment time. R3 recommendations can opt into descent reachability and a fuel criterion without changing default R3 output. Frozen tests: `2994417`.

**Completion evidence** — Every proposed route respects its configured fuel reserve and vehicle limits.

### R6 — Risk and uncertainty

- [x] Source or label sensor, engine, landing-position and terrain uncertainty assumptions.
- [x] Implement seeded S10 Monte Carlo runs and failure-cause tracking.
- [x] Display dispersion, fuel distributions and sensitivity results.

**R6 delivery** — See [risk and uncertainty](risk-uncertainty.md). Seeded Monte Carlo over the R4 descent with independent Gaussian errors in thrust, Isp, PDI state, DEM height and navigation (all labelled planning assumptions). Success requires the R4 touchdown tolerances, the R5 reserve and a known landing cell within the R3 slope limit. The output gives a Wilson 95 % interval, failure causes, 1σ/3σ dispersion ellipses with the steep DEM cells inside them, a propellant histogram and rank-correlation sensitivity. On Malapert (200 runs, seed 2026), 52 % of landings succeed, and terrain-height error dominates because no landing radar is modelled. Frozen tests: `9e0b58d`.

**Completion evidence** — Fixed inputs and seed reproduce results, with assumptions and success criteria visible.

### R7 — Alternative landing plans

- [x] Implement S11 backup-site selection from the current approach state.
- [x] Check remaining fuel and maneuver limits for every backup.
- [x] Compare primary and backup plans using common metrics.

**R7 delivery** — See [alternative landing plans](alternative-landing.md). Backups are DEM cells within a search radius that pass the R3 hard constraints, kept a minimum distance apart. Each is evaluated from the R4 state at high or low gate: downrange is flown exactly with the R4 guidance (state rotation), and crossrange uses the analytic minimum-energy lateral divert (Δv = 3|c|/T) once the engine leaves the fixed-throttle position. A backup is reachable only if it avoids propellant exhaustion, keeps the R5 reserve, meets the R4 touchdown tolerances and stays within maximum thrust. The primary and backups share one comparison table, and backups can be marked. On Malapert, 6 of 6 backups are reachable from high gate and all are safer than the primary. Frozen tests: `946618c`.

**Completion evidence** — Each offered backup is reachable under the stated state and limits.

### R8 — Post-landing exploration

- [x] Implement S12 science-target selection and terrain-constrained surface routing.
- [x] Evaluate sunlight and communication along time-dependent routes.
- [x] Report distance, duration and shadowed segments with mobility assumptions.

**R8 delivery** — See [post-landing exploration](surface-exploration.md). A* finds the shortest 3-D route over DEM cells at or below a mobility slope limit (15° planning assumption) to up to five targets given by bearing and distance. Unreachable targets are explained (outside terrain, target too steep, or no path). With a site analysis, sampled route points each get their own terrain horizon and the analysis' Sun/Earth direction at arrival. Each route reports length, duration, maximum slope, climb, detour and shadowed or no-Earth points, and the site gets a documented efficiency score. On Malapert (4 Nov), the sunlit east route never sees Earth and the north route regains Earth in shadow. Frozen tests: `46cb1d5`.

**Completion evidence** — Generated routes satisfy documented slope and mobility constraints.

### R9 — Mission simulation and digital-twin prototype

- [x] Extend S13 with position, velocity, mass, remaining propellant, available thrust, battery state and mission duration, with explicit units and coordinate frames.
- [ ] Define versioned contracts for vehicle state, mission requirements, resource availability, uncertainty, events and plans.
- [x] Separate simulated ground truth from sensor observations and the planner's estimated state.
- [x] Model sensor health, measurement uncertainty and state-estimation updates.
- [x] Implement a simulation clock, deterministic event log and replay with fixed seeds.
- [x] Track input dependencies and invalidate affected plans/results when state, observations or resources change.
- [x] Compare modified plans against a preserved baseline with numerical differences.
- [x] Label simulated telemetry and synthetic hazards separately from sourced observations.
- [x] Display predicted versus observed state and explain why a plan was invalidated.

**R9 delivery** — See [descent digital twin](descent-twin.md). The R4 descent is flown with separate ground truth, range-scaled noisy observations and a constant-gain estimate, and guidance steers by the estimate. With perfect sensing it reproduces R4 exactly. Timestamped thrust loss, sensor degradation, propellant leaks, comm outages and synthetic obstacles change the model and invalidate only the plans that depend on them, in a hashed, replayable event log. A same-seed no-event baseline gives numerical differences. On Malapert (seed 2026), a navigation sensor lost at 450 s puts the lander 471 m off target while its own expected error stays at a few metres. The contracts item stays open: the twin uses local models whose names match TK's H0 contracts (`record_kind`, `thrust_loss`, `sensor_degradation`), and it is ticked once H0 is merged and an adapter is added. Frozen tests: `ac30279`.

### R10 — Hybrid quantum-classical optimization

- [x] Define a common solver interface receiving the same candidate options, objectives, constraints and state/version identifiers.
- [x] Keep physical modeling, candidate generation, state estimation and final feasibility validation classical.
- [x] Implement a classical exact reference for small problems and select a CP-SAT or MILP baseline for larger problems.
- [x] Define binary mission-option variables and encode selection, incompatibility and resource constraints as a QUBO.
- [x] Verify that the QUBO corresponds to the original constrained problem; document penalty scales, coefficient normalization and any auxiliary variables.
- [x] Implement an initial QAOA experiment using three missions with four options each; verify all feasible assignments against classical enumeration.
- [x] Add a classical outer loop for quantum circuit parameters and record circuit depth, shots, seeds and optimization settings.
- [x] Scale experiments across problem size, constraint density and resource contention; measure logical-variable and circuit resource growth.
- [x] Add bounded subproblem selection so larger mission plans can use a small quantum optimization step inside a classical search.
- [x] Validate every solver result against the original constraints independently of the QUBO objective value.
- [x] Record raw quantum feasibility separately from any classical repair, including repair cost and changes.
- [x] Compare quantum-generated candidates with classical and random candidates using the same repair/local-improvement procedure.
- [x] Run noiseless simulation, noise-model experiments and a bounded hardware experiment when access is available; label each execution mode explicitly.
- [x] Benchmark feasible-solution rate, objective quality, optimum gap where known, variability and end-to-end runtime, including preparation, tuning, queueing and validation.
- [x] Use multiple reproducible instances and held-out evaluation cases with documented compute budgets.
- [x] Define go/no-go criteria for each quantum application and retain the classical solver when measured benefit is absent.
- [x] Publish reproducible classical-versus-hybrid results without assuming or claiming an unmeasured quantum advantage.

**R10 status (completed 2026-11-15)** — H0–H2 and H7 deliver the shared solver input, classical validation, exact enumeration, the audited QUBO/QAOA experiment and labeled noiseless, noise-model and hardware runs. [R10](hybrid-benchmark.md) adds the remaining items:

- a HiGHS MILP baseline revalidated by the exact validator;
- enumeration-free resource sweeps over size, constraint density and contention;
- bounded subproblem search;
- raw-versus-repaired records and same-repair comparison against random and annealing samplers;
- dev/held-out instances with documented budgets;
- pre-registered go/no-go rules.

The published results ([summary](verification/r10-hybrid-benchmark-2026-11-15.json)) are **no-go** for both direct QAOA selection and QAOA-solved subproblems, so the classical MILP solver is retained. No quantum advantage is claimed.

### R11 — Multi-mission planning and shared resources

- [x] Define mission types, priorities, deadlines, dependencies and required energy/communication/science resources.
- [x] Build an initial three-mission scenario with multiple site/start-time options and shared communication capacity.
- [x] Generate candidate options from validated terrain, illumination, communication and vehicle constraints.
- [x] Separate mandatory feasibility constraints from weighted preferences such as science value, fuel use, waiting time and reserve margins.
- [x] Implement classical joint site/time selection and compare it with independently planned missions.
- [x] Schedule communication demand within visibility windows while modeling station availability, capacity, data rates and exclusivity as separate inputs.
- [x] Implement energy and science-activity scheduling with battery limits, task durations, precedence and deadlines.
- [x] Add explicit defer/cancel alternatives where mission policy permits, and explain infeasible mission sets.
- [x] Model inter-vehicle relay links and their equipment/geometry constraints before crediting one mission with communication support for another.
- [x] Evaluate the downstream value of an early mission's location and schedule for subsequent missions.
- [x] Connect the same candidate problem to the R10 hybrid solver and independent classical validator.
- [x] Show a shared mission timeline, resource conflicts, dependencies and the reasons for selected/rejected options.
- [x] Verify cases where the best individual choices conflict but a joint feasible plan exists, and cases where no joint plan is feasible.

**R11 status (completed 2026-11-15)** — H1, H3 and H6 delivered the earlier ticked items:

- the synthetic three-mission shared-channel case;
- hard constraints separated from weighted objectives;
- independent-versus-joint comparison with feasible and infeasible capacity cases ([H1](classical-planning.md));
- ideal battery/activity limits ([H3](resource-integration.md));
- landing-to-downstream dependencies ([H6](hybrid-recovery.md));
- the same scenario passed to QAOA and the independent validator.

[R11](multi-mission-planning.md) adds the remaining items:

- a versioned campaign contract with mission types, priorities, deadlines, dependencies, resource requirements and defer/cancel policies;
- candidates generated from Task 3 sunlight, R3 terrain safety, R4 descent propellant and vehicle evidence, with coded rejections;
- slot-level downlink scheduling with station calendars, capacities, data rates and exclusivity as separate inputs;
- relay credit only with relay equipment, line-of-sight evidence, range and the relay's own station link;
- a HiGHS joint planner with a solver-independent validator;
- minimal infeasible mission sets and measured explanations for each choice;
- the shared timeline in the **Campaign** tab.

The R10 decision applies: the classical MILP is retained, and QAOA covers only H0-representable selection subproblems. Frozen tests: `8504f81`. Results: [verification](verification/r11-campaign-planning-2026-11-15.json).

### R12 — Self-healing landing and event-driven replanning

- [x] Define a bounded terminal-descent scenario with an explicit initial position/velocity, vehicle model and touchdown tolerances, building on R4/R5.
- [x] Validate classical gravity, thrust, mass-flow and propellant calculations with reference cases, limiting cases and time-step convergence checks.
- [x] Generate reachable alternative landing targets and trajectories under current thrust, fuel reserve and maneuver constraints.
- [x] Independently validate candidate trajectories at finer time resolution, including touchdown speed, terrain clearance and reserve limits.
- [x] Inject timestamped sensor degradation, thrust loss, newly observed obstacles, communication outages and propellant shortfalls.
- [x] Make each event alter the relevant physical/state-estimation/resource model rather than only the visualization.
- [x] Propagate sensor uncertainty into landing dispersion and candidate acceptance margins.
- [x] Implement event-triggered classical replanning with an explicit deadline and updated state/version identifiers.
- [x] Define policies for continuing, retargeting, deferring or aborting where the vehicle and mission model permit those actions.
- [x] Revalidate fallback plans against the current state; reject stale solver outputs and report when no feasible continuation exists.
- [x] Evaluate preservation of reachable backup targets during descent, alongside fuel and primary-goal performance.
- [x] Use hybrid optimization for discrete candidate-plan selection and downstream mission rescheduling, while keeping immediate flight control classical.
- [x] Run the quantum path asynchronously with a time budget; use a currently validated classical plan when no valid quantum result arrives in time.
- [x] Propagate changed landing time/location and lost resources into the R11 multi-mission schedule.
- [x] Replay identical events under the original plan, classical replanning and hybrid replanning, comparing feasibility, reserves, delays and mission value.
- [x] Add a visual event panel, original/replanned trajectories, uncertainty display, decision explanations and solver timing.
- [x] Validate both recoverable and unrecoverable scenarios, including combinations of faults, without assuming every event has a safe solution.

**R12 status (completed 2026-11-15)** — All items are delivered on `main`. H4–H6 provide the backend models and notebooks ([descent foundation](descent-foundation.md), [self-healing](self-healing.md), [hybrid recovery](hybrid-recovery.md)); the event panel, replay API and solver timing are described in [R12 event panel](r12-event-panel.md). The older `feature/r12-self-healing` branch was not merged because its backend conflicts with H5; the new panel is built on H5/H6 instead.

### R13 — Additional quantum application benchmarks

- [x] Benchmark communication-slot allocation using shared-station conflicts, unmet demand, delivered data and latency.
- [x] Benchmark science-activity selection and ordering under energy/time constraints using completed science value and missed deadlines.
- [x] Benchmark relay-location selection after validating relay geometry/capacity, measuring coverage and resilience to one relay failure.
- [x] Benchmark rover target/route selection after R8, measuring science return, energy use and communication gaps.
- [x] Benchmark robust plan selection across reproducible uncertainty/fault scenarios after R6, measuring success rates and worst-case costs.
- [x] For each application, define the concrete decision variables, classical baseline, quantum subproblem, validation rules and measurable benefit before implementation.
- [x] Record negative as well as positive results and enable quantum execution only where its role and observed contribution are demonstrated.

**R13 status (completed 2026-11-15)** — [R13](quantum-applications.md) pre-registers each application before implementation. For each it defines the decision variables, an independent validator, a HiGHS MILP baseline, the QUBO and QAOA subproblem, validation rules and the measured benefit. It then benchmarks all five applications on dev/held-out instances using the R10 protocol:

- communication-slot allocation;
- science-activity selection and ordering;
- relay selection after geometry/capacity validation;
- rover routes built from R8 `plan_routes`;
- robust plan pairs flown through R6 `run_monte_carlo`.

**Negative results:** every application is **NO-GO**. Noiseless QAOA beats matched random sampling on raw feasibility (0.45–0.98 versus 0.06–0.37), but final plans are identical after the shared repair. The hybrid path is 26–97× slower than MILP, and the bounded search falls short of the optimum on communication slots and science activities.

The execution gate (`solve_application`) therefore keeps the classical MILP everywhere and runs QAOA only for a held-out GO. Frozen tests: `351c1e1`. Results: [verification](verification/r13-application-benchmark-2026-11-15.json). No hardware job or quantum advantage is claimed.

## Hybrid development sequence

- [x] **H0 — Shared contracts:** define the R9 state/event/plan model, constraint semantics, data provenance and benchmark scenario formats.
- [x] **H1 — Classical multi-mission baseline:** prepare explicitly supplied R11 site/time candidates, jointly plan small static problems by exact enumeration, and independently validate selections. Real-data candidate integration continues in H3/H4.
- [x] **H2 — Quantum mapping:** deliver the bounded R10 QUBO/QAOA simulator experiment and prove mapping consistency against enumeration; compact encoding uses six qubits for the three-by-four example. General resource encodings and hardware experiments remain later work.
- [x] **H3 — Resource integration:** connect explicit R2 geometry, station calendars and bounded ideal battery/activity models; compare classical/hybrid scheduling on identical supported inputs and report unsupported encodings. Real mission calibration and general resource encodings remain open.
- [x] **H4 — Descent foundation:** share fuel-limited dynamics with R4/R5, restart a bounded planar terminal descent, and screen explicit landing candidates at two resolutions. Synthetic/reference physics validation is complete; real-terrain/3D flight validation remains outside this model.
- [x] **H5 — Classical self-healing:** deliver bounded planar fault replay, separate synthetic observations from truth, screen candidates under seeded uncertainty, and replan with recoverable/unrecoverable cases. Full sensor fusion, real-time execution and 3D mission validation remain outside this milestone.
- [x] **H6 — Hybrid recovery:** connect physically screened landing options to downstream scheduling through a bounded local QAOA worker, with acceptance deadlines, stale-result rejection and freshly validated classical fallback. Offline replay is complete; live latency compensation and hardware runs remain outside this milestone.
- [x] **H7 — Evidence and demonstration:** complete one bounded two-qubit OpenQuantum/Rigetti experiment, publish raw counts and classical/ideal/noisy comparisons, and replay its validated proposal across a fault that changes both landing and downstream scheduling. Repeated hardware trials, larger scaling and live flight latency compensation remain outside this milestone.

**H0 delivery** — Shared Python contracts, five synthetic benchmark cases and an executable Jupyter notebook are available. See [contracts, semantics and verification](hybrid-contracts.md). H1/H2 solvers, physical event execution and quantum hardware runs remain open.

**H1 delivery** — Deterministic candidate preparation, independent-choice comparison, bounded exact joint planning, explained constraint validation and a 12-code-cell notebook are available. See [classical planning, semantics and verification](classical-planning.md). Unknown inputs and unapplied events are reported explicitly; no quantum execution is included.

**H2 delivery** — The 12-variable QUBO was checked across all 4096 bit strings against H1. A six-qubit local QAOA experiment and notebook compare one/two layers over three fixed seeds, report raw outcomes and logical gate estimates, and reject unsupported resource encodings. See [quantum mapping and measured limits](quantum-mapping.md). No hardware jobs or quantum advantage are claimed.

**H3 delivery** — R2-shaped contact evidence, separately declared station calendars, and ideal storage/activity constraints feed the same H0 scenario. An eight-code-cell notebook shows binding energy limits, classical/QAOA comparison, unknown contact handling and spillage. See [resource integration and limitations](resource-integration.md).

**H4 delivery** — Fuel exhaustion now stops thrust inside a time step. A restartable terminal checker validates sampled terrain contact, speed, slope and reserve; explicit target screening retains coarse/fine evidence. The 10-code-cell notebook and 55 H4 tests are described in [descent foundation and limits](descent-foundation.md). No quantum/provider calls or flight-certification claim are included.

**H5 delivery** — Five fault types change physical/navigation/landing-policy constraints at exact UTC boundaries. Classical recovery preserves a valid target or selects a screened backup, retaining baseline comparison, state revisions and explicit infeasibility. Fifty-one frozen tests and a 10-code-cell notebook are documented in [self-healing and replay limits](self-healing.md). H6 integration is documented below.

**H6 delivery** — A two-qubit local QAOA example changes both the post-fault landing target and the selected downstream mission slot. Thirty-seven frozen tests cover the async acceptance boundary and current fallback; the 10-code-cell notebook reports three seeds, raw outcomes, deadline/stale guards and an end-to-end offline replay. See [hybrid recovery and timing limits](hybrid-recovery.md). No hardware jobs or end-to-end quantum advantage are claimed; H7 remains open.

**H7 delivery** — The optional OpenQuantum adapter, pinned separate environment and executed 12-cell notebook are available. Sixty frozen tests pass. One two-qubit Rigetti job completed: 200/256 optimal samples, 247/256 feasible samples and zero best-plan gap to the exact reference. The offline fault replay changes primary/early to remote/late and lands with fuel accounting checked. One free Spark credit was used; 24 Spark and zero Full remain. See [raw counts, provider attribution and limitations](openquantum-hardware.md). No quantum advantage, paid spending or live flight-control guarantee is claimed.

### Proposed hybrid demonstration features

Completed demonstrations are checked below; unchecked items remain planned concepts. H8 reuses the H1–H3 contracts, solvers and validator and adds presentation and measurement rather than a new scientific model.

- [x] **H8 — Side-by-side solver arena:** deliver the bilingual mission-control comparison panel, exact shared-input benchmarks, real optimizer-history playback, independently checked final outcomes and inclusive local compute timings. H7 hardware is shown only as archived evidence; see [solver arena](solver-arena.md).
  - Animate the QAOA measurement distribution across optimization iterations, highlighting feasible plans and the known optimum.
  - Finish with a shared scoreboard: feasible-solution rate, objective value, optimum gap and end-to-end runtime, including tuning and validation.
  - Label the execution mode (noiseless simulator, noise model or hardware) and never declare a winner the measurements do not support.
- [x] **H9 — Artemis-style shared-antenna scenario:** source-bound three-mission scheduling, independent/classical/QAOA timelines, pinned crew choices, explicit infeasible/incomplete cases and one six-qubit Rigetti trial are delivered. See [campaign evidence and measured limits](shared-antenna-campaign.md).
  - Use candidate sites and start times from the existing sunlight and R2 communication analyses, each with a deadline and communication demand.
  - Show a shared timeline in which moving one mission's landing date shifts the others' antenna windows and resolves or creates conflicts.
  - Contrast independently chosen "best" options that conflict with a jointly feasible plan, and explain mission sets with no feasible plan.
  - Label the scenario as illustrative; it is not a NASA mission plan or a DSN scheduling commitment.
- [x] **H10 — Problem-scaling outlook:** measured bounded enumeration/MILP runs, encoding-size charts, explicitly labeled extrapolations and a runnable notebook. MILP solved all recorded cases; no general classical limit or quantum advantage is claimed. See [scaling evidence](problem-scaling.md).
  - Plot the number of candidate plans, measured exact/CP-SAT/MILP runtime and required logical qubits against mission and option counts.
  - Mark measured points separately from extrapolated trends, and record the hardware and compute budget behind each measurement.
  - State plainly that classical methods suffice at the current three-mission scale; the chart identifies the problem sizes worth benchmarking, not a demonstrated quantum advantage.
- [x] **H11 — Three-body relay-orbit selection:** audited JPL CR3BP candidates, sampled terrain/Earth-center geometry and validated k=1/2 relay selection using the H8 exact/QAOA pair. Includes the ideal synchronous-frame assumption, rejected surface-intersecting orbit and offline notebook. Real ephemeris frame conversion and RF/station scheduling remain outside this concept. See [relay evidence and limits](relay-orbit-selection.md).
  - Motivation: the Task 4 Malapert verification case (2026-11-04, 24 hours) reports 0% Earth-center visibility, so direct-to-Earth links cannot be assumed.
  - Classical physics: propagate candidate relay orbits in the Earth–Moon circular restricted three-body problem (for example, L2 halo and near-rectilinear halo orbit families) with documented initial conditions, integrator tolerances and reference checks.
  - Classical geometry: compute each candidate's line-of-sight windows to the terrain-relative sites and to Earth, reusing the existing horizon and R2 visibility rules.
  - Hybrid decision: encode "select k relay orbits from n candidates" with coverage, gap and per-mission communication-demand constraints as a QUBO, solved by the H8 classical/QAOA pair.
  - Independent validation: check every selected set against the original coverage constraints, independent of the QUBO objective value.
  - Scope: quantum computation selects among classically computed orbits; it does not propagate trajectories. Relay link budgets, station keeping and launch constraints remain outside this concept unless separately modeled.

## Unified mission planner integration

This sequence builds on the merged H0–H11 and R10–R13 implementations. Existing modules are retained; completing their individual milestones does not imply a lossless common planner. See [stage 1 inventory, baseline and acceptance gates](unified-mission-planner.md).

- [x] Stage 1 — Audit current contracts and connections; record fresh R11 classical and H6/R12 local-replay baselines, explicit adapter rejection and existing R13 NO-GO policy. This is an inventory/evidence delivery, not a new solver.
- [x] Stage 2 — Versioned native R11/H6 envelope, sealed snapshots and bounded synthetic option-identity adapters; full constraints retained and proposals require classical completion/physical revalidation. All H0–H11 roles are catalogued; unsupported real frame/relay-only mappings reject explicitly. See [contracts and limits](unified-planner-contracts.md).
- [x] Stage 3 — Full classical lander/dependent-rover/shared-resource pilot, exact arrival/fuel evidence, event replanning, independent physical/campaign admission, stale/expired rejection and no-continuation cases. Completed choices are frozen; incompatible historic allocations reject explicitly. See [pilot and limits](unified-classical-pilot.md).
- [x] Stage 4 — Explicit local H2/H6 target proposals, frozen outside choices, full R11 completion and physical admission, matched random/greedy caps and independent score checks. Unsupported compact domains retain the classical baseline; production NO-GO gates stay unchanged. See [scope and measurements](unified-quantum-proposals.md).
- [ ] Stage 5 — Event-driven coordination and unified plan identity across existing views; timing, fallback and execution-mode evidence.
- [ ] Stage 6 — New pre-registered dev/held-out comparisons and separately authorized hardware tests; later rover/activity and orbital-relay extensions only after their own validation.
