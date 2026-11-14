# H9 — Three missions sharing one antenna

H9 turns the H8 solver arena into an illustrative Artemis-style campaign: one crewed lander and two CLPS-class robotic missions share one assumed DSS-43 channel. It compares independent choices with a joint classical optimum and locally simulated QAOA. Every proposed plan is checked against the original constraints. Mission labels, priorities, deadlines, contact demands and station availability are explicit teaching assumptions; this is not a NASA mission plan or DSN scheduling commitment.

## Delivery and history

Branch: `codex/h9-shared-antenna`, based on completed H8 `74bcfba`, which contains current main `cb17bdd`. H8 had not been merged into main when work started. Only the new branch is delivered; main and tahakrcc are not merged or pushed. The user's existing H6 notebook edits remain local and excluded.

- Campaign skeleton: `071e1fa`.
- Frozen campaign acceptance tests: `945a718` (40 tests).
- Optional physical-layout interface: `84dac54`.
- Frozen layout tests: `a366a5c`, with user-approved reader-only correction `d66685f`. Qiskit's QASM reader now enables its standard legacy gate instructions for SDK-normalized `sx`; assertions and tolerances were not changed.

## Source-backed alternatives

`shared/examples/campaign/evidence.json` is a small, checksummed fixture containing projections of four saved visibility analyses, their R2 communication records and four candidate intervals. It contains no terrain raster, credentials or provider signed URLs. The original full analysis-result hashes are retained; those full results and NASA raw responses remain in the local data/state volumes.

| Alternative | Site (MOON_ME) | Contact interval UTC | DSS-43 geometry | Sunlit seconds |
| --- | --- | --- | --- | --- |
| 1 | Mons Mouton feature center, -84.6°, 31° E | 2026-11-04 00:00–00:30 | Available | 0 |
| 2 | Explicit custom point, -84.40115°, 21.95459° E | 2026-11-04 01:00–01:30 | Available | 0 |
| 3 | Mons Mouton feature center, -84.6°, 31° E | 2026-11-05 00:00–00:30 | Unavailable | 0 |
| 4 | Same explicit custom point | 2026-11-05 01:00–01:30 | Available | 0 |

These are historical replay dates, not future launch dates. The custom point retains its literal coordinates and is not promoted to an officially named or certified landing site. The example represents candidate surface-start/contact periods, not a propagated landing trajectory. There is no independent landing-dynamics or energy sufficiency assertion in H9.

Each alternative requires matching analysis/job identity, submitted and calculated site/frame, analysis and communication time span, complete job state, contiguous Sun timeline and covering per-station geometry. Unknown geometry is preserved. A missing or mismatched record is rejected. The blocked third alternative is retained so users can see why fixing the crew to it makes the campaign infeasible.

R2 calculates station geometry using JPL Horizons and the project's DSN coordinates; details and sources are in [R2 communication](ground-station-communication.md). H3 converts that geometry into candidate-specific gates and separately declared station calendars; see [resource integration](resource-integration.md). The backend does not download data. The new day-five analyses and R2 jobs were acquired through the existing backend → RabbitMQ → ingestion flow. An old running ingestion process had no DSN queue consumer; restarting it loaded the already-implemented DSN consumer and allowed the jobs to finish. No queue data was deleted and no R2 source changes were required.

All four selected contact periods are dark. Sunlit duration contributes to the score but is not a hard solar-power constraint. These results cannot support a claim that a solar-powered spacecraft has sufficient energy. Adding a calibrated power budget is a separate modeling decision.

## Scheduling semantics

Every mission must select exactly one alternative. Each requests one channel for its half-hour interval; all intervals are half-open `[start, end)` in UTC. Capacity is one concurrent channel under the normal teaching calendar. Geometric availability does not constitute an antenna reservation or guaranteed radio link.

The assumed preference score is `mission_priority * alternative_preference / 4`. Mission priorities are crew=3, robot-a=2 and robot-b=1; alternative preferences are 4, 3, 2 and 1. A small solar contribution adds `0.1 * sunlit_seconds / 1800` per mission. All weights and original source records participate in the scenario fingerprint. These are declared preference values, not measured science returns or real crew-safety requirements.

