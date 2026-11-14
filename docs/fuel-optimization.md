# Fuel optimization across descent routes — R5 (scenario S7)

Start from `temel` `f497f21` (R4); developed on `feature/r5-fuel-optimization`.
Skeleton: `0c0ef15`. Frozen test baseline: `2994417`. `git diff 2994417 -- backend/tests` is empty, and the R3 and R4 frozen tests (`cee01e4`, `9ed69c8`) are also unchanged.
Implementation: `d285a15`. Interface: `95c3b3a`.

## What it answers

For a site and vehicle, R5:

- generates descent route variants and estimates the propellant of each, from descent orbit insertion to touchdown;
- rejects every route that breaks the touchdown propellant reserve or a vehicle limit, with the reason and values;
- shows the trade-off between fuel and a safety margin.

R5 also lets the R3 site recommendation include descent reachability and a fuel criterion.

## User flow

**Descent → Optimize routes.** The run uses the site, vehicle, inclination and plane-change setting from the form, plus the touchdown reserve. The reserve defaults to 145 fps. The default grid has 27 routes and takes about 13 s. The result shows:

- how many routes keep the reserve;
- a scatter of propellant used against assessment time, with:
  - one hue per orbit altitude;
  - the Pareto front as larger dots joined by a dashed line;
  - rejected routes as ✕;
  - labels for the lowest-fuel and longest-assessment routes;
  - a hover readout;
- a route table sorted by propellant, with tags (lowest fuel, longest assessment, Apollo 11 reference, Pareto) and rejection reasons.

**Recommend → Include descent reachability and fuel.** Each candidate is planned with the R4 Apollo 11 LM descent from the 60 n.mi. polar orbit. An unreachable candidate is rejected with the descent's limiting factor. Reachable candidates get a **fuel** criterion with its own weight slider. With the option off, R3 behaves exactly as before.

## Route variants

| Variable | Default values | Meaning |
|---|---|---|
| Initial orbit altitude | 40 / 60 / 80 n.mi. | Changes the DOI delta-v and the PDI speed |
| Approach duration | 60 / 100 / 140 s (Apollo 11: 100 s) | Time from high gate to low gate |
| Terminal-descent duration | 60 / 108 / 150 s (Apollo 11: 108 s) | Time from low gate to touchdown; longer is closer to hovering |

The gate states stay at the Apollo 11 values (NASA TN D-6846, Table I); only phase durations change. Each orbit gets its own PDI-range search, and each route is integrated at the R4 0.1 s step. The API accepts up to 4 values per variable: orbits 30–500 km, durations 20–400 s. The interface uses the defaults.

## Propellant, reserve and safety measures

- **Budget from DOI.** The Apollo profile mass is given at PDI, after DOI from the 60 n.mi. reference orbit. The DOI mass is backed out with the rocket equation, m_DOI = m_PDI·exp(Δv_DOI/vₑ). That gives 15 254 kg at the reference site, against 33 669.6 lb (15 272 kg) in the Apollo 11 Mission Report Table A-I. Every route shares the budget usable propellant + (m_DOI − m_PDI). Route propellant is m_DOI − m_touchdown.
- **Reserve.** The default is **145 fps** (44.2 m/s), the sum of two NASA TN D-6846 Table II contingency allowances: landing-site **redesignation (60 fps)** and **manual hover (85 fps)**. The reserve must remain at touchdown as propellant m_land·(1 − exp(−Δv_res/vₑ)).
- **Hover margin.** The hover time bought by the propellant above the reserve, with thrust equal to weight: t = vₑ/g·ln(m₀/(m₀ − m_p)), where g is surface gravity at the site radius.
- **Assessment time.** Approach plus terminal-descent duration: the time to see and assess the landing area. It is a planning proxy for safety, not a probability of success.
- **Pareto front.** The feasible routes that no other route beats on both less propellant and more assessment time.

## Rejection reasons

