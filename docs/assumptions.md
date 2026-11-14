# MVP Input Contract and Assumptions

## Development defaults

These limits are implementation defaults selected for the initial setup and can be reviewed by the team. They are not spacecraft requirements or NASA operating limits.

| Input | Initial rule |
|---|---|
| Dates | 2026-01-01 through the exclusive operational boundary 2028-01-01, in UTC |
| Interval | Start precedes end; at most 31 days |
| Sampling | 3600 s default; 300 s and 60 s for refinement |
| Size | At most 10,000 regularly spaced samples per request, including the start |
| Default demonstration duration | 24 hours |
| Coordinate frame | Moon Mean Earth / polar axis, stored as `MOON_ME`; DE421 Mean-Earth realization; source metadata verified in terrain-and-sites.md |
| Latitude | Degrees, south negative, [-90, 90] |
| Longitude | Degrees east, [0, 360); normalize input explicitly at the UI boundary |
| Altitude | km relative to the 1737.4 km reference sphere reported by the existing Horizons query |
| Observer height | Proposed 2 m above local terrain for the first terrain demonstration; this is a team assumption |

`altitude_km` is distinct from height above terrain. Product 90 heights in meters are relative to the 1737.4 km sphere. The frame/reference match and provisional pixel-based observer altitudes are documented in terrain-and-sites.md. Task 1 now caches the full raw source and processed windows. Task 2 computes a bounded sampled horizon; see terrain-horizon.md for radius and sampling limitations.

## Terrain and initial sites

Initial product: PGDA 90 `LDEM_80S_80MPP_ADJ.TIF` (80 m/pixel; nominal 80–90° S). Actual verification covers two 501 × 501 pixel windows around the initial observers, both entirely finite and unmasked. Arbitrary locations require fresh bounds/NoData checks. These windows do not establish a sufficient horizon radius.

Initial source-backed observers: Mons Mouton feature center (−84.60°, 31.00° E; IAU/USGS feature 16070) and Malapert Massif R23 (−85.9948°, 359.76422° E; NASA M2M-30044, p.75). They are comparison inputs, not safe landing-site certifications. Runtime preset selection remains unimplemented.

See [terrain verification and sources](terrain-and-sites.md), [machine-readable catalog](initial-sites.json) and [inspection evidence](verification/terrain-2026-11-14.json). The previous 0° E, 89.5° S, 0 km query remains a spherical-reference fixture only.

## Visibility semantics for Step 3

- Center clearance is target elevation minus terrain horizon elevation at the target azimuth.
- Positive clearance indicates visible center, negative indicates blocked center, and zero is a boundary state.
- Under the documented locally constant horizon approximation, solar center clearance plus angular radius above zero permits partial visibility. Center clearance minus radius above zero gives full disk visibility. Fractional visible area requires its own calculation.
- Missing ephemeris or incomplete horizon coverage yields an unknown result with its duration reported.
- A setting event outside the computed interval is reported as no event found in that interval.
- Earth-center visibility is a geometric metric. DSN geometry, station availability and link budgets enter the later communication release.

## Current verification status

Local Docker services and the full test suite passed on 2026-11-14. Horizons CLI acquisition returned 25 samples per target and cache reuse passed; see horizons-ingestion.md. Python transitive dependency locks and a fresh-clone Docker installation were verified; see clean-install-verification.md. Step 0 terrain/source definition is complete for the two documented initial observers. Task 1 ingestion and Task 2 bounded horizon calculation are now locally verified; see ingestion-pipeline.md and terrain-horizon.md. Task 3 now derives sampled visibility intervals and estimated metrics; see visibility-metrics.md for temporal, terrain and disk-model limitations. API/UI remain Task 4. The CLI accepts only whole-second UTC timestamps.
