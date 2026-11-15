# C2: exact physics reuse and demand-driven evaluation

C1 is unchanged. This follow-up isolates the cost of physics from the cost of
choosing among already evaluated plans. The pre-registered protocol is
[physics-contribution-c2-protocol.md](physics-contribution-c2-protocol.md),
committed before measurements at `3c35663`.

## Measured outcome

Eight cases (four fresh seeds, two fixed limits) and five method conditions gave
40 retained comparisons. All 40 native audits passed: seven feasible optima and
one infeasible case per condition. Every revealed R6 record and full-result hash
matched the uncached run. Lazy scores matched exact native enumeration; native
MILP scores passed its existing numerical tolerance. No tolerance was loosened.

| Method | Mean total | Nominal misses | Perturbed dynamics misses | Plan batches |
|---|---:|---:|---:|---:|
| Eager MILP, uncached | 14.474 s | 8 | 192 | 8 |
| Eager MILP, cold cache | 5.881 s | 4 | 48 | 8 |
| Eager MILP, warm repeat | 0.734 s | 0 | 0 | 8 |
| Exact lazy, cold cache | 5.944 s | 4 | 48 | 8 |
| Exact lazy, warm repeat | 0.873 s | 0 | 0 | 8 |

Cold caching saves **59.3%** mean paired end-to-end time and **75%** of perturbed
integrations in these inputs, passing the pre-declared classical benefit gate.
The nominal planning miss count is halved. Warm repeats retain the cost of
copying trajectories and rechecking every hazard/statistical result.

Lazy search has a dynamics-miss ratio of **1.0** and a mean paired runtime ratio
of **1.010** against cold cached eager MILP. It required all eight candidate
batches in every case, so it fails the additional-savings gate. It is a working
on-demand evaluation flow, but this data does not let it avoid any further
expensive physics. The perfect-plan unit case can certify after two batches;
that constructed test is not presented as a measured mission advantage.

Cold cached eager time is composed of approximately 3.067 s nominal planning,
2.071 s perturbed integration, 0.706 s cache/key/copy work, 0.033 s remaining R6
work and 0.0035 s candidate construction/selection (plus small request/input
bookkeeping). Optimizing the millisecond selector does not eliminate the seconds
spent on nominal planning and trajectories.

A separate, instrumented single R6 batch called the integrator 40 times: 24
perturbed draws plus 16 nominal-planning calls. Its cumulative times overlap and
are excluded from the table above. This confirms that nominal search is real
physics work, not a free setup stage.

[Compact evidence and per-case counters](verification/physics-contribution-c2-2026-11-15.json)
include source hashes, original input seeds, audit outcomes and the separate
profile. Full per-method outcomes are retained locally at
`~/.local/share/lunar-contribution-c2/physics-c2.json`; the compact evidence records
its SHA-256. None of the preliminary or interrupted passes is included in the
40 retained comparisons.

## Implementation and boundary

`PhysicsCache` is a bounded, opt-in, process-local cache for exact nominal plans
and perturbed trajectories. Every argument and resolved vehicle is part of the
key. Outputs are isolated copies, failed computations are not retained, and LRU
capacity is 128 entries in the experiment. Code/constants must remain unchanged
for its lifetime. It is single-worker and not a persistent cross-revision cache.

`run_monte_carlo(..., physics_cache=cache)` still performs every touchdown,
reserve, terrain, unknown-cell, sensitivity and uncertainty calculation. Only
identical nominal plans and dynamics are reused. The existing default API path
has no cache supplied; this experiment does not silently change live sessions.

`select_lazy` receives only input sizes, a fixed miss limit and a callable that
flies a requested candidate. It reveals complete 24-draw results only after
choosing that candidate. It never consumes the full score table ahead of time.
Safe objective upper bounds choose the next pair; the search stops only at an
optimality certificate, infeasibility certificate or explicit batch exhaustion.
The returned optimality is for the finite R13 scenario objective, not a physical
safety certificate or proof about rare events.

## Why the existing quantum method cannot fairly enter this test

`build_robust` calls `_pair_table` to form the joint success counts and worst
combined miss for every candidate pair. These numbers set its objective,
forbidden pairs, MILP and QUBO coefficients. The existing QAOA methods consume
that QUBO. Constructing it first reveals all of the expensive outcomes that the
lazy experiment is supposed to avoid. Charging this preparation removes the
claimed physics saving; treating it as free would be an unfair comparison.

We therefore did not invent an unvalidated surrogate, create another QAOA
variant, or weaken the classical competitor. No quantum contribution has been
demonstrated by C2. Cache gains are classical. A future quantum proposal needs a
separately validated cheap objective or an efficient oracle with every data
preparation cost included; C2 is not evidence that such a proposal will succeed.

