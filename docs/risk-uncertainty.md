# Risk and uncertainty — R6 (scenario S10)

Developed on `feature/r6-risk-uncertainty` from `tahakrcc` `b678a8b` (R1–R5 delivered).
Skeleton: `bd8f44c`. Frozen test baseline: `9e0b58d` (`backend/tests/test_risk.py`, 41 cases). `git diff 9e0b58d -- backend/tests` is empty; earlier frozen tests are unchanged.

## What it answers

For a landing plan (site, vehicle, orbit and reserve from the Descent page), R6:

- flies the R4 descent many times with seeded random errors;
- reports the success rate with a 95 % confidence interval and the cause of every failure;
- draws the landing dispersion with 1σ and 3σ ellipses and checks the terrain inside it;
- shows the propellant left at touchdown and which uncertainty drives failures.

The same inputs and seed always give the same result and the same `result_id`.

## User flow

**Descent → Risk & uncertainty · Monte Carlo.** The run uses the site, vehicle, orbit, plane-change setting and reserve from the form. The user sets runs (100/200/500), seed, navigation error, terrain-height error, thrust error and approach azimuth. 200 runs take about 18 s. The result shows:

- the success rate, its Wilson 95 % interval, the seed, the result ID and the main driver of failures;
- the 3σ ellipse size, the share of steep DEM cells inside it and the 5th-percentile fuel above the reserve;
- a dispersion plot (direction of travel up): green dots for successes, red ✕ for failures, dashed 1σ and solid 3σ ellipses;
- failure causes with counts;
- a sensitivity bar per source with the noise level marked;
- a histogram of propellant left at touchdown;
- the model assumptions.

API: `POST /descents/risk` (409 without cached terrain, 422 for invalid input or plans the R4 model rejects).

## Model

Each run perturbs the R4 inputs and flies `simulate_powered_descent` (0.5 s step; 0.5 s and 0.1 s differ by under 1 cm at touchdown for the Apollo 11 profile).

| Source | Default 1σ | Effect |
|---|---|---|
| Thrust | 1 % | scales maximum and design thrust |
| Specific impulse | 0.5 % | scales Isp |
| PDI position | 500 m | changes the actual downrange start |
| PDI speed | 1 m/s | changes the actual start speed |
| Terrain height (DEM) | 2 m | true ground minus believed ground at the site |
| Navigation (down/cross) | 100 m each | moves the landing point one-to-one |

All defaults are **planning assumptions** chosen to show the method. They are not certified vehicle or navigation values, and they are visible and editable.

- **Navigation error** is a knowledge error. Closed-loop guidance flies to where it believes the site is, so the vehicle lands that far from the real site. The planar R4 model has no crossrange dynamics, so crossrange dispersion comes only from navigation.
- **Terrain height error.** If the true ground is higher, contact happens at the first simulated sample at or below it. If it is lower, the engine cuts off at the believed touchdown (as at the Apollo contact-light cut-off) and the vehicle falls the remaining height under lunar gravity.
- **Success** needs all of the following: no propellant exhaustion, the R5 reserve kept, the R4 touchdown tolerances met (guidance miss, vertical and horizontal speed), and, when terrain is cached, a landing cell at or below the R3 slope limit. A landing off the slope map or on an unknown cell is a failure (`hazard_unknown`), never assumed safe.
- **Dispersion ellipse**: sample covariance (ddof = 1) of the landing points in the downrange/crossrange frame. For terrain inside it, the 3σ ellipse is inflated by half a DEM pixel so that cells near a tiny ellipse are still checked.
- **Sensitivity**: Spearman rank correlation of each source's sampled value (and of its magnitude) with failure. The larger absolute value is the *association*. Values below 2/√runs are marked as indistinguishable from noise. This shows which inputs matter. It is not a variance decomposition.

## Verification

Unit and API tests (`test_risk.py`) cover:

- reproducibility from the seed;
- Wilson intervals against the formula and reference values;
- ellipse axes and orientation against an exact covariance;
- rank correlation with ties and constant inputs;
- free fall and early contact for terrain error;
- the approach-frame map lookup;
- zero uncertainty reproducing the deterministic R4 plan;
- propellant exhaustion versus reserve violation;
- terrain error identified as the dominant source;
- flat, steep and too-small slope maps;
- the API's 409/422 handling.

Real data, Malapert R23, 200 runs, seed 2026, defaults, cached LOLA DEM ([evidence](verification/r6-risk-2026-11-14.json)):

- 104 of 200 landings succeed (52 %, 95 % CI 45–59 %). All 96 failures are hard landings (vertical speed above 1.0 m/s).
- Main driver: terrain height (association 0.87). PDI position is also significant (0.23 against a 0.14 noise level).
- 3σ ellipse about 633 × 558 m. 2 % of the 52 DEM cells inside it are steeper than 10°. Fuel above the reserve: 5th percentile 638 kg.
- Repeating the request returned the same `result_id`. The UI run produced the same ID.

**How to read this result.** The R4 model has no landing radar or contact-probe altitude update. A 2 m DEM height error therefore becomes free fall or early contact, which the strict 1.0 m/s vertical tolerance rejects. This is a finding about the model's sensitivity to terrain knowledge, not a prediction for a radar-equipped lander. Lowering the terrain error, or widening the vertical-speed tolerance, shows how much each assumption matters.

## Not modelled

- Landing radar, terrain-relative navigation updates and hazard avoidance during descent.
- Crossrange dynamics, attitude dynamics, engine failure modes, mascons and lunar rotation (inherited from R4).
- Slopes below the 80 m DEM resolution: craters and boulders at lander scale.
- Correlated error sources: every source is an independent Gaussian.
