# Powered descent trajectories — R4 (scenario S6)

Start from `temel` `a0b7b8d` (R3); developed on `feature/r4-descent-trajectories`.
Skeleton: `9031154`. Frozen test baseline: `9ed69c8` (unchanged; `git diff 9ed69c8 -- backend/tests` is empty).
Implementation: `19e3b16`. Interface: `c63ecb7`.

## What it answers

For a selected landing site and vehicle, R4 computes a descent from a circular lunar orbit to touchdown. It reports:

- the timing and delta-v of each phase, and the propellant used;
- touchdown errors and the physical assumptions;
- whether the site is reachable within the vehicle limits.

When the site is not reachable, it names the limiting factor:

- a plane change the orbit needs;
- propellant;
- a touchdown outside tolerance.

The result is a planning-level trajectory, not flight software.

## User flow

Open **Descent** in the navigation. Then choose:

- a preset or custom site;
- the Apollo 11 Lunar Module profile or custom vehicle parameters;
- the initial orbit (default 60 n.mi. circular, 90° inclination);
- whether a plane change may be performed.

Select **Compute descent**; a plan takes about 2–3 s. The result shows:

- the feasibility verdict;
- powered delta-v and its comparison with the Apollo 11 plan;
- descent time and propellant;
- an altitude-versus-downrange chart with a *Final 10 km* zoom and a hover readout;
- the phase table and the assumptions with sources.

## Sources

| Input | Value | Source |
|---|---|---|
| Lunar GM | 4902.800118 km³/s² | [JPL Astrodynamic Parameters](https://ssd.jpl.nasa.gov/astro_par.html) (DE440/DE441) |
| Reference radius | 1737.4 km | existing constants module (PGDA product 90) |
| Phase structure, gates, timing | PDI ~50 000 ft; high gate 7515 ft at 8:26 (4.5 n.mi. uprange, −145 fps, 506 fps); low gate 512 ft at 10:06 (−16 fps, 55 fps horizontal, ~16° glide); touchdown 11:54 at −3 fps | [NASA TN D-6846](https://ntrs.nasa.gov/api/citations/19720018205/downloads/19720018205.pdf), Table I and approach-phase text |
| DOI and orbits | ~75 fps retrograde; 60 n.mi. to 50 000 ft (Hohmann) | TN D-6846, Figure 1 and text |
| Engine | max ≈ 10 000 lbf (fixed throttle position); design 10 500 lbf; throttleable 10–60 % of design | TN D-6846, system descriptions |
| Mass at PDI | 33 401.6 lb (after DOI cutoff) | [Apollo 11 Mission Report](https://www.nasa.gov/wp-content/uploads/static/apollo50th/pdf/A11_MissionReport.pdf), Table A-I |
| Propellant at PDI | 18 260.5 lb loaded − 268.0 lb used by DOI = 17 992.5 lb | TN D-6846 Table II; Mission Report Table A-I |
| Specific impulse | 309.7 s (Apollo 9 time-integrated DPS average) | NASA DPS flight evaluation |
| Reference result | total powered descent delta-v 6775 fps | TN D-6846, Table I |

Profile values are stored in their published units in `shared/vehicle_profiles.json` and converted with the exact constants in `shared/constants.py`.

## Model

- **Frame:** planar Moon-centred frame in the orbit plane, with the site at radius R + terrain height. The site height comes from the cached LOLA 80 m DEM pixel. Downrange is arc length on the site sphere; altitude is above the site.
- **Gravity:** point mass with no mascons and no J2. Lunar rotation (about 0.4 m/s of surface speed at 85° S) is neglected.
- **DOI:** impulsive Hohmann burn from the circular orbit to periapsis 50 000 ft above the site. For the Apollo profile, DOI propellant is already spent in the PDI mass.
- **Plane change:** a ground track reaches latitudes up to min(i, 180° − i). The required rotation Δi costs 2v·sin(Δi/2) as an impulsive burn in the initial orbit. When allowed, its propellant comes out of the PDI budget.
- **Powered phases:** braking (506 s), approach (100 s) and terminal descent (108 s). Each uses closed-loop ZEM/ZEV guidance (Ebrahimi et al. 2008) to the next Apollo 11 gate state. The command is held for the last 2 s of each phase, where the gains become singular.
- **Engine:** constant specific impulse, with the TN D-6846 throttle rule. Commands above 60 % of design thrust run at the fixed throttle position (maximum). Commands below 10 % are raised to 10 %. Propellant exhaustion cuts the thrust.
- **Integration:** RK4 with thrust held per step. A 0.5 s step is used for the PDI-range search and a 0.1 s step for the final run. Per-step delta-v is the rocket-equation value.
- **PDI range:** a golden-section search over 300–700 km minimizes powered delta-v, with a penalty for missing the site.
- **Feasibility:** these factors make a plan infeasible, in this order:
  1. a plane change is needed but not allowed;
  2. propellant runs out, or the use exceeds what is available at PDI;
  3. touchdown falls outside the tolerances (default 5 m position, 1.0 m/s vertical, 0.5 m/s horizontal).

## Validation (frozen tests and live run)

| Check | Model | Reference |
|---|---|---|
| PDI speed (vis-viva) | 5559.9 fps | 5560 fps (Table I) |
| DOI delta-v | 71.6 fps | ~75 fps |
| Total powered delta-v (site at sphere) | ≈ 6640–6660 fps (−1.7 to −2.0 %) | 6775 fps |
| Phase delta-v: braking / approach / terminal | 5073 / 993 / 592 fps | 5375 / 801 / 599 fps |
| ZEM/ZEV on uniform gravity | 0.0005 m, 0.043 m/s miss | exact in continuous time |

The model leaves Apollo's hover and redesignation reserves out, so it uses less delta-v and lands heavier than Apollo 11: about 7.8 t versus 7.3 t at landing.

**Live run (local Docker, 2026-11-14):**

- **Malapert R23** (site 4785 m above the sphere): feasible. PDI is 543 km uprange. Powered delta-v is 2023 m/s (−2.0 %), using 7369 kg of propellant with a 793 kg margin. Touchdown is 4 mm from the site at 0.91 m/s vertical.
- **Mons Mouton:** similar.
- **Inclination 80°:** not feasible. A 5.99° plane change (170.3 m/s) is required.
- **Inclination 80° with the plane change allowed:** feasible, with a 343 kg margin.
- **Inclination 70° with the plane change allowed:** not feasible. Propellant needed exceeds the available amount by about 310 kg.

**Checks:** backend 421 tests, ingestion 112 and frontend 29. Ruff and the production build pass.

## Limitations

- **Terrain clearance:** not checked along the descent path. Terrain near polar sites can rise above the planned path.
- **Gravity model:** a spherical point mass. Mascons perturb low lunar orbits and are not modeled, and neither is lunar rotation.
- **Planar model:** crossrange, out-of-plane targeting and launch-window timing are not modeled. Reachability is a geometric inclination check.
- **Guidance:** ZEM/ZEV guidance is not the Apollo P63/P64 law. There is no hover, redesignation, landing radar or dispersions; Monte Carlo dispersions follow in R6.
- **Engine and propellant:** Isp is constant across throttle, though Apollo flight data show about 296–311 s. Unusable propellant residuals are not subtracted.
- **Fuel optimization:** fuel-optimal routing and reserves follow in R5. Only the Apollo 11 LM profile is sourced; other vehicles use custom parameters.
- **Visualization:** a 2D profile. A 3D trajectory view is not part of this release.