## Accounting and limitations

Cold cached eager MILP is the relevant strong baseline for lazy selection.
The uncached run diagnoses redundant work; it is not used to claim a quantum
advantage. Warm timings are incremental repeat costs; each method pays its own
cold preparation first. Caches are never shared between competing methods.
Input construction is timed once per case and charged equally to every method.
Audits run afterwards and cannot influence candidate selection.

Nominal time includes its internal PDI range search and integration calls.
The separate dynamics count is **only perturbed Monte Carlo integrations**,
not the integrations inside nominal planning. Cache overhead includes key
construction, lookup, insertion, eviction and copying. Remaining R6 time includes
profile loading, contact/hazard classification and statistical aggregation.
Candidate selection time includes native MILP/QUBO construction for eager MILP
and bound/scoring work for lazy search. Timings are descriptive observations on
one shared machine, not hardware-independent guarantees.

All cases use synthetic terrain, identical vehicles and zero nominal terrain
height, with common-cause seeded errors. Those assumptions create exact dynamics
reuse across sites/directions. Real missions with distinct heights, vehicles or
error draws may have fewer cache hits; those inputs are not rounded or collapsed
by the cache. Fixed 500/1500 m limits are not fitted to observed outputs.

## Reproduction

With the backend dependencies installed, from the repository root:

```bash
PYTHONPATH=backend:. python -m app.run_physics_contribution --output /tmp/physics-c2.json
```

For Docker, use a separate process with this checkout mounted rather than a
shared application container that other work can recreate. With the existing
backend image (locked dependencies), run from the repository root:

```bash
mkdir -p "$HOME/.local/share/lunar-contribution-c2"
docker run --rm --entrypoint python -v "$PWD:/workspace:ro" -v "$HOME/.local/share/lunar-contribution-c2:/evidence" -w /workspace/backend -e PYTHONPATH=/workspace/backend:/workspace nasa-task4-backend -m app.run_physics_contribution --output /evidence/physics-c2.json
```

For deployments that mount only backend/shared, `--protocol-path` can point to
an explicitly copied protocol file. The full-root mount above already includes
it. Keep the backend dependency versions aligned with the committed lockfile.

Source/protocol files are checked before expensive work. A `.partial.json`
checkpoint is written after each completed case. Run benchmarks sequentially,
without concurrent test/build/profiling jobs. Source hashes and per-method full
outcomes support independent replay of each comparison.

The first execution completed its eight internal audits but failed to write the
final evidence because the container lacked the documentation mount. Its
progress summaries are retained separately. Only artifact handling was fixed;
seeds, limits, methods, acceptance criteria and physics were unchanged. The full
protocol was rerun to retain auditable outcomes. Timings from the first pass are
not silently combined with the complete run or selected for better performance.

A second pass was interrupted when the shared backend container was recreated
(exit 137; its temporary files disappeared). The final complete pass used a
separate container pinned to image
`sha256:182ab99c4db3976bb7fa794885ae5eaf7b1d17d5afbf7acf6bf3fcd0e729d053`,
with this worktree mounted and evidence written to the host-owned directory.
Future repeat runs should use an isolated container too.

## Hardware and credit boundary

No new job, preparation quote or credit purchase was requested. New consumption
is **0 Spark / 0 paid credits**. A read-only account check observed 21 Spark and
0 Full. The previously submitted stage-5 job
`dd340d59-3d25-43e0-8268-df5607b84e3a` was still Queued at that observation; it was
not resubmitted. This old job is unrelated to C2 and supplies no C2 evidence.

## Next justified work

Reuse identical nominal/dynamics calculations where the input identity can be
preserved; profile the remaining nominal search and copy overhead. Keep this
cache opt-in until worker lifetime and memory ownership are designed for the
calling service. Do not route current QAOA into the lazy selector: it needs the
hidden full outcome table. Neither the lazy result nor the cache result passes
a quantum hardware gate. A broader quantum research hypothesis remains open,
but no runtime quantum promotion or new hardware spending is justified here.

## Verification

- Frozen test baseline: `f2bdf24`, 43 new checks, unchanged since that commit.
- Full backend: **1,940 passed**; backend/shared Ruff passed.
- Data ingestion: **218 passed**; Ruff passed.
- Optional local quantum: **94 passed**; no provider jobs submitted by tests.
- Total: **2,252 passed**. Existing Starlette/AnyIO and Qiskit deprecations remain.
- Frontend: clean locked `npm ci`, TypeScript and Vite build passed in an isolated
  container; existing bundle-size warning remains. No frontend tests added.
- Source hashes match the retained measurement. C1 report/evidence unchanged.
- The pre-existing H6 notebook edit is byte-identical to its pre-task backup and
  excluded from commits. No main/tahakrcc merge.
