# Unified mission planner — stage 1 baseline and integration plan

Stage 1 starts from main `e92174e` on `codex/unified-quantum-stage1`. The user authorized this stage, tests and a push to the new branch only. No main/tahakrcc merge or hardware credit spending is authorized. The unchanged local H6 notebook edit remains excluded.

**Stage 1 delivers a verified integration inventory and baseline, not a new solver.** Existing R11 campaign planning and H6 recovery work independently, but there is no lossless R11-to-H6 adapter or jointly propagated R11 recovery campaign. The common coordinator discussed below remains planned. No production contracts, physics, solver gates, APIs or frozen tests are changed in this stage.

## Intended outcome

One versioned mission plan should join landing choice, mission timing, communication allocation and event-driven replanning. Classical physics generates and screens candidates. A classical campaign solver produces a complete plan. Supported quantum subproblems may propose changes; classical completion and independent validation decide whether the entire updated plan can be accepted. Existing R10/R13 NO-GO decisions remain effective until a new pre-registered evaluation supports a change.

The first integrated pilot will contain an active lander, a dependent rover mission and another mission sharing a declared communication resource. It will be explicitly synthetic. Physical rover traversal and orbital relay support are later extensions. Existing R11 and H6 examples have different dates, frames and time grids; their results cannot be pasted together to claim this pilot already exists.

## Inventory of reusable components

| Area | Existing implementation and input | Current capability | Integration boundary |
| --- | --- | --- | --- |
| Terrain, sunlight and communication | `app.horizon`, `app.terrain_safety`, `app.communication_windows`; source-bound analysis evidence | Terrain and station visibility, source provenance | Geometry is not station availability, capacity, a data rate or an RF link budget. Preserve unknown intervals. |
| Descent and fuel | `app.descent`, `app.descent_dynamics`, `app.terminal_descent` | Bounded planar dynamics, reserves, target screening | H4 terminal state is `PLANAR_MOON_CENTERED_NONROTATING`; H0 state is MOON_ME. A field rename is not a frame transformation. |
| Risk and backups | `app.risk`, `app.alternatives`, `app.self_healing` | Seeded uncertainty, reachable backup checks and fault replay | Finite samples are not certified success probabilities. Re-screen against the new state before accepting a changed target. |
| Surface routes | `app.surface_routes` | R8 terrain routes, distance/duration and sampled visibility | R11's rover mission is a scheduled mission; it does not automatically fly an R8 traversal. Activity/energy coupling remains later work. |
| R9 digital twin | `app.twin.TwinRequest`, `run_twin` | Separate truth, observations and estimates; hashed event replay | Own request/output structures. No implemented R9-to-common-state adapter; retain estimate/truth separation and explicit frames. |
| H0/H1 planning | `shared.hybrid.Scenario`, `app.hybrid_planning` | Candidate selection, exact reference and independent validator | Does not express R11's complete cancellation, variable link-allocation, relay and data-volume semantics. |
| H6 recovery | `HybridRecoveryRequest`, `RecoveryCoordinator`, `replay_hybrid` | Screened landing-to-H0-option bindings, deadline/stale guards, classical fallback and bounded local QAOA | Input campaign is H0 `Scenario`, not R11 `CampaignProblem`. Simulated flight clock pauses during planning. Only the landing vehicle is physically propagated. |
| R10 optimization | `app.hybrid_scaling`, `app.hybrid_benchmark` | MILP, small subproblem search, matched repair and held-out comparisons | Reuse rather than duplicate. Restricted pairwise mappings reject unsupported capacities; do not weaken the original model. |
| R11 campaign | `shared.campaign.CampaignProblem`, `app.campaign_planning` | Evidence-backed options, defer/cancel policies, dependencies, link slots, MILP, explanations and independent validation | Full campaign remains authoritative. Any quantum projection must be completed and checked against this original problem. |
| R12 presentation | `RecoveryReplayRequest`, `build_replay_view` | Original/classical/H6 replay comparison and decision timing | It presents H5/H6 results; it does not add an R11 coordinator or live flight control. |
| H9 shared antenna | `app.shared_antenna` | Source-bound H0 scheduling experiment and archived hardware counts | Keep as a benchmark, not a second authoritative campaign database. |
| H11 orbital relays | `app.relay_orbits`, `quantum.relay_evidence` | Sampled CR3BP candidate selection and independent coverage checks | Orbiting relays are not R11 surface relay landers. Real frame/time conversion and appropriate link evidence are still needed. |
| R13 application gates | `app.quantum_applications.solve_application` | Per-application policy; all five published held-out decisions retain MILP | H6/arena local demonstrations are separate execution paths, not governed by one global gate. A future coordinator must state its own mode explicitly. |
| Optional hardware adapter | `quantum.openquantum_bridge.OpenQuantumGateway` | Explicit quotation, bounded submission, durable receipts, checked decoding | Not imported into the common runtime for stage 1. No automatic jobs, retries or default credit budget. |

