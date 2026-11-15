# Terrain-aware recovery integration — 2026-11-15

The previously unmerged R12 implementation is now available alongside H5/H6. The current `self_healing.py`, `hybrid_recovery.py`, `recovery_api.py`, public `twin.py` and their frozen tests are unchanged.

## Integration boundary

- `app.terrain_recovery`: original R12 algorithm, importing its isolated stepwise engine from `app.terrain_twin`.
- `POST /descents/self-healing`: real cached DEM candidate search, sensor drift margins, latency-aware simulated flight, independent retarget validation, original/replanned outcomes and backup reachability over time.
- `POST /recovery/replays`: existing H5/H6 original/classical/hybrid behavior remains available.
- `/twin/runs`: still registered exactly once by the existing R9 module. Ordinary `terrain_twin.run_twin` delegates to the public R9 implementation to preserve its exact output contract; only terrain recovery uses the archived stepwise engine.
- `TerrainRecoveryPanel`: uses the current Descent form's site, vehicle, orbit and reserve. Keeps stale responses from replacing results after inputs change. Decision selection updates the candidate map, and all evaluated target metrics/rejection reasons can be expanded.
- The terrain experiment does not overwrite the shared hybrid mission session. It is a distinct classical, terrain-based experiment; the H5/H6 comparison remains accessible under Advanced.

## Frozen verification baseline

Skeleton/isolation: `eca4986`. Tests: `1fa44d5`. The 26 archived R12 cases were copied with module imports renamed only; their expectations were not changed. Two additional tests cover API coexistence/unique twin routing and public nominal twin output compatibility. All 28 pass. The archived branch remains at `archive/2026-11-15/feature/r12-self-healing` (`85ce6d4`).

## Live DEM check

Local API request at latitude -85.9948°, east longitude 359.76422°, seed 2026, obstacle at 560 s and solver latency 2 s returned HTTP 200. Eight backup candidates were returned. The original failed its landing checks; the replanned branch passed. The retarget decision at approximately 560 s was applied at approximately 562 s, confirming simulated flight advancement during latency. This is simulator evidence, not certified landing safety. Faults/obstacles are synthetic and the terrain comes from the existing cached DEM.

## Restored behavior and original evidence

The following original R12 description is retained for traceability. Its original commits and historical real-data results refer to the archived implementation; current verification is recorded above and below.

# Self-healing landing — R12 (classical part)

Developed on `feature/r12-self-healing` on top of R9.

Commits:
- Refactor: `517c9b4`. It exposes the R9 twin as a step-wise `TwinFlight`. The R9 frozen tests pass and the real-data lost-sensor `log_hash` `a0cdb05e` is unchanged.
- Skeleton: `dbbd828`.
- Frozen test baseline: `1b87714` (`backend/tests/test_terrain_recovery.py`, 26 cases). `git diff 1b87714 -- backend/tests` is empty.

## What it answers

When something goes wrong during the descent, can the lander notice, find a safe alternative it can still reach, check it, and fly it? Or must it say honestly that no safe landing exists?

The same faults are flown twice: once on the original plan (the R9 twin) and once with in-flight replanning. The two outcomes are compared side by side.

## User flow

**Descent → Further analysis → Terrain-aware landing recovery.** It uses the site, vehicle, orbit and reserve from the Descent form. Pick a fault scenario:
- boulder field on the target at 560 s;
- engine −25 % at 520 s;
- navigation sensor lost at 450 s;
- engine −50 % plus a 1.5 t leak at 550 s.

Also set a seed and an optional solver latency. A run takes about 5 s and shows:

- two outcome cards: *original plan, no replanning* and *with self-healing*;
- an altitude–downrange chart of both flights in the original target's frame;
- a map with the original target, the detected (synthetic) obstacle, the backups (reachable ●, rejected ✕) and the chosen target;
- a decision log: time, trigger, action, options evaluated of the budget, solver time, safety margin, explanation, independent validation results, and the rejection reasons when an abort is required;
- reachable backups over time: at high gate, at each decision and at low gate;
- the model assumptions and the decision deadline.

API: `POST /descents/self-healing` (409 without cached terrain, 422 for invalid input).

## How a decision is made

1. **Trigger.** An R9 event that invalidates the active plan (thrust, sensor, propellant or an obstacle on the target). Events with no plan effect, such as a comm outage, do not trigger replanning.
2. **What the planner sees.** The *estimated* state, never the truth. Its uncertainty σ:
   - with a working sensor: the estimator's error for the current noise;
   - with the sensor lost: a conservative bound to touchdown, est σ + speed term·T + ½·bias·(max thrust/mass)·T², where the bias is the 1σ engine performance uncertainty.
3. **Options, within an evaluation budget** (count-based so it is deterministic; default 12):
   - *continue* to the current target;
   - then R7-style backups: DEM cells passing the R3 hard constraints, ranked by safety score.
