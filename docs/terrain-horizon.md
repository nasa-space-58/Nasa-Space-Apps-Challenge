# Task 2 — bounded terrain horizon

The backend now computes a reproducible, sampled 360-degree terrain horizon from
Task 1's local cache. It performs no download and never writes the data volume.
The output is a finite-distance geometric profile, not an operational visibility
or landing-safety prediction. Visibility/time metrics remain Task 3; API/UI remain
Task 4. Task 2 is delivered only to `feature/task2-terrain-horizon`, based on
`feature/task1-ingestion-cache`. Branch integration is performed by the user.

## Run

After Task 1 returns a ready event, pass its terrain reference:

```bash
docker compose --env-file .env.example build backend
docker compose --env-file .env.example run --rm --no-deps backend \
  python -m app.compute_horizon \
  terrain/processed/2d16e4a91f72903dc821c84342efe7350bb308b479b41e2b7a6ae62669ecbd29 \
  > horizon.json
```

Use the reference from your own ready event if different. The example reference
requires the documented Malapert ingestion envelope and matching software/source
versions. A missing cache is an error, never a request for an external download.
`DATA_DIR` defaults to `/data`. JSON is written to stdout; shell redirection writes
the report on the host, not in the shared data volume. An incomplete directional
profile is still a valid report (exit 0); invalid inputs/checksums exit nonzero.

For an isolated local Compose setup, insert
`-p nasa-task1 --env-file .env.example -f docker-compose.yml -f docker-compose.isolated.yml`
after `docker compose` in place of the ordinary `--env-file` option. This reuses
Task 1's independent data volume and publishes no host ports.

Options:

| Setting | Default | Accepted range |
|---|---:|---|
| `--azimuth-step-deg` | 1 | 0.25–90, must divide 360 evenly |
| `--search-distance-m` | 100000 | 80–300000 |
| `--radial-step-m` | 40 | 10–80 |
| `--source-extent` | raw | raw or crop |

The settings reject work above a conservative bound of five million samples,
including the near field. Every ray also samples each meter from 1 m through
min(1 km, search radius), and the exact outer endpoint. The radial step controls
additional samples, particularly beyond 1 km. This prevents coarse radial steps
from skipping much of the nearby pixel geometry. It does **not** create 1 m
terrain detail: the underlying cells remain 80 m.

## Inputs, reference alignment and scope

The input reference identifies a Task 1 processed manifest. Its observer is used
without altering latitude, longitude or altitude. The loader checks product ID,
MOON_ME / DE421 mean-Earth realization, the 1,737,400 m reference sphere, both crop
and raw-source SHA-256 hashes and containment of local paths. Network URLs cannot
be supplied as input paths. Raster metadata must match the spherical south-polar
stereographic 80 m, float32, single-band, north-up grid with unit scale/offset.
The maximum raster is the selected 7,600-square source.

By default, computation reads the already-cached **full raw DEM**, not only the
40 km crop. No source files are modified. This is needed because a crop's edge
cannot stand in for a distant horizon. `--source-extent crop` is a diagnostic way
to expose this limitation. The loader retains one raster grid in memory, about
231 MB for the full float32 source plus masks, GDAL buffers and temporary arrays;
allow several hundred MB of container memory.

The observer altitude is the radial height above the reference sphere, in km.
The containing native pixel supplies ground height in meters; their difference
must be positive. It is recorded as `observer_height_above_terrain_m`. No 2 m
height is silently added and no already-downloaded Horizons request is changed.
The documented Malapert envelope yields 2 m above its containing pixel. A ground
height interpolated differently would require a separately aligned observer
request and ephemeris, not a hidden change inside this calculation.

## Geometry

All internal angular calculations use radians. Output azimuth/elevation use
degrees. Azimuth is clockwise from local true north toward east, not projected
map-grid north. At the exact south pole, the input longitude defines the north
meridian. A local orthonormal north/east/up basis generates great-circle rays,
including at the pole and across longitude zero.

For reference-sphere surface distance `s`, target terrain height `h_t`, observer
height `h_o` (both radial heights above the same sphere), and `theta = s / R`:

```text
horizontal_m = (R + h_t) * sin(theta)
up_m = (h_t - h_o) - 2 * (R + h_t) * sin(theta / 2)^2
elevation_rad = atan2(up_m, horizontal_m)
```

This is exactly the local projection of a spherical Cartesian target minus the
observer, using the half-angle identity to avoid near-distance subtraction of
large nearly equal radii. It includes lunar curvature; it is not a flat-DEM
slope approximation. Refraction and non-spherical gravity/geoid effects are not
modeled. The maximum sampled elevation along a ray is its sampled horizon;
negative horizons are preserved rather than clamped to zero.