| Reason | When |
|---|---|
| `orbit` | The orbit is not above the PDI altitude (50 000 ft over the site) |
| `plane_change` | The orbit never overflies the site and plane changes are not allowed |
| `propellant` | Propellant runs out or use exceeds the budget from DOI |
| `reserve` | Propellant left at touchdown is below the reserve requirement |
| `touchdown_tolerance` | Touchdown is outside the R4 tolerances (5 m, 1.0 m/s vertical, 0.5 m/s horizontal) |

Every rejection lists all its reasons with values and limits. No route that breaks the reserve is ever listed as feasible.

## R3 extension (opt-in)

With `include_descent=true`, each candidate is planned with `plan_descent`, using the cached terrain height and the default R4 inputs.

- **Hard constraint `descent`.** An infeasible plan rejects the candidate. The explanation quotes R4's limiting factors.
- **Fuel criterion.** Score = propellant margin / propellant available at PDI, clamped to 0–1. It uses its own weight (`fuel`, default 0, so it only counts when set).
- **Ranking.** The rank, rescore and contribution bars cover five criteria when fuel is present, and "fuel" leaves the not-modelled list. Without the option, the four R3 criteria and the R3 output are unchanged; the frozen R3 tests verify this.

## API

`POST /descents/optimize` takes the following body:

- `site_id`, `latitude_deg`, `longitude_deg_east`;
- `profile_id` or `custom_profile`;
- `inclination_deg`, `allow_plane_change`;
- `orbit_altitudes_km`, `approach_durations_s`, `terminal_durations_s`;
- `reserve_delta_v_mps`, `tolerances`.

It returns `routes[]` and `feasible_ids`, `rejected_ids`, `pareto_ids`, `lowest_fuel_id`, `longest_assessment_id`, `reference_route_id`, plus `mass_at_doi_kg`, `propellant_at_doi_kg`, `reserve` with its source, and `assumptions`. Errors follow `/descents`: `409` when no terrain is cached, `422` for invalid input or a site outside coverage.

Recommendations add `include_descent`, `descent_profile_id` and `weights.fuel`.

## Verification (2026-11-14, local Docker)

**Checks:** backend 456 tests, ingestion 112 and frontend 32. Ruff and the production build pass.

**Malapert R23** (site 4785 m above the sphere), default grid:

- 27 routes in 13 s, all feasible at 145 fps. The reserve needs about 108–118 kg.
- Lowest fuel: 40 n.mi., 60 s + 60 s: 7080 kg, with a 1067 kg reserve margin and 263 s of hover.
- Longest assessment: 40 n.mi., 140 s + 150 s: 7783 kg, with 97 s of hover.
- The Apollo 11 reference route (60 n.mi., 100 s + 108 s) uses 7472 kg, with 172 s of hover.
- The Pareto front has 8 routes, all from the 40 n.mi. orbit. A higher orbit only adds DOI propellant.

**700 fps reserve:** 24 of 27 routes kept. The three 140 s + 150 s routes are rejected with the reason `reserve`.

**Recommendation with descent** (Malapert vs Mons Mouton feature center, 12° slope limit, fuel weight 2):

- Both descents are reachable, each with about 793 kg of propellant margin (fuel score 0.097).
- The weights normalize over five criteria, and fuel contributes to both totals. Malapert ranks first.

## Limitations

- **Variants.** Routes vary three parameters on a fixed grid. This is a parameter sweep with a Pareto filter, not a continuous trajectory optimizer.
- **Assessment time.** It is a planning proxy. Visibility, sun angle, landing-radar performance and crew workload are not modeled.
- **Inherited from R4.** All R4 limitations apply: planar point-mass model, no terrain clearance, no mascons or lunar rotation, constant specific impulse, and no Apollo hover or redesignation in the trajectory itself. The reserve stands in for those.
- **Dispersions.** Uncertainty and Monte Carlo dispersions follow in R6.
