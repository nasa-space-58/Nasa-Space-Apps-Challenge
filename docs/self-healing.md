# H5 — Classical self-healing and deterministic fault replay

H5 continues from H4 `00fa0b3` on `codex/quantum-hybrid`. Push only this branch; the user merges manually.
Skeleton: `9fd35a7`. Frozen acceptance tests: `9c9a6d1`. Additional frozen boundary cases: `1a4fc36`; coarse-resolution hazard regression: `432cd67`.
No pre-existing test or frozen H5 test was modified during implementation.

## Delivered computation

`backend/app/self_healing.py` supplies bounded, deterministic fault replay and classical target selection. `compare_recovery()` runs the same declared events twice: once keeping the original target, once allowing event-triggered replanning. Neither path invokes a quantum solver or provider.

`propagate_terminal()` extends H4 with an exact pause time, persistent position/velocity navigation bias, a thrust ceiling (including exactly zero), and a finer integration-step override. It preserves the original guidance arrival time while pausing for an event. Normal H4 calls retain their prior behavior and fingerprint; their notebook and tests were rerun.

Inputs are immutable validated contracts. They include a UTC initial state, explicit nonrotating planar frame inherited from H4, target IDs and absolute touchdown deadlines, ordered fault declarations, uncertainty assumptions with provenance, a seed, and an explicit communication policy. No backend network calls, downloaded data, frontend changes or new runtime dependencies were introduced.

## Fault effects

| Fault | Applied effect |
|---|---|
| `thrust_loss` | Lower the physical thrust ceiling. The command is capped during propagation and candidate checks. Zero means no thrust and no propulsion fuel consumption. A later thrust-loss event cannot increase the ceiling. |
| `propellant_loss` | Remove the declared fuel amount, limited by the fuel actually remaining at that instant. Record loss separately from propulsive consumption. Never refill or create negative fuel. |
| `sensor_degradation` | Update the declared planar position/velocity standard deviations and sensor identity. Generate a seeded persistent measurement bias. The physical position and velocity remain unchanged at the event. |
| `landing_hazard` | Add a closed downrange landing-exclusion interval. Reject prohibited targets and actual/sample touchdown inside it. This is a landing-zone constraint, not a terrain-height edit or volumetric obstacle model. |
| `communication_outage` | Extend a half-open outage interval. If the declared policy requires a link to retarget, a currently valid target may continue but alternatives are rejected while the link is unavailable. Explicit autonomous permission removes this policy restriction. |

Faults apply at their UTC timestamps, including times between normal integration samples. Simultaneous faults use distinct sequence numbers and trigger one planning decision after the group. Duplicate IDs/order keys, wrong vehicle IDs, naive timestamps, backward outages and out-of-horizon events are rejected. Input event order is canonicalized, making reordered declarations replay identically.

Every applied event increments the state revision and retains before/after truth, the synthetic observation, the planner estimate, current thrust availability and any fuel lost. Landing terminates execution; future declared events are returned as unapplied, not silently simulated after landing.

## Truth, observations and uncertainty

The physical state, synthetic observation and estimated state are distinct records. H5 deliberately uses an **identity estimator**: the noisy observation is the estimate. It is not a Kalman filter, covariance-propagation system or calibrated sensor model. Ground truth is available to the simulation and evidence log; candidate selection receives the estimate and declared assumptions, not the actual observation error.

Navigation errors are independent zero-mean Gaussian planar Cartesian offsets. Bias is sampled initially and when a sensor event occurs, then held until another sensor change. No sensor-rate, temporal-correlation or white-noise process is implied. Thrust and fuel uncertainty use independent uniform shortfalls over explicitly supplied bounds.

At each decision, the planner draws a common sample set keyed by `(seed, state revision, stream)`. Every candidate receives the same sample set. For each sampled possible true state, the controller receives the opposite fixed navigation offset, so it starts from the same declared estimate rather than being given the sample's true navigation state. The H4 mass-flow model remains ideal, including exact mass for thrust scaling; this is not a separate onboard mass-estimator simulation.

Each candidate must:

1. Have at least five seconds remaining before its unchanged absolute deadline, satisfy hazard restrictions and satisfy the communication policy.
2. Pass the nominal H4 landing/speed/slope/reserve checks at `dt` and `dt/2`.
3. Agree across resolutions within 0.5 m position, 0.1 m/s velocity, 0.05 kg fuel and 0.2 s time, with identical termination and failure categories.
4. Pass **every** declared uncertainty sample propagated at the finer resolution, including the landing exclusions.

Sample counts, raw per-sample outcomes and failure-cause counts are retained. These are empirical finite-sample checks, **not calibrated success probabilities or coverage guarantees**. Gaussian support is unbounded, and no finite sample set proves robustness. Invalid sampled states are rejected explicitly. A failed nominal candidate may still have sample outcomes for diagnosis; it is never accepted on sample performance alone.

H4 guides toward contact at the specified arrival time. It does not introduce an extra uncertainty allowance after that deadline. Navigation errors, particularly radial ones, can therefore make this screen conservative through `no_contact` as well as speed/position failures. The notebook exposes these failures rather than changing tolerances to obtain success.

## Decision and replay policy

Preserve the current target when it still passes. Otherwise choose the passing candidate with least nominal predicted propellant use, breaking ties by target ID. Decisions report `continue`, `retarget` or `no_feasible_continuation` and retain every candidate's reasons.

