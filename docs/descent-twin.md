# Descent digital twin — R9 (scenario S13)

Developed on `feature/r9-descent-twin` on top of R8.
Skeleton: `63670aa`. Frozen test baseline: `ac30279` (`backend/tests/test_twin.py`, 32 cases). `git diff ac30279 -- backend/tests` is empty. R4–R8 code is unchanged.

## What it answers

During the descent, what does the lander *know*, and what is *true*? What happens to the original plan when something goes wrong?

The twin flies the R4 descent and keeps three separate streams, using the same `record_kind` vocabulary as TK's H0 contracts:

- **ground truth:** the actual vehicle;
- **observation:** a noisy navigation sensor;
- **estimate:** what the guidance computer believes and steers by.

Timestamped faults change the model. The twin then records which plan assumptions they break. The twin never replans; R12 does. Its job is to show the consequences of keeping the original plan.

## User flow

**Descent → Digital twin.** It uses the site, vehicle, orbit and reserve from the Descent form. Pick a fault scenario and a seed:
- no fault;
- navigation sensor lost at 450 s;
- engine −25 % at 520 s;
- 700 kg leak at 600 s;
- boulder field on the target at 640 s.

A run takes about 2 s and shows:

- the landing verdict with the reasons, and whether the original plan became invalid and why, with a replay ID;
- the differences from the same flight without the fault (landing point, propellant, navigation error at touchdown);
- a log-scale chart of the *actual* estimate error (known only in simulation) and the error the vehicle *believes*, with fault markers;
- the event log: applied events, plan invalidations and events with no effect;
- the model assumptions.

API: `POST /twin/runs` (409 without cached terrain, 422 for invalid input).

## Model

- **Flight:** the R4 model (planar point-mass Moon, ZEM/ZEV guidance through the Apollo 11 gates, RK4, default 0.2 s step), started from the R4 PDI state. With zero noise, unit gain, no engine bias and no events, the twin reproduces R4 `fly_gates` exactly (test, 1e-9).
- **Observation:** truth plus Gaussian noise along local downrange/up for position and speed.
  - The configured sigma (default 5 m, 0.1 m/s) is a cap.
  - Noise is range-scaled like a radar or lidar: min(cap, max(0.1 m, 0.002 × altitude)). Without this, ZEM/ZEV guidance (command ∝ error / time-to-go²) amplifies metre-level noise into several m/s at touchdown.
  - The sensor's health is nominal, degraded (5× noise unless values are given) or lost (no observations).
- **Estimate:** a constant-gain predictor–corrector.
  - Prediction uses the commanded acceleration. Correction is (1 − K)·estimate + K·observation, with K = 0.05.
  - The believed error is σ·√(K/(2 − K)) while observing. Without observations it grows by the speed term each step.
  - Mass is known to the estimator (propellant gauging).
- **Engine bias:** actual thrust differs from the command by a constant bias drawn once per run (σ 0.5 %). The estimator does not know it, so the estimate drifts once observations stop.
- **Events** (each changes the model, not only the display):

  | Event | Effect | Plan dependency |
  |---|---|---|
  | `thrust_loss` | available thrust fraction | `available_thrust` |
  | `sensor_degradation` | sensor noise/health | `navigation_sensor` |
  | `propellant_leak` | instant mass loss (exhaustion if below dry mass) | `propellant` |
  | `comm_outage` | no ground uplink for a window | `ground_uplink` (the default autonomous plan does not depend on it) |
  | `obstacle_detected` | synthetic hazard disk relative to the target | `target_hazard`, only if it covers the target |

- **Log and replay:** applied events (with state revisions), invalidations, no-effect notes, touchdown and unreached events form an append-only log. Its SHA-256 (`log_hash`) identifies the run, and the same seed and events give the same hash.
- **Baseline:** the same seed without events. Differences are reported per quantity.
- **Battery:** constant load (default 2 kW from 50 MJ), a planning assumption.
- **Labels:** every telemetry row is `simulated` and obstacles are `synthetic`. The vehicle profile and terrain height keep their R4/LOLA sources.

## Verification

Tests (`test_twin.py`) cover:

- validation;
- the filter formula and exact copy at K = 1;
- the observation noise statistics, and a fixed number of random draws so that later draws do not depend on sensor health;
- exact equality with R4;
- the three streams and labels;
- replay and seed sensitivity;
- estimate drift after sensor loss (5 seeds), with the believed error growing monotonically;
- thrust loss saturating the engine and invalidating the plan;
- a 100 kg leak costing 90–100 kg of margin, and a 5 t leak exhausting propellant;
- dependency-specific invalidation;
- only covering obstacles invalidating the plan;
- time-ordered events with revisions and unreached events;
- the zero-difference baseline;
- determinism and the API.

Real data, Malapert R23, seed 2026, default sensor ([evidence](verification/r9-twin-2026-11-14.json)):

| Scenario | Landing | Plan |
|---|---|---|
| No fault | 0.09 m from target, 0.84 m/s down; success | valid |
| Sensor lost at 450 s | **471 m** from target, 2.85 m/s down, 1.86 m/s sideways; fails | invalid (navigation sensor) |
| Engine −25 % at 520 s | 0.09 m, success; 16 kg more propellant | invalid (available thrust), yet recoverable |
| 700 kg leak at 600 s | success with only 31 kg above the reserve | invalid (propellant) |

After the sensor loss, the *actual* error reaches 471 m while the error the vehicle *believes* stays at a few metres. The lander does not know it is lost. This is the case R12 must detect and handle.

Over seeds 1–10 with the default sensor, 8 nominal flights succeed. The other two exceed the strict 1.0 m/s vertical tolerance at 1.03 and 1.11 m/s; the R4 nominal touchdown rate is 0.91 m/s.

## Not modelled

- A full Kalman filter, IMU or terrain-relative navigation algorithms, attitude dynamics, crossrange motion (planar R4), engine dynamics and actual landing-radar models.
- Battery physics beyond a constant load.
- Replanning: that is R12.
