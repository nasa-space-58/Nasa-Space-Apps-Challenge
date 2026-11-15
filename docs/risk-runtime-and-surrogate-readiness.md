# Bounded local risk API reuse and surrogate readiness

Exact physics reuse is now enabled by default in this branch's
`POST /descents/risk` route, used by `frontend/src/components/RiskPanel.tsx`.
The response schema and scientific result identity are unchanged. This is a
local API integration, not a production deployment or a change to other mission,
recovery or quantum endpoints. Shared running Docker services were not restarted.

[Pre-registered scope](risk-runtime-protocol.md) was committed before the tests
and measurements. C1 and C2 reports remain unchanged.

## Worker, memory and safety ownership

Each app factory owns one dedicated risk computation thread and at most two
admitted requests, including the running one. Excess requests return HTTP 429.
Shutdown rejects new work, signals active work, cancels queued work, joins the
thread, then clears retained entries. An instance used in a different process
without a new app factory fails closed; workers do not share a cache.

The default cache retains at most 128 entries and **32 MiB of encoded payloads
plus digest bytes**. This is not a whole-process RSS limit: a running simulation,
JSON decoding, terrain window and Python metadata require additional memory.
Only one full simulation request runs per worker. Entries use immutable JSON
payloads and isolated decoded values; NumPy scalars retain their numeric values.
Oversized results are computed normally but not retained. A request protects
entries it hits or admits so that a long scan cannot evict its own reusable
prefix. The next request may replace unprotected old entries. This avoids LRU
thrashing when the default 200 draws exceed capacity.

Every dynamics argument and resolved vehicle is included in the key. Nominal
input/tolerance changes miss the appropriate key. Changes used only after the
trajectory, such as approach direction, navigation dispersion, reserve and
terrain hazards, are evaluated again even when dynamics are identical. Terrain
height and the slope window are read from verified files on each request;
changed or corrupt terrain cannot be hidden by a cached risk response because
whole risk responses and hazard classifications are never cached.

Results describe the inputs read for that request, not a continuously updated
flight state. Acquisition files must retain the repository's immutable raw-data
contract. In-place code/constant hot mutation is unsupported: restart the worker
on model changes. Entries never persist across restart. The internal explicit
`clear(revision=...)` is atomic with computation and invalidates retained entries.
There is no public cache-management or diagnostics endpoint in the normal app.

Cancellation is cooperative. Client disconnect or coroutine cancellation sets a
stop flag, checked before planning, between full integrations, and before
result delivery. An integration already running is allowed to finish; no
partial Monte Carlo report is returned. Completed deterministic trajectories
may still be retained. This is not a hard real-time cancellation guarantee.

## Actual HTTP verification

A separate loopback Uvicorn server used an 81 x 81 synthetic, checksum-verified
80 m DEM. Two 24-draw seeds (831/832) and the normal 200-draw case (seed 833) were
run with independent cold servers for cache on/off, then repeated warm. Full
response objects and result IDs matched exactly in all cases.

| Draws / seed | Cache off first | Cache off repeat | Cache on first | Cache on repeat |
|---|---:|---:|---:|---:|
| 24 / 831 | 2.180 s | 2.173 s | 2.143 s | 0.072 s |
| 24 / 832 | 1.914 s | 1.928 s | 2.146 s | 0.077 s |
| 200 / 833 | 10.507 s | 10.565 s | 15.000 s | 7.066 s |

The 24-draw repeats reused all 24 integrations. At 200 draws the byte cap retained
76 trajectories plus the nominal result: **76 hits / 124 new integrations** on
the repeat, using **33,519,763 bytes**, below 33,554,432 bytes. Capacity bypass is
explicit and never skips a required calculation. Do not describe this as an
across-the-board speedup: the 200-draw cold cached run was slower in this sample.
Its counters recorded about 0.97 s cache overhead; uncached integration time also
varied (13.10 s in that cold run versus 9.63 s in the off run). Shared-machine
observations do not isolate all of that difference as cache overhead.