If none passes, recovery returns `status="infeasible"`, no selected target, and the physical state at that decision boundary. **This is not a safe abort or a physical vehicle stop.** No unvalidated fallback trajectory is executed. The fixed-target reference continues its physical simulation where H4 supports the remaining horizon, exposing collision/landing/constraint failures.

The target arrival/deadline is absolute UTC: an event never grants a new full descent duration. The five-second minimum is inherited from H4's bounded restart model. A later fault with less than five seconds remaining is unsupported and rejected; the code does not claim terminal flight-control behavior below that bound.

Both replay branches use the same exogenous fault declarations and observation-seed convention. Physical trajectories may diverge after retargeting, so subsequent truth/observations can differ. If one branch lands earlier, later events remain unapplied in that branch. Remaining fuel at different stopping times is not presented as a valid efficiency comparison.

`scenario_sha256` includes model version `h5-replay-v1` and all validated scenario inputs. Decision fingerprints additionally identify the estimate, revision, previous target, active landing hazards, outage end, thrust ceiling and uncertainty. H4 propagation fingerprints include pause/bias/thrust controls when applicable. These identify deterministic inputs, not measurement authenticity or a full software-environment lock.

## Reproduce in Jupyter

Open `notebooks/H5_self_healing.ipynb` with the existing **Lunar H0 (Python 3.12)** kernel and run all cells. The notebook imports the repository's model, displays event/decision evidence, plots baseline versus replanned downrange, exercises recoverable/unrecoverable cases, separates truth from observations and checks three uncertainty seeds.

On this machine the existing Jupyter server exposes:

http://localhost:8888/lab/tree/quantum-hybrid-workspace/notebooks/H5_self_healing.ipynb

Inputs are in:

- `shared/examples/recovery/combined_recovery.json`: at 5 s, lose 3 kg of fuel, reduce available thrust to 3000 N and close the original −5 to +5 m landing interval; backup at +40 m, deadline still 30 s.
- `shared/examples/recovery/engine_out.json`: zero thrust at 5 s; no validated continuation in the supplied candidate set.

All fault and uncertainty inputs are explicitly synthetic. The nominal vehicle/terrain inherit the H4 example. The underlying physical-source provenance and scientific limits are documented in [descent foundation](descent-foundation.md).

## Executed evidence — 2026-11-14

- **51 new H5 tests**, covering exact/fractional event times, preserved guidance deadlines, zero thrust, navigation bias, controls in fingerprints, no-fault equivalence, hazard retargeting, single/combined unrecoverable faults, observation separation, seeded repeatability, common sample draws, bounded uncertainty, communication policy/expiry, event ordering, malformed inputs, unchanged absolute deadlines, late events, revisions and complete fuel accounting.
- **790 backend tests passed**, including all H0–H4 and existing R4/R5 tests. One existing Starlette/AnyIO deprecation warning remains.
- **112 ingestion tests passed**. Backend/shared Ruff checks passed; modified Python and new frozen test files pass formatting.
- **H5: 10 code cells executed, zero errors** with `lunar-h0`; saved tables/chart and assertions are included. **H4: its existing 10 code cells also reran with zero errors and the same nominal fingerprint**; its notebook file was not rewritten.
- The combined-fault notebook example rejects the fixed original target because it lies in the exclusion interval. Classical recovery reaches the +40 m backup at UTC `2030-01-01T00:00:29.999953Z`, burns **17.333022 kg**, loses **3 kg**, and retains **79.666978 kg**, above the 10 kg reserve.
- Engine-out, empty-tank and large-navigation-error cases produce explicit infeasibility. The communication example distinguishes ground-approved and autonomous retargeting. Identical inputs replay with identical results.
- No frontend tests or screenshots were used, and no quantum/provider jobs were submitted.

Checks from the existing development runtime:

```sh
docker exec nasa-task4-backend-1 pytest -q
docker exec nasa-task4-data-ingestion-1 pytest -q
docker exec nasa-task4-backend-1 ruff check app tests /workspace/shared
git diff 9c9a6d1 -- backend/tests/test_self_healing.py
git diff 1a4fc36 -- backend/tests/test_recovery_boundaries.py
git diff 432cd67 -- backend/tests/test_recovery_resolution_hazard.py
```

The last three commands produce no diff. Notebook environment setup remains in [H0 contracts](hybrid-contracts.md).

## Limits and H6 handoff

H5 is synchronous. Planning is instantaneous in simulated time; there is no hard wall-clock deadline guarantee. Work is bounded by 8 explicit targets, 8 events, 32 uncertainty draws and H4's 300-second maximum horizon. Even these caps are not flight-computer performance qualification. The returned evidence does not authorize a stale result to control a real vehicle.

This is a bounded H5 computation milestone, not completion of every R6/R9/R12 feature. No full digital twin, 3D MOON_ME transformation, real sensor fusion, DSN booking/radio simulation, UI event panel, volumetric obstacle avoidance, landing footprint, attitude/gimbal model or continuous terrain-collision proof is included. H4's sampled planar terrain limitations still apply. Only declared candidate targets are explored; no global reachability or globally optimal control claim is made.

H6 remains responsible for asynchronous hybrid candidate selection, runtime deadline handling, stale-result rejection, currently validated fallback behavior and downstream multi-mission scheduling. Quantum hardware experiments remain separate later work. H5's classical truth/estimate separation, revisions, candidate evidence and deterministic replay provide the integration boundary.
