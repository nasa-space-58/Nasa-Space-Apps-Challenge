# H1 — Classical multi-mission baseline

H1 prepares explicitly supplied site/time alternatives, compares independent choices with exact joint selection, and validates plans against the original H0 constraints. It is a bounded static scheduling baseline for the later quantum experiment, not a trajectory, radio-link or event-replay model.

## Delivery

Base: H0 branch `codex/h0-hybrid-contracts` at `ac14ec8`. Continuing branch: `codex/quantum-hybrid`, following the user's instruction to keep subsequent hybrid work together. Only this branch is pushed; the user merges manually into other branches. Skeleton commit: `33901c3`. Frozen tests: `1bc2ce8` (62 tests). H0 source, fixtures and tests remain unchanged.

Implementation: `backend/app/hybrid_planning.py`. Notebook: `notebooks/H1_classical_planning.ipynb`. No new dependency, endpoint, frontend component, external data download or quantum job is introduced.

## Start in Jupyter

Open the notebook using the existing **Lunar H0 (Python 3.12)** kernel. It imports the backend module directly; there is no duplicated solver in notebook cells. Optional local dependencies and kernel setup remain those in `notebooks/requirements.txt` and [H0 contracts](hybrid-contracts.md).

On this development machine the existing server serves `/Users/tk/Desktop/nasa-quantum`. A local `quantum-hybrid-workspace` symlink points at the working checkout:

http://localhost:8888/lab/tree/quantum-hybrid-workspace/notebooks/H1_classical_planning.ipynb

On another machine launch Jupyter from the repository or its `notebooks` directory, or set `LUNAR_REPO_ROOT` before starting the kernel. A fresh kernel avoids an already-imported module from a different checkout; the first cell checks the module path.

The notebook has 12 code cells: environment, inputs, candidate preparation, independent proposal, exact solution, shared timeline, capacity variants, all five cases, bounded search, cumulative energy, result export/stale rejection, and determinism. Bars depict candidate intervals, not spacecraft trajectories. All example scientific scores and energy values are explicitly synthetic/assumed.

## Public Python interface

| Entry point | Input | Output |
|---|---|---|
| `generate_candidates` | Nonempty tuple of `CandidateInput` rows | Sorted tuple of H0 `Candidate` records with content IDs |
| `validate_plan` | H0 `Plan` and `Scenario` | `ValidationReport` against original constraints |
| `select_independent` | H0 `Scenario` | `SolverResult` containing independent best-score choices and their validation |
| `solve_exact` | H0 `Scenario`, positive integer `max_evaluations` (default 10,000) | `SolverResult` with search counts, incumbent, validation, timing and proof status |

Backend imports use `app.hybrid_planning`; the notebook uses `backend.app.hybrid_planning` from the repository root. The functions are pure with respect to scenario content: no input mutation, network access, state propagation or hidden resource updates. Runtime is measured with a monotonic clock and includes input validation, preparation, enumeration and output validation, but not caller file I/O or notebook rendering.

### Candidate preparation

Each input row explicitly supplies mission, MOON_ME site, UTC window, demands with units, metric values (or null) and source IDs. H1 assigns SHA-256 content IDs and deterministic order; it rejects duplicate rows and malformed candidates. It does not cross-multiply sites/times while copying an assumed score to a new condition, infer missing measurements, or certify candidate feasibility.

Scenario assembly still uses H0 validation to resolve mission, resource, metric and source references. Regenerating candidate IDs requires explicitly rebuilding any incompatibility references. H3 will integrate real communication windows and energy/activity models; H4 addresses physical descent feasibility. H1's generated candidates are supplied planning alternatives, not newly validated landing sites.

### Independent validator

The validator revalidates nested input values and the H0 plan identity, including full scenario fingerprint and state revisions. Malformed input, unknown option IDs or stale plans raise `ValueError`. It then checks:

- Exactly one selected option for every required mission.
- Selected candidate end time at or before its mission deadline.
- Each positive demand interval fully covered by the union of resource availability windows. Adjacent windows may jointly cover a demand; a gap cannot.
- Concurrent `channel` demand over exact half-open UTC intervals. All overlapping demands are summed, including multiple demands from one candidate.
- Cumulative `J` consumption across all selected demands, even at different times. This is not power or recharge.
- Declared at-most-one incompatibility sets.

Zero demand consumes no capacity and needs no availability. An unused unknown resource does not block a plan. No implicit exclusivity is inferred from a shared vehicle ID; callers must encode explicit exclusions. Uncertainty, initial vehicle state and all other physics are not evaluated by this static scheduling validator.

Reports include option/mission/resource IDs where applicable, offending time windows, unit-tagged required/capacity quantities and explanations. Required and capacity values are exact rational strings (for example `2/5` channels). A definitive violation gives `infeasible`, even when other selected data are unknown; unknown issues are still retained. Otherwise required unknown data gives `incomplete`. `feasible` certifies only these declared static rules.

The H0 Plan deliberately remains `unvalidated`. The H1 report is separate evidence bound to the full input fingerprint and selected IDs. Imported reports are not trusted certificates: call `validate_plan` again before use. Solver-produced plans use the same constraint-evaluation logic as the public validator, independent of selection strategy and of any future QUBO penalties.