Independent selection chooses each mission's highest-score option and then checks the joint proposal. It therefore puts all three missions into alternative 1 and exceeds capacity. Joint exact planning checks all 64 assignments. With option 3 blocked, six assignments are feasible; the best plan gives alternative 1 to the crew, alternative 2 to robot A and alternative 4 to robot B, with normalized score 4.75.

Fixing the crew option adds a hard gate to all other crew alternatives; it does not silently change the user's choice or remove required missions. Rebuilding the problem changes its fingerprint. Fixing alternative 3 yields infeasibility. The independent proposal displayed under a pin respects that pin while leaving robots independently selected.

Four controls illustrate distinct outcomes:

- **Standard:** explicitly assumed one-channel calendar.
- **Deadline:** all missions must finish by the first alternative's contact end; there is no jointly feasible assignment.
- **Closed:** declared antenna capacity zero; infeasible.
- **Unknown:** antenna capacity is missing; incomplete input, not zero or unlimited capacity. Quantum optimization is not run.

These are static what-if scenarios, not a live event controller. H5/H6 descent recovery is unchanged. Unsupported quantum mappings keep the classical result and an explicit reason.

## Interface and API

Open Mission Control → **Shared antenna**, or `http://localhost:5174/#/shared-antenna`.

The English/Turkish interface presents three mission cards, inspectable option/source records, scenario conditions and a fixed crew-choice selector. Independent, classical and QAOA plans share the same UTC axis. Antenna occupancy is colored separately from mission contacts, with precise intervals and capacity violations in text and tables. Empty time gaps remain proportional on the axis. Updating controls does not relabel old results; a new run clears previous output and the old hardware association. Hidden panels abort waiting and stop H8 playback; server work is bounded rather than forcibly terminated.

The H8 details panel is reused for actual optimizer-history playback, raw measurement counts and the classical/QAOA scoreboard. It receives the exact H9 comparison object, without changing or re-solving the scenario in the browser. The browser performs presentation only; scheduling, validation, scores, timeline occupancy and coordinates are computed in Python.

- `GET /campaign/catalog`: fixed supported alternatives and provenance.
- `POST /campaign/compare`: mode, crew option 0–4, depth 1–3, evaluations 1–256, shots 1–8192, seed 0–2^32−1.
- `GET /campaign/hardware`: optional recorded H9 result, independently revalidated. Never submits a job.

Comparison and recorded-hardware calculation share H8's process-local nonblocking admission lock; overlap returns 429. Invalid request fields return 422; evidence/calculation errors return 400 or 409. No arbitrary path or uploaded scenario is accepted. The local deployment is single-process; the lock is not a distributed rate limiter.

H8 solver timing covers its shared-snapshot validation, mapping/audit, optimizer work and result validation. H9 evidence loading, campaign assembly, independent proposal construction, timeline formatting, HTTP transfer and rendering are additional work, not secretly counted as faster quantum computation.

## Quantum experiment and limits

The exact same 12 candidate choices use six logical qubits. The compact Hamiltonian can contain higher-order interactions: fewer qubits is not automatically fewer gates. The first experiment remains capped at one free Spark, with paid credit use requiring separate authorization.

Before observing hardware output, the local sweep was fixed at one/two layers, seeds 17/23/41, 80 tuning evaluations and 256 final samples. Selection maximizes ideal feasible probability, then ideal optimal probability, then prefers smaller depth and seed. All six runs are retained in the notebook and private experiment record; this is an instance-specific choice, not a general optimizer benchmark.

The default provider transpilation used noncontiguous physical indices and preparation rejected it with `Virtual qubits must be contiguous`. Preparation did not submit a job or spend credits. The gateway now optionally accepts validated initial physical indices and supplies them to Qiskit before routing. It does not renumber a finished physical circuit. The default H7 behavior is unchanged. H9 uses initial indices 0–5, preserving final measurement mapping; an independent six-qubit statevector check confirms the uploaded circuit's decoded probabilities match the logical circuit to absolute tolerance 1e-11.

