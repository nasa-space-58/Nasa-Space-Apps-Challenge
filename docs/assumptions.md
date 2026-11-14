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
| Coordinate frame | Moon Mean Earth / polar axis, stored as `MOON_ME`; source frame metadata must be verified during ingestion |
| Latitude | Degrees, south negative, [-90, 90] |
| Longitude | Degrees east, [0, 360); normalize input explicitly at the UI boundary |
| Altitude | km relative to the 1737.4 km reference sphere reported by the existing Horizons query |
| Observer height | Proposed 2 m above local terrain for the first terrain demonstration; this is a team assumption |

`altitude_km` is distinct from height above terrain. Step 1 must reconcile the DEM's vertical reference with the Horizons site definition before deriving observer altitude. No terrain coverage is currently downloaded or certified.

## Terrain and initial sites

The initial terrain acquisition target is the 80–90° S region from PGDA product 90. Actual usable coverage will be determined by raster bounds, NoData values and the horizon search radius. A valid latitude/longitude alone does not establish terrain coverage.

The existing query reference at 0° E, 89.5° S and 0 km is a reproducibility fixture, not a curated landing site. Initial candidate regions are Mons Mouton and the Malapert region. Exact preset coordinates and source product identifiers remain pending verification in Step 1, before they are exposed as selectable presets.

References are recorded in [data-sources.md](data-sources.md) and [2gbram_horizons_veri_notlari.md](2gbram_horizons_veri_notlari.md).

## Visibility semantics for Step 3

- Center clearance is target elevation minus terrain horizon elevation at the target azimuth.
- Positive clearance indicates visible center, negative indicates blocked center, and zero is a boundary state.
- Under the documented locally constant horizon approximation, solar center clearance plus angular radius above zero permits partial visibility. Center clearance minus radius above zero gives full disk visibility. Fractional visible area requires its own calculation.
- Missing ephemeris or incomplete horizon coverage yields an unknown result with its duration reported.
- A setting event outside the computed interval is reported as no event found in that interval.
- Earth-center visibility is a geometric metric. DSN geometry, station availability and link budgets enter the later communication release.

## Current verification status

Local Docker services and the full test suite passed on 2026-11-14. Horizons CLI acquisition returned 25 samples per target and cache reuse passed; see horizons-ingestion.md. Python transitive dependency locks and a fresh-clone Docker installation were verified; see clean-install-verification.md. Terrain coverage and curated site coordinates remain open. The CLI accepts only whole-second UTC timestamps.
