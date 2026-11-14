# H2 — Audited QUBO and compact-register QAOA

H2 implements a small, reproducible quantum **simulation** experiment on H1's static mission-option problem. It builds an exact rational QUBO, audits its ground states against the classical validator, and simulates a six-qubit QAOA circuit. No provider API, hardware execution, paid job or classical repair is used.

Implementation: `backend/app/hybrid_quantum.py`. Notebook: `notebooks/H2_quantum_mapping.ipynb`. Continuing branch: `codex/quantum-hybrid`; only that branch is pushed. Skeleton commit: `00c6801`; frozen tests: `7c26cfc` (48 tests). H0/H1 source and tests are unchanged.

## Method and sources

The cost-phase / X-mixer construction follows [Farhi, Goldstone and Gutmann, A Quantum Approximate Optimization Algorithm (2014)](https://arxiv.org/abs/1411.4028). [IBM's QAOAAnsatz reference](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qiskit.circuit.library.QAOAAnsatz) describes the cost operator, layer count, initial Hadamard state and X-rotation mixer. Both were checked on 2026-11-14. H2 implements these mathematical operations directly with NumPy; it does not call Qiskit or IBM services.

The compact register, bounded compiler, penalty bound, numeric normalization and optimizer budget below are explicit project implementation choices. They are not claims taken from those sources about mission planning performance.

## One-hot QUBO

There is one binary variable `x_i` per candidate. Let `s_i` be its H0 weighted normalized score. Define `A = 1 + sum(abs(s_i))` and minimize:

```text
E(x) = -sum(s_i * x_i)
       + A * sum_m (sum_{i in mission m} x_i - 1)^2
       + A * sum_{i forbidden} x_i
       + A * sum_{(i,j) conflicting} x_i*x_j
```

`x_i^2 = x_i` gives the diagonal and quadratic coefficients; the constant offset is preserved. Coefficients are rational strings, and `qubo_energy` uses Fraction arithmetic. A violated constraint contributes at least A, exceeding the entire objective range over binary assignments. Thus, when a feasible assignment exists, an invalid assignment cannot beat the feasible optimum.

Single-option and pair checks use the original H1 validator, ignoring only the expected missing-mission selection messages in these partial checks. The compiler then proves the unary/pairwise representation matches original feasibility on **every exactly-one assignment**. It refuses a mismatch rather than approximating a higher-order resource constraint.

The separate audit examines all binary assignments (including invalid mission counts), validates every exactly-one choice against H1, compares each feasible energy with negative original score, and compares the full ground-state set with the constrained optimum set. Stale or altered QUBO models are rejected. On the example: 12 variables, 4096 bit strings, 64 one-choice assignments, 24 feasible assignments, 6 optimum assignments, minimum energy `-13/5`, penalty `53/5`.

### Explicit compiler limits

- Maximum 12 one-hot variables, enforced before enumeration. Missing data and events fail explicitly.
- Static rules must be exactly unary/pairwise on the bounded choice domain. General energy/capacity inequalities may require slack variables or a different encoding, which this H2 implementation does not invent.
- For example, capacity two with three overlapping unit demands is unsupported: every pair fits but the triple does not. Capacity one in the teaching example is representable.
- An infeasible problem can be compiled/audited, but `run_qaoa` refuses to launch an optimization experiment for it.
- Candidate generation and physical feasibility remain classical. This bounded exhaustive audit is not a scalable compiler or a performance advantage.

## Compact quantum representation

The same choice domain is encoded in binary mission registers. Four options use two bits; three missions use six qubits and 64 basis states. Bit indexing is little-endian: the lowest register bits belong to the first mission in mission-ID order; candidate order is option-ID order. Every compact state represents one option per mission, so no auxiliary or selection-violation states are needed.

This is **not a six-variable quadratic QUBO**. The cost Hamiltonian is the one-hot QUBO restricted to the compact choice domain. Its Pauli-Z expansion can have higher-order interactions; the current example has maximum locality four. The compact code supports power-of-two option counts only and 1–8 qubits, with a default cap of six.

For numerical tuning the diagonal energy is centered by its mean and divided by the largest absolute centered value (or one for a constant diagonal). `energy_shift` and `energy_scale` preserve reconstruction. A Walsh-Hadamard transform computes the Z expansion. Terms below `1e-12` are omitted only in the gate estimate/expansion; statevector evolution uses the full normalized diagonal. Reconstruction tests use absolute tolerance `1e-12`.

Starting from uniform `|+>`, each layer applies the diagonal phases `exp(-i gamma C)` followed by commuting `exp(-i beta X)` rotations on every qubit. A separate dense-matrix test constructs the 2-qubit unitary independently, alongside a known 1-qubit rotation, global-phase invariance and norm checks.

### Gate accounting

For each nonconstant k-body Z term, the simple parity-chain construction uses one RZ and `2*(k-1)` CX gates. Each layer also uses one RX per qubit. Initial preparation requires one Hadamard per qubit; measurement and global phases are not included in the reported RX/RZ/CX totals.

The teaching example uses **30 CX, 15 RZ and 6 RX per layer**, plus the initial six Hadamards. These are logical all-to-all estimates, not device-transpiled gate counts, native-gate counts, noisy-circuit depth or money estimates. Connectivity/routing can change the actual cost. Fewer qubits does not automatically mean a cheaper physical circuit.

## Tuning, sampling and acceptance

Default: depth 1, 160 objective evaluations, 512 final shots, seed 17, at most six qubits. Supported hard limits are depth 1–3, evaluations 1–512, final shots 1–8192 and seed 0–2^32−1. Invalid budgets are rejected.

The first objective evaluation is the uniform distribution. Roughly half the fixed budget explores seeded random angles; the remaining evaluations perturb one incumbent coordinate, retaining improvements in expected energy. Optimization and final sampling use separate child random streams. This is a bounded simple classical outer loop, not a globally optimal parameter search.

Each objective evaluation uses an **exact simulator expectation over the full statevector**. The final 512 shots are samples after tuning; they do not include hardware measurement effort for parameter optimization. A hardware adaptation needs a separate measurement, queue, tuning and monetary budget.

Every one of the 64 decoded outcomes is checked using H1. Full probability and count records include zero-count and infeasible outcomes. `best_sampled_plan` must actually occur in the sampled counts and pass H1. Raw feasible/optimal probabilities, observed finite-shot rates, uniform baselines and timing remain separate. There is no repair, hidden post-selection or quantum advantage claim.

Before the initial experiment we set a bounded simulator acceptance condition: across seeds 17, 23 and 42, mean feasible and optimal probabilities exceed uniform and every run samples a feasible plan. This criterion establishes a working small example only; it is not a held-out generalization benchmark or a claim that QAOA beats classical optimization.

## Measured teaching-example results

All runs used 160 simulator objective evaluations and 512 final shots. The uniform baseline has feasible probability 0.375 and optimal probability 0.09375.

| Layers | Seed | Feasible probability | Optimal probability | Observed optimal rate |
|---|---:|---:|---:|---:|
| 1 | 17 | 0.805944 | 0.205210 | 0.1953125 |
| 1 | 23 | 0.795934 | 0.202351 | 0.2187500 |
| 1 | 42 | 0.811094 | 0.198733 | 0.1953125 |
| 2 | 17 | 0.955768 | 0.250328 | 0.2363281 |
| 2 | 23 | 0.897702 | 0.234190 | 0.2500000 |
| 2 | 42 | 0.821416 | 0.209280 | 0.2050781 |

Both configurations met the declared simulator acceptance condition. Mean optimum probability is about 20.21% for one layer and 23.13% for two layers. The default is one layer because it halves these logical gate counts while retaining improved per-sample probability in this example. This is a measured tradeoff among the tested settings, not proof of the lowest possible cost or best achievable probability.

The exact classical solver already solves the problem cheaply. At 512 trials even uniform sampling is overwhelmingly likely to find at least one optimum: its miss probability is `(1 - 6/64)^512`. Improved per-sample probability alone is **not** evidence of an end-to-end speedup or a useful monetary advantage. Full mapping/audit and simulator tuning time are included in each reported run time; file I/O and rendering are excluded.

## Jupyter and dependencies

Use the existing **Lunar H0 (Python 3.12)** kernel. NumPy 2.5.4 is added only to the optional notebook requirements, matching the already installed backend version. No service dependency or lock changes are needed.

```sh
python -m pip install -r notebooks/requirements.txt
```

On the development machine:

http://localhost:8888/lab/tree/quantum-hybrid-workspace/notebooks/H2_quantum_mapping.ipynb

The 10 code cells cover environment, classical reference, QUBO audit, compact representation, a low-depth run, raw outcomes, the six-run comparison, gate accounting, unsupported inputs and evidence export. The notebook runs no external jobs.

## Verification

- 48 new tests failed only with `NotImplementedError` at frozen test commit `7c26cfc`, then passed with implementation.
- Full backend suite: 643 passed. Ingestion suite: 112 passed during H1; H2 changes no ingestion code/shared contracts. Frontend build passed during H1; H2 changes no frontend code.
- Backend Ruff and new-file formatting checks passed. No frozen H0/H1/H2 tests were changed during implementation.
- H2 notebook: 10 code cells executed with zero errors on the local kernel.
- No provider credential was used and zero hardware jobs were submitted.

```sh
docker exec nasa-task4-backend-1 pytest -q
docker exec nasa-task4-backend-1 ruff check app tests /workspace/shared
git diff 7c26cfc HEAD -- backend/tests/test_hybrid_quantum.py
git diff 6bf9137 HEAD -- backend/app/hybrid_planning.py backend/tests/test_hybrid_planning.py
```

Broader resource integration, held-out problem families, noise/device measurements and matched classical/hybrid compute budgets remain later H stages.
