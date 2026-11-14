# H6 — Guarded hybrid recovery and downstream scheduling

H6 joins the H5 physical recovery boundary to H1/H3-compatible static campaigns and H2's bounded local QAOA worker. A fault can change both a landing target and the selected time slot of a later mission. This is an offline integration milestone, not real-time flight control or a hardware experiment.

Delivery remains on `codex/quantum-hybrid`; do not merge into or push to `tahakrcc` or `main`. Test-first history: skeleton `5b9252a`, frozen integration tests `d872606`, frozen history/cancellation/metadata tests `bfe96d5`.

## Inputs and preparation

Production implementation: `backend/app/hybrid_recovery.py`. The example is `shared/examples/recovery/hybrid_campaign.json`; it contains declared synthetic recovery/campaign inputs, explicit bindings, dependencies and a quantum budget. It deliberately contains no cached checkpoint: the notebook derives one from a fresh H5 replay.

`HybridRecoveryRequest` combines:

- An H5 `RecoveryScenario` and a `RecoveryCheckpoint`: current estimated terminal state, available thrust, active landing exclusions, communication outage end, navigation uncertainty and previous target. All timestamps use explicit UTC; fuel/thrust stay inside the vehicle envelope.
- An H0 `Scenario`, with no unapplied events, at most 32 options and eight missions. Callers supply current calendars, resource capacities and remaining mission options. This adapter does not fetch contact geometry, reserve a station or ingest telemetry.
- A bijection from each planar landing target to an existing landing-mission option. The campaign's site coordinates remain exactly as supplied. These identifiers do not assert a planar-to-MOON_ME conversion or real-site calibration.
- Optional dependencies declaring that a downstream option can start only after landing plus a nonnegative minimum delay. The two-vehicle example explicitly declares both downstream options; omitted dependencies are not inferred.
- An explicit fuel objective weight and scale: defaults are weight 1 and scale 1000 kg. The reserved `recovery_propellant_kg` objective minimizes predicted remaining burn; all supplied objective terms remain intact. Every option receives the metric, with zero for downstream options. The scale makes the extra term visible and reproducible; it is not a universally calibrated mission preference.

`prepare_problem()` rechecks every target from the supplied **estimate**, not truth or an earlier acceptance flag. It uses H5 coarse/fine propagation, current landing exclusions, communication policy and seeded uncertainty draws. It then:

1. Removes physically rejected landing options and already-past unexecuted downstream options.
2. Moves each surviving landing service window to predicted fine-resolution touchdown, keeping its duration and relative resource-demand timing. Windows outside the campaign horizon are removed. Mission deadlines and resource availability remain original constraints checked by H1.
3. Adds pairwise incompatibilities for landing/downstream combinations violating the declared arrival dependency. Downstream service slots themselves are never shifted or invented.
4. Restricts existing at-most-one constraints to surviving options; a rule with fewer than two survivors is a tautology. Other mission/resource/objective rules remain unchanged. Unsupported quantum constraints are not removed to make the circuit fit.
5. Returns no scenario if any mission has no remaining option. Otherwise, it adds derived provenance and hashes the complete input snapshot, including recovery state, resources, bindings and objective settings.

Finite uncertainty samples and the planar sampled-terrain model inherit all [H4](descent-foundation.md) and [H5](self-healing.md) limits. A screened option is not a certified safe landing site.

## Classical incumbent and asynchronous quantum acceptance

`RecoveryCoordinator` is driven by one controlling caller and owns one background worker. A second submission while the worker is active receives a freshly computed classical result rather than adding work to an unbounded queue. This is a local Python API; no new HTTP endpoint or external provider adapter is introduced.

`submit(request, budget)` prepares the current problem and runs bounded exact enumeration before launching QAOA. The ticket exposes the incumbent and its classical status. Enumeration is capped at 4096 assignments; callers must inspect `classical_status`, and optimality is claimed only for a completed exact search. No job starts when no incumbent exists. Unexpected programming errors propagate instead of being silently presented as successful fallback.

Default budget: one QAOA layer, 64 objective evaluations, 256 shots, seed 17, four-qubit cap and a five-second acceptance deadline. Validated absolute caps are three layers, 512 evaluations, 8192 shots, six qubits and 30 seconds. The notebook uses a ten-second acceptance deadline. H2 additionally caps its one-hot QUBO at 12 variables, audits the mapping and requires power-of-two option counts in compact mission registers. An unsupported mapping returns the classical result without discarding valid options or relaxing rules.

`resolve(ticket, current_request)` does not wait for a pending quantum job. It does perform fresh physical preparation and classical validation, so it is not a constant-time or hard real-time operation. The caller must provide the latest snapshot; the coordinator cannot discover unreported changes. Acceptance requires:

- The complete request hash and quantum problem fingerprint still match.
- The half-open acceptance deadline has not expired, including after fresh validation.
- Register sizes, sample basis domain, probability normalization, counts, seed, depth, shots and evaluation totals match the bounded job.
- The proposed plan occurs in a positive-count simulator outcome and is independently feasible against the current original H0 constraints.
- Its exact rational score is at least the freshly validated classical incumbent's score.

Reasons such as `pending`, `worker_busy`, `cancelled`, `deadline`, `stale_snapshot`, `unsupported`, `worker_error`, `invalid_quantum`, `no_sampled_plan` or `inferior_quantum` retain the current classical answer. If that current answer is infeasible, the report contains no plan or selected target. An old fallback never authorizes an action under new constraints.

