# H0 — Solver-neutral mission contracts

H0 defines shared inputs and unvalidated plan records for classical and quantum experiments. It implements neither a solver nor a physics model. H1 will add independent feasibility checking and a classical reference; H2 will map the same problem to QUBO/QAOA.

## Delivery and test-first evidence

Base: `origin/tahakrcc` at `d67a32f`. Branch: `codex/h0-hybrid-contracts`; merge remains manual. Skeleton commit: `a4437db`. Frozen tests: `774a50f`. The H0 implementation changes no existing contract, endpoint, scientific calculation or frontend file. Before delivery, upstream tahakrcc `b678a8b` (including R4/R5) was merged into this feature branch and the combined tree was verified.

## Start in Jupyter

Open `notebooks/H0_shared_contracts.ipynb` using **Lunar H0 (Python 3.12)**. The notebook imports `shared.hybrid`, displays all 12 options, checks export/reload and fingerprints, demonstrates invalid and missing data, separates truth/observations/estimates, and records an unvalidated proposal. It does not install packages or contact NASA/quantum services when run.

On the development machine, Jupyter serves `/Users/tk/Desktop/nasa-quantum`. The `h0-hybrid-workspace` symlink exposes this worktree without modifying the separate team checkout. Open:

http://localhost:8888/lab/tree/h0-hybrid-workspace/notebooks/H0_shared_contracts.ipynb

The user's existing `Untitled.ipynb` was not overwritten. The dedicated kernel uses `/Users/tk/.local/share/lunar-h0-venv`; existing Python environments and kernels were not replaced.

