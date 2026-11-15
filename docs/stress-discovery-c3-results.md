# C3: quantum contribution to offline stress discovery

**Decision: NO-GO for this six-qubit generator/configuration.** No device job was
submitted, no credit was consumed, and operational planning/API/UI were unchanged.
The experiment is research tooling; it is not enabled in the flight/recovery path.
Existing C1 and C2 findings remain intact.

## What was measured

The [preregistered protocol](stress-discovery-c3-protocol.md) was committed in
`f4ae435` before candidate outcomes. Acceptance tests were frozen in `4980b1e`;
implementation is `279a7c2`. A runner portability test was added in `6e6865f` and
fixed in `06cf5c8`: the locked image has no Git executable, so the host supplies
the source revision explicitly. No frozen test was changed.

Six new synthetic surface event-chain templates have 64 variants each. Their
derivatives remain within development, validation or test; two roots per split.
Five seeds, seven methods, two budget types give **420 search runs per runtime**.
This is 384 unique perturbed scenarios, not thousands of independent scenarios.
Every run pays for a successful no-fault qualification and its initial examples.
All searches finish before exhaustive reference outcomes are computed. No
scenario outcome table is given to a generator.

The objective is discovery of distinct task shortfalls. A shortfall is often a
correct decision to defer work after a fault, **not a software bug or vehicle
crash**. Cluster keys exclude timing, severity and sample identity. None of the
384 exhaustive scenarios breached the modeled fuel, energy or thermal limits.
Exhaustive shortfall prevalence ranges from 11/64 to 33/64; these are not rare-event
probabilities and do not represent real spacecraft event frequencies.

## Initial local preflight

Environment: macOS ARM64, Python 3.12.4, NumPy 2.5.4, SciPy 1.18.1. Recorded
source revision `279a7c28f7243d8485ca9459d610232792ade73c`; all source hashes are in
the machine-readable evidence. Final-test means below use two roots × five seeds.

| Method | Clusters, 16 replay calls | Total time at 16 calls | Clusters, 0.35 s limit |
| --- | ---: | ---: | ---: |
| Random without replacement | 1.1 | 41.4 ms | 1.5 |
| Scrambled Sobol | 1.3 | 40.2 ms | 1.5 |
| Cross-entropy | 0.8 | 41.0 ms | 1.5 |
| Annealing with restarts | 0.9 | 39.0 ms | 1.5 |
| Classical Hamming-kernel generator | 1.0 | 42.5 ms | 1.5 |
| QCBM, local ideal simulation | 1.0 | 101.4 ms | 1.2 |
| QCBM distribution, classical inverse-CDF sampling | 1.0 | 100.5 ms | 1.4 |

The 16-call budget includes qualification, leaving 15 perturbed evaluations.
At equal time, all five classical methods exhaust the 64-state final-test domain
in about 0.17 s; QCBM averages 46.4 total calls before the 0.35 s deadline. This
small replay is cheap, so generator training cannot be amortized here.

QCBM's mean final-test difference from the per-case best classical envelope is
**−0.4 clusters**, hierarchical paired bootstrap 95% interval **[−0.9, 0.0]**, at
equal calls; **−0.3 [−0.8, 0.0]** at equal time. The preregistered gate requires
at least +1 and a positive lower bound in validation and final test under both
budgets. Neither passes. The intervals do not establish universal inferiority.
Two roots are too few for broad generalization; seeds are repeated searches,
not new independent physical models.

All 30 call-budget QCBM/inverse-CDF pairs produce identical candidate sequences.
The time-budget sequences can have different lengths due to scheduling and
deadline cutoffs. An exactly reproducible classical distribution at six qubits
does not show a need for quantum hardware, even if a sample happens to be useful.

Cost: 16,156 replay calls including repeated qualification, development,
validation, final tests and a separate 390-call exhaustive audit. Search took
63.999 s, exhaustive audit 1.153 s, and campaign including statistics 65.320 s.
15,319 eligible candidate observations matched the later exhaustive reference.
27 late results were paid for but excluded; maximum overrun was 0.021 s. Fits and
replays are cooperative, not hard-preempted at the deadline.

For the QCBM final-test call-budget run, average time was 37.5 ms replay,
49.5 ms classical training overhead, 9.7 ms statevector simulation, 3.5 ms input
preparation, 0.2 ms sampling, 0.4 ms postprocessing, plus measured bookkeeping.
The seven-method campaign performed 52,715 statevector evaluations, including
the classical emulation ablation. These are local calculations, not QPU jobs.
Exact statevector training is optimistic: physical training would add circuit
execution, noise, queue and possibly monetary costs.

## Locked backend reproduction and verification

