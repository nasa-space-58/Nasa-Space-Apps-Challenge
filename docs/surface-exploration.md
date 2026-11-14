# Post-landing exploration — R8 (scenario S12)

Developed on `feature/r8-surface-exploration` on top of R7 (`feature/r7-alternative-landing`).
Skeleton: `8a9c969`. Frozen test baseline: `46cb1d5` (`backend/tests/test_surface_routes.py`, 34 cases). `git diff 46cb1d5 -- backend/tests` is empty.

## What it answers

After landing, how well does a site support science? For a landing site and up to five science targets, R8:

- finds a traversable surface route to each target on the LOLA 80 m DEM, avoiding slopes above a mobility limit;
- reports each route's length, duration, maximum slope, climb and detour;
- when a site analysis exists, checks sunlight and Earth visibility at points along each route at the time the rover would arrive;
- gives one mission-efficiency score for the site.

## User flow

**Descent → After landing · surface routes to science targets.** The landing site comes from the Descent form. The user edits up to five targets (name, bearing clockwise from north, distance up to 8 km), the mobility slope limit and the traverse speed.

If Mission control has a site analysis, its ID is read from the browser (`lunar-analysis-id`) and sunlight/Earth checks are offered. Without sunlight checks a run takes about a second. With them it takes about 2 s per sampled point (about 22 s for three targets).

The result shows:

- the efficiency score, the number of reachable targets with their directness, and the share of sunlit route points with the departure time;
- a north-up map of the routes: gold circles for sunlit points, squares for points in shadow, grey for unknown, dashed lines and ✕ for unreachable targets;
- a table per target: route length and time, detour, maximum slope and climb, sunlit points, points where Earth is hidden, and the sun state sequence along the route. Unreachable targets show the reason.

API: `POST /surface/routes`.
- 409 without cached terrain, or when the referenced analysis is not finished.
- 404 for an unknown analysis.
- 422 for invalid input, terrain outside coverage, an impassable landing cell, or an analysis more than 10 km from the landing site.

## Model

- **Traversability:** a cell is passable when its R3 Horn slope is known and at or below the mobility limit. The default 15° is a planning assumption, not a specific rover's capability. Unknown cells are never passable.
- **Routing:** A* over 8-connected passable cells. The step cost is the 3-D distance between cell centres (80 m or 113 m horizontally plus the height change). The straight-line horizontal heuristic is admissible, so the path is the shortest on this grid. Ties are broken deterministically.
- **Duration:** route length divided by a constant speed (default 0.1 m/s including stops). No power, thermal or wheel model.
- **Sunlight and Earth:** evaluated at up to `waypoints_per_route` (default 5) evenly spaced route cells.
  - For each sampled cell, a fresh Task 2 terrain horizon is computed at the cell's height plus the observer height (default 2 m), with the analysis' horizon settings.
  - The Sun/Earth direction at the arrival time is interpolated linearly (azimuth the short way round) between the analysis frames.
  - The landing site's ephemeris is reused along the route. Directions change by at most distance/R, about 0.033° per km.
  - The Sun is classed full, partial or dark against the mean solar angular radius (959.63″ at 1 au). Earth is classed visible or blocked by its centre.
  - Arrivals outside the analysis window are unknown. Stretches between sampled points are not evaluated.
- **Mission efficiency (0–1):** 0.5 × reachable share + 0.25 × directness (mean straight distance / route length) + 0.25 × sunlit share of sampled points. Without sunlight checks, the weights become 2/3 and 1/3. These weights are a documented planning choice.

## Verification

Tests (`test_surface_routes.py`) cover:

- A* on straight and diagonal routes, detours through a gap, unreachable cases and preferring flat ground over a hill;
- the 3-D length, climb and duration;
- even waypoint sampling;
- sky interpolation across 0°/360° and outside the window;
- the sun and Earth classes;
- a straight 2 km route on flat terrain;
- targets that are outside the terrain, too steep, or have no path;
- a detour around a ridge where every route cell stays within the limit;
- an impassable landing cell;
- sunlit, dark and horizon-blocked routes with a stub horizon;
- arrivals after the analysis window and an explicit departure time;
- the efficiency weights;
- determinism and the API.

Real data, Malapert R23, Mission control analysis of 2026-11-04 (24 h), three example targets ([evidence](verification/r8-surface-2026-11-14.json)):

- **North rim** (0°, 2.5 km): a direct route (detour ×1.00, max slope 11.6°, 6.9 h). It enters shadow after the first point, but **Earth becomes visible**.
- **East slope** (90°, 3 km): a direct route (max 8.5°, 8.5 h). It stays **sunlit** at every sampled point, but **Earth is never visible**.
- **South** (200°, 4 km): not reachable. The target cell is steeper than the 15° limit.
- Efficiency 0.73: 2 of 3 targets reachable, fully direct, 60 % of sampled points sunlit. The browser showed the same values with no console errors.

This shows the trade-off R8 is meant to expose: on this date, the sunlit route loses direct contact with Earth, and the route with Earth contact is in shadow.

## Not modelled

- Rocks, craters and slopes below the 80 m cell. Wheel–soil interaction, rover power and thermal budgets.
- Sunlight and Earth between sampled points. Eclipses, and radio links beyond geometric Earth-centre visibility.
- Sun/Earth directions computed separately for each route point. The landing site's ephemeris is reused within the stated bound.

## Mission control: post-landing view

After the touchdown close-up finishes, mission control shows a surface-operations panel and a rover in the landing scene:

- The rover drives the first reachable route of the three example targets above. The route comes from `POST /surface/routes`, with the current analysis when one exists.
  - The route geometry is laid on the illustrative terrain with one uniform scale. The 3D terrain is not the DEM.
  - Playback runs at ×60, ×600 or ×3600 simulated time and can be scrubbed.
- The panel shows backend values only:
  - route length, time, speed assumption, steepest slope, climb and detour;
  - the Sun/Earth state at the last sampled waypoint the rover has passed (no interpolation between samples);
  - the lander profile from `GET /vehicle-profiles`;
  - the NOAA SWPC solar activity snapshot and forecast ([space weather](space-weather.md)), sharing the job of the Solar activity panel.
