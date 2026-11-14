# R13 — Additional quantum application benchmarks

R13 benchmarks five further discrete planning applications classically against hybrid methods, using the R10 protocol. This document first records the **pre-registered definitions**: decision variables, classical baseline, quantum subproblem, validation rules, measured benefit and budgets. That section was committed with the skeleton, before any implementation, and is not changed afterwards. Results are appended in a separate section.

Every quantum number comes from the local H2/R10 statevector simulator, sometimes passed through the R10 illustrative noise model. R13 submits no hardware or provider job, does not tune on held-out instances, and claims no quantum advantage.

## Pre-registration (committed before implementation)

### Common method

Each application turns its input into an `ApplicationInstance`:

- **Binary decision variables:** labelled bits `x_i ∈ {0, 1}`.
- **Independent validator:** `evaluate(bits)` works from the original input, never from the QUBO. It returns:
  - feasibility;
  - an exact rational violation measure that is 0 exactly when the assignment is feasible;
  - an exact rational objective (maximized);
  - coded issues and the variables involved in them;
  - the application's own metrics.
- **MILP:** a HiGHS formulation of the original rules (`scipy.optimize.milp`, `mip_rel_gap = 0`). Every incumbent is re-checked by the validator, and a disagreement raises an error.
- **QUBO:** energy `E(x) = c + Σ l_i x_i + Σ q_ij x_i x_j`, minimized. The penalty weight is `A = 1 + 2·Σ|objective coefficients|`. This is twice the H2 sum, so that one violated exact rule always costs more than the whole objective range.
  - Equality and pairwise rules use exact penalties. A QUBO is declared `exact` only when every rule and the whole objective are represented exactly.
  - Inequalities with general weights (energy, duration, capacity above 1) use unbalanced penalization, `A·(−λ1·h + λ2·h²)` with `h = 1 − Σ(w_i/C)·x_i`, λ1 = 0.96 and λ2 = 0.0371 (Montañez-Barrera et al., *Unbalanced penalization*, 2023). This is a heuristic without slack qubits, and its QUBO is declared not exact.

The shared engine (`backend/app/quantum_applications.py`) provides the following:

| Component | Rule |
|---|---|
| Exact reference | Enumerate all 2^n assignments through the validator when n ≤ 16 |
| MILP reference | HiGHS optimum, revalidated; the optimum reference when n > 16 |
| QUBO audit | For n ≤ 16: whether feasible energies equal −objective, whether the ground state is feasible and optimal, and how many infeasible assignments lie below the optimum |
| Repair | Steepest descent on the key (violation, −objective) over single bit flips, then pairwise swaps when no flip improves; at most 64 moves |
| Hybrid method | Classical large-neighbourhood search; each neighbourhood frees at most 8 variables and is sampled from the QUBO restricted to those variables |
| Subproblem samplers | QAOA (noiseless simulator), QAOA (R10 illustrative noise model), uniform random (same shot count) and exact sub-enumeration, with the same outer search |

The search runs as follows:

1. **Start:** the all-zero assignment, repaired.
2. **Neighbourhood:** at most 8 steps. Each frees the variables involved in current violations; otherwise it frees a seeded variable and its strongest QUBO neighbours, up to 8.
3. **Samples:** the eight most frequent distinct sampled sub-assignments are validated in the full assignment. A proposal is accepted only if it improves the key strictly.
4. **Finish:** a final repair. An instance with n ≤ 8 is therefore sampled directly in one neighbourhood.

### Budgets

The budgets are taken from R10 and are not changed for R13:

- QAOA: depth 2, 160 seeded objective evaluations, 512 shots, seeds 17/23/42;
- 8 search steps of at most 8 free variables, with 8 candidates per step;
- at most 64 repair moves;
- exact enumeration up to 16 variables;
- MILP time limit 30 s.

Instance seeds are split per application into dev (101, 102) and held-out (201, 202, 203, 204). Held-out instances are not used to change any setting.

### Go/no-go rule (per application, held-out only)

The five R10 criteria apply unchanged. The hybrid method is the search with noiseless-simulator QAOA; the classical method is MILP; the matched baseline is the same search with uniform random sampling.

| Criterion | Pass when |
|---|---|
| `validated_feasibility` | Every hybrid run returns a validator-accepted assignment |
| `objective_quality` | Mean hybrid optimum gap − mean MILP gap ≤ 0 |
| `raw_feasibility_vs_random` | Mean raw (pre-acceptance) feasible sample rate is strictly above random |
| `repaired_quality_vs_random` | Mean final gap is no worse than random |
| `end_to_end_runtime` | Mean hybrid end-to-end time ≤ 1.0 × MILP (preparation, tuning, sampling, validation and repair; no provider queue) |

The gap is `optimum − objective`, in the application's objective units.

**Quantum execution gate.** `solve_application` runs QAOA only for an application whose held-out decision is GO. A missing decision or a NO-GO keeps the HiGHS MILP. A pass on the raw-feasibility criterion is evidence about the sampler alone; it does not establish an end-to-end contribution.

### Applications

All inputs are **synthetic planning benchmarks**. Where marked, they are generated through the existing project models. They are not NASA plans, DSN bookings, validated relay sites or certified landing or traverse plans.

#### 1. Communication-slot allocation (`comm_slots`)

- **Input:**
  - downlink requests, each with a mission, data volume (bit), ready and deadline slots;
  - stations, each with availability slots and a link capacity;
  - per-mission/station visibility slots (R2-shaped geometry evidence, kept separate from availability) and link rates (bit/s).
- **Decision variables:** `x[r,k,t] = 1` when request `r` uses station `k` in slot `t`. A variable exists only when the mission sees the station, the station is available and `ready ≤ t ≤ deadline`.
- **Validator rules:**
  - at most one pass per request;
  - at most `capacity_k` requests per station and slot;
  - at most one station per mission per slot (a single transmitter).