### Objective and arithmetic

The maximization score is the sum over selected candidates and H0 objectives of `weight * value / scale`, positive for `maximize` and negative for `minimize`. Metrics retain their declared units; normalized contributions are dimensionless. Negative objective values are supported. `metric_totals` records each raw summed metric.

Decision arithmetic uses exact fractions of the decimal spelling of validated H0 numeric values. This avoids float-summation boundary errors such as 0.1 + 0.2 versus 0.3. It is exact relative to those supplied decimal values, not exact physical measurement or arbitrary precision recovery of pre-H0 inputs. `exact_score` is a rational string; `score` and metric totals are finite display floats. Values outside finite float output range raise `ValueError` rather than exporting infinity. Uncertainty margins are not added implicitly.

A score is allowed on an infeasible proposal for comparison, but must never be presented as an achievable score. Missing a selected objective leaves score and totals unavailable.

### Search and missing-data policy

Independent selection chooses the highest scoring option for each mission without joint constraint filtering, then validates the whole proposal. Its result status is `proposal`, never `optimal`, even if the proposal happens to be feasible.

Exact search enumerates the Cartesian product in stable mission-ID / option-ID order. It validates each assignment before considering it as the incumbent. Equal exact scores use the lexicographically smallest sorted selected-ID tuple. `optimal_plan_count` is the number of equally optimal assignments only after complete search; it is null for incomplete searches and infeasible problems.

| Result status | Meaning |
|---|---|
| `optimal` | All assignments evaluated; a best feasible plan exists |
| `infeasible` | All assignments evaluated; no feasible plan exists |
| `limit_reached` | Evaluation budget exhausted with assignments remaining; incumbent may be present |
| `incomplete` | At least one candidate metric or positively demanded resource capacity is unknown |
| `unsupported` | Scenario declares events not applied to a static input snapshot |
| `proposal` | Independent choices, with a separate validation report |

Both solvers conservatively stop before search when **any candidate** objective or positively demanded capacity is unknown, even when some complete alternative might exist. They never silently discard unknown alternatives and claim optimality. Individual validation only needs the selected options' data. A future missing-data policy must be explicit.

The budget bounds **evaluated assignments**, not wall time. Counts describe evaluated assignments only; there is no extrapolated feasible rate. `failure_counts` counts plans with each issue code once per plan, so sums across codes may exceed the rejected-plan count. A partial search never claims infeasibility or an optimum. This exhaustive reference is intended for small problems; larger classical CP-SAT/MILP integration remains later work.

### Events

Any declared event (outage, thrust loss or sensor degradation) returns `unsupported`, with no optimization or state transition. H1 never deletes or ignores events to obtain an answer. Event replay and application belong to later stages. A static snapshot prepared by a caller must explicitly preserve provenance and revised identity; H1 does not supply that conversion.

## Verified synthetic results

| Case | Evaluated | Feasible | Best normalized score | Status |
|---|---:|---:|---:|---|
| Independent capacity 3 | 64 | 64 | 3.0 | optimal |
| Shared capacity 1 | 64 | 24 | 2.6 | optimal |
| Capacity 0 | 64 | 0 | — | infeasible |
| Unknown capacity | 0 | 0 | — | incomplete |
| Declared outage | 0 | 0 | — | unsupported |

For capacity 1, independent choices have raw score 30 but demand three channels at once. Joint planning selects raw score 26, exact normalized score `13/5`, with six equally optimal plans. The deterministic representative selects mission 1 option 1, mission 2 option 2 and mission 3 option 3. All 40 rejected assignments violate channel capacity.

The notebook additionally checks capacities 0/1/2/3, 25/30 J budgets with an assumed 10 J per mission, a seven-assignment limited run, stale result rejection and JSON export/reload. A limited run can find the eventual optimum by chance without having proven it.

## Verification evidence

On 2026-11-14:

- All 62 H1 tests initially failed with `NotImplementedError` against the skeleton; frozen test commit is `1bc2ce8`.
- All 595 backend tests passed, including 62 H1 and 77 H0 tests. H1 includes 12 seeded small cases checked with a separate discrete-time occupancy oracle, plus boundary, invalid-input, objective, provenance and determinism cases.
- All 112 ingestion tests passed; backend/worker Ruff checks and formatting checks for new Python files passed.
- Frontend production build passed. No frontend tests or screenshots were run. Existing large-chunk and Starlette deprecation warnings remain.
- All 12 H1 notebook code cells ran without errors using the dedicated Python 3.12 kernel. The running Jupyter server returned the notebook and its kernel metadata.
- Frozen H1 tests and H0 implementation/tests were unchanged.

```sh
docker exec nasa-task4-backend-1 pytest -q
docker exec nasa-task4-data-ingestion-1 pytest -q
docker exec nasa-task4-backend-1 ruff check app tests /workspace/shared
git diff 1bc2ce8 HEAD -- backend/tests/test_hybrid_planning.py
git diff ac14ec8 HEAD -- shared/hybrid.py backend/tests/test_hybrid_contracts.py
```

The final two commands must produce no diff. No quantum circuit, service invocation, hardware benchmark or quantum advantage is demonstrated by H1.
