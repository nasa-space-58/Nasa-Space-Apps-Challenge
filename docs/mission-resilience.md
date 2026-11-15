# Causal mission resilience replay

This branch adds a classical, offline mission replay to the actual application
factory and a **Mission resilience** mission-control tab (`#/resilience`). It is
separate from the older single-landing unified pilot. It does not turn that
pilot, a decorative 3D flight, or archived quantum experiments into live control.
C1/C2 evidence and NO-GO conclusions are unchanged. No quantum provider is called.

## What executes

A scenario declares an explicit UTC epoch, MET horizon, initial resources,
vehicle, planar terminal state/corridor, launch windows, cruise routes, surface
activities, and up to 32 phase-relative fault events. The maximum horizon is
300 seconds. These short timelines are deliberate illustrative scenarios, not
real Earth-to-Moon travel durations. JSON input is editable and strictly
validated. Seven API-provided examples exercise all four entry phases.

| Phase | Working decision and carryover | Model boundary |
|---|---|---|
| Launch | Weather/window gates; finite hold consumes energy and mission time; launch bookkeeping consumes propellant; arrival deadline screened | Illustrative ground hold/injection accounting; no ignition, ascent, atmosphere, weather feed or launch abort dynamics |
| Cruise | Choose among declared routes under thrust/link/fuel/time limits; progress and consumed resources survive route changes; missed arrival is rejected | Declared route duration/fuel/minimum-thrust values, no orbit propagation or Lambert solution |
| Descent | Replan explicit alternate targets using current estimated state, remaining deadline, thrust and fuel; H4/H5 coarse/fine dynamics, terrain exclusions and five navigation probes; actual simulated vehicle keeps moving while plans are pending | Nonrotating planar corridor; finite probes and sampled terrain, not continuous safety certification; fewer than five seconds remaining is outside the existing replanning model |
| Surface | Prioritize interruptible declared tasks; check battery reserve, communication, equipment, deadlines and temperature; heating/cooling consumes power; failed tasks remain unfinished with reasons | Constant-power battery balance and declared linear temperature drift/control, no calibrated thermal hardware or battery degradation model |

Cruise-to-descent geometry is explicitly the supplied terminal-state handoff.
Fuel, energy, temperature, equipment and absolute time carry forward without
replenishment. Surface completion means all declared tasks completed, not that
an infinite mission or an unmodeled return flight is safe. If no candidate can
be validated, the result is `no_feasible_continuation`; it is not an invented
safe stop, hover or abort. Tasks unfinished when the replay ends are reported.

## Causality and knowledge

The environment alone owns the event schedule and latent truth. `plan_mission`
accepts `MissionConfig` and `PlannerView`, neither of which contains future
events. Notices may arrive late; their occurrence time prevents an older notice
from overwriting a newer state. Resource telemetry is explicitly ideal in this
version. Two synthetic navigation channels can disagree: their midpoint is the
estimate, their disagreement enlarges the declared uncertainty envelope, and a
large conflict blocks landing admission. Five axial probes do not certify all
points in an uncertainty set or a calibrated failure probability.

The planner never selects from simulator truth or a future fault list. The UI
can expose **post-hoc simulator truth** for diagnosis, clearly separated from
the planner estimate. Scrubbing limits visible observations and decisions to
those available at that replay time. The final task summary remains explicitly
labeled as a final result, even when inspecting an earlier frame.

## Planning and control time

The first decision at MET zero is an offline preflight check. Subsequent
calculations, including phase transitions, incur the declared simulated
`planning_delay_s`. This is not inferred from workstation runtime. The vehicle
and resource model advance during this interval. The proposal records its input
time and revision; a changed observed revision or expired proposal is rejected.
A surviving target is independently screened at the current estimate before
acceptance, rather than copying an old trajectory into the new state.

Immediate action checks also guard existing controls while calculation is
pending. Known-invalid surface work and cruise actions are inhibited and finite
baseline power is still consumed. If even this consumption violates reserves or
temperature bounds, the replay fails explicitly. During descent, momentum and
the existing controller continue; an invalidated control is labeled
**unvalidated**, not safe waiting. True thrust/fuel changes invalidate the
simulated trajectory and use the current physical state for propagation.
A newly accepted landing target changes subsequent simulated control.

Classical choice is deterministic finite enumeration with explicit ordering:
launch over hold, feasible route duration, near-nominal landing target with
physical checks, and thermal protection before prioritized surface work. It is
not a globally optimal stochastic mission policy or a learned controller.
H4/H5 numerical checks retain their existing tolerances. No test expectation,
physical tolerance or quantum gate was loosened to improve a result.

## API, persistence and lifecycle

- `GET /resilience/examples`: complete, reproducible example inputs.
- `POST /resilience/runs`: `{request_id, scenario, ttl_s}`; returns HTTP 202.
- `GET /resilience/runs`: recent 20 job summaries.
- `GET /resilience/runs/{id}`: job state and complete result after completion.
- `POST /resilience/runs/{id}/cancel`: idempotent offline cancellation.

