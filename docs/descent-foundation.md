# H4 — Terminal descent, fuel and bounded reachability

H4 continues on `codex/quantum-hybrid`. No merge into `tahakrcc` or `main` is part of this delivery.
Skeleton: `6292085`. Frozen acceptance tests: `3436c14`. Additional frozen boundary tests: `aa07539`.
The pre-existing tests and both H4 test commits are unchanged during implementation.

## Delivered behavior

- `backend/app/descent_dynamics.py` supplies the shared planar integrator and the existing ZEM/ZEV guidance formula. R4 calls this integrator, so R5's existing R4-based calculations receive the same fuel correction.
- `backend/app/terminal_descent.py` starts a bounded terminal simulation from an explicit position, velocity, UTC epoch, vehicle identity/revision and remaining propellant. It accepts a declared terrain corridor and validates the engine, tank capacity and frame.
- `vehicle_from_r4` adapts the existing SI engine/mass profile without importing web services or reading/downloading terrain. Apollo engine compatibility is tested against R4's `load_profiles()`.
- The simulator terminates at a detected terrain contact, loss of terrain coverage, or the deadline. It rejects out-of-tolerance position/speed, upward departure at contact, excessive terrain slope and insufficient remaining reserve. An initial state below terrain is rejected without integrating through it.
- Every trace sample contains a state that can start another run. Restart recomputes guidance; it does not refill fuel. Revision is inherited from the input snapshot, not used as a sample counter.
- `evaluate_targets` checks 1–16 explicit landing locations/durations at the requested time step and half that step. It retains both runs, all rejection reasons and numerical differences; only candidates passing both physical verdicts and numerical agreement can be recommended. Among those, selection minimizes measured fuel use, with target ID breaking ties.
- `shared/examples/descent/terminal_nominal.json` and `notebooks/H4_descent_foundation.ipynb` supply the reproducible synthetic experiment. The notebook calls the production modules, not a second flight solver.

This adds the H4 computation foundation and notebook, not a new web page or API endpoint. No quantum/provider job is submitted. H4 is the classical physical check that later hybrid selections must pass.

## Fuel correction

Previously, R4 applied full thrust over an entire time step, then clamped the final mass to dry mass if fuel ran out. The position/velocity integration therefore received thrust beyond the available fuel.

For a 1000 kg vehicle with only 1 kg propellant, 300 s specific impulse, a 500 kN command and a 0.1 s step, the former R4 probe produced **50.2701486 m/s** upward velocity while reporting only **2.9434670 m/s** rocket-equation delta-v. These are a deliberately extreme synthetic regression, not Apollo flight measurements.

The corrected step computes:

- equivalent exhaust speed: `ve = Isp * g0`;
- mass flow: `flow = thrust / ve`;
- burn duration: `min(step, available_propellant / flow)`;
- powered RK4 integration up to depletion, then unpowered integration for the remaining time;
- consumed propellant from actual mass loss and delta-v from `ve * ln(m_initial / m_final)`.

RK4 substeps limit the mass loss in each powered substep to at most 0.5% of the step's final mass. Ordinary R4 steps need only one substep. Intermediate and final mass never fall below dry mass. Thrust is held in direction and magnitude within each step; no instantaneous extra impulse is added at depletion.

The zero-gravity analytic test measures **0.00588399 s** powered time and **0.09411601 s** coasting, with **2.94346698 m/s** velocity change. The separate R4 regression includes lunar gravity and bounds velocity below that rocket-equation value.

## Frame and terrain semantics

The frame is explicitly `PLANAR_MOON_CENTERED_NONROTATING`, with two coordinates. At zero downrange, +y is radial up and +x is forward. Downrange is `reference_radius * atan2(x, y)`. Height is measured above that same reference sphere. Velocity is resolved into signed radial and tangential components.

**This is not H0's rotating 3D `MOON_ME` frame.** No implicit conversion from a MOON_ME state or R4's unsigned horizontal-speed sample is provided. A real-data adapter must explicitly construct the orbit-plane axes, height datum and appropriate velocity transformation.

The supplied corridor consists of strictly increasing downrange samples and piecewise-linear heights, with a provenance record. Heights are never extrapolated. At a terrain vertex the steeper adjacent segment determines the reported slope. Reserve is a touchdown acceptance threshold; it does not automatically stop the engine at the reserve boundary.

The model uses the R4 throttle rule: throttle between the declared minimum and throttleable maximum; higher requests use the fixed maximum. Thrust direction can change instantly. Guidance is recomputed at each integration step, with the final 0.2 s command held to avoid singular time-to-go gains. If the step is larger than 0.2 s, the hold window is at least that step.

Terrain clearance is **sampled**, with bisection to refine a crossing bracketed by a step. This is not continuous collision detection: a narrow obstacle crossed entirely between samples may be missed. Coarse/fine agreement does not eliminate that possibility. The ridge test verifies a resolved obstacle; it does not certify arbitrary terrain geometry.

## Bounds and acceptance

