# C2: physics reuse and lazy evaluation pre-registration

This protocol is recorded before running C2. C1 evidence and NO-GO conclusions
remain unchanged. C2 is a classical bottleneck experiment, not a new quantum
variant or a flight-certification exercise.

## Fixed scope and budgets

- Fresh seeds 811, 812, 813, 814; two landers with the existing four R13 options.
- 24 full R6 draws per plan, common seed across all plans, unchanged 0.5 s
  integration, guidance, reserve, touchdown, slope and unknown-terrain rules.
- Fixed worst-case combined miss limits 500 and 1500 m, chosen before outcomes.
  Unlike the original generator, limits must not depend on precomputed outcomes.
- Synthetic terrain retains existing R13 assumptions; not real mission data.
- At most eight unique plan batches per method; all 16 pairs remain eligible.
- Compare eager native MILP with caching disabled, eager MILP with cold reuse,
  eager MILP on an explicitly warm repeat, exact lazy branch-and-bound with cold
  reuse, and the same lazy method on a warm repeat. No cross-method cache sharing.
  Each warm method pays and reports its own cold pass separately. Rotate cold
  method order by seed. All methods receive identical inputs, limits and draws.
- Record input construction, nominal planning, perturbed dynamics, cache
  lookup/copy overhead, remaining R6 classification/aggregation, candidate
  scoring and total wall time separately. No profiler overhead in timed runs.
- Audit every method against full native enumeration and MILP afterwards;
  audit results are never available to lazy selection. Retain unsupported or
  failed cases. Store source hashes, inputs, plan outcomes and call counters.

## Safe reuse and lazy bounds

Only identical deterministic nominal/dynamics calls may be cached. Include all
arguments and resolved vehicle values; copy outputs, bound entries, do not cache
exceptions. Process-local lifetime assumes code/constants are immutable. Site
hazards, reserve tests, uncertainty aggregation and all safety rules still run.
No persistent reuse across software revisions is introduced.

Each known plan supplies its full 24 outcomes. An unknown plan has at most 24
successes and nonnegative miss. For a pair, joint successes are at most the
minimum known success count (24 if neither known); worst combined miss is at
least the largest known individual maximum miss (zero if neither known).
Therefore `success_upper - miss_lower / 1000` is an admissible objective upper
bound for the existing default R13 objective. Discard a pair only if its miss
lower bound exceeds the fixed limit or its objective upper bound cannot beat a
fully validated incumbent. Never present budget exhaustion as proven optimal.

## Acceptance and stop rules

1. Every cached result must reproduce the uncached safety outcomes and numeric
   values. Any mismatch stops promotion; never loosen tolerances to accept reuse.
2. Every completed lazy search must match native exact optimal objective or
   infeasibility. MILP comparisons retain native numerical tolerances.
3. Report cold and warm savings separately. A useful classical optimization
   requires >=20% mean end-to-end cold saving, no quality loss on any case and
   explicit accounting of repeats. Savings from caching are classical savings.
4. Lazy evaluation is useful here only if it saves >=20% real dynamics misses
   against **cold cached** eager MILP, with no quality loss and no mean total
   time increase. Comparing only against uncached MILP is insufficient.
5. Before any quantum comparison, inspect whether existing QAOA coefficients
   can be constructed without accessing hidden full R6 outcomes. If not, record
   the dependency and stop: no invented proxy, new variant, free precomputed
   score table, or weak classical baseline. Hardware is not justified by cache
   savings. No new hardware job is part of C2; estimated new consumption is zero.

This bounded test cannot prove all quantum methods useless, nor establish that
24 scenario draws certify rare-event probabilities.
