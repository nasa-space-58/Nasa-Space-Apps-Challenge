# Mission run: physics-gated simulation with fault scenarios

Before this feature, the mission-control flight was a fixed 45 s illustrative
playback that always ended in a successful landing. A mission run replaces
that with an outcome that follows from the chosen site, date, vehicle and
injected faults. It does not use a failure probability. Each failure has a
physical cause, a time and an explanation.

- Backend: `backend/app/mission_run.py`, `GET /mission-faults`,
  `POST /analyses/{analysis_id}/mission-run` (synchronous, about 2 s).
- Curated fault scenarios: `shared/mission_faults.json`.
- Interface: the "Simulation" module in step 4 of mission control
  (`frontend/src/components/MissionRun.tsx`). The 3D landing close-up ends in
  a crash or tip-over when the run fails at those gates.
- Tests: `backend/tests/test_mission_run.py`. Skeleton `3c43546`, frozen
  tests `af70068`, approved correction `d8d990c` (new frozen baseline).

## Gates

The run evaluates four gates in order. A failure at one gate stops the
mission and marks the later gates as `not_reached`. The two surface gates
run in parallel, and the earliest violation in time ends the mission.

| Gate | Physics | Failure | Warning |
|---|---|---|---|
| Powered descent | R4 planar point-mass descent from PDI with ZEM/ZEV guidance to the TN D-6846 gates and the TN D-6846 throttle rule, flown again with time-activated vehicle faults. Ground contact is found by solving the exact crossing of the site sphere inside the integration step. A vehicle still airborne after the schedule continues a bounded controlled descent (at most 600 s). | `crash`: contact faster than 10 ft/s vertical or 4 ft/s horizontal (Apollo LM landing-gear design envelope, NASA TN D-6850). `no_contact`. `nominal_plan_infeasible`. | `hard_landing` above the R4 soft limits (1.0 / 0.5 m/s). `propellant_exhausted` before a survivable contact. `target_miss` more than 50 m from the target. |
| Touchdown terrain | R3 Horn slope of the cached LOLA 80 m DEM. The touchdown point is the target shifted by the along-track guidance miss and any navigation bias, on the 1737.4 km sphere. | `tip_over`: the touchdown pixel's slope exceeds the slope limit (default 10°). | `steep_terrain_nearby` within the landing radius. `landing_area_unknown`. |
| Surface power | Exact piecewise-linear propagation of an ideal battery over the analysis' Sun intervals: full output in full sun, a configurable fraction in partial sun, none in darkness, with constant loads. | `battery_depleted` at the computed instant. | – |
| Earth contact | Geometric Earth-centre visibility from the analysis, minus outages. | `first_contact_deadline`: no contact within the deadline after touchdown (default 1 h; the landing cannot be confirmed). `contact_gap`: silence longer than the maximum gap (default 72 h). | – |

When sunlight or Earth visibility is `unknown` or `boundary`, the gate is run
twice: once with the pessimistic and once with the optimistic interpretation.
If the two runs disagree, the gate is `unknown` and the run is
`inconclusive`. The simulation never guesses.

Outcomes: `success`, `success_with_warnings`, `failure` (with the gate, code,
UTC time and detail of the first failure) or `inconclusive`.

## Fault models

Faults are typed, bounded Pydantic models and act only from their activation
time. Descent times count from powered descent initiation (PDI), surface times
from touchdown.