Inputs are finite, carry units, and reject extra fields. State epochs require explicit UTC. State radius is limited to 1600–2000 km and speed to 5000 m/s; these are computational bounds, not a definition of an admissible mission. Corridors hold 2–4096 points, within ±50 km downrange and ±10 km height. Terminal duration is 5–300 s, with a requested integration step of 0.02–1 s. The half-step replay may use 0.01 s.

Default flight tolerances are 5 m downrange error, 2 m/s downward speed, 1 m/s tangential speed and 15 degrees terrain slope. These are illustrative configurable thresholds, not mission-certified limits. A surface state with upward radial velocity is not accepted as a completed descent.

Numerical agreement uses separate limits: 0.5 m final position, 0.1 m/s final velocity, 0.05 kg remaining fuel and 0.2 s contact/end time. Both runs must also have identical termination and rejection categories. Failure at any stage prevents recommendation; the candidate and evidence are still returned. This is a finite candidate screen, not an exhaustive reachability envelope or a globally optimal controller.

A SHA-256 fingerprint includes model version `h4-planar-v1`, the complete request including provenance, and the actual integration step. It does not identify external software environments or claim measurement authenticity.

## Executed evidence — 2026-11-14

- **55 new H4 tests**: analytic constant-thrust velocity/position, exact fuel boundary, zero fuel/thrust, mass monotonicity, input immutability, finite/physical validation, ballistic energy conservation, R4 regression, Apollo SI adapter, UTC/frame/tank validation, terrain interpolation/coverage/slope, nominal landing, reserve and thrust failures, deadline failure, elevated surface, early ridge contact, restart, candidate bounds and two-resolution agreement.
- **739 backend tests passed**, including existing R4/R5 and H0–H3 tests. One existing Starlette/AnyIO deprecation warning was reported.
- **112 ingestion tests passed**. Backend/shared Ruff checks passed; modified Python and the new frozen tests pass formatting checks.
- **10 notebook code cells executed with the `lunar-h0` kernel; zero error outputs**. Charts and tables are saved in the notebook. Jupyter on port 8888 serves it under `quantum-hybrid-workspace/notebooks/`.
- No frontend files changed; no frontend tests or screenshots were used.

The synthetic nominal case starts at 100 m height, −3 m/s radial velocity, 900 kg dry mass and 100 kg fuel. It reaches contact at **29.999953 s**, approximately **−0.500258 m/s**, using **17.260540 kg** and retaining **82.739460 kg**. The reserve is 10 kg. With the half step, fuel use is approximately **17.260504 kg**. These numbers characterize this declared case only.

Five candidate offsets (−100, −20, 0, 20, 100 m) pass the two-resolution screen for a 30 s descent; the zero-offset point uses the least fuel. A 10 km diversion with a 5 s deadline is rejected. The notebook also rejects near-empty fuel, insufficient thrust, reserve violation, a steep surface and unknown target terrain.

Reproduce from the repository:

```bash
docker compose --env-file .env.example run --rm --no-deps backend pytest -q
docker compose --env-file .env.example run --rm --no-deps data-ingestion pytest -q
docker compose --env-file .env.example run --rm --no-deps backend ruff check app tests /workspace/shared
```

Open the H4 notebook with the existing `lunar-h0` kernel and select **Restart Kernel and Run All Cells**. The kernel needs the H0 notebook environment (Python 3.12, NumPy, Pydantic, IPython/Jupyter); no FastAPI, Rasterio or quantum SDK import is needed for H4's notebook. See the prior H0–H3 notebook setup for the kernel installation.

## Limits and next stages

This foundation remains planar and nonrotating: no mascons, J2, third bodies, attitude dynamics, gimbal slew, thrust uncertainty, sensor error, landing footprint, surface material or plume model. Radial/tangential contact speeds and along-corridor slope do not describe full 3D touchdown loads. No actual LOLA corridor was certified in this task. R4's full descent still uses its original site-sphere geometry and gate endpoint verdict; the H4 terrain-contact engine is a separate terminal checker, not a retroactive terrain-safety claim for R4 routes.

Restart captures physical state, not controller memory. Restart inside the final guidance hold interval may choose a different command. H5 still needs uncertainty, time-stamped fault events and classical recovery; H6 must reject stale results and apply validated fallback behavior before hybrid selection. Hardware comparisons remain later work. Passing H4 tests is numerical/model validation, not flight qualification or a quantum advantage claim.

## Primary sources

- [NASA Glenn — Specific impulse](https://www1.grc.nasa.gov/beginners-guide-to-aeronautics/specific-impulse/): relation between thrust, mass flow, equivalent exhaust velocity and specific impulse.
- [JPL — Astrodynamic parameters](https://ssd.jpl.nasa.gov/astro_par.html): lunar GM, 4902.800118 km³/s², from DE440; the code reuses `shared/constants.py`.
- [NASA TN D-6846](https://ntrs.nasa.gov/api/citations/19720018205/downloads/19720018205.pdf) and [Apollo 11 Mission Report](https://www.nasa.gov/wp-content/uploads/static/apollo50th/pdf/A11_MissionReport.pdf): existing R4 engine/profile provenance, detailed in [descent trajectories](descent-trajectories.md) and [fuel optimization](fuel-optimization.md). They do not validate the synthetic H4 demo vehicle or controller.
