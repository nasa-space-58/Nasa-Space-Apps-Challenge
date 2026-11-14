# R11 — Multi-mission planning and shared resources

R11 plans several lunar missions together. They share landing sites, time and ground-station capacity, and a mission may depend on another mission's landing. The planner builds landing options from validated terrain, sunlight, descent and vehicle evidence. It then picks one option, a deferral or a cancellation for every mission and schedules each downlink slot through the stations or a relay lander. Every plan is checked again by a validator that does not use the solver. The planner compares the joint plan with the plan each mission would choose alone and explains every decision. A mission set with no feasible plan is reduced to a minimal conflicting set. The shared timeline appears in the **Campaign** tab of mission control.

H1/H3/H6 already provided the static three-mission baseline, ideal battery/activity scheduling and the downstream value of an early landing. R11 adds:

- mission types and priorities;
- candidates generated from evidence;
- communication scheduling with data rates and exclusivity;
- defer/cancel policies;
- relay links;
- infeasibility explanations;
- the timeline interface.

## Delivery and test-first evidence

- **Branch:** `feature/r11-multi-mission-planning`, from `feature/r10-hybrid-optimization` at `5f9a65b`. R11 uses the R10 decision to keep the HiGHS MILP solver.
- **Push and merge:** only this branch is pushed; merging is manual.
- **Commits:** skeleton `67d24ca`; frozen tests `8504f81` (115 tests); implementation `ce55473`.
- **Frozen tests:** no earlier frozen test, H0 contract or fixture was changed. The H0 V1 `Scenario` is unchanged; R11 has its own versioned contract.

| File | Content |
|---|---|
| `shared/campaign.py` | R11 contracts: missions, vehicles, site evidence, stations, link rates, relay geometry, preferences, fingerprint and loader |
| `backend/app/campaign_planning.py` | Candidate generation, MILP planner, validator, independent planner, infeasibility and choice explanations, timeline, result adapters and API routes |
| `shared/examples/r11/*.json` | Three synthetic example campaigns |
| `frontend/src/components/MissionPlanner.tsx` | Shared timeline and decision panel (presentation only) |
| `docs/verification/r11-campaign-planning-2026-11-15.json` | Results for the examples and one real-evidence case |

The teammate branch `codex/h9-shared-antenna` (not merged) has a related shared-antenna demonstration. R11 does not depend on it and uses separate file names.

## Campaign contract (`shared/campaign.py`)

All models reject extra fields, non-finite numbers and naive timestamps. Units are in field names, sites use MOON_ME degrees and times are explicit UTC. Set-like collections are sorted, so input order does not change the SHA-256 `campaign_fingerprint`.

| Model | Meaning |
|---|---|
| `MissionSpec` | Mission ID, vehicle, `mission_type` (`crewed_lander`, `robotic_lander`, `rover`, `relay_lander`) and `priority` 1–5. Also: nominal landing window, deadline, allowed sites with science scores, `requirements`, `policy`, `dependencies`, `requires_exclusive_link`, `relay_terminal` and optional `relay_service` |
| `Requirements` | Surface-operation duration (s), consumed energy (J) and data that must be delivered by the deadline (bits) |
| `MissionPolicy` | Whether cancellation is allowed, and its score penalty. Whether deferral is allowed, `max_defer_s`, and its fixed and per-hour penalties |
| `Dependency` | `landed_before_landing`: the successor lands at least `min_lag_s` after the predecessor; a successor cannot fly if its predecessor is cancelled |
| `VehicleModel` | Usable propellant, propellant reserve, landing slope limit, maximum unknown-terrain fraction, full-sunlight power (W), initial energy and energy reserve (J). Each vehicle flies at most one mission |
| `SiteEvidence` | Site, evidence window, terrain slope evidence, sunlight intervals, per-station geometric visibility and descent propellant per vehicle. Gaps inside the window are unknown |
| `GroundStation` | Calendar windows and simultaneous-link capacity; null capacity means unknown |
| `LinkRate` | Assumed sustained data rate per mission and station (bit/s); this is not a link budget |
| `RelayGeometry` | Line-of-sight evidence between a client site and a relay site |
| `Preferences` | Priority weights and weighted, normalized soft objectives: science, fuel, waiting time and propellant reserve |

Station **geometry**, **calendar**, **capacity**, **data rate** and **exclusivity** are separate inputs. None of them is inferred from another. A visible station is unusable if its calendar is closed, its capacity is unknown or no rate is declared.

## Candidate generation

For every mission and allowed site, landing times are laid on a `landing_step_s` grid inside the nominal window. When deferral is allowed, the grid continues for `max_defer_s` past the window, and a deferred option's deadline moves later by `max_defer_s`. Each landing time is accepted only if all hard checks pass:

| Check | Rejection code |
|---|---|
| Surface operations end inside the campaign window, the deadline and the evidence window | `outside_campaign_window`, `deadline`, `outside_evidence` |
| Landing-area slope is known and within the vehicle limit; unknown terrain fraction within limit | `terrain_unknown`, `terrain_slope`, `terrain_unknown_fraction` |
| Descent evidence exists for the vehicle, is feasible, and used propellant plus reserve fit in usable propellant | `descent_unknown`, `descent_infeasible`, `propellant_reserve` |
| Sunlight is known throughout surface operations | `sunlight_unknown` |
| Initial energy + generated energy − consumption ≥ energy reserve | `energy` |

Only full-disk sunlight generates power; partial or boundary sunlight counts as zero, which is conservative. The energy check is a net balance, not a time-resolved battery trace; H3 provides the ideal storage model. Every rejection keeps all failing codes with plain-language details. An accepted candidate has an option ID (`mission.site.YYYYmmddTHHMMSSZ`) and the following metrics:

- fuel, propellant margin above reserve, generated energy and energy margin;
- waiting time from the start of the nominal window;
- deferral time;
- an exact rational score.

The score is the priority weight × science term, minus fuel and waiting terms, plus the reserve term, minus deferral penalties. Arithmetic uses exact fractions of the decimal inputs.

`site_evidence_from_results` adapts real result shapes:

- Task 3 `sun.intervals`;
- R2 `windows.stations[*].intervals` (station elevation and Earth above the terrain horizon);
- the R3 terrain-safety result;
- R4 `plan_descent` totals.

Provenance is kept: the analysis and communication jobs are observed sources, the LOLA DEM is an observed source, and terrain safety and descent are derived sources. `POST /analyses/{id}/campaign-evidence` builds this evidence from a stored analysis, an optional R2 job, the cached DEM and the selected R4 vehicle profiles.

## Joint planning (HiGHS MILP)

Variables:

- one binary per candidate;
- one binary per cancellable mission;
- one binary per usable link slot: a direct link for a candidate, station and slot, or a relayed link for a client candidate, relay candidate, station and slot.

A link slot is usable only if all of these hold:

- the slot lies fully inside the candidate's communication window (landing to the effective deadline);
- the station calendar covers the slot;
- the station capacity is known and positive;
- station geometry from the site is `available` for the whole slot;
- a data rate is declared.

Hard constraints:

- exactly one option or a permitted cancellation per mission;
- links only for selected candidates;
- one radio link per mission per slot;
- station capacity per slot;
- an exclusive mission's slot cannot be shared with any other mission at that station;
- relay capacity per slot;
- delivered bits ≥ required bits;
- dependency lag. A successor option needs a compatible selected predecessor option, so a cancelled predecessor cancels the successor.

The objective maximizes the summed candidate scores minus cancellation penalties. A second pass keeps that selection and picks the fewest link slots, preferring earlier and direct ones. Results are always revalidated; a validator disagreement raises an error. The model is bounded to 20,000 variables and 20,000 landing evaluations; larger inputs fail with an explicit error.

**Relay credit.** A mission gets relay support only when all of these hold:

- the client has a relay terminal and the relay mission has relay service;
- both missions are selected;
- the slot is inside both communication windows;
- line-of-sight evidence for the site pair is available for the whole slot;
- the sites' straight-line distance on the 1737.4 km reference sphere is within the relay range;
- the relay site has its own usable station link in the same slot (bent pipe).

The relayed rate is the lower of the hop rate and the relay's station rate. Forwarded streams count against relay capacity and station capacity, not against the relay's own radio.

## Independent validator

`validate_campaign_plan` regenerates the candidates and checks a plan against the original rules without any solver. It raises `ValueError` for:

- stale campaign identity or fingerprint;
- unknown missions or options;
- an option from another mission.

It then reports issues with mission, station and slot context:

- `missing_mission`, `cancel_not_allowed`, `dependency_lag`, `dependency_cancelled`;
- `allocation_for_inactive_mission`, `invalid_slot`, `outside_comm_window`;
- `station_unknown`, `station_unavailable`, `station_capacity_unknown`, `geometry_unavailable`, `rate_unknown`, `bits_mismatch`;
- `radio_conflict`, `station_capacity`, `exclusive_link`;
- `relay_equipment`, `relay_inactive`, `relay_range`, `relay_geometry`, `relay_capacity`;
- `downlink_shortfall`.

Links that fail a precondition deliver no data; shared-capacity conflicts are reported without discarding data. A plan with an infeasible status may still show a score for comparison, but that score is not achievable.

## Comparison, explanations and infeasible sets

- **Independent plan.** Each mission takes its highest-scoring option that fits alone. It uses the earliest usable slots at the fastest station and ignores other missions, dependencies and relays. The validator then shows what breaks when these choices are combined.
- **Choice explanations.** For each mission, the panel shows the selected option (or cancellation) and up to three next-best options. Each is re-solved with that option forced: the result is either "would make the campaign infeasible" or "feasible, with a measured score drop". Cancellation is evaluated the same way. Deferred and cancelled missions also report whether their nominal or completed alternatives are infeasible or only worse. Rejected landing times are counted by code.
- **Infeasible mission sets.** When no joint plan exists, a deletion filter over the mandatory missions finds one minimal conflicting set; the other missions may be cancelled without penalty in these trial solves. Dropping any member of the set restores feasibility. A set of one mission explains its own failure: no evidence-backed option (with rejection counts) or downlink capacity below its requirement even alone. A time-limited solve is never treated as proof of infeasibility.