The Public/Standard preparation quote is checked against a maximum of one Spark; submission rechecks balance and price and uses an exclusive persistent receipt. Automatic refill was visibly off, the account had 24 Spark and zero Full before submission. A failed/ambiguous submission must not be retried under a new trial label. Credentials, preparation IDs and signed URLs remain outside Git.

The UI only offers a hardware timeline when its scenario fingerprint matches the displayed input. Modifying capacity, crew choice or deadlines prevents applying a stale hardware plan. Even for a matching problem, the archived circuit's original tuning and shots are distinct from arbitrary current local settings. Opening recorded results spends nothing. Provider attribution follows [Open Quantum's citation guidance](https://www.openquantum.com/citation).

## Notebook and validation

`notebooks/H9_shared_antenna.ipynb` contains 11 code cells: sources, exact model, independent conflict, exact occupancy, pinned choices, failure conditions, H8 comparison, six-run local sweep, independent Qiskit verification and recorded hardware. Run All is offline and cannot spend credits. Use the existing Lunar H7 kernel. No new dependency is added to the backend, ingestion or frontend.

The 40 campaign tests include an independent interval-overlap oracle over all 64 assignments, exact objective comparison, malformed source association, unknown/blocked geometry, pinning, deadline/capacity cases, stale-plan rejection, adjacent half-open intervals, seeded samples and API admission. The nine physical-layout tests cover preserved measurement distributions and rejection before upload of malformed placement.

Frontend verification follows the repository's user instruction: TypeScript/Vite build only, no frontend tests or screenshots. Human visual acceptance remains with the user. Existing large LunarSurface chunk and Starlette/AnyIO warnings are unrelated to H9.

## Completed hardware trial — 2026-11-15 (Europe/Istanbul)

| Item | Observed value |
| --- | --- |
| Job | `03f3af9d-ddfe-4540-a1e5-d429aa9916d9` |
| QPU | `rigetti:cepheus-1-108q` |
| Input fingerprint | `f924633e9740d2446b4c5d327d6770ef2606c1135a4f105eab65444581f8764f` |
| Uploaded circuit SHA-256 | `0f6e6ebc0a7c8479c0345a3ed642c1c15deb1a73e8d91c6ebd59b5e3955ac2a4` |
| Logical / used physical qubits | 6 / 6, initial physical indices 0–5 |
| Local tuning | 2 layers, seed 41, 80 evaluations |
| Compiled operations | 192 CX, 66 SWAP, 72 RZ, 12 RX, 6 H, 6 measurements |
| Submission UTC | 2026-11-14T21:22:58.364614+00:00 |
| Completion first observed UTC | 2026-11-14T21:24:32.160287+00:00 |
| Observed elapsed interval | 93.796 s, including queue/network/polling |
| Shots | 256 |
| Feasible observations | 17 / 256 = 6.640625% |
| Optimal observations | 3 / 256 = 1.171875% |
| Best observed score / gap | 4.75 / 0 |
| Credit use | 1 Spark; balance 24 → 23, Full remained zero |

The selected ideal circuit had feasible probability 67.8607% and optimal probability 11.7440%. Uniform selection over the 64 assignments has probabilities 9.375% and 1.5625%, respectively. The actual hardware frequencies were far below the ideal distribution and numerically below uniform; one finite-shot run does not establish a statistically reliable disadvantage or identify its cause. The routed circuit is substantially deeper than the H7 two-qubit example. Device noise, routing and readout effects need separate calibrated investigation before attributing the difference. Finding the optimum three times is not evidence of quantum advantage. No extra trial was submitted to replace this unfavorable distribution.

Raw counts and sanitized metadata are retained in `shared/examples/campaign/hardware.json`. Both the notebook and API independently decode every count against the original H9 constraints without repair. The best observed plan is crew-1 / robot-a-2 / robot-b-4. The stored hardware record is not applied to modified scenarios.

Final checks: **1050 backend tests, 112 ingestion tests and 69 optional quantum tests passed**, with Ruff checks and the TypeScript/Vite build passing. All **11 notebook code cells executed without error**. The recorded hardware fixture was additionally exercised by the targeted H8/H9 suite and the running API. No frontend tests or screenshots were run.
