# R12 — Self-healing event panel

This closes the last open R12 item: a visual event panel with original and replanned trajectories, an uncertainty display, decision explanations and solver timing. The panel is built on the H5 fault replay ([self-healing](self-healing.md)) and the H6 guarded hybrid recovery ([hybrid recovery](hybrid-recovery.md)). It adds no new physics, estimator or solver.

Branch: `feature/r12-event-panel`, started from `main` `90d6561`, with `main` `8c3dff5` (PR #18) merged in before delivery. Test-first history: skeleton `e9600d0`, frozen tests `7332c3e` (`backend/tests/test_recovery_api.py`), implementation `251e81d`, UI `1daceff`.

The older `feature/r12-self-healing` branch was not merged. Its backend is a separate twin-based implementation that conflicts with H5 in `backend/app/self_healing.py`, its tests and `docs/self-healing.md`.

**Subsequent restoration (2026-11-15):** The terrain-based R12 features are now restored under separate `terrain_recovery` / `terrain_twin` modules and `/descents/self-healing`, without replacing this H5/H6 panel or its API. See [terrain recovery](terrain-recovery.md) for the integration boundary and verification.

## Backend: replay view

`backend/app/recovery_api.py` exposes:

| Route | Purpose |
|---|---|
| `GET /recovery/examples` | Lists `shared/examples/recovery/*.json` as ready request bodies, and says whether each includes a hybrid campaign. |
| `GET /recovery/examples/{name}` | Returns `{recovery, hybrid}`. `hybrid` is `null` for plain H5 scenarios. |
| `POST /recovery/replays` | Validates a `RecoveryReplayRequest` and returns the replay view (`r12-replay-view-v1`). Invalid input returns 422. |

`build_replay_view()` replays the same declared events up to three times:

1. **original**: H5 `replay_recovery(replan=False)`, which keeps the original target.
2. **classical**: H5 event-triggered replanning.
3. **hybrid** (only when `hybrid` is supplied): H6 `replay_hybrid()` with the supplied campaign, bindings, dependencies and quantum budget.

For each branch, the view reports:

- status and final reasons
- end time and delay against the nominal deadline
- touchdown downrange
- propellant burned, lost and remaining, and the margin against the declared reserve
- applied and unapplied events
- the replay's wall time
- a trajectory capped at 400 points

Each decision also reports:

- its revision and H5 input hash
- the action taken, and the previous and selected targets
- active landing exclusions
- each candidate's reasons, predicted burn and sample pass count
- the min/max/mean/std of sampled touchdown downrange and fuel, with raw sample points
- a deterministic explanation
- `planning_wall_s`

For hybrid decisions, the view also reports:

- the source (`quantum` or `classical_fallback`) and the reason
- the joint schedule's option IDs and exact objective score
- preparation, simulator-worker and revalidation times
- elapsed time against the acceptance deadline

Per-decision wall time uses a new optional `decision_timings` list on `replay_recovery()` and `replay_hybrid()`. Timing is kept outside the returned H5/H6 evidence, so identical inputs still replay identically. A frozen test checks that H5 output does not change when the hook is supplied. Apart from the timing fields, the view is deterministic.

## Frontend: Self-healing tab

`frontend/src/components/SelfHealing.tsx` is a new mission-control tab with English and Turkish text. It contains:

- **Outcome cards** for each branch: status, final target, delay, fuel left against the reserve, burned/lost fuel and replay wall time.
- **Trajectory chart**: altitude against downrange for each branch, with landing-exclusion bands, target markers, event markers, and the selected decision's sampled touchdown dots (pass or fail) with their spread.
- **Timeline**: target deadlines, event lines and clickable decision markers for each branch.
- **Event panel**: time, sequence number, kind and the applied effect.
- **Decision detail**: the explanation, timing, and a candidate table with reasons, predicted burn, samples passed and dispersion.
- The hybrid execution note and the backend's limitations.

The frontend performs no scientific calculation. It only maps API values to pixels.

## Executed evidence — 2026-11-15

- 31 new frozen R12 tests passed. The frozen test file is unchanged since `7332c3e`.
- 1373 backend tests and 185 ingestion tests passed. Backend/shared Ruff checks passed. One existing Starlette/AnyIO deprecation warning remains.
- `docker compose run --rm frontend npm run build` passed. As the project rules require, no frontend tests or screenshots were used.
- Live run on the local stack with the `hybrid_campaign` example:
  - **Original**: fails inside the new −5…5 m landing exclusion.
  - **Classical**: retargets to `backup`, landing with 79.667 kg left.
  - **Hybrid**: retargets to `remote`, landing with 76.814 kg left. Its schedule is `land-remote` + `follow-late`, and the quantum result was accepted.
  - `engine_out` reports no validated continuation.

## Limits

All H4–H6 limits still apply. Inputs are synthetic, and the physics is a planar model with sampled terrain. Sample pass counts are not calibrated probabilities. Infeasible does not mean a safe abort. The hybrid branch is a noiseless local simulator run with the simulated clock paused while it plans; it is not live control, hardware execution or evidence of quantum advantage. Wall times depend on the machine and its load.