The second run used the existing locked image (Linux ARM64, Python 3.12.12,
NumPy 2.5.4, SciPy 1.16.3), source `06cf5c8`, after the regression tests had
finished to avoid competing benchmark/test workloads. No setting was tuned and
the earlier run is retained, not replaced. The core experiment and replay source
hashes are identical in both runs. Call-budget aggregate cluster means and paired
intervals reproduced, as did all classical call-budget candidate sequences.
Six of 30 QCBM sequences (and their matching inverse-CDF ablations) differed
between SciPy versions; exact cross-version optimizer reproducibility is not
claimed. Exhaustive replay outcomes are identical. Time-budget cutoffs also vary.

| Final-test result | QCBM | Sobol | Random | CE / annealing / kernel |
| --- | ---: | ---: | ---: | ---: |
| Clusters at 16 calls | 1.0 | 1.3 | 1.1 | 0.8 / 0.9 / 1.0 |
| Total time at 16 calls | 108.0 ms | 42.7 ms | 43.7 ms | 43.1 / 42.9 / 45.5 ms |
| Clusters within 0.35 s | 1.1 | 1.5 | 1.5 | 1.5 / 1.5 / 1.5 |
| Calls within time budget, including qualification | 45.0 | 65 | 65 | 65 / 65 / 65 |

The locked run also returns **NO-GO**. Final-test paired differences are
−0.4 [−0.9, 0.0] clusters under both budgets. Classical distribution emulation
again matches all call-budget QCBM candidate sequences; its time-budget mean is
1.1 clusters. All qualification cases passed. The independent audit contains the
same 384 outcomes and zero resource-limit breaches.

Locked-run cost: 16,050 replay calls, 66.276 s search, 1.192 s exhaustive audit,
67.662 s total including statistics. All 15,224 eligible observations match the
post-hoc reference. Sixteen late results were charged but excluded; maximum
overrun 0.020 s. Local statevector evaluations: 51,545. Across both environments:
840 search runs, 32,206 replay calls and about 133 s of campaign time, but still
only six template roots and 384 distinct perturbed scenarios.

Verification: **2,380 passing tests** (2,064 full backend + 3 added CLI portability
tests + 218 ingestion + 95 optional quantum tests). Ruff passed for backend,
shared and ingestion code. Clean frontend installation reported zero package
vulnerabilities; TypeScript and Vite build passed. Existing Starlette/Qiskit
deprecation and Vite large-chunk warnings remain. No frontend tests were run.
The QCBM probabilities independently match Qiskit's six-qubit circuit for three
seeded angle vectors to 1e-12 tolerance. Both frozen test files and the later CLI
test remain unchanged since their respective test commits. The user's H6 notebook
edit is excluded and preserved byte-for-byte.

[Compact machine-readable evidence](verification/stress-discovery-c3-2026-11-15.json)
contains both environments, every selected index and score, all exhaustive
outcomes, training observation ledgers, component timing, confidence intervals,
source hashes, and hashes of the full local artifacts. Component costs separately
record preparation, classical training, statevector simulation, sampling,
postprocessing, replay and bookkeeping. There is no hidden validation/training
campaign, device execution or additional oracle table used during selection.

## Interpretation and next decision

The useful output is an honest, replay-budget-aware stress-search harness and
an exhaustive reference, not a new quantum production feature. This experiment
does not justify spending device credits, increasing qubits or connecting QCBM
to the live planner. The best next operational choice here is a classical search.

This result is limited to bounded surface accounting. It says nothing conclusive
about expensive descent simulation, calibrated rare-event distributions, large
fault graphs or other quantum algorithms. A future study would first need an
independently specified expensive black-box workload, many untouched template
families, strong classical stress search and a measured bottleneck. Merely making
the example larger would not establish quantum value. Reusing these now-exposed
six roots as a new blind test would be invalid.

## Reproduction

From the repository root, with the locked backend image built locally:

```sh
docker build -f backend/Dockerfile -t lunar-c3-local .
mkdir -p /tmp/lunar-c3-output
docker run --rm -v "$PWD:/workspace:ro" -v /tmp/lunar-c3-output:/evidence \
  lunar-c3-local python -m app.run_stress_discovery \
  --revision "$(git rev-parse HEAD)" --output /evidence/results.json
```

This command does not start shared services, download mission data or contact
a quantum provider. Dependencies are the existing locked backend requirements.
Call-budget outcomes are seed-reproducible within the recorded numerical
environment; wall-time cutoffs and optimizer behavior across SciPy versions need
not reproduce exactly. All timing includes fitting and data preparation. Raw
full results stay outside Git; the checked-in compact evidence retains every
selected index, root reference and run ledger with raw-artifact SHA256.
