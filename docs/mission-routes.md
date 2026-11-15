# Mission run route set

When a mission run starts (step 4 of mission control, "Simulation"), the
interface also requests every landing route the planner computed around the
analysis site and draws them in the 3D scene:

| Role | Colour | Line | Meaning |
|---|---|---|---|
| `primary` | green | solid | The planned landing target of the analysis, flown from the divert point. |
| `backup` | yellow | solid | A backup site that passes the R3 hard constraints and is reachable from the divert point. Ranked like R7. |
| `rejected` (flown) | red | dashed | A backup site that was flown but is not reachable: propellant, reserve, thrust or touchdown tolerance (the R7 rejection reasons). |
| `rejected` (terrain) | red | point only | A cell in the search area that fails the R3 hard constraints (landing-area slope above the limit or unknown terrain). It is never flown, so it has no path. |

Selecting a route (in the scene or in the list) shows its metrics and the
reasons it was chosen, kept as a backup or rejected.

- Backend: `backend/app/mission_routes.py`,
  `POST /analyses/{analysis_id}/mission-routes`. R7 support:
  `lateral_offset_m`, `evaluate_divert(record_path=True)` and
  `plan_alternatives(trajectories=True)` in `backend/app/alternatives.py`
  (default behaviour and `result_id` unchanged).
- Interface: `frontend/src/components/MissionRun.tsx` and the analysis scene.
- Tests: `backend/tests/test_mission_routes.py` (skeleton `0b272e0`, frozen
  `58af088`) and `backend/tests/test_mission_route_terrain.py` (skeleton
  `c4b60e2`, frozen `c7cf970`).

The interface sends the route request in parallel with the run and reuses
the result for later runs of the same analysis, because the route set does
not depend on faults. When the run returns, the scene switches to the route terrain view
(`frontend/src/components/RouteTerrainScene.tsx`): the real DEM grid at true
scale, every path from the divert point down to its landing point, a pole at
each landing point and a cross at each terrain-rejected cell. The lander
model follows the selected route (else the flown run) with the playback bar; "Watch flight" returns to the illustrative flight. The flown
(faulted) descent of the run is drawn in white from the divert time onward,
along-track only, so it can be compared with the plans.

## Terrain view

The endpoint also returns `terrain`: the cached LOLA DEM resampled
(bilinear) onto a regular grid in the route frame that covers every path and
landing point plus a margin, at most 160 x 160 cells and never finer than the
80 m DEM. Heights are relative to the primary site height, the same reference
as the path altitudes, so the interface can draw the routes over the real
terrain at true scale. Unknown or uncovered samples are `null`, never zero.
The planar frame flattens the Moon's curvature (about 29 m over 10 km).

## Frame

All paths share one local planar frame at the primary target: `down_m` along
the approach azimuth, `cross_m` to its right, `altitude_m` above the primary
site sphere and `t_s` since powered descent initiation. A backup's altitude is
shifted by its terrain height difference to the primary.

## Observed behaviour (cached LOLA data, 2026-11-15)

With the defaults (Apollo 11 LM, high-gate divert, 3 km search radius, up to
8 backups and 6 terrain rejections), Haworth, Malapert and the Shackleton rim
each give 1 primary, 8 reachable backups and 6 cells rejected for slope, in
about 3.6 s. At Haworth the terrain grid is 140 x 80 cells at 80 m spacing
(relief -243 m to +90 m relative to the target), and the whole response is
about 360 KB. Flown rejections (propellant, reserve, thrust, tolerance) appear
with a tighter vehicle or a low-gate divert; the tests cover them with a
660 kg shorter propellant load.

## Limits

The route set is computed for the nominal (unfaulted) vehicle. It is the
pre-flight plan set, not a replay of the run's injected faults. It inherits
all R4 and R7 assumptions (`docs/descent-trajectories.md`,
`docs/alternative-landing.md`): planar descent, analytic decoupled
crossrange divert, an 80 m DEM that cannot resolve lander-scale hazards. It
is not landing-safety certification or a guaranteed divert capability.
