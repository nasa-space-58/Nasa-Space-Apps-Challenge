# Stage 5 — event coordination and shared mission views

Stage 5 adds a durable coordinator around the stage 3 complete classical planner
and stage 4 bounded local proposal comparison. Campaign, Self-healing and Solver
Arena share a session, input revision, fingerprint and accepted plan identity.
The existing independent experiments remain available in collapsed sections;
their results cannot overwrite the shared plan.

## Reproduce

Run the existing development Compose services. Open `#/campaign`, create a pilot
session and calculate a classical plan. Open `#/recovery` and submit the default
landing exclusion at simulation second 8 over downrange [-5, 5] metres. Select
local quantum comparison before submitting to include matched QAOA, random and
greedy proposals. Open `#/solver-arena` to inspect the same session's results.
The session selection is saved locally and recent sessions can be reopened.
English and Turkish labels are provided.

The example changes the accepted landing target from `primary` to `remote`.
Dependent rover timing and the shared antenna schedule are recomputed for all
three missions. There is no strict full-plan improvement over the classical
incumbent in this pilot; classical remains the accepted source. Unsupported
compact encodings explicitly retain the current classical baseline. All-target
exclusion produces `no_feasible_continuation`, with no stale fallback.

## Durable lifecycle

`STATE_DIR/unified.sqlite` persists native inputs, events, job commands, full
experiment evidence and accepted/history records. A session is created from the
versioned synthetic pilot fixture, not arbitrary user-supplied scientific input.
The service has a separate sequential worker. SQLite transactions claim queued
jobs atomically, and calculations execute outside write transactions. Pending
work is bounded to eight jobs globally. Default acceptance TTL is 30 seconds;
the API allows a positive TTL of at most 120 seconds.

Commands bind a request ID to the complete validated command. Repeated identical
commands return the original job; conflicting reuse returns HTTP 409. Session
revision checks prevent stale commands. Each landing exclusion has a unique event
ID and must advance the frozen simulation clock within the native [0, 10 s)
decision window. Replaying an event does not propagate the state twice.

An event atomically increments the input revision, archives the old accepted
plan, supersedes older queued/running work and enqueues a new job. An old thread
may finish its computation but cannot commit its result. Admission requires the
same revision, active job ID, running status, input fingerprint and an unexpired
wall-clock deadline after independent physical and full campaign validation.
Queued expiry avoids computation. Expiry during calculation or validation
prevents application. Expiry is an acceptance fence, not a hard CPU interrupt.

On startup and shutdown, interrupted running jobs are fenced off and are not
retried automatically. Queued jobs survive restart. A failed refresh attempt on
unchanged inputs may leave the prior valid plan in place; an input-changing event
always clears it. Historical plans are explicitly separated from current plans.

## API

- `POST /unified/sessions`: create a synthetic pilot session.
- `GET /unified/sessions`: list the 20 most recent sessions.
- `GET /unified/sessions/{id}`: shared state, current/historical plans and job summaries.
- `POST /unified/sessions/{id}/plans`: queue a revision-bound command.
- `POST /unified/sessions/{id}/events`: atomically apply an exclusion and queue replanning.

A plan command supplies `request_id`, `expected_revision`, `mode` (`classical` or
`local_experiment`), optional `ttl_s` and bounded stage 4 `budget`. An event adds
`event_id`, `elapsed_s`, `start_downrange_m`, and `end_downrange_m`. Conflicting
commands return 409, invalid input returns 422, missing sessions return 404.
Neither endpoint accepts a hardware mode or has access to quantum credentials.

## Evidence and limits

Frozen test commits: `c2fef39` (19 checks including parameter cases) and
`202a824` (four full-outcome checks). The tests cover durable reopening,
duplicate requests, old revision rejection, event/result races, exclusive claims,
shutdown fencing, expiry before and after calculation, queue capacity, generic
failure handling, invalid and backward events, no-go outcomes, route registration
and separate timing fields. Existing frozen tests were not modified.

The live API replay is recorded in
[stage 5 evidence](verification/unified-planner-stage5-2026-11-15.json).
Queue, aggregate calculation and independent admission times are reported
separately, with preparation, baseline, proposal, full completion and candidate
admission times inside comparison evidence. The aggregate calculation time
includes these substeps; they must not be added to it again.

This is one service process and a bounded synthetic pilot. Do not run multiple
worker processes against this database: startup recovery assumes sole ownership.
There is no user authentication, distributed lease, history retention policy,
telemetry integration or atomic vehicle command dispatch. The simulation clock
is paused between explicit events; cloud queue latency is not silently treated
as live flight time. Event support currently means landing exclusion only, not
all sensor, relay, weather or rover fault types. The wider stage 6 evaluation and
domain adapters remain open.

Frontend validation uses TypeScript and production build only, following the
repository's no-frontend-tests/no-screenshot rule. Visual layout still requires
human inspection. API behavior was exercised against the running local worker.

## Final local verification

On 2026-11-15, 1,860 backend tests, 218 ingestion tests and 91 optional quantum
tests passed (2,169 total). Backend/shared and ingestion Ruff checks passed.
TypeScript and the Vite production build passed. Existing Starlette/AnyIO
deprecation and Vite large-chunk warnings remain. The five read-only cells in
[the stage 5 evidence notebook](../notebooks/Unified_stage5_coordination.ipynb)
executed successfully without a service request or hardware submission.

A separately authorized one-qubit, 256-shot QPU trial uses a maximum of one
Spark credit, with no automatic retries. Its fixed protocol is recorded in
[hardware protocol](verification/unified-stage5-hardware-protocol.json).
Hardware evidence is kept separate from the shared application's local-only
execution modes and cannot overwrite an expired or revised session.
