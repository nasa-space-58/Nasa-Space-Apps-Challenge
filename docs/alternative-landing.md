# Alternative landing plans — R7 (scenario S11)

Developed on `feature/r7-alternative-landing` on top of R6 (`feature/r6-risk-uncertainty`).

Commits:
- Refactor: `a856773`. It extracts the R4 gate-flight loop into `fly_gates` so a flight can start mid-descent. R4/R5/R6 frozen tests pass, and the real-data R6 `result_id` is byte-identical.
- Skeleton: `9c56e45`.
- Frozen test baseline: `946618c` (`backend/tests/test_alternatives.py`, 36 cases). `git diff 946618c -- backend/tests` is empty.

## What it answers

During the descent, if the primary site turns out to be unusable, which nearby sites can the vehicle still reach safely, and what does each cost? For the plan on the Descent page, R7:

- finds nearby sites that pass the R3 terrain hard constraints;
- checks whether each is reachable from a divert point with the propellant, reserve, touchdown tolerances and engine thrust of the vehicle;
- compares the primary and every backup with the same metrics, and lets the user mark backups.

## User flow

**Descent → Alternative landing plans · backups.** The run uses the site, vehicle, orbit and reserve from the form. The user chooses:

- the divert point: high gate (start of approach, about 2 km up) or low gate (start of terminal descent, about 150 m up);
- the search radius;
- the number of backups;
- the approach azimuth.

A run takes about 3 s. The result shows:

- how many backups are reachable, and the divert state (altitude, speed, time to touchdown);
- a local map (direction of travel up) with the primary, the search radius and each backup: green dot = reachable, red ✕ = not reachable;
- one table for the primary and the backups: distance and bearing, safety score and landing slope, divert Δv and time to touchdown, propellant above the reserve and hover time, status with rejection reasons, and a "mark as backup" box;
- the model assumptions.

Marks are kept in the browser for that result (`localStorage`, keyed by `result_id`). If storage is unavailable, they stay in memory.

API: `POST /descents/alternatives` (409 without cached terrain, 422 for invalid input, unknown vehicle, a site outside coverage, or a primary site without terrain around it).

## Model

1. **Primary descent:** the R4 plan, flown at a 0.1 s step. The vehicle state (position, velocity, mass) is taken at the first sample of the approach phase (high gate) or the terminal phase (low gate).
2. **Candidates:** DEM cells every `candidate_spacing_m` (default 160 m) within the search radius (default 3 km).
   - Each candidate must pass the R3 hard constraints: landing-area slope within the limit (default 10°) and no unknown cells.
   - Candidates are ranked by the R3 safety score.
   - They are kept at least `min_separation_m` (default 400 m) from the primary and from each other.
3. **Downrange:** flown exactly with the R4 ZEM/ZEV guidance to the backup's own terrain height. Because gravity is central, retargeting d metres downrange is the same as rotating the vehicle state by d/R about the Moon's centre.
4. **Crossrange:** the planar R4 model has no crossrange dynamics. A crossrange offset c is flown as a decoupled minimum-energy lateral divert.
   - With zero lateral velocity at start and end, the formula is a(t) = 6c/T²(1 − 2t/T): Δv = 3|c|/T and peak 6|c|/T².
   - The lateral move starts once the engine leaves the fixed-throttle (maximum thrust) position. At high gate the Apollo profile is still at maximum thrust for a few seconds.
   - Its propellant is added after the in-plane flight with the rocket equation. This overstates the combined cost, so it is conservative.
5. **Reachable** means all of the following:
   - no propellant exhaustion;
   - the R5 reserve is kept;
   - the R4 touchdown tolerances are met;
   - at every sample, √(a_in-plane² + a_lateral²) is within maximum thrust / mass.

## Verification

Tests (`test_alternatives.py`) cover:

- the lateral formula, including a numerical integration showing the profile moves c metres and stops;
- rotation of the planar state;
- reconstruction of the divert state;
- a zero offset reproducing the primary touchdown exactly;
- downrange retargets landing within 5 m;
- costs growing monotonically with crossrange;
- reserve and thrust rejections with reasons;
- low gate costing more than high gate;
- a target at a different height;
- candidates respecting radius and separation and avoiding steep or unknown terrain;
- every offered backup meeting reserve, tolerance and slope limits;
- determinism;
- DEM loading and the API.

Real data, Malapert R23, high gate, 3 km radius ([evidence](verification/r7-alternatives-2026-11-14.json)):

- Divert at 2 159 m altitude and 222 m/s, 208 s before touchdown.
- **6 of 6 backups reachable.** All have higher R3 safety scores (0.44–0.54) than the primary (0.29), with landing slopes of 1.6–4.0° against the primary's 5.6°.
- Divert cost is 5–56 m/s and the fuel above the reserve is 540–666 kg, against 680 kg for the primary.
- From low gate the same sites stay reachable, but lateral diverts cost more (up to 63 m/s) because less time remains.
- A repeated request returns the same `result_id`. In the UI, B2 was marked as a backup with no console errors.

## Not modelled

- Crossrange dynamics beyond the decoupled analytic divert, attitude dynamics and engine-out cases.
- Pilot or sensor constraints on what can be seen and designated (for example, the Apollo landing-point designator's view).
- Sunlight and communication at the backups. Run the site analysis for a chosen backup.
- Hazards smaller than the 80 m DEM cell.