## Fresh baseline observations

[Machine-readable evidence](verification/unified-planner-stage1-2026-11-15.json) records the source commit, input hashes, contract fields, scenario identities and results. These are new local observations, not a claim that all missing adapters are implemented.

| Existing scenario | Observed result | Meaning |
| --- | --- | --- |
| R11 `shared-dsn-conflict` | Independent choices infeasible; joint MILP optimal, 426 variables. Crew at 00:00, CLPS at 12:00 and rover at 18:00 UTC on 2026-12-01. | A genuine shared-resource baseline; the full plan independently validates. |
| R11 `infeasible-mandatory` | Joint infeasible, 47 variables; no plan | The planner must preserve explicit infeasibility instead of inventing a safe continuation. |
| R11 `relay-support` | Joint optimal, 58 variables; relay and crater mission selected | Existing surface-relay semantics validate; this is not the H11 orbital-relay problem. |
| Existing H6/R12 replay, original | Failed at the primary target | Unchanged baseline under the declared fault. |
| Same replay, classical | Landed at `backup`, 79.666978 kg remaining | Current classical recovery baseline. |
| Same replay, local hybrid | Landed at `remote`, 76.814439 kg remaining; `land-remote` + `follow-late` | Existing H0 joint selection works. The pre-fault unsupported encoding uses the classical fallback. Fuel conservation was checked in all branches. |
| R11 payload supplied as R12 hybrid campaign | HTTP 422; direct H0 model validation also rejects all three R11 fixtures | The missing contract bridge is observable, not merely a documentation concern. |
| Combined read-only API routes | Seven endpoints returned HTTP 200 | Modules are available together; endpoint availability is not proof of unified planning. |

The H6 replay is local noiseless simulation, seed 17, 256 shots and 64 optimization evaluations, with zero hardware jobs and a paused simulation clock. The different residual fuel is an objective trade-off, not proof that the hybrid plan is safer or better for every mission. R13's five NO-GO decisions were read from the published report, not rerun or tuned using its held-out instances.

## Contract differences that must not be lost

| Concern | H0/H6 today | R11 today | Stage 2 decision |
| --- | --- | --- | --- |
| Identity | Scenario ID/revision/fingerprint; state revisions | Campaign ID/revision/fingerprint | An envelope references both identities and their provenance. Do not equate matching integer schema versions. |
| Decision | One explicit option per mission | Generated option, deferred option or allowed cancellation | Represent these states explicitly; never drop cancellation/defer policies during projection. |
| Communications | Declared resource demands and capacities | Station calendars, capacities, per-link rates, data volumes and actual slot allocations | Keep the full R11 validator and completion solver authoritative. Pairwise conflicts alone are insufficient. |
| Dependencies | Explicit H6 landing-option/follower bindings | Mission-level dependency and cancellation rules | Define mapping cardinality and downstream invalidation, not just matching names. |
| Time | Continuous UTC terminal trajectory; second-scale synthetic replay | UTC campaign, landing grid and communication slots (minimum 60 s) | Preserve absolute UTC and exact event time; no silent rounding that changes deadlines or availability. |
| Coordinates | H0 MOON_ME versus H4/H6 nonrotating planar terminal frame | MOON_ME sites with evidence-backed descent metrics | Require an explicit frame/origin/epoch convention; reject unsupported conversions. |
| Energy | H3 ideal time-resolved storage model | Campaign net-energy balance | Do not claim battery-trajectory validity from net energy. Choose an authoritative check and attach its evidence. |
| State knowledge | Truth/observation/estimate distinguished in existing models | Static evidence and campaign revision | Planner sees estimates; ground truth remains evaluation-only. Unknown is not zero or safe. |
| Solver provenance | Quantum sample, exact fallback and validation | MILP plan and link allocations | Record proposal source, classical completion changes, rejection reasons and final validated plan separately. |

## Ordered implementation stages and acceptance gates

