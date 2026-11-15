# Unified mission planner — stage 3 classical pilot

Stage 3 completes the full R11 campaign around an H6 physical continuation. It adds an internal Python planning/admission interface and a reproducible three-mission synthetic pilot. It does not add a web endpoint, change the existing UI, run QAOA, submit hardware jobs or enable production quantum policies.

Implementation: `backend/app/unified_classical.py`. Fixture: `shared/examples/unified/classical-pilot.json`. [Recorded evidence](verification/unified-planner-stage3-2026-11-15.json). The stage 2 envelope and existing solvers/validators remain unchanged.

## Delivered behavior

The pilot contains an active lander, a rover mission that must start at least 15 seconds after the lander arrives, and a separate robotic lander. All three require data delivery through the same capacity-one station at a declared 1 bit/s rate. The rover is a scheduled R11 mission; this pilot does not simulate rover driving or the second vehicle's flight.

| Case | Active lander | Rover start, seconds from epoch | Remaining propellant | Result |
| --- | --- | --- | --- | --- |
| Nominal | `primary`, approximately 30 s | 45 s | 82.739496 kg | Full campaign independently feasible; physical continuation passes |
| Exclusion detected at 8 s | `remote`, approximately 35 s | 105 s | 79.668699 kg | Primary rejected; dependent mission rescheduled; full campaign and continuation pass |
| All targets excluded at 8 s | No selection | No accepted plan | Not reported as a successful landing | `no_feasible_continuation` |

Both feasible cases contain selections for all three missions, actual link allocations and independently checked data-delivery/dependency outcomes. These results demonstrate functional integration, not a NASA operational plan or a quantum advantage.

## Planning and physical validation

`plan_classical(snapshot, current=..., now_utc=..., completed=..., time_limit_s=...)` performs the following steps:

1. Revalidate both native inputs, snapshot identity and the decision window using stage 2's adapters. Admit one active H6 landing mission; other missions stay in full R11. Reject unapplied future faults and inconsistent initial fuel inventories.
2. Call the existing H6 physical preparation. H5 screening propagates each target at two resolutions, checks terminal conditions and exclusion zones, and evaluates seeded uncertainty samples. H1 independently checks the prepared single-mission H0 resources/constraints. No H0 optimization or QAOA worker is invoked.
3. For each surviving target, derive a complete R11 problem with the **actual fine-integration arrival time**, without rounding to the campaign's communication grid. Arrival must be within the original nominal landing policy. The active mission's window is restricted to that arrival; unrelated mission policies, station capacities/calendars, data demands/rates, dependencies, relay rules and preferences are retained.
4. Replace the selected site's active-vehicle descent estimate with total fuel depletion from the declared initial stock to the predicted final stock. This includes depletion before the current checkpoint; it does not reset the budget during replanning. The R11 reserve must also hold in every recorded uncertainty outcome, in addition to H6's own reserve checks.
5. Force that particular active choice and any completed choices, exclude past unexecuted candidates, and solve **all R11 missions** with the existing HiGHS MILP. No `only_mission_ids` or relaxed `required_mission_ids` mode is used. Validate every returned plan independently with `validate_campaign_plan`.
6. Compare feasible target branches using exact rational scores. Because fixing the active arrival shifts R11's window-relative waiting origin, restore the original active waiting penalty before comparing branches. This prevents a later touchdown from receiving a free waiting-cost reset. Ties use stable target identity.

Each branch carries its physical evidence, derived full campaign, full plan, validation, score and rejection/limit reason. `optimum_proven_over_screened_targets` applies only to the supported finite target set and the completed native MILP solves. It does not claim a globally optimal trajectory, all landing times/sites or certified real-world safety.

`time_limit_s` is a finite per-target native solver budget in `(0, 30]`, not a total wall-clock deadline. Physical preparation and acceptance rechecking are additional work; R11 also performs its existing allocation-refinement solve. The result records elapsed computation time and explicitly labels the paused simulation clock.

## Admission is separate from solving

`accept_classical(decision, snapshot, current=..., now_utc=..., completed=...)` repeats snapshot/ledger freshness checks, verifies the decision checksum, **re-runs physical screening**, rebuilds the expected derived problem, and independently validates the full R11 plan again. It also checks the selected active target, past-choice exclusions and completed-action preservation. A recomputed checksum cannot make missing communication allocations feasible.

The caller supplies the current authoritative inputs and execution ledger. Hashes provide identity/change detection, not authentication, telemetry freshness or atomic actuator dispatch. The returned candidate is an admitted offline continuation; this function does not command a vehicle or execute a mission. Event-driven persistence, concurrent acceptance and shared UI identities remain stage 5 work.

