# R10 — Classical-versus-hybrid benchmark

R10 completes the hybrid quantum-classical optimization release on top of H0–H2 and H7. It adds a classical MILP baseline, scaling measurements, bounded subproblem search, raw-versus-repaired candidate evidence, a dev/held-out benchmark and pre-registered go/no-go rules. **Result: both quantum applications are no-go and the classical HiGHS MILP solver is retained.** Every quantum number below comes from a local statevector simulation, some of it passed through an illustrative noise model. R10 submits no hardware or provider job and claims no quantum advantage.

## Delivery and test-first evidence

- **Branch:** `feature/r10-hybrid-optimization`, from `main` at `a5171d4`.
- **Push and merge:** only this branch is pushed; merging is manual.
- **Benchmark and scaling module:** skeleton commits `428a0da`, `a36a448`, `c7ea0d1` and `0cce1dd`; frozen tests `6c96376` (177 tests); implementation `51dd705`.
- **Compact summary output:** skeleton `43eff10`, frozen tests `411d549` (9 tests), implementation `f0deb6a`.
- **Frozen tests:** no H0–H8 frozen test, source module or fixture was changed.

| File | Content |
|---|---|
| `backend/app/hybrid_scaling.py` | Seeded instance families, enumeration-free pairwise QUBO model, exact circuit-resource accounting, HiGHS MILP baseline and scaling sweep |
| `backend/app/hybrid_benchmark.py` | Exact Pauli-Z resources, H2-identical QAOA tuning, illustrative noise model, classical repair/improvement, direct sampler comparison, bounded subproblem search, benchmark runner and go/no-go |
| `backend/app/run_r10_benchmark.py` | Pre-registered published configuration and CLI |
| `docs/verification/r10-hybrid-benchmark-2026-11-15.json` | Compact published summary; it cites the SHA-256 of the full report |

SciPy 1.16.3 was already listed in [technologies](technologies.md) and is now locked in the backend for `scipy.optimize.milp` (HiGHS). No service, endpoint or frontend code is added.

## Reproduce

```sh
docker compose build backend
docker compose run --rm backend pytest -q tests/test_hybrid_scaling.py tests/test_hybrid_benchmark.py tests/test_r10_summary.py
docker compose run --rm --no-deps -v ./docs:/workspace/docs backend \
  python -m app.run_r10_benchmark \
  --output /workspace/docs/verification/r10-hybrid-benchmark-full.json \
  --summary /workspace/docs/verification/r10-hybrid-benchmark-2026-11-15.json
git diff 6c96376 HEAD -- backend/tests/test_hybrid_scaling.py backend/tests/test_hybrid_benchmark.py
git diff 411d549 HEAD -- backend/tests/test_r10_summary.py
```

The run takes about 30 s on one x86-64 container. Everything except wall-clock fields is deterministic for the frozen configuration: plans, scores, gaps, counts, raw rates and every non-runtime criterion. This was checked by running the benchmark twice. The full 2 MB report is not committed (it is about 50× larger than any other verification file). The 2026-11-15 run had full-report SHA-256 `b17d4418…1eb9`. The configuration fingerprint is `9f88b297…3230`; both full values are in the summary. Rerunning changes timings, and therefore the full-report hash, but not the non-timing content.

## Components

### Classical references

- **Exact reference:** H1 `solve_exact` enumerates up to `exact_max_assignments` (4096 in the published run) and certifies optima exactly.
- **MILP baseline:** `solve_milp` formulates the original V1 rules directly as a 0/1 linear program, independently of the validator and of any QUBO penalty. Its rows are:
  - one option per mission;
  - deadline and availability bounds;
  - at-most-one sets;
  - one capacity row per elementary demand interval;
  - cumulative joule budgets.

  Non-binding rows are omitted. HiGHS runs with `mip_rel_gap=0` and an explicit time limit. Every incumbent goes back through H1 `validate_plan`; a disagreement raises an error rather than being accepted.
- **Cross-checks:** the frozen tests compare MILP against enumeration on generated instances and on capacity-2 (higher-order), fractional-capacity, budget, deadline, availability and incompatibility cases. The benchmark also checks MILP against enumeration on every instance small enough to enumerate.
- **Float tolerance:** HiGHS uses floating-point feasibility tolerances. An answer that the exact validator rejects fails loudly; it is never silently accepted.

### Instance families

`generate_instance` builds seeded synthetic scenarios with `random.Random`, whose output is stable across NumPy versions. Each option occupies one 1-hour UTC slot on one capacity-1 antenna. The family parameters are:

- **Size:** missions and options per mission.
- **Contention:** the number of slots and antennas.
- **Constraint density:** the probability of an explicit cross-mission at-most-one pair.
- **Deadline tightness:** how far mission deadlines move earlier.

Objectives are `science_value` (1–20, maximized, scale 10) and `waiting_s` (minimized, scale 36000 s). Every mission keeps at least one option that ends by its deadline, but joint feasibility is not guaranteed. These are planning benchmarks, not NASA missions, DSN bookings or validated landing sites.

### Pairwise model and resource growth

`build_pairwise_model` derives unary and pairwise hard rules directly from the input, without enumerating assignments. It accepts a resource only when the pairwise representation is provably exact:

- **Non-binding:** the sum over missions of the largest per-option demand fits within capacity.
- **Exactly pairwise:** every positive demand exceeds half the capacity.

Anything else, such as capacity 2 with unit demands or a binding budget of small demands, raises an error naming the need for slack variables or higher-order terms. Penalty and normalization follow H2 (`A = 1 + Σ|s_i|`). The tests check the model against the audited H2 QUBO on all 4096 teaching assignments and against the original validator on every assignment of the generated instances.

`resource_profile` reports logical and circuit size without building a statevector:

- one-hot variables and quadratic terms;
- compact binary-register qubits;
- the exact rational Walsh–Hadamard Z expansion, accumulated per mission and per conflicting mission pair;
- maximum locality and a locality histogram;
- parity-chain logical CX/RZ/RX per layer;
- statevector size.

It reproduces H2's documented 30 CX, 15 RZ and 6 RX per layer and locality 4. Option counts that are not a power of two are reported as unsupported for the compact encoding; they are never dropped. Gate counts are logical all-to-all estimates, not transpiled device counts.

### Repair and raw evidence

`repair_and_improve` applies the same classical procedure to every candidate source:

1. **Repair:** steepest descent on an exact violation measure: capacity/budget excess, plus 1 per deadline or availability issue, plus (selected − 1) per at-most-one set.
2. **Improve:** steepest ascent over feasible single-mission changes.

Ties use a fixed key, and the procedure uses the H1 constraint evaluator. Each `RepairOutcome` keeps the following separately from the repaired plan:

- the raw assignment, raw status, raw violation measure and raw score;
- every move;
- changed missions and the score change;
- validator calls (the repair cost);
- whether repair failed or hit its move budget.

A repaired plan is labelled `classical` with solver `<source>+r10-repair-v1`. Only an unchanged raw plan keeps its quantum execution label.

### Direct sampler comparison

`compare_direct` runs four samplers on problems of at most 8 qubits:

| Sampler | Execution mode | What it does |
|---|---|---|
| QAOA | noiseless simulator | Same seeded loop as H2. On the teaching example the tests confirm that probabilities and sampled counts are identical to H2 `run_qaoa`. |
| QAOA | noise model | Samples the same tuned state through the noise model below. |
| Uniform random | classical | Draws the same number of shots as QAOA. |
| Annealing | classical | One seeded Metropolis chain per candidate. |

The noise model is an **illustrative assumption**, not a device calibration. It mixes the ideal distribution with the uniform distribution using a global depolarizing survival probability. That probability is computed from the H7 rates (0.1% per single-qubit gate, 1% per CX) and the logical gate counts. Independent 2% readout flips follow. Tuning is always noiseless.

From each sampler, up to eight distinct raw assignments (most frequent first) pass through the same repair. The record keeps the per-shot raw feasible/optimal rates, the per-candidate outcomes, the best repaired plan and its exact optimum gap.

### Bounded subproblem search

`solve_subproblem_search` is a classical large-neighbourhood search:

1. **Initial plan:** greedy construction plus repair.
2. **Neighbourhood:** each step frees at most `subproblem_missions` (3) missions and keeps all others fixed. It takes the missions involved in current violations, or else a seeded mission plus the missions it conflicts with most.
3. **Subproblem solve:** the at-most-8-qubit subproblem is solved by QAOA (noiseless or noise model), by exact enumeration or by uniform random sampling.
4. **Acceptance:** every sampled or enumerated sub-assignment is validated in the full plan by H1. Only a strictly better key (lower violation measure, then higher exact score) is accepted.

Steps record the selected missions, qubits, raw feasible fraction, proposal and acceptance. This lets a large plan use a small quantum step while candidate generation, acceptance and final validation stay classical. As in H2, simulating the subproblem requires its full diagonal, which the classical code computes anyway.

## Pre-registered protocol

The configuration (`published_config`) and decision rules were committed before the held-out run:

- **Splits:** dev instance seeds are 101–103 and held-out seeds are 201–206. The runner rejects any dev/held-out pair with identical generation parameters.
- **QAOA settings:** taken from H2/H7 (two layers, 160 evaluations, 512 shots, seeds 17/23/42), not tuned on held-out data.
- **Other budgets:**
  - 8 repaired candidates per sampler, at most 64 moves;
  - 64 annealing proposals per chain;
  - 8 subproblem steps with 3 missions each;
  - exact enumeration up to 4096 assignments;
  - MILP time limit 30 s.

A held-out application is **GO** only if all five criteria pass on held-out instances with a known optimum:

| Criterion | Rule |
|---|---|
| `validated_feasibility` | Every hybrid run returns a plan accepted by the independent validator (fraction ≥ 1.0) |
| `objective_quality` | Mean hybrid optimum gap − mean MILP gap ≤ 0 |
| `raw_feasibility_vs_random` | Mean raw (pre-repair) feasible rate above the matched-budget random baseline |
| `repaired_quality_vs_random` | Mean repaired gap no worse than random under the same repair |
| `end_to_end_runtime` | Mean hybrid end-to-end time ≤ 1.0 × MILP |

End-to-end time covers mapping/preparation, tuning, sampling, validation and repair. Local execution has no provider queue, so `queue_s` is recorded as `null` rather than zero. A real hardware queue was measured separately in H7 (118.8 s for one job). On NO-GO the retained solver is `r10-highs-milp-v1`.

## Published results (2026-11-15)

### Instances

| Split | Seed | Missions | Compact qubits | Conflict pairs / density | Optimum source | Optimum |
|---|---:|---:|---:|---:|---|---:|
| dev | 101 | 3 | 6 | 9 / 0.19 | exact | 47/10 |
| dev | 102 | 3 | 6 | 9 / 0.19 | exact | 23/5 |
| dev | 103 | 6 | 12 | 30 / 0.13 | exact | 69/10 |
| held-out | 201 | 3 | 6 | 20 / 0.42 | exact | 23/5 |
| held-out | 202 | 3 | 6 | 21 / 0.44 | exact | 41/10 |
| held-out | 203 | 3 | 6 | 12 / 0.25 | exact | 49/10 |
| held-out | 204 | 4 | 8 | 19 / 0.20 | exact | 39/10 |
| held-out | 205 | 8 | 16 | 50 / 0.11 | MILP | 87/10 |
| held-out | 206 | 12 | 24 | 109 / 0.10 | MILP | 21/2 |

MILP matched exact enumeration on all seven enumerable instances. It solved the 8- and 12-mission instances in 0.02–0.08 s.

### Held-out aggregates

Runtimes are one container's wall-clock measurements; the other columns are deterministic.

| Method | Mode | Runs | Feasible runs | Optimum hits | Mean gap | Mean raw feasible rate | Mean time (s) |
|---|---|---:|---:|---:|---:|---:|---:|
| MILP | classical | 6 | 6 | 6 | 0 | — | 0.015 |
| exact | classical | 4 | 4 | 4 | 0 | — | 0.024 |
| QAOA direct | noiseless simulator | 12 | 12 | 12 | 0 | 0.492 | 0.105 |
| QAOA direct | noise model | 12 | 12 | 12 | 0 | 0.210 | 0.118 |
| random direct | classical | 12 | 12 | 12 | 0 | 0.147 | 0.107 |
| annealing direct | classical | 12 | 12 | 12 | 0 | 1.000 | 0.049 |
| subproblem search with QAOA | noiseless simulator | 18 | 15 | 12 | 0.127 | 0.436 | 0.225 |
| subproblem search with QAOA | noise model | 18 | 15 | 12 | 0.127 | 0.182 | 0.279 |
| subproblem search, exact | classical | 18 | 15 | 12 | 0.127 | 0.117 | 0.193 |
| subproblem search, random | classical | 18 | 15 | 12 | 0.127 | 0.118 | 0.192 |

### Decisions

| Application | Feasibility | Quality vs MILP | Raw vs random | Repaired vs random | Runtime vs MILP | Verdict |
|---|---|---|---|---|---|---|
| Direct QAOA selection (≤ 8 qubits) | pass (1.0) | pass (0) | pass (+0.345) | pass (0) | **fail (6.9×)** | **NO-GO**, MILP retained |
| Subproblem search with QAOA (3-mission subproblems) | **fail (0.83)** | **fail (+0.127)** | pass (+0.318) | pass (0) | **fail (14.6×)** | **NO-GO**, MILP retained |

In the first run the runtime ratios were 4.6× and 9.8×; the verdicts were the same.

### Interpretation