Three real TCP clients abandoned admitted 1,000-draw requests. The worker became
available in about **0.113–0.194 s** after disconnection in these checks. All six
server shutdowns completed with zero pending work and zero retained entries.
A cancelled request never became a completed scientific result. Separately,
real API tests changed a verified flat map to a steep map, then corrupted the
terrain file: hazards were recomputed and corruption returned 422 despite warm
physics. Tests also cover concurrency, queue overflow, exceptions, changed
vehicle/orbit/seed/uncertainty/reserve/tolerances, invalid configuration and
shutdown while work is running.

These timings do not measure large NASA raster I/O, distributed workers,
production deployment, or the other H/R flows. Terrain checksums still run; they
were not bypassed to make the benchmark faster. The verifier's private counter
route exists only in its test factory, never in `app.main`.

## Configuration and reproduction

Defaults are recorded in `.env.example` and passed through local Compose:

```dotenv
RISK_PHYSICS_CACHE_ENABLED=1
RISK_PHYSICS_CACHE_BYTES=33554432
RISK_PHYSICS_CACHE_ENTRIES=128
```

Set enablement to `0` at worker startup for the uncached path. Invalid flags or
non-positive capacities fail startup. This task did not restart anyone's
existing backend. This branch must be the source of the app worker to use the
new behavior; merging or deploying remains a separate user action.

With the locked backend dependencies and this checkout mounted:

```bash
PYTHONPATH=backend:. python -m app.verify_risk_runtime --output /tmp/risk-http.json
PYTHONPATH=backend:. python -m app.surrogate_readiness /path/to/physics-c2.json --output /tmp/surrogate-readiness.json
```

The HTTP verifier creates and terminates only its own loopback child processes.
It uses synthetic fixtures and no RabbitMQ acquisition jobs. The initial check
misclassified Uvicorn's graceful SIGTERM exit as a failure; the runner now accepts
that signal exit only alongside its verified post-lifespan cleanup snapshot.
The final evidence is from the final implementation, including scan protection.

## Quantum: a concrete stop decision

The readiness audit counts **32 unique candidate records in four independent
scenario groups**, plus **288 repeated record occurrences** across methods and
cost limits. Each group has **16 possible joint choices**. Repeated solver runs,
warm requests and changed cost limits do not supply independent physics labels.
All C2 groups have already been inspected: **zero untouched holdout groups**.

An input-only representation is possible: orbit altitude/direction, vehicle and
uncertainty settings, and local terrain summaries. Those features are not a
validated predictor of joint success or worst-case miss. A nominal trajectory
is not free input data; its simulation cost must be counted. The present
QAOA still requires the fully evaluated pair table, and the complete cheap
16-choice space is already enumerable by the strong classical reference.

Therefore the next justified deliverable here is the working readiness audit,
not a newly fitted surrogate or another circuit. No model, threshold or feature
set was selected using C1/C2 held-out outcomes. **No fitting, new physics calls,
circuit evaluations or hardware jobs occur in this audit.** C1/C2 NO-GO decisions
remain in force, and quantum contribution has not been demonstrated.

What is missing is an independently validated input-only predictor over the
intended variation in terrain, vehicles and uncertainty, an untouched group
holdout, and evidence that candidate selection is a bottleneck after strong
classical enumeration and reuse. Future experiments must pre-register scenario
family train/development/test splits; give the same features/model to classical
references; charge dataset construction, fitting, encoding, circuit simulation,
compilation, queue time and true physics checks; and preserve original safety
validation. A simulator win alone would not establish practical hardware
advantage. This iteration deliberately stops before that unsupported claim.

## Final verification and provider observation

All **2,286 tests passed**: backend 1,974, ingestion 218 and optional quantum 94.
The 34 new frozen tests are unchanged from their test commits. Backend and
ingestion Ruff checks, Compose configuration validation, frontend clean install,
TypeScript and Vite production build passed. Existing Starlette/Qiskit
deprecation and frontend chunk-size warnings remain. The user's pre-existing
H6 notebook edits were preserved and excluded from these commits.

A read-only provider observation at 2026-11-15 03:18:26 UTC found the prior job
`dd340d59-3d25-43e0-8268-df5607b84e3a` in **Canceled** state, with **22 Spark /
0 Full** visible. The job was not resubmitted and no cancellation was sent by
this task. This observation supplies no new quantum quality measurement.
**Zero new hardware jobs and zero credits spent** in this iteration.

Machine-readable evidence: [runtime verification](verification/risk-runtime-2026-11-15.json).