The deadline starts **after classical preparation**, not at fault arrival. Reports separate preparation, revalidation and elapsed-since-submission times. `wait()` bounds waiting for the worker by the remaining acceptance budget, then revalidates. Cancelling cannot forcibly terminate already-running Python computation. `close()` waits for that bounded worker; it is a cleanup operation, not a real-time abort. The API does not provide atomic live-actuator dispatch or estimator advancement during computation.

## H5 integration and history correction

`replay_recovery(..., target_selector=...)` gives the selector a deep copy of each decision. H5 accepts only a target in its currently screened candidate set; `None` means no validated joint continuation. The default H5 policy is unchanged when no selector is supplied.

`replay_hybrid()` uses this hook to submit, wait and validate a joint plan at each H5 decision point. It logs both the propagated landing and the selected downstream schedule. It does not physically propagate the second vehicle. **Simulated flight time is paused during planning**, explicitly reported by `simulated_clock_paused_during_planning=True`. Offline success is not evidence that a moving real vehicle can execute the same plan after a wall-clock delay.

Integration tests exposed an H5 history bug: decision records retained a reference to the mutable active-hazard list. A later hazard could appear in an earlier record and invalidate its recorded hash. Records now copy the list at the decision boundary. A frozen regression test reconstructs the historical hash, and the unchanged H5 notebook was re-executed successfully.

## Executed synthetic example — 2026-11-14

At 5 s, the vehicle loses 3 kg, available thrust becomes 3000 N and the original −5 to +5 m landing interval closes. Explicit alternatives are +40 m at 30 s and +80 m at 35 s. The campaign offers follow-up slots at 33 s and 40 s, shares a one-channel antenna and assigns illustrative site utility values. Eight uncertainty draws are used per screened target.

- Before the fault, three landing choices do not fit compact binary encoding. The validated classical schedule selects `land-primary` and `follow-early`.
- After the fault, two surviving landing choices × two downstream slots require **two logical qubits**, with four one-hot QUBO variables.
- H5's local policy selects the +40 m backup. Joint planning selects `land-remote` (+80 m) and `follow-late` (40 s). The remote/early pair is disallowed because landing occurs after the early slot.
- H6's replay lands with **76.814439 kg** remaining; burn, the 3 kg loss and remaining fuel sum to the initial 100 kg. The original H5 backup retains 79.666978 kg. The remote plan intentionally trades additional fuel for the declared campaign utility while satisfying the same reserve checks.
- The accepted exact objective is `109982263595723410617/1000000000000000000`, identical to the enumerated optimum on this instance.

One layer, 64 objective evaluations, 256 shots, unchanged inputs:

| Seed | Simulator probability of optimum | Uniform probability | Sampled optimum rate | Accepted source |
|---|---:|---:|---:|---|
| 17 | 0.580875 | 0.25 | 0.601563 | QAOA |
| 23 | 0.652961 | 0.25 | 0.652344 | QAOA |
| 42 | 0.577499 | 0.25 | 0.605469 | QAOA |

Logical estimates are two CX, three RZ and two RX gates, maximum Z locality two; these are not transpiled hardware counts. Observed local worker time was about 0.032–0.033 s for these runs, excluding physical preparation/revalidation. Timings vary by machine/load. Raw distributions, outcomes and timing scope are saved in the notebook. Increasing probability relative to a uniform sample does **not** establish end-to-end quantum advantage: classical enumeration already solves this four-state problem and is also used in the mapping audit and fallback.

**Execution mode: noiseless local simulator. Hardware jobs: zero. No paid/provider request was submitted.**

## Reproduce and verification

Open `notebooks/H6_hybrid_recovery.ipynb` with the existing **Lunar H0 (Python 3.12)** kernel. This machine's Jupyter server exposes the notebook at:

http://localhost:8888/lab/tree/quantum-hybrid-workspace/notebooks/H6_hybrid_recovery.ipynb

The 10 code cells cover physical screening, service-window preparation, exact classical comparison, async two-qubit execution, three seeds, controlled deadline/stale-input guards, unsupported encoding, engine loss, full recovery and a trajectory chart. It imports production code, not test fixtures.

Observed checks:

- **37 new frozen H6 tests** passed: physical/schedule linkage, explicit site retention, dependencies, real local QAOA, H5 hook validation, deadline before/after checking, stale state/fuel/resource inputs, current fallback infeasibility, busy/cancelled/error workers, corrupted quantum metadata, unobserved/inferior proposals and immutable historical decisions.
- **827 backend tests** passed; one pre-existing Starlette/AnyIO deprecation warning remains.
- **112 ingestion tests** passed. Backend/shared Ruff checks and modified Python formatting passed.
- H6's **10 code cells executed with zero errors** and saved outputs. H5's existing **10 cells reran in memory with zero errors**, without rewriting that notebook.
- The frozen H6 tests and all prior-stage tests are unchanged. No frontend code/tests or screenshots were involved.

```sh
docker exec nasa-task4-backend-1 pytest -q
docker exec nasa-task4-data-ingestion-1 pytest -q
docker exec nasa-task4-backend-1 ruff check app tests /workspace/shared
git diff d872606 -- backend/tests/test_hybrid_recovery.py
git diff bfe96d5 -- backend/tests/test_hybrid_recovery_boundaries.py
```

## H7 boundary

H6 completes bounded local coordination. H7 remains open for separately authorized hardware/provider execution, noise and transpilation evidence, broader scenarios and cost/latency benchmarks. Live estimation, latency-aware descent, actual station reservations, 3D terrain/attitude physics, continuous collision proof, general resource encodings and real mission calibration are not implied by this milestone.