## Exact-time event and completed actions

`apply_landing_exclusion(snapshot, event)` advances the declared synthetic estimate toward its current target to the exact event instant using the existing terminal propagator. It increments state, campaign and integration revisions, records the exclusion and versioned event provenance, and retains the original decision deadline. Earlier decisions therefore become stale.

This helper supports zero navigation uncertainty and a single explicitly supplied landing-exclusion event at a time. It rejects backward/expired events, duplicate event IDs, invalid intervals, future queued faults and termination before the requested airborne event. It is a deterministic replay helper, not a state estimator. Planning itself still uses H5's finite uncertainty-screening machinery. Handling other fault streams, nonzero-uncertainty state estimation and live telemetry requires the later coordinator.

`CompletedMission` is a caller-supplied executed-action ledger. Entries must name known non-active missions whose modeled surface operation and supplied allocations are already in the past. Completed choices are forced and their allocations must match exactly; acceptance rechecks the same ledger. The tests demonstrate preservation of a completed zero-downlink mission and exclusion of that past choice when no completion record exists.

The native R11 solver cannot force arbitrary historic link-allocation tuples. If its completion would alter a supplied executed allocation, the branch is **not accepted** and reports `completed_allocation_lock_unsupported` / `limit_reached`. It does not rewrite history or mislabel this limitation as proved infeasibility. Partially executed missions and a general immutable allocation scheduler remain outside this bounded pilot.

## Failure states and modeling limits

- `feasible`: a complete native campaign and physical continuation exist; a time-limited incumbent may still be feasible without an optimum claim.
- `limit_reached`: a solver limit or unsupported exact allocation lock prevents a conclusive accepted completion. This is not proved infeasibility.
- `no_feasible_continuation`: no continuation passed the supported physical/nominal-policy/H0/R11 checks. No plan is applied, and this is never described as a safe abort.

The input uses declared synthetic target/site correspondences. H6 remains a nonrotating planar model; R11 site labels remain MOON_ME. No real coordinate conversion is inferred. Physical arrival and fuel evidence are refreshed, while other source-bound R11 evidence remains under its existing coverage/unknown checks. R11 net-energy balance is not a battery trajectory, sampled uncertainty passes are not certified probabilities, and only the active lander's continuation is propagated.

All H0–H11 roles remain in the common architecture catalog. This stage provides the classical reference the later bounded quantum proposal path must match. H7 credit approval and R10/R13 NO-GO policies remain unchanged.

## Reproduce in the backend environment

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.unified_planning import UnifiedPlanningEnvelope, seal_inputs
from app.unified_classical import (
    LandingExclusion, plan_classical, accept_classical, apply_landing_exclusion,
)

now = datetime(2030, 1, 1, tzinfo=timezone.utc)
current = UnifiedPlanningEnvelope.model_validate_json(
    Path('/workspace/shared/examples/unified/classical-pilot.json').read_text()
)
snapshot = seal_inputs(current)
decision = plan_classical(snapshot, current=current, now_utc=now)
accepted = accept_classical(decision, snapshot, current=current, now_utc=now)

event = LandingExclusion(
    event_id='obstacle-1', epoch_utc=now + timedelta(seconds=8),
    start_downrange_m=-5.0, end_downrange_m=5.0,
)
current = apply_landing_exclusion(snapshot, event)
snapshot = seal_inputs(current)
decision = plan_classical(snapshot, current=current, now_utc=event.epoch_utc)
accepted = accept_classical(
    decision, snapshot, current=current, now_utc=event.epoch_utc,
)
assert accepted.target_id == 'remote'
assert accepted.validation.status == 'feasible'
```

## Test provenance

Skeleton: `4c4110b`. Frozen pilot tests/fixture: `bdeeb7c`. Additional frozen independent admission/reserve tests: `6dbc897`. None of these tests or the fixture changed during implementation. The 35 new tests cover the complete three-mission plan, exact physical arrival, total fuel accounting, sampled R11 reserves, event-driven target/dependency changes, stale/expired/tampered decisions, rehashed invalid allocations, repeat physical validation, infeasibility versus limits, executed-action preservation, objective consistency and absence of QAOA calls.

## Full verification

1,806 backend, 218 ingestion and 91 optional-quantum tests passed: **2,115 total**, including 35 new stage 3 tests. Backend/ingestion Ruff checks and the TypeScript/Vite production build passed. Existing Starlette/AnyIO deprecation and large Vite bundle warnings remain. No frontend tests/screenshots or live provider jobs were run. The pre-existing local H6 notebook edit is unchanged and excluded from commits.