For another machine, use a separate Python 3.12 virtual environment and the optional notebook dependencies:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r notebooks/requirements.txt
.venv/bin/python -m ipykernel install --user --name lunar-h0 --display-name 'Lunar H0 (Python 3.12)'
```

Launch Jupyter from the repository or its `notebooks` directory. Alternatively set `LUNAR_REPO_ROOT` before starting the kernel. The notebook imports no optimizer. Notebook packages are development tools; they are not added to service runtime locks.

## V1 data dictionary

All models reject extra fields and non-finite numbers. Numeric strings and booleans do not substitute for numerical quantities. IDs use ASCII letters, numbers, underscores, periods, colons and hyphens. Collections with identity keys reject duplicates. Models are frozen at the attribute level; nested dictionaries are not deeply immutable. To edit, export a dictionary, change it, increment the revision and validate again. Canonical export, hashing and plan-reference checking revalidate nested data.

| Model | Important fields and meaning |
|---|---|
| `Scenario` | `schema_version=1`, ID, revision, seed, UTC window, missions, candidates, resources, objectives, incompatibilities, sources, optional states and events |
| `TimeWindow` | Half-open `[start_utc, end_utc)`; explicit UTC; positive duration |
| `Mission` | ID, vehicle ID, UTC deadline, source IDs; exactly one option is required in V1 |
| `Site` | MOON_ME latitude in degrees `[-90,90]`, east longitude `[0,360)`; no implied altitude, terrain coverage or safety claim |
| `Candidate` | Globally unique option ID, mission reference, site, interval, demands, metric values and source IDs |
| `Resource` | ID, mode, unit, capacity (or null), nonoverlapping availability intervals and sources |
| `Demand` | Resource reference, amount, matching unit and an interval contained in its candidate |
| `Objective` | Metric name, unit, maximize/minimize direction, positive weight and positive normalization scale |
| `Incompatibility` | `at_most_one` among a distinct known set of option IDs; all pairs in that set are mutually exclusive |
| `StateSnapshot` | State ID/revision, vehicle, epoch, record kind, explicit frame and optional known physical quantities |
| `Event` | ID, UTC epoch, chronological sequence, discriminated payload and sources |
| `Source` | ID, observed/derived/synthetic/assumption classification, description, optional locator/version/checksum/analysis ID, parent sources |
| `Plan` | Plan ID, scenario ID/revision/fingerprint, state-ID/revision map, selected options, solver identity, execution mode and `unvalidated` status |

### Hard rules and preferences

The V1 problem means exactly one candidate per mission, completion by each mission's deadline, compliance with each declared resource's availability/capacity, and all declared incompatibilities. These rules are mandatory. H1 must evaluate them against original inputs independently of any QUBO penalty or score.

An objective contributes `weight * value / scale` to a common maximization score, with positive sign for maximize and negative sign for minimize. Every candidate must have exactly the named objective metrics; use null for unknown values. The unit on each objective governs all candidate values of that metric. H0 declares these semantics but does not compute scores or feasibility. Fuel constraints, link budgets, optional missions, vehicle exclusivity, precedence, charging/recharge and detailed activity scheduling require explicit later extensions; do not infer them from V1.

### Resource semantics

- `concurrent` / `channel`: constant demand over its half-open interval; sum simultaneously active demands and compare to capacity. Adjacent intervals do not overlap. Fractional capacity is allowed for explicitly declared aggregate allocations; V1 does not enforce indivisible antenna units.
- `budget` / `J`: amount is total consumed energy, counted once per demand across the scenario. Capacity is a cumulative budget, not watts, battery state or a recharge model. Availability restricts when consumption can occur.
- Empty availability means no available interval. Null capacity means unknown, never zero or infinity. H1 must report incomplete input rather than silently approve a plan requiring an unknown quantity.
- Candidate demands may exceed capacity, lie outside resource availability or miss their mission deadline. Such inputs describe potentially infeasible choices and are intentionally accepted. Demands outside their own candidate interval, dangling references and overlapping resource availability definitions are malformed and rejected.

### State and event semantics

Position is meters from the Moon's center, expressed in the rotating MOON_ME frame. Velocity is the derivative of those rotating-frame components in m/s, not an inertial velocity. No frame transformations are performed. Additional frames, including normalized Earth–Moon three-body coordinates, need an explicit versioned extension before H11.

Mass is kg, available thrust N, battery energy/capacity J, and position standard deviation m. Propellant cannot exceed known total mass, nor stored energy known capacity. Missing values are null. A snapshot needs at least one known measurement; it need not have a full vehicle model for H0 planning examples.

`ground_truth`, `observation` and `estimate` are distinct records. Observations require sensor identity/health. Ground truth cannot carry estimated position uncertainty. This is a storage distinction, not an estimator implementation: later planner adapters must deliberately select estimates and must not leak simulated ground truth into decision inputs.

Event payloads currently support resource outage, thrust loss and sensor degradation. Sequence IDs are unique and must follow timestamp order; simultaneous events have explicit sequence order. An outage begins at its event epoch. Events only describe changes: H0 does not apply them, invalidate caches, propagate uncertainty or perform replay/replanning.

### Provenance

Synthetic and assumed values require an explicit description. Observed sources require a locator plus version, checksum or analysis ID. Derived sources require a model version and resolved parent sources. Source lineage must be acyclic. Each mission, candidate, resource, state and event cites known source IDs. Citations describe the record collectively; mixed per-field provenance needs further explicit modeling before mixing real and assumed values inside a record.

## Canonical export and version identity

`canonical_json` validates again, uses sorted JSON keys, explicit null/default fields, normalized UTC serialization, integer-equivalent numbers (including negative zero), and deterministic ordering for set-like collections. Position/velocity components retain their order. All content is included, including descriptions, sources, revisions, states, seeds and events; changing any of these can change the fingerprint. SHA-256 fingerprints the canonical UTF-8 bytes without the trailing file newline.

This is the project's V1 Python canonicalization contract, not RFC 8785 or a cross-language float-serialization promise. Do not independently reimplement hashing in JavaScript; use the same shared package. `load_scenario` rejects duplicate JSON keys and nonstandard NaN/Infinity. Unsupported schema versions fail explicitly. Schema migrations are not implicit.

`validate_plan_references` compares scenario ID/revision, the complete input fingerprint, exact state revision references and known option IDs. It catches wrong/stale input identity but intentionally does not check exactly-one selection, deadlines, resource limits, objective quality or physical feasibility. Every H0 plan remains `unvalidated`. Asynchronous stale-result handling, deadlines and fallback policies remain H6 work.

## Shipped synthetic cases

All are in `shared/examples/hybrid/`, with three missions and four options per mission. The 64 assignments before constraints are not evaluated by H0.

| File | Intended case |
|---|---|
| `independent.json` | Three channels allow independent preferred choices |
| `shared-conflict.json` | One channel makes the three preferred first-hour options conflict; other assignments are available |
| `infeasible.json` | Zero capacity with positive demand for every option |
| `missing-capacity.json` | Unknown resource capacity requiring an explicit missing-data policy |
| `resource-outage.json` | Declared first-hour outage and an estimated vehicle record; no state transition has run |

A feasible/infeasible label here is a hand-authored benchmark intention, not output from a deployed solver. None of these examples represents a NASA mission, DSN booking, validated landing location, trajectory or radio link.

## Verification

On 2026-11-14: 77 H0 contract tests passed, within 533 passing backend tests after synchronizing with upstream; 112 ingestion tests passed. Notebook execution completed all 8 code cells with zero errors using the dedicated kernel. The running Jupyter server returned the notebook with its `lunar-h0` kernel metadata. Frontend production build passed; no frontend tests or screenshot checks were performed.

Re-run from the existing isolated preview:

```sh
docker exec nasa-task4-backend-1 pytest -q
docker exec nasa-task4-data-ingestion-1 pytest -q
docker exec nasa-task4-backend-1 ruff check app tests /workspace/shared
git diff 774a50f HEAD -- backend/tests/test_hybrid_contracts.py
```

The last command must produce no diff. Dependencies and numerical models of the existing services remain unchanged. Existing build/dependency deprecation warnings are not evidence of quantum execution or quantum advantage.