## Coverage and output contract

Each direction includes azimuth, elevation, sampled maximum, distance of the
maximizing sample, valid/requested sample counts and first missing distance.

- `complete_within_radius`: all requested samples exist inside raster bounds and
  the nominal 80–90 degrees south domain and are finite/unmasked.
- `incomplete`: one or more samples are missing. `elevation_deg` is JSON `null`.
  `sampled_max_elevation_deg` is diagnostic only and must not be used as a known
  horizon or to claim unobstructed sunlight/communication.

Completeness applies to the **sampled ray inside the declared radius**, not every
point on a continuous surface or the whole Moon. Missing samples are never filled
or silently skipped. The result explicitly says `beyond_search_radius:
not_evaluated` and `global_horizon_verified: false`.

`interpolate_horizon` provides circular linear interpolation between neighboring
rays. It handles negative angles and 360-degree wrap-around. A missing neighbor
returns unknown; an exact known ray is usable independently of its neighbor.
Interpolation cannot guarantee the absence of an obstacle between rays.

The profile includes settings, site, source hashes, frame, resolution, software
versions, model version and a deterministic SHA-256 `profile_id` of the complete
result before adding that ID. There is no timestamp-dependent ID or automatic
result-cache write in this task. Repeating the same computation is deterministic
in the recorded container environment.

## Verification and practical uncertainty

Frozen tests: `c7960cb`. The 39 new cases cover independent Cartesian geometry,
analytic sphere dip, pole/longitude conventions, a directional ridge, NoData,
out-of-bounds rays, invalid observers/CRS, circular interpolation, corrupt files,
path containment, provenance, parameter identity and read-only CLI execution.
The pre-existing tests are unchanged. Analytic angular tolerances separate
floating-point comparisons from finite radial sampling.

[Real-data evidence](verification/task2-horizon-2026-11-14.json) is generated by:

```bash
docker compose --env-file .env.example run --rm --no-deps -T backend \
  python - terrain/processed/2d16e4a91f72903dc821c84342efe7350bb308b479b41e2b7a6ae62669ecbd29 \
  < scripts/verify-horizon.py > verification.json
```

On Malapert R23, all 360 default rays are covered. The 20, 50, 100 and 150 km
profiles have identical elevations on the tested 1-degree/40 m grid. This is
site-specific evidence supporting the initial 100 km bounded setting, **not**
a global proof that distant terrain is irrelevant. The 40 km crop produces 360
unknown directions when asked to support a 100 km search.

Refining the outer radial step from 40 to 20 m changes individual elevations by
up to approximately **0.1135 degrees**. The computed half-degree rays differ from
linear interpolation of the 1-degree rays by up to **0.7307 degrees**. These are
measured sensitivity examples, not universal error bars. Near-field pixel edges
and narrow features matter. Therefore this default profile is a development
baseline; accurate transition timing in Task 3 must retain spatial uncertainty
and evaluate refinement rather than infer precision from the JSON decimals.
80 m is grid spacing, not the independent LOLA measurement density or a guarantee
that subpixel obstacles are captured. No finer DEM accuracy is claimed.

A separate scalar great-circle destination calculation followed by Cartesian
subtraction and ENU projection agrees with production cardinal-ray maxima within
1e-7 degree. That verifies implementation geometry, not the terrain's physical
accuracy. Repeated results are identical; checksums are verified on each load.

## Sources

- [NASA PGDA product 90](https://pgda.gsfc.nasa.gov/products/90): product,
  south-polar stereographic grid, MOON_ME/DE421 frame and height units.
- [Barker et al. 2023](https://doi.org/10.3847/PSJ/acf3e1): the underlying
  adjusted LOLA terrain product; dataset DOI 10.60903/gsfcpgda-lola-spole.
- [NAIF rectangular to azimuth/elevation](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/cspice/recazl_c.html):
  local rectangular angle conventions; our spherical projection is derived above.
- [Rasterio sample documentation](https://rasterio.readthedocs.io/en/stable/api/rasterio.sample.html):
  dataset-coordinate sampling used in the independent real-data check.
- [Existing frame/height evidence](terrain-and-sites.md): observer definitions,
  vertical reference and Horizons alignment sources.

Final local checks on 2026-11-14: 69 backend tests, 72 ingestion tests and
1 frontend test passed (142 total). Both Python lint checks, new-code formatter
checks, pip check, frontend clean install/build and npm audit passed. npm reported
zero vulnerabilities. The existing Starlette/AnyIO deprecation warning remains
non-failing. This is local implementation verification; teammate review and
manual merge are not implied.
