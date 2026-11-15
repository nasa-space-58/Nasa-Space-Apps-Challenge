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
- Tests: `backend/tests/test_mission_routes.py`.

## Frame

All paths share one local planar frame at the primary target: `down_m` along
the approach azimuth, `cross_m` to its right, `altitude_m` above the primary
site sphere and `t_s` since powered descent initiation. A backup's altitude is
shifted by its terrain height difference to the primary.

## Limits

The route set is computed for the nominal (unfaulted) vehicle. It is the
pre-flight plan set, not a replay of the run's injected faults. It inherits
all R4 and R7 assumptions (`docs/descent-trajectories.md`,
`docs/alternative-landing.md`): planar descent, analytic decoupled
crossrange divert, an 80 m DEM that cannot resolve lander-scale hazards. It
is not landing-safety certification or a guaranteed divert capability.