1. **Stage 1 — inventory and baseline (this delivery).** Record current behavior, missing adapters, policy decisions and reproducible evidence. Complete the combined tests. No new solver is enabled.
2. **Stage 2 — versioned integration envelope and bounded adapters.** Define references to the original R11 campaign, H6 estimated checkpoint, source evidence and decision deadline. Preserve existing contracts. Provide supported-case adapters with explicit error results for missing identities, unavailable rates, unsupported frames, ambiguous bindings, changed revisions and unrepresentable constraints. Acceptance: supported input round trips preserve semantics; stale/unsupported inputs fail without substituting assumptions; no provider dependency.
3. **Stage 3 — complete classical pilot.** Implement one synthetic lander/dependent-rover/shared-resource campaign and a deterministic communication-loss or landing-exclusion event. Update affected evidence, regenerate candidates, solve the full R11 campaign and validate its allocations plus the selected physical continuation. Freeze unaffected completed actions. Acceptance: original, feasible replan, no-continuation and stale-result cases; fuel accounting, dependencies, data demand and reserves hold. Explain every changed decision. Do not yet claim physical rover driving or both vehicles' flight propagation.
4. **Stage 4 — bounded quantum proposals.** Reuse R10's supported subproblem machinery and original-to-projected variable mapping. Freeze outside choices while retaining their resource use; run classical completion for each proposal. Begin with a small supported encoding, not a promise that every R11 case fits four or six qubits. Acceptance: unsupported projections fail explicitly, the full validator is final, raw samples and any classical repair are separate, and the same completion budget is used for random/classical candidates. Production remains classical under existing NO-GO evidence; local experiments are explicitly selected and bounded.
5. **Stage 5 — event-driven coordination and presentation.** Reject results for outdated state, campaign or evidence; revalidate fallback against the current snapshot and enforce acceptance deadlines. Connect Campaign, Self-healing and solver-comparison views to one plan identity. Acceptance: changes propagate to dependents, expired results cannot apply, infeasibility is visible, and preparation/queue/solve/completion/validation timings are separate. Offline paused-clock replay is labeled; live telemetry and atomic actuator dispatch require further work.
6. **Stage 6 — pre-registered comparisons and optional hardware.** Evaluate new dev/held-out scenarios against MILP, matched classical/random proposals and local QAOA. Count all preparation, tuning, completion, validation, queue and credit costs. Existing held-out R10/R13 cases are historical evidence, not tuning data. Hardware submission requires a separate user-approved quote and cap; never infer authorization from a successful local run. Wider rover/activity and orbital-relay integrations follow only after their specific evidence/contracts pass.

## What counts as improvement

Primary metrics are full-plan feasibility, completed mission value, missed deadlines, unmet data demand, reserve margins, elapsed decision time and total cost. Solver status must distinguish a proved optimum, a feasible incumbent, a limit and proved infeasibility. Raw quantum feasibility/optimum rates are secondary diagnostics. The discussed 30% optimal and 70% feasible sample goals are provisional small-benchmark targets, not physical requirements, production release gates or guarantees.

A valid full plan is a condition for acceptance, not a claim of real-world safety. Maintain the existing original-constraint validators and source/model limitations. Record negative results. If quantum adds no measured end-to-end benefit, retain the classical production path and keep the quantum path explicitly experimental.

## Reproduce the stage 1 checks

Run the normal backend, ingestion and optional-quantum test suites plus the TypeScript/Vite build. No frontend tests or screenshots are authorized. The evidence uses the production functions `load_campaign`, `plan_campaign`, `validate_campaign_plan`, `example_requests`, and `build_replay_view`, not imported test fixtures.

To exercise the same scenarios through the running preview (these endpoints calculate locally and do not submit provider jobs):

```sh
curl -fsS http://localhost:8001/campaigns/examples/shared-dsn-conflict | curl -fsS -H 'Content-Type: application/json' --data-binary @- http://localhost:8001/campaigns/plan
curl -fsS http://localhost:8001/campaigns/examples/infeasible-mandatory | curl -fsS -H 'Content-Type: application/json' --data-binary @- http://localhost:8001/campaigns/plan
curl -fsS http://localhost:8001/campaigns/examples/relay-support | curl -fsS -H 'Content-Type: application/json' --data-binary @- http://localhost:8001/campaigns/plan
curl -fsS http://localhost:8001/recovery/examples/hybrid_campaign | curl -fsS -H 'Content-Type: application/json' --data-binary @- http://localhost:8001/recovery/replays
```

The contract rejection probe replaces `hybrid.campaign` in the recovery request with an R11 example; the existing API must return 422. It is not a supported adapter call. Repeat outputs can differ in machine-dependent timing or deadline fallback under heavy load; compare identities, validated decisions and explicit execution mode rather than treating stored wall times as constants.

## Delivery verification

The unchanged merged baseline passed 1,716 backend tests, 218 ingestion tests and 91 optional-quantum tests: **2,025 tests total**. Backend/ingestion Ruff checks and the frontend TypeScript/Vite production build passed. The existing Starlette/AnyIO deprecation and large Vite chunk warnings remain. No frontend tests, screenshots, provider requests or hardware jobs were run.

This branch changes documentation and the recorded baseline evidence only. The pre-existing local H6 notebook edit is excluded from the commit. Stage 2 remains pending: define the integration envelope and supported adapters before implementing the common planner.

## Stage 2 delivery

The native envelope and bounded synthetic identity adapters are now implemented; see [stage 2 contracts, tests and remaining limits](unified-planner-contracts.md). Earlier pending statements above describe the stage 1 delivery. The common coordinator and general quantum proposal path remain later stages.

## Stage 3 delivery

The full classical synthetic pilot and event replan are implemented; see [stage 3 results, admission checks and bounded execution limits](unified-classical-pilot.md). The quantum proposal path, live coordinator and common UI remain later stages.
