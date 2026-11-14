# H11 — Audited relay-orbit selection

H11 is a bounded concept demonstration: classical Earth–Moon circular restricted three-body dynamics produce candidate relay paths, terrain geometry produces contact intervals, and the existing H8 exact/QAOA pair selects one or two distinct candidates. No provider job or credit spending occurs in this stage.

## Sources and frames

Initial conditions come from [NASA/JPL Three-Body Periodic Orbits API v1.0](https://ssd-api.jpl.nasa.gov/doc/periodic_orbits.html), Earth–Moon L2 southern halo family, filtered to periods of 6–9 days. The raw query URL, response checksum, original row indices, initial states, periods, Jacobi constants and stability indices are retained in `shared/examples/relays/inputs.json`. Four accepted rows are 0, 95, 190 and 240. These are catalog halo orbits, not an operational Gateway trajectory or an independently designed launch mission.

The catalog uses nondimensional barycentric synodic coordinates: X points from Earth toward Moon, Z follows orbital angular velocity and Y completes the right-handed basis. Length and time units come directly from the catalog system record. Dynamics follow the [JPL CR3BP model](https://ssd.jpl.nasa.gov/tools/periodic_orbits.html): gravity from two circular primaries plus rotating-frame Coriolis and centrifugal terms. The Jacobi invariant is independently checked against the catalog values.

Terrain profiles are projections of existing LOLA analyses:

- Mons Mouton feature center: analysis `e430cbda-c6fb-5828-b105-f16eb6579441`, -84.6°, 31° E.
- Malapert Massif R23: analysis `c88ce319-e99f-44fd-97b2-1c427121773a`, -85.9948°, 359.76422° E.

Each projection retains analysis/result/profile identity, source checksum and URL, observer height and 360 horizon directions. The shared circular horizon interpolator is reused by the existing terrain pipeline and H11; unknown neighbors remain unknown. The existing horizon test suite still passes.

**Frame approximation:** an explicitly assumed synchronous Moon maps the terrain axes to the synodic axes with diag(-1,-1,1), so the prime meridian points toward Earth and spin/orbital poles are aligned. This is not a date-dependent MOON_ME-to-synodic/SPICE transformation. The 2026-11-04 UTC timestamp is a replay label for H0 contracts; elapsed seconds are ideal dynamical time. Lunar libration, inclination, eccentricity, solar perturbations and real ephemeris time-scale conversions are not modeled. The terrain radius of 1737.4 km is reconciled explicitly with the catalog radius of 1737.1 km when placing observers.

## Physics verification and rejected data

DOP853 integrates one full catalog period, with rtol 1e-12 and atol 1e-14 for recorded evidence and a maximum step of period/200. Tests compare two tolerance settings, analytic L4 equilibrium/Jacobi values, catalog Jacobi constants and endpoint closure. The recorded paths contain 10,001 samples per period and are replayed periodically; position interpolation is linear on that dense grid.

| Catalog row | Period, days | Dimensionless closure norm | Jacobi drift | Minimum sampled lunar radius, km |
| --- | ---: | ---: | ---: | ---: |
| 0 | 8.998224 | 3.29e-13 | 3.76e-12 | 11135.99 |
| 95 | 7.937388 | 8.15e-13 | 3.48e-12 | 7197.82 |
| 190 | 6.947288 | 2.65e-12 | 9.62e-12 | 3991.98 |
| 240 | 6.452364 | 1.45e-12 | 1.36e-11 | 2661.04 |

The original candidate row 286 passed periodic dynamics checks but reached about 1682.31 km from the Moon center, inside its 1737.1 km reference sphere. The existing physical-clearance test rejected it. It was removed from selectable production input, replaced by catalog row 240 and retained in `rejected_orbits` with its source state and rejection reason. No physical tolerance or assertion was weakened. Catalog periodicity does not imply a usable spacecraft orbit. Surface clearance here is sampled, not a proof of continuous terrain avoidance or long-term stability.

## Contact and coverage semantics

Two geometric legs must be open: site-to-relay above the local terrain horizon with lunar-sphere clearance, and relay-to-Earth-center without lunar occultation. Segment tests use a finite segment, not an infinite ray. A horizon equality is unavailable, consistent with R2's strict boundary convention. Unknown terrain is never treated as clear.

R2's existing DSN station-to-Moon elevation windows are not repurposed as relay-to-station windows: they describe a different target. H11 reports Earth-center geometry only. It does not assert DSN visibility, reservations, antenna capacity, radio throughput, occultation by the finite Earth disk, or a closed RF link budget.

The example spans eight ideal days. A 300-second interval counts as available only if both endpoints and midpoint are available. The 600-second grid is also computed: all 9,216 coarse classifications agree with the conjunction of the corresponding fine intervals, while individual covered durations increase by up to 300 seconds on the finer grid. The recorded per-orbit/site deltas distinguish this from exact continuous agreement. Brief events between samples can still be missed.

Coverage uses the union of selected relays, never the sum of overlapping durations. Each site must satisfy all three declared thresholds: minimum coverage fraction, maximum contiguous unavailable/unknown gap, and minimum total contact seconds. Contacts for different sites do not compete for bandwidth in this model. An unknown input conservatively stops the solver rather than selecting around incomplete evidence.

## Hybrid decision and measured example

The supported scope is k=1 or k=2 out of four candidates. H0 represents k selection slots, each choosing one orbit. Duplicate selections are forbidden. For k=1, failing candidates receive hard gates; for k=2, every pair failing the original coverage validator becomes an exclusion. H2 compiles the resulting one-hot QUBO and checks all binary assignments against the original H1 constraints. An independent post-solve check recomputes union coverage, gaps and per-site demand directly from the geometry grid.

Slot encoding has four or eight one-hot variables and two or four compact logical qubits. Each unordered two-orbit set has two slot permutations; these are not distinct physical constellations. The objective minimizes declared preference costs 1,2,3,4 for the ordered catalog candidates. These are illustrative preferences, not propellant, money, launch difficulty or station-keeping cost. k greater than two and general higher-order coverage constraints are rejected rather than approximated by invalid pairwise penalties.

Default conditions are coverage >=0.8, gap <=86400 s and contact >=3600 s per site. These are teaching assumptions, not NASA mission requirements. The classical optimum selects rows 0 and 240, preference cost 5 (H8 maximization score -5):

| Site | Direct Earth in the ideal model | Selected-relay coverage | Longest sampled gap |
| --- | --- | ---: | ---: |
| Mons Mouton | Available | 88.1944% | 81600 s |
| Malapert R23 | Terrain-blocked | 100% | 0 s |

The result shows geometric restoration for the modeled Malapert case, not guaranteed real-world communications. Single row 240 also meets the weaker default requirements; the two-relay case specifically asks for exactly two relays and improves Malapert coverage to 100%. Requiring 100% coverage and zero gaps at both sites produces an explicitly infeasible case; QAOA is not run.

The local noiseless QAOA example uses four logical qubits, one layer, 80 objective evaluations, seed 17 and 256 samples. It returned 208 feasible samples (81.25%), including 55 optimal samples (21.484375%), with best-plan gap zero. The logical cost-circuit estimate is 34 CX gates, before hardware transpilation/routing. This is a simulator result, not a hardware performance prediction. The problem has only 16 ordered assignments and remains easy classically. No quantum advantage is claimed.

## Reproduction and delivery

- `#/relays` displays source/frame limits, direct Earth geometry, orbit projection/audits, rejection evidence, one/two-relay controls and H8 comparison details.
- `GET /research/relays` reads the checksummed fixed report. `POST /research/relays/compare` runs only a bounded local comparison, under the H8/H9 process-local lock.
- `notebooks/H11_relay_orbits.ipynb` has ten code cells, all executed without errors. It rechecks a catalog orbit, displays source/geometry evidence, audits the QUBO, reruns local QAOA and demonstrates infeasibility. Run All cannot spend credits.
- `quantum/relay_evidence.py` rebuilds the report offline from the fixed source projection. In the optional environment, use `PYTHONPATH=backend:. python -m quantum.relay_evidence --inputs shared/examples/relays/inputs.json --output /tmp/h11-report.json`.
- The ingestion worker provides explicit catalog acquisition: `python -m app.relay_catalog --output-dir /data/relays/h11-jpl-catalog`. It validates the API signature/schema and preserves raw bytes plus a checksum manifest. Backend routes never download external data. Catalog response key order can change raw hashes while numerical rows remain identical; the original input archive is retained locally outside Git.

Frozen test history: dynamics/selection skeleton `15b5914`, tests `de28ebe`, user-approved infeasible-ground-state expectation correction `3255d5f`; evidence-builder skeleton `c06f813`, tests `9241090`, approved import-order-only correction `c2e474c`; acquisition skeleton `baf8e1f`, tests `f2ec6f8`. Existing tests were preserved. The original H6 notebook local edit remains excluded from commits.

H10/H11 delivery branch: `codex/h10-h11-scaling-relays`, based on H9 `fb59791`. Push only this branch; the user merges. No main/tahakrcc push and no real quantum credit expenditure are authorized by running these local examples. Final combined checks are recorded below after completion.

## Final combined verification

On 2026-11-15 (Europe/Istanbul), the complete suites passed: **1178 backend tests, 191 ingestion tests and 91 optional quantum tests**, with Ruff checks passing. TypeScript/Vite build passed. H10's seven and H11's ten notebook code cells executed without errors. No frontend tests or screenshots were run; visual acceptance remains with the user. The pre-existing large LunarSurface chunk and Starlette/AnyIO deprecation warnings remain.

Live API checks returned HTTP 200 for both recorded reports. Default two-relay and one-relay comparisons returned optimal classical results and completed local simulation; the strict all-sites 100%-coverage/zero-gap case returned infeasible and did not run QAOA. Every API response reported zero credit spending. The offline report-builder CLI was rerun, and all four accepted plus the rejected orbit state were checked against a newly downloaded worker catalog. No QPU submission or provider account access occurred.