The app owns `STATE_DIR/resilience.sqlite` and a dedicated sequential worker.
At most eight jobs may be queued/running; the archive is bounded to 100 jobs,
then fails closed instead of silently deleting idempotency records. Request IDs
are bound to complete inputs; identical retries return the original job, and
conflicting reuse returns 409. No automatic retry occurs. Completed results
must match the input fingerprint. Cancellation, wall-clock expiry and restart
fence a late worker result from publication. Cooperative checks run between
simulation steps and after candidate planning; they are not hard CPU interrupts.
An exception records a failed job, keeps diagnostic details in the local log,
and does not prevent later jobs from running.

As with the existing unified pilot, use one application owner per state directory.
This is a local development API without authentication or distributed worker
leases, not a public deployment or an actuator service. The replay worker is
started with the normal app lifecycle. The isolated verification factory used
the same `create_app` routes and same worker method, with unrelated acquisition
workers disabled. Other running Docker projects were not recreated or stopped.

## Interface

The new tab includes example selection, editable JSON, run/cancel/refresh,
recent durable results, JSON export, a keyboard-operable replay slider, four
phase indicators, fuel/energy/temperature/thrust cards, communication/equipment
status, navigation uncertainty, delivered notices, every rejected candidate,
chosen-plan conditions and replanning history. Exported results include the full
original scenario, including custom resources and event timing, for exact replay. English and Turkish interface
labels are supplied. Scenario and source descriptions preserve their original
English input text. The existing 3D overview is not driven by this replay.

## Verification

Acceptance was recorded before implementation in
[mission-resilience-protocol.md](mission-resilience-protocol.md).
Frozen tests: `83468d9` (contracts/replay/lifecycle), `90d3e06` (current controls and
old observations), `eda5afe` (actual app routes), `b2a005a` (phase-transition
latency), `c040cf8` (custom-input export). All frozen test files remain unchanged.

Final measurements and validation are recorded in
[the evidence file](verification/mission-resilience-2026-11-15.json).
The measured scenarios are synthetic acceptance examples, not estimates of
mission success rate. A rejected recovery is an expected, informative outcome
in the deliberately impossible examples.

## Remaining model work

Real telemetry and sensor fusion, correlated sensor/actuator errors, validated
launch/orbital/thermal models, 3D attitude and terrain, distributed execution,
mission-wide robust policy optimization, uncertainty calibration, and independent
flight qualification remain unimplemented. The finite candidate set can miss
other possible recoveries; a rejected set is not a proof that every physical
recovery is impossible. No result implies quantum advantage or flight readiness.

## Observed scenario outcomes

| Scenario | Outcome | End MET | Planning decisions |
|---|---|---:|---:|
| Nominal four-phase mission | completed | 54.00 s | 6 |
| Weather delay and cruise outage | completed | 70.00 s | 13 |
| Cruise thrust loss and fuel leak | no_feasible_continuation | 16.00 s | 5 |
| Landing fallback then fallback failure | no_feasible_continuation | 9.00 s | 4 |
| Conflicting landing sensors | no_feasible_continuation | 1.00 s | 2 |
| Surface power and communications recovery | completed | 14.00 s | 5 |
| Surface equipment and energy exhaustion | no_feasible_continuation | 5.00 s | 2 |

The landing failure chain selected primary at MET 0, backup at 2 s and the
second backup at 6 s, then rejected continuation at 9 s after all three were
excluded. Conflicting navigation channels blocked continuation at 1 s. The
surface communication-recovery case resumed transmission and finished at 14 s.
These are deterministic model outcomes, not flight success statistics.

The final HTTP checks ran seven scenarios with duplicate-command verification.
Browser checks exercised normal and failure replay, cursor navigation, truth
inspection, cancellation without stale output, invalid JSON, restored-link
completion, English/Turkish labels and evidence download. Desktop (1280 px)
and mobile (390 px) screenshots were inspected; the panel had no horizontal
overflow. The existing 3D overview remains a separate visualization.

Local timing observations are included in the evidence, but overlapping machine
load and this small fixture set do not support a throughput or flight-computer
performance claim. Earlier intermediate checks were superseded after the
phase-latency and export-provenance changes; final evidence uses the final code.

## Final checks

**2,343 tests passed:** 2,031 backend (including 57 new checks), 218 ingestion,
and 94 optional quantum tests. Backend/shared and ingestion Ruff checks passed.
Frontend clean `npm ci` reported zero vulnerabilities; TypeScript lint and
production build passed. Compose configuration validated. Existing Starlette,
Qiskit and Three.Clock deprecations and Vite's large-chunk warning remain.
The source hashes in the evidence were rechecked after final validation.

No quantum job was submitted, including the previously canceled OpenQuantum
job `dd340d59-3d25-43e0-8268-df5607b84e3a`; zero credits were spent. C1/C2
reports and the user's pre-existing H6 notebook edit were preserved.
Only this development branch is published; no merge or production rollout.
