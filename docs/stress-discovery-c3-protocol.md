# C3: preregistered offline stress-discovery preflight

Frozen before observing candidate outcomes. This is a narrow, synthetic surface
operations test of a possible quantum contribution, not a promise of advantage
or a flight safety evaluation. C1/C2 NO-GO evidence remains unchanged.

## Research and hypothesis

Adaptive stress testing searches a simulator for failures instead of treating a
successful optimizer call as a result. The NASA/Stanford trajectory study compares
against Monte Carlo and cross-entropy search:
https://arxiv.org/abs/2011.02559 . Our model has no calibrated event probabilities;
we therefore measure discovery, never real accident likelihood.

Generator-enhanced optimization learns from evaluated samples and proposes new
ones without a polynomial cost oracle. The 2024 GEO paper primarily demonstrates
**classical tensor-network** models, not hardware advantage:
https://www.nature.com/articles/s41467-024-46959-5 . QCBM generalization is a
research hypothesis supported by specific synthetic experiments, not a guarantee
for this application: https://arxiv.org/abs/2207.13645 .

Hypothesis: a six-qubit Born sampler trained only on already evaluated scenarios
discovers more distinct mission shortfalls under the same replay-call budget AND
under the same total wall-time budget than strong classical searches.

## Data and boundaries

Six new event-chain roots, 64 bit-encoded variants per root:

| Split | Root templates |
| --- | --- |
| Development | shade then link loss/restoration; heating then radio loss/restoration |
| Validation | energy loss then camera outage; link outage then heating |
| Final test | heater outage then cold shaded interval; radio outage then energy loss |

All variants of a root remain in its split. No model, elite, result or tuned
parameter transfers across runs or roots. Settings below are fixed, with no
outcome-based tuning in this preflight. Development and validation are still
charged. Existing seven public resilience demos are not blind tests. These new
roots share the same declared surface accounting equations, so this is only
template-held-out testing, not independent physics or a large external dataset.
Two test roots are insufficient for a broad advantage claim even on a positive
screen. Passing would motivate a new, larger preregistered experiment.

Bit order (LSB first): onset 2/8 s; second-fault gap 2/6 s; restoration duration
6/18 s; severity low/high; notice delay 0/3 s; restoration offset 0/5 s. Events
are bounded, begin after MET zero, and have explicit restoration. All variants
share a no-fault baseline; baseline must complete before any discoveries count.
No initially impossible case, arbitrary invalid input or repeated seed is a new
failure. The domain is deliberately small enough for an exhaustive audit.

## Outcomes, deduplication and budgets

Failure/shortfall = completed baseline followed by non-completed perturbed replay.
This includes safe task deferral/refusal; it is **not** synonymous with a crash
or a controller bug. Cluster key = final status, sorted terminal reason codes,
sorted unfinished task IDs. Times, sample IDs and fault magnitudes are excluded.
Unique clusters per run is primary; unique failing inputs, first failure call,
normalized unfinished priority, accepted action coverage, and actual resource
limit breaches are secondary. No full outcome table or surrogate labels exist
before selection. Exhaustive 64-case reference is computed AFTER all searches,
never passed to a sampler; report its independent cost.

Five seeds: 11,29,47,71,101. Seven methods: random without replacement, scrambled
Sobol, smoothed Bernoulli cross-entropy, Metropolis single-bit annealing with
restarts, classical elite Hamming-kernel generator, local QCBM, and its exact
classical inverse-CDF sampling ablation. Shared first four Sobol candidates per
seed, then adaptive decisions from each method's own history. Every method pays
for its baseline and initial candidates; no shared replay cache.

Replay budget: 16 total calls including baseline. Separate time experiment:
0.35 s total per run, up to baseline + all 64 variants. Include data preparation,
qualification, fitting, local statevector simulation, sampling, dedup and replay.
Do not count a candidate whose evaluation/summary finishes after the deadline;
charge its time and call nonetheless. Cooperative wall-time checks cannot
preempt a single fit/replay; report overruns explicitly. Shuffle method order
deterministically within each root/seed to reduce warm-up/order confounding.

QCBM: RY(6), CX nearest-neighbor chain(5), RY(6), 12 parameters; retrain every four
observations, COBYLA max64 objective evaluations; top quartile evaluated elites,
5% uniform smoothing; 128 simulated measurement draws per proposal, first unseen
draw wins, uniform unseen fallback recorded. Six logical qubits do not imply
six physical qubits or a hardware quote. Exact statevector training is optimistic
and classically cheap; training on hardware would require additional circuits.
The inverse-CDF ablation receives the same fitted probabilities and pays the same
training cost. This checks whether sampling requires hardware; it is not an
independent hardware speedup experiment. No provider package, secret or API used.

## Decision rule

For each seed/root compare QCBM cluster count to the best of the five classical
methods on that paired case (a conservative classical envelope). Separately for
each split and budget report mean difference and hierarchical paired bootstrap
95% interval, resampling roots then seeds, 2000 draws, seed20261115. Require
mean gain >=1 unique cluster and lower bound >0 on BOTH validation and final test
under BOTH budgets before considering a larger study. No post-hoc metric choice,
no seed selection, no tuning against final-test outcomes. Repeated samples and
training observations do not count as new independent families.

A failed gate is NO-GO for this candidate/configuration, not proof that every
quantum algorithm is useless. Even a passed gate is only an exploratory signal:
classical emulation, two-family uncertainty and uncalibrated physics prevent a
practical quantum advantage claim. No automatic hardware follow-up; explicit
user approval required before any credit consumption. No merge or deployment.