- **Objective:** delivered data `Σ min(volume, rate·slot_s)/bit_scale`, minus `latency_weight · (t − ready)`.
- **Metrics:** shared-station conflicts (reported for raw samples too), unmet demand (bit), delivered data (bit), mean and maximum latency (s), served requests.
- **Classical baseline:** MILP with assignment, capacity and transmitter rows.
- **Quantum subproblem:** the restricted QUBO. Rules for capacity 1 are exact pairwise penalties; capacity above 1 uses unbalanced penalties, so the QUBO is declared not exact.

#### 2. Science-activity selection and ordering (`science_activities`)

- **Input:**
  - activities, each with a science value, duration (slots), energy (J), earliest slot and deadline slot;
  - precedence pairs;
  - one instrument, so activities cannot overlap;
  - an ideal battery: initial energy, reserve and per-slot solar input (J). Storage is unlimited and lossless, an explicit H3-style assumption.
- **Decision variables:** `x[a,t] = 1` when activity `a` starts in slot `t`. A variable exists only when `t ≥ earliest` and `t + duration ≤ deadline`, so selected activities always finish on time.
- **Validator rules:**
  - at most one start per activity;
  - no overlap;
  - a successor requires its predecessor to be selected and finished first;
  - cumulative energy at the end of every slot is at least the reserve.
- **Objective:** completed science value − `lateness_weight · start slot`.
- **Metrics:** completed science value, completed activities, missed deadlines (activities not completed by their deadline), minimum battery margin (J), makespan (s).
- **Classical baseline:** MILP with cumulative energy rows.
- **Quantum subproblem:** the restricted QUBO. The energy rows use unbalanced penalties, so the QUBO is not exact.

#### 3. Relay-location selection (`relay_selection`)

- **Input:**
  - sites with local east/north/up positions (m) in a site-centred tangent frame;
  - Task 2-format horizon masks;
  - demand slots without direct-to-Earth visibility;
  - candidate relays with position, mast height, relay equipment, capacity (links), maximum range (m) and Earth-visible slots.
- **Geometry and capacity validation (before selection):**
  - A site–relay link is credited only with relay equipment and a range within the relay's limit.
  - The relay's elevation seen from the site must clear the site horizon by the margin. Elevation includes the drop of a spherical surface, `d²/(2R)`.
  - The relay must see Earth in that slot.
  - A candidate whose simultaneous credited demand exceeds its capacity is rejected with a coded reason. Only validated candidates become variables.
- **Decision variables:** `x_i = 1` selects validated relay `i`. Exactly `k` relays are selected.
- **Validator metrics:**
  - coverage (fraction of demand points covered);
  - resilience (minimum coverage after removing any one selected relay);
  - double-coverage fraction;
  - the longest uncovered run per site (s).
- **Objective:** covered points + `resilience_weight` · resilient points.
- **Classical baseline:** MILP with coverage indicators and one resilience row per relay (big-M on selection).
- **Quantum subproblem:** the QUBO uses second-order inclusion–exclusion coverage and an exact-k penalty. It is declared not exact, because resilience is a minimum.

#### 4. Rover target and route selection after R8 (`rover_routes`)

- **Input:** routes generated by R8 `plan_routes` on a seeded synthetic DEM at two mobility slope limits (12° and 20°). Each target therefore has up to two route variants, and sky states are evaluated at the waypoints.
- **Candidate filtering:** unreachable routes keep their R8 reason. Routes whose sampled communication gap exceeds the limit are rejected with a code.
- **Decision variables:** `x[target, variant]`.
- **Validator rules:**
  - at most one variant per target;
  - total out-and-back traverse time within the time budget;
  - total energy within the battery budget.
- **Energy and communication model:** energy is `2 · (drive power · duration + mass · g · climb / efficiency)`, a stated assumption. The sampled communication gap is `duration · no-Earth waypoints / waypoints`.
- **Objective:** science value − `energy_weight · energy/energy_scale` − `gap_weight · gap/gap_scale`.
- **Metrics:** science return, energy use (J), communication gap (total and maximum, s), traverse time (s), targets visited.
- **Classical baseline:** MILP with knapsack rows.
- **Quantum subproblem:** the restricted QUBO. The budget rows use unbalanced penalties, so the QUBO is not exact.

#### 5. Robust plan selection after R6 (`robust_plans`)

- **Input:** two landers, each with four candidate plans (two orbit altitudes × two approach azimuths).
  - Every plan is run through R6 `run_monte_carlo` with the same seed and run count.
  - Scenario `s` is therefore the same seeded error draw for every plan and both landers. This is an explicit common-cause assumption.
  - Each lander has a seeded synthetic slope map.
  - Per scenario, the inputs are R6 success and miss distance `hypot(down, cross)` (m).
- **Decision variables:** `x[lander, plan]`, one-hot per lander.
- **Validator rules:**
  - exactly one plan per lander;
  - the worst-case campaign cost `max_s (miss_A + miss_B)` must not exceed the cost limit (m).
- **Objective:** scenarios in which both landers succeed − `cost_weight · worst-case cost / cost_scale`.
- **Metrics:** campaign success rate with a Wilson 95% interval, per-lander success rates, worst-case and mean campaign cost (m).
- **Classical baseline:** MILP over plan pairs (pair indicators).
- **Quantum subproblem:** the QUBO is exact, because any function of a pair of one-hot choices is pairwise.

## Delivery

- **Branch:** `feature/r13-quantum-application-benchmarks`, from `main` at `456f4ef`. Only this branch is pushed; merging is manual.