4. **Each option is flown from the estimate** with R4 ZEM/ZEV guidance. Downrange retargeting rotates the state about the Moon's centre. Crossrange uses the R7 analytic minimum-energy lateral divert (3|c|/T after the fixed-throttle block). An option is acceptable only if it:
   - avoids propellant exhaustion and keeps the R5 reserve after the divert and a k-σ correction allowance (3·kσ/T);
   - meets the R4 touchdown tolerances;
   - stays within maximum thrust (in-plane plus lateral) at every sample;
   - keeps k-σ clearance (k = 3) from detected obstacles and from R3 steep terrain.
5. **Selection.** A selector orders the acceptable options. The classical default is highest safety score, then least Δv. The selector is a plug-in point for hybrid/quantum selection (TK's H6).
6. **Independent validation.** Before anything is flown, the selected option is re-flown from the *true* state at 0.05 s. If it fails, the next option is tried.
7. **Actions:**

   | Action | Meaning |
   |---|---|
   | `continue` | The current target is still reachable; the plan is re-validated with a new revision |
   | `retarget` | The vehicle switches to the validated backup |
   | `abort_required` | No evaluated option is feasible or passes validation. Abort-to-orbit would be required; it is not simulated, and the old plan keeps flying so its consequences are visible |
   | `budget_exhausted` | The budget ran out before an acceptable option was found; the current plan is kept without validation |
   | `no_time_to_replan` | The event came after the deadline (default low gate + 10 s) |
   | `stale_result_rejected` | With solver latency, new events arrived before the result was ready; it is discarded and recomputed for the newer state revision |

   Hover/defer is not modelled.

## Verification

Tests (`test_self_healing.py`) cover:

- validation;
- the margin rule;
- R4 time-step convergence (0.5/0.2/0.1/0.05 s; mass differences shrink and stay under 1 kg);
- the ranking order;
- options from high gate: continue, a sideways backup and an infeasible 60 km divert;
- obstacle, margin and hazard rejections, with obstacle frames after a retarget;
- no events → no decisions and identical outcomes;
- an obstacle → validated retarget and a safe landing, while the original plan fails;
- backups over time;
- moderate thrust loss → continue;
- thrust loss plus a large leak → abort required, with reasons for every option;
- a degraded sensor widening the margin;
- stale results rejected and recomputed;
- an exhausted budget;
- a bad selection caught by validation;
- the selector hook;
- events after the deadline;
- determinism (wall-clock time excluded from the result ID);
- the API.

Real data, Malapert R23, seed 2026, default sensor ([evidence](verification/r12-self-healing-2026-11-14.json)):

| Scenario | Original plan | With self-healing |
|---|---|---|
| Boulder field (150 m) on the target at 560 s | ✕ lands inside the obstacle | ✓ retarget to B1, 461 m away, validated from the true state; safety score 0.29 → 0.50 |
| Engine −25 % at 520 s | ✓ | ✓ *continue*: one evaluation, plan re-validated |
| Engine −50 % + 1.5 t leak at 550 s | ✕ runs out of propellant | **abort required**: all 9 options infeasible, with reasons |
| Navigation sensor lost at 450 s | ✕ lands 471 m off | **abort required**: projected 3σ uncertainty 2.35 km, while the nearest steep terrain is 288 m from the target |

The last row is the counterpart of R9. There, the lander did not know it was lost. Here, the planner bounds the drift and refuses to land blind.

## Roadmap items left open (hybrid, with TK)

- Hybrid/quantum candidate selection. The selector interface is ready.
- An asynchronous quantum path with a time budget and a classical fallback.
- Propagating a changed landing time or location into the R11 multi-mission schedule.
- A three-way replay (original vs classical vs hybrid). Original vs classical is done.

## Not modelled

- Abort-to-orbit, hover and landing-point redesignation by a pilot.
- Crossrange dynamics beyond the analytic divert, attitude dynamics and engine dynamics.
- Hazards smaller than the 80 m DEM cell (obstacles here are synthetic). Sunlight and communication at the new target; use R8 and the site analysis for that.

## Integration verification — 2026-11-15

- Full backend suite: **2,057 passed** in 334.06 s; one existing AnyIO deprecation warning.
- Full ingestion suite: **218 passed**.
- Ruff passed for restored modules, route registration and new tests.
- Frontend TypeScript/Vite production build passed. Existing bundle-size advisory remains. No frontend tests or screenshots were used, per repository instructions.
- `/descents/self-healing` and existing `/recovery/replays` both returned HTTP 200 in local runtime checks. The existing H5/H6 and R9 implementation files have no diff from pre-integration main.
- `git diff 1fa44d5 -- backend/tests/test_terrain_recovery.py backend/tests/test_recovery_coexistence.py` is empty. The archived R12 assertions match exactly after renaming module imports.
- Compact live evidence: [terrain-recovery-2026-11-15.json](verification/terrain-recovery-2026-11-15.json).