- **Raw samples:** noiseless QAOA produces raw samples that are feasible far more often than uniform sampling (0.49 versus 0.15 on held-out). The illustrative noise model removes most of this gain (0.21). Classical annealing endpoints were always feasible on these small cases.
- **Repair hides sampler differences:** after the shared repair/improvement step, every direct sampler reached the exact optimum on every held-out run. On these instance sizes, repair rather than the sampler determines the final quality.
- **Subproblem solver does not matter here:** the four neighbourhood solvers gave identical final plans for every seed. With ≤ 6-qubit subproblems and 512 samples, all of them found the subproblem optimum. The limiting factor is the classical outer search: 8 steps of 3-mission neighbourhoods.
  - Seed 205 (8 missions): gaps of 2/5–9/10.
  - Seed 206 (12 missions): no feasible plan within the budget, while MILP found the optimum in 0.08 s.
- **Not tuned after the fact:** these held-out results were not used to change settings. A larger outer-search budget or better neighbourhood selection would be a new pre-registered experiment.
- **Runtime:** MILP certified every optimum faster than any hybrid path, even with zero queue time. Simulated QAOA tuning is cheap at 6 qubits; repair and validation dominate the hybrid time. Hardware would add measurement, queueing and cost (see H7).

The classical solver is therefore retained for both applications. Better-than-random raw feasibility is evidence about the sampler only; it is not a quantum advantage and not an end-to-end benefit.

### Scaling measurements

Each row is a generated instance; MILP time is wall-clock.

| Family | Missions × options | Antennas | Incompat. p | One-hot vars | Quadratic terms | Compact qubits | Z terms | Max locality | CX / layer | MILP rows | MILP status | MILP time (s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| size | 2 × 4 | 4 | 0.1 | 8 | 17 | 4 | 15 | 4 | 34 | 6 | optimal | 0.006 |
| size | 4 × 4 | 4 | 0.1 | 16 | 38 | 8 | 43 | 4 | 112 | 21 | optimal | 0.011 |
| size | 8 × 4 | 4 | 0.1 | 32 | 86 | 16 | 169 | 4 | 490 | 47 | optimal | 0.019 |
| size | 16 × 4 | 4 | 0.1 | 64 | 268 | 32 | 717 | 4 | 2256 | 224 | optimal | 0.046 |
| size | 32 × 4 | 4 | 0.1 | 128 | 967 | 64 | 2965 | 4 | 9622 | 897 | infeasible | 0.047 |
| density | 8 × 4 | 4 | 0.0 | 32 | 55 | 16 | 87 | 4 | 226 | 15 | optimal | 0.016 |
| density | 8 × 4 | 4 | 0.05 | 32 | 69 | 16 | 131 | 4 | 372 | 29 | optimal | 0.017 |
| density | 8 × 4 | 4 | 0.2 | 32 | 115 | 16 | 179 | 4 | 528 | 101 | optimal | 0.037 |
| density | 8 × 4 | 4 | 0.4 | 32 | 180 | 16 | 225 | 4 | 690 | 193 | infeasible | 0.011 |
| contention | 8 × 4 | 1 | 0.1 | 32 | 105 | 16 | 174 | 4 | 504 | 58 | optimal | 0.019 |
| contention | 8 × 4 | 8 | 0.1 | 32 | 94 | 16 | 201 | 4 | 606 | 56 | optimal | 0.021 |
| options | 8 × 8 | 4 | 0.1 | 64 | 384 | 24 | 1294 | 6 | 6100 | 180 | optimal | 0.042 |
| options | 8 × 3 | 4 | 0.1 | 24 | 48 | unsupported | — | — | — | 47 | optimal | 0.015 |

- **One-hot variables** grow linearly in missions × options. Compact qubits grow as missions × log2(options).
- **Circuit cost** grows faster than qubit count: CX per layer rises by 3.3–4.6× per doubling of missions and about 10× when options grow from 4 to 8 at 8 missions. Locality stays bounded at twice the register width, because every rule involves at most two missions.
- **Constraint density** raises the term and CX counts.
- **MILP** stays below 0.05 s up to 32 missions.

The statevector simulator is capped at 8 qubits, so every instance above that runs only through bounded subproblems.

## Limits

- Synthetic instances only. Results are not a statement about real lunar mission planning, DSN capacity or landing safety.
- No hardware or provider job; the H7 single hardware trial remains the only device evidence. The noise model is illustrative, not calibrated.
- Logical, untranspiled gate estimates. Higher-order resource rules (slack-variable encodings) are reported as unsupported rather than approximated.
- Small held-out suite (6 instances, 3 seeds). Timing is descriptive wall-clock on one machine.
- Annealing, random and QAOA budgets are matched by sample or candidate count, not by computational cost.