## Shared timeline interface

The **Campaign** tab loads an example, plans it through `POST /campaigns/plan` and renders the backend `timeline`:

- **Mission lanes:** landing window band, surface-operation bar (dashed outline when deferred), communication window line, deadline marker, status and type, and the independent choice as a dashed ghost bar.
- **Station lanes:** calendar shading, link slots colored by mission (relayed slots outlined) and hatched independent-plan conflicts.
- **Below the chart:** dependency arrows, a summary of the joint versus independent status and the model size, decision cards with alternatives and score changes, the list of independent conflicts and the model limitations.

The frontend computes no scientific values; it only maps UTC times to pixels.

API: `GET /campaigns/examples`, `GET /campaigns/examples/{name}`, `POST /campaigns/plan` (422 for invalid input), and `POST /analyses/{id}/campaign-evidence`. The evidence route returns 404 for a missing analysis or communication job, 409 when a job is incomplete or terrain is not acquired, and 422 for an unknown vehicle profile.

## Verified results (2026-11-15)

From [the verification record](verification/r11-campaign-planning-2026-11-15.json):

| Case | Independent choices | Joint plan |
|---|---|---|
| `shared-dsn-conflict` (crewed lander, CLPS-class lander, rover; two assumed stations) | Conflict: station capacity, crew exclusivity and rover dependency lag; proposed score 28.872 is not achievable | Optimal, score 28.122. Crew lands at 00:00; CLPS-1 is deferred to 12:00 at rim-b because forcing any nominal option is infeasible; the rover lands at 18:00, after its 6 h lag. 426 variables |
| `infeasible-mandatory` (two mandatory missions, one antenna) | Each mission is feasible alone | No joint plan. Minimal set {crew-2, science-2}: 2.16e10 + 1.44e10 bits exceed the single station's eight pre-deadline slots |
| `relay-support` (crater site with no direct station geometry) | No downlink for the crater lander | Optimal, score 32.152. All 5.4e9 crater bits go through relay-3. Without line of sight, or with a 1 km relay range, the crater mission is cancelled |
| Real evidence: Malapert Massif, 2026-11-04 (stored Task 3 analysis, cached LOLA DEM, R4 Apollo LM profile) | — | Two candidates pass: slope 5.60°, propellant 7369 kg with a 493 kg margin above reserve. With no R2 job attached there is no station geometry, so the mission is cancelled: "even alone, lm-1 can deliver at most 0 of the 3.6e+09 required bits". With a 5° slope limit and a mandatory policy, the conflict set is {lm-1} with `terrain_slope` rejections |

The real case matches the Task 4 finding that Malapert has no Earth-center visibility on that date. Direct-to-Earth communication cannot be assumed there; it would need relay geometry evidence.

Test coverage includes:

- contract rejection cases;
- every rejection code;
- exact scores;
- every validator issue code;
- relay preconditions and chord range;
- solver behavior for capacity shifts, deferral, cancellation economics, dependencies, exclusivity, unknown capacity and the variable limit;
- the three examples, explanations, the timeline and determinism;
- result adapters on real `communication_windows`, `site_safety` and `plan_descent` outputs;
- the API routes.

Fourteen seeded random campaigns are compared with an exhaustive oracle that enumerates every selection, combined with a per-slot dynamic program over link choices. The oracle agrees on status and exact optimum score: eleven cases are optimal and three infeasible. Relay paths are present in seven of these cases but none is selected. Relay scheduling is therefore checked by the dedicated relay tests, not by the oracle.

## Reproduce

```sh
docker compose build backend
docker compose run --rm backend pytest -q tests/test_campaign_planning.py
docker compose run --rm backend pytest -q
docker compose run --rm frontend npm run build
git diff 8504f81 HEAD -- backend/tests/test_campaign_planning.py
```

The last command must produce no diff. On 2026-11-15:

- all 115 R11 tests failed against the skeleton and pass after implementation;
- the full backend suite passed: 1342 tests;
- Ruff check and format passed;
- the frontend production build passed.

No frontend tests or screenshots were run.

## Limitations

- Candidates passing the evidence checks are not a landing-safety certification.
- Example campaigns are synthetic teaching inputs, not NASA mission plans. Their stations are assumed and are neither DSN bookings nor link budgets.
- Data rates are assumed constant per mission and station, and links occupy whole slots. Rates are never computed from geometry.
- Energy is a net balance with conservative full-sun-only generation, not a battery trace.
- Relay geometry must be supplied as evidence: R11 computes no inter-site line of sight from the DEM. Relays are bent pipe only; store-and-forward is not modeled.
- The optimum is exact only for the declared candidate grid, slot size and MILP model. Explanations evaluate at most three alternatives per mission.
- The quantum path (R10 QAOA) applies only to H0-representable selection problems. R11's data-volume, deferral, cancellation and relay extensions use the classical MILP, following R10's no-go result.
