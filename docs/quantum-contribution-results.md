# C1 contribution experiment: no demonstrated operational benefit

The frozen C1 experiment completed on 2026-11-15 on
`codex/unified-quantum-stage1`. No application behavior was changed. No new
hardware job was submitted and zero Spark credits were spent.

Protocol: [pre-registered design](quantum-contribution-protocol.md) and
[machine-readable configuration](verification/quantum-contribution-c1-protocol.json).
Results: [compact evidence](verification/quantum-contribution-c1-2026-11-15.json).
This is a local contribution preflight, not completion of the broad 6A-H plan.

## What ran

24 fresh synthetic native instances (6 development, 12 held-out, 6 scaling),
three solver seeds, seven methods: 504 method attempts, including explicitly
unsupported attempts. 126 attempts actually executed local quantum simulation.
Methods were native HiGHS MILP, multistart coordinate search, simulated annealing,
uniform random, ordinary QAOA, warm-start QAOA and a conflict-preserving QAOA.
Native independent validators and candidate ranking were shared, with no repair.
The MILP optimum was never passed to any sampler. Each sampled method received
the same deterministic classical starting assignment and paid for preparing it.

The physics preparation used 1,536 perturbed R6 descent draws across eight robust
instances (8 candidate plans and 24 draws per instance). These are common
upstream inputs, not quantum evaluations or adaptive candidate validations.
The three families cover station/time allocation, science scheduling with energy
and precedence rules, and robust two-lander plan selection. They do not constitute
a full combined target/time/relay/resource mission replay.

## Held-out evidence

Each application has 4 instances x 3 seeds = 12 paired runs. Times below are
mean successful method times on this machine, excluding common input preparation.
They are descriptive measurements, not statistical performance guarantees.

| Application | Method | Quality outcome | Mean method time |
|---|---|---|---:|
| Communication | MILP | Matches best classical score, 12/12 | 0.496 ms |
| Communication | Ordinary QAOA | Matches, 12/12 | 2.524 ms |
| Communication | Warm QAOA | Matches, 12/12 | 2.174 ms |
| Communication | Conflict QAOA | Matches, 12/12 | 1.128 ms |
| Science scheduling | MILP / multistart / annealing | Matches, 12/12 each | 0.557 / 7.998 / 9.737 ms |
| Science scheduling | Ordinary QAOA | Matches 6; 6 unsupported | 1.896 ms on supported runs |
| Science scheduling | Warm QAOA | Matches 3; 3 inferior; 6 unsupported | 1.513 ms on supported runs |
| Robust plans | MILP | Within 5.28e-9 objective units of best classical | 1.633 ms |
| Robust plans | Random | Exact-score match, 12/12 | 1.640 ms |
| Robust plans | Ordinary QAOA | Exact-score match, 12/12 | 1.757 ms |
| Robust plans | Warm QAOA | Exact-score match, 12/12 | 1.495 ms |

All communication samplers ended with their shared classical starting assignment
on all 12 held-out runs. The conflict-preserving variant spent one native check,
but so did MILP. Thus the better restricted sampler did not improve the plan or
reduce checks relative to the strong reference. The initial zero-angle circuit
can remain optimal for the tuning objective; an unchanged warm start is labelled
`common_warm_start`, not credited as a quantum discovery.

Robust warm-QAOA was slightly faster than random in this particular local timing
sample. It did not reduce checks to the reference-quality plan (ratio 1.0) or
improve final quality over classical search. The MILP near-ties above arise from
floating-point objective tolerances versus exact decimal-rational ranking. Other
classical methods also obtain the higher exact score. Differences of order 1e-9
are not evidence of useful quantum or physical improvement. The pre-registered
strict comparison was retained; no tolerance was changed after seeing results.

Ordinary and warm QAOA receive NO-GO for communication and robust selection.
Conflict QAOA receives NO-GO for communication. Science-wide evidence is
inconclusive because half the ordinary/warm runs exceed the 12-qubit cap;
conflict QAOA explicitly rejects energy/precedence and auxiliary-column models.
Unsupported cases remain in the denominator. No local gate recommends hardware.

A separate audit rebuilt every native problem from the saved inputs, verified
its fingerprint, and reran the native MILP without rerunning physics. All 24
returned `optimal` under the existing native solver's numerical tolerances and
reproduced the recorded MILP objectives. Communication and robust encodings are
declared exact by their builders; science energy/precedence QUBOs are heuristic.
This distinction is retained in the evidence. A good surrogate energy is never
substituted for native validity or mission quality.

Scaling communication cases have 144 and 181 variables; scaling science cases
have 27 and 43. Full native classical methods still run. Quantum methods report
unsupported rather than quietly solving an easier projection. These are limits
of C1, not a claim that all larger quantum methods are impossible.

## Actual bottleneck and the untested hypothesis

Held-out robust input generation takes about 13.5-14.2 seconds per instance.
Optimization then takes milliseconds. The existing R6 adapter simulates every
candidate before constructing its table. Changing its sampler therefore saves
zero online physics calls: there are no online physics calls in this experiment.
The proposed 20% saving in expensive adaptive physics evaluations is **not tested**
by this oracle, and no such saving is claimed. C1 rejects promotion of these
methods to the current operational path; it does not disprove every possible
quantum algorithm or future lazy-physics integration.

The next product performance work should address reuse/batching of physics and
the validity of uncertainty models. A new quantum experiment would need a real
measured adaptive-physics bottleneck and new held-out data. Increasing qubits or
spending credits on the present easy/precomputed cases is not justified by C1.

## Reproduce and audit

Inside the existing backend environment, run each split sequentially, without
concurrent benchmarks, to avoid contaminating timing:

```bash
python -m app.run_quantum_contribution --split dev --output /tmp/contribution-c1-dev.json
python -m app.run_quantum_contribution --split held_out --output /tmp/contribution-c1-held-out.json
python -m app.run_quantum_contribution --split scaling --output /tmp/contribution-c1-scaling.json
```

The full reports contain native inputs, proposal counts, independent validation
traces, time/budget counters, protocol and source hashes. The original full
reports are preserved outside Git at `~/.local/share/lunar-contribution-c1/`;
their SHA-256 hashes are in the compact evidence. Timing fields vary across runs.
The report records statevector basis work; this is not a hardware query count.
Sparse Ising coefficients are constructed algebraically, but the local simulator
still materializes an exponential state. No speed extrapolation is warranted.

Frozen tests: `f063105` (37 backend checks), `b6e533b` (3 independent Qiskit gate
equivalence checks). Tests cover unsupported constraints, seeded sampling,
deadline rejection, real validator-call limits, feasible-state preservation,
matching warm mixers, and no false GO from missing or worse results. Qiskit
verification is local and uses no credentials. Existing H6 notebook edits remain
outside this change.

## Final verification

1,897 backend tests, 218 ingestion tests and 94 optional quantum tests passed:
2,209 total, including 40 new checks. Backend/shared and ingestion Ruff checks
passed, as did the new optional circuit tests' Ruff check. TypeScript and the
Vite production build passed; no frontend tests or screenshots were used.
The four read-only code cells in
[the evidence notebook](../notebooks/Quantum_contribution_C1.ipynb) executed and
the notebook schema validated. Both frozen test files remained unchanged from
their test commits. The user's H6 notebook matched its pre-task backup byte for
byte. Existing Starlette/AnyIO and Qiskit deprecations and Vite large-chunk
warnings remain; none caused a failed check.