| Kind | Phase | Effect |
|---|---|---|
| `engine_thrust_loss` | descent | Caps thrust at a fraction of maximum thrust. |
| `propellant_leak` | descent | Removes mass at a constant rate during a window, without producing thrust (the leak's momentum is neglected). |
| `isp_degradation` | descent | Reduces the specific impulse, so the same thrust burns more propellant. |
| `navigation_bias` | descent | The vehicle lands an offset distance away along a bearing. |
| `landing_delay` | landing | Wave-off: the landing occurs later inside the analysis window. |
| `battery_capacity_loss` | surface | Removes a fraction of capacity and of its stored charge. |
| `power_load_increase` | surface | Adds a constant load (for example a stuck heater) during a window. |
| `solar_array_degradation` | surface | Scales array output (for example dust). |
| `communication_outage` | surface | Removes Earth contact during a window. |

The curated scenarios in `shared/mission_faults.json` are team-chosen teaching
cases, not NASA failure statistics or reconstructions of real anomalies. A
request may combine a curated scenario with up to 12 custom faults in total.
Users can build their own scenarios in the interface. These are saved in the
browser (localStorage) and sent as custom faults.

## Observed behaviour (cached data, 2026-11-15)

- Nominal Apollo LM descent: 0.91 m/s contact with about 780 kg propellant
  left. A 0.5 kg/s leak for 600 s removes 300 kg, but the margin falls by
  only 216 kg because the lighter vehicle burns less. A 1.5 kg/s leak lands
  with about 50 kg left. A 2.0 kg/s leak exhausts the propellant 16 m above
  the site and the vehicle crashes at 7 m/s.
- A 10 % thrust loss from 200 s is fatal. The braking phase runs at the fixed
  maximum-thrust setting, so the vehicle cannot reach the high gate.
- On 2026-11-04 and 2026-11-18, Earth is below the terrain horizon at
  Malapert and Haworth at landing. The nominal run fails at the
  first-contact gate, which is the real direct-to-Earth constraint at these
  dates.
- At Malapert from 2026-11-21, the nominal mission survives about six days and
  dies in the 109 h lunar night (11-27). With half the battery lost at
  touchdown, it dies within the first hours of darkness. A transmitter outage
  ends it with a 72 h contact gap.

## Surface outlook, baseline and window context

The verdict above stops at the first failed gate. In a window where Earth
never rises, every landed run therefore fails the first-contact gate one hour
after touchdown, and surface faults that start later (a stuck heater at 6 h,
a transmitter fault at 2 h) cannot change the verdict. Three additive response
fields keep these scenarios distinguishable without changing the verdict
(`failure`, `gates`, `power`, `communication`, `mission_end_utc`):

- `surface_outlook`: a what-if continuation in which the lander keeps running
  autonomously, without contact, from touchdown to the window end. Power uses
  the pessimistic run (unknown sunlight generates nothing). The run stops at
  battery depletion, and contact counts only while the lander is alive. It
  lists time-ordered `findings`: first contact, the missed first-contact
  deadline, every contact gap above the limit (not just the first), surface
  faults starting and ending, and battery depletion. It is `null` after a
  crash, a tip-over or a landing at the window end.
- `baseline`: the fault-free run for the same analysis and vehicle (landing at
  the window start), with a `delta` per quantity: propellant margin,
  touchdown offset, landing delay, lowest battery energy and first-contact
  shift. A delta is `null` when either side lacks the quantity.
- `context`: hours of Earth and Sun visibility in the window, the verdict time
  and `limitations`:
  - `earth_never_visible`: no visible, boundary or unknown Earth interval;
  - per surface fault, `fault_not_reached`: no surface phase;
  - `fault_after_window`: starts at or after the window end;
  - `fault_after_mission_end`: starts after the verdict, visible only in the
    outlook;
  - `fault_exceeds_window`: still active at the window end.

The outlook is an illustration, not a second verdict. A lander without contact
cannot be confirmed by the ground, whatever the outlook shows. The curated
surface faults last up to 80 h, so a 3–7 day window at a date when Earth is
visible shows their full effect. Tests: `backend/tests/test_mission_run_outlook.py`
(frozen at `178e38a`); the interface shows the limitations banner, the delta
card, the full battery trace with the verdict marked, and the findings.

## Assumptions and limits

The vehicle power values (4 kWh battery, 900 W array, 350 W base load) are
illustrative, not a sourced NASA vehicle. The descent inherits all R4
assumptions (`docs/descent-trajectories.md`) and does not check terrain along
the path. The planar model has no MOON_ME heading, so the approach azimuth
used to place the touchdown point is an explicit input (default 0°). An 80 m
DEM cannot resolve lander-scale boulders. Communication is geometric only:
no DSN schedule, pointing or link budget. The mission run is a deterministic
educational simulation. It does not certify landing safety, power or thermal
survival, or communication availability.
