# H8 — Side-by-side solver arena

H8 adds a bilingual mission-control panel comparing the existing exact classical solver with the existing locally simulated QAOA optimizer. Both consume the same immutable `Scenario` and publish its fingerprint. No new solver, quantum-provider client, physics model or service dependency is introduced.

Start point: `main` at `cb17bdda4c8c16d2b7b7e31e708b8d5863ff3fd9`. Delivery: `codex/h8-solver-arena` only; no merge into or push to `main` or `tahakrcc`. Test-first history: skeleton `8bfd5be`, frozen H8 tests `4071a81`.

## User flow

Open Mission Control → **Solver arena**, or `http://localhost:5174/#/solver-arena`. The panel supports Turkish and English, a wide native-dialog view, keyboard-accessible controls, reduced-motion styling, explicit play/pause, an adjustable playback cursor, a completed-run scoreboard, raw outcome tables and JSON download.

Choose one of five bundled synthetic cases:

- **Recovery:** the exact H7 two-qubit landing/follow-up campaign, with four possible assignments.
- **Shared conflict:** three missions, four choices each, shared-resource conflicts, six qubits.
- **Independent:** the same small scale without the shared conflict, six qubits.
- **Infeasible:** classical proof that the declared instance has no feasible assignment; QAOA is not run.
- **Missing capacity:** incomplete input, never treated as zero capacity or a feasible solution; QAOA is not run.

Settings are strict integers: 1–3 QAOA layers, 1–256 tuning evaluations, 1–8192 shots and a seed in `[0, 2^32-1]`. Defaults are two layers, 80 evaluations, 512 shots and seed 17. Controls changed after a run are explicitly marked as unapplied; old results retain their actual original settings. Starting a new run clears previous results and archived evidence. Hidden panels stop playback and abort waiting for a response; bounded server computation is not forcibly cancelled.

No frontend scientific calculation is performed. The frontend formats supplied results, positions probability bars and aligns two recorded progress sequences. Selection, feasibility, objective scores and optimum gaps are computed and validated in Python.

## Authentic observations and timing

`solve_exact(..., on_assignment=...)` now offers an optional detached observation after each actual validated assignment. It reports examined choices, feasible count, current incumbent and elapsed time. `run_qaoa(..., on_iteration=...)` optionally records each candidate's angles, ideal distribution and cost, together with the best distribution so far. Array-valued observations are immutable tuples; changing the observation dictionary cannot change solver state. Observer errors propagate. The original behavior and seeded results are unchanged when observers are absent.

These are actual completed computation records, not fabricated animation frames. Playback maps the two sequence lengths to shared progress. It does **not** claim concurrent solver execution, equal step duration or a live speed race. The user can switch between the candidate and incumbent probability distributions. The raw-count column always describes final sampling; the probability column follows the selected recorded tuning step.

The scoreboard distinguishes:

- exact classical optimum and independently validated best sampled quantum plan;
- feasible/optimal shot rates for QAOA, versus enumeration counts for the classical solver (not comparable sampling rates);
- exact-reference objective gap, with `null` when no feasible sampled plan is available;
- each solver's measured duration and the shared preparation time.

QAOA timing includes mapping, exhaustive mapping audits, classical parameter training, sampling, observation capture and final plan validation. Classical timing includes enumeration, constraint checks and observation capture. Combined compute time is measured around the comparison; HTTP serialization, browser transfer and animation are not benchmark execution time. These synchronous runs are sequential and the timing is descriptive, not a controlled hardware benchmark.

This small problem is already solved exactly by the classical reference. Better-than-uniform QAOA probabilities do not establish end-to-end quantum advantage.

## Preserved hardware provenance

`shared/examples/arena/recovery-h7.json` is the **exact small synthetic input snapshot** from the H7 submitted experiment. Its fingerprint is `176fe28ceab9f98edef3ddc8d64175e0574d3459de96242a3705275cfbdf6bf3`. It is intentionally frozen: recomputing terminal physics on a different platform can change last-bit floating-point values and therefore the fingerprint. H8 does not silently declare a recomputed input identical to archived hardware evidence.

`shared/examples/arena/h7-rigetti.json` contains the small sanitized measured-result fixture from [H7](openquantum-hardware.md): job `4c0adc79-6bd3-4578-9147-bbf8ab7b2209`, 256 shots, raw counts `00:18, 01:29, 10:9, 11:200`, one historical Spark credit. `recorded_hardware()` verifies the input identity and count domain, revalidates every outcome against the original constraints and recomputes rates/gap.

The archived card is opt-in for the matching recovery preset. It identifies the original 160-evaluation, seed-17, two-layer budget, original UTC timestamp, observed queue/network/polling interval and historical cost. Its hardware optimum rate is 200/256, feasible rate 247/256 and best-plan gap zero. These are **not new observations** and are not a like-for-like timing comparison with arbitrary new local settings. Opening the card makes no provider call and spends no credit.

Open Quantum infrastructure attribution follows its [citation guidance](https://www.openquantum.com/citation): Kuhn, Armbruster and Wold (2026), [doi:10.5281/zenodo.22103261](https://doi.org/10.5281/zenodo.22103261). The archive is provider-reported evidence, not independent hardware attestation.

## API and execution limits

- `GET /arena/presets`: fixed supported cases.
- `POST /arena/compare`: an `ArenaRequest`, returning input/settings, full classical and quantum results, actual traces, validation and timings.
- `GET /arena/hardware`: validated archived H7 evidence only.

FastAPI runs the synchronous handlers in its worker pool. One process-local nonblocking lock admits a single comparison/archive computation; overlap returns HTTP 429 without enqueuing additional work. The lock is released on errors. Invalid budgets, extra fields, unknown presets and attempted hardware-mode parameters return 422; unsupported calculation errors return 400, and an archive identity mismatch returns 409. Arbitrary file paths and arbitrary uploaded scenarios are not accepted. The existing deployment is single-process; this lock is not a distributed quota.

The service neither imports the provider SDK nor reads credentials. No network downloads enter the backend. Compute imports remain FastAPI-free so the notebook can use the existing optional Python notebook environment.

## Notebook and verification

`notebooks/H8_solver_arena.ipynb` has **11 executed code cells**: input identity, scoreboard, classical observations, unitary-checked QAOA trace, raw counts, six-qubit cases, infeasible/incomplete cases, seeded reproducibility, archived hardware and a final execution summary. Use the existing Lunar H7 kernel (or the pinned H0 notebook environment). No provider credentials are required.

Observed checks:

- **40 new frozen H8 tests** pass, covering detached observers, original solver behavior, independent statevector verification of recorded iterations, exact gaps, no hidden quantum execution, strict budgets, API admission and archived hardware identity.
- **1010 backend tests**, **112 ingestion tests** and **60 optional quantum tests** pass. Python Ruff checks pass. All previously frozen tests remain unchanged.
- All **11 H8 notebook cells** executed without errors and saved their outputs.
- TypeScript and Vite production build pass. The existing large LunarSurface chunk warning and Starlette/AnyIO deprecation warning remain.
- No frontend tests or screenshots were created or run; visual acceptance remains with the user.

H9's illustrative Artemis/CLPS shared-antenna scenario, H10 scaling and H11 relay-orbit selection remain later stages. H8 does not implement live quantum submission, noise calibration, actual DSN reservations or a live flight controller.
