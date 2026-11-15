# Quantum contribution experiment C1

This is a falsifiable preflight experiment, not an integration of every planned
6A-H feature. Same branch; no main/tahakrcc merge; no hardware credit spending.

## Frozen question and stop rules

Does changing only the candidate generator yield better independently validated
native plans than strong classical methods under declared budgets? The desired
20% reduction in expensive physics evaluations is a separate hypothesis. Native
R13 robust-plan builders already perform R6 physics before optimization; native
assignment validators consume these records. Count them as assignment checks,
never as new physics simulations. If the dominant cost is upstream, report this
structural barrier instead of fabricating an expensive oracle or sleep delay.

Seven methods: native HiGHS MILP, multistart coordinate QUBO search, simulated
annealing, uniform random, ordinary QAOA, warm-start QAOA, and conflict-preserving
QAOA. The latter is restricted to certified binary at-most-one native rows;
unsupported energy, precedence, capacity>1 or auxiliary-column rules are rejected.
All sampled methods use the same deterministic QUBO warm start, candidate ranking,
independent validator and cache policy; no repair. MILP solves native constraints
directly. Its solution and score are never passed to another method.

Fresh seeds: development 701,702; held-out 711,712,713,714; scaling 721,722.
Solver seeds 17,23,42. Native communication and science instances vary with split;
R6 robust instances have two landers/four plans/24 uncertainty draws. Scaling
communication: 8 missions, 3 requests, 3 stations, 12 slots; science: 8 activities,
12 slots, 2 precedence pairs. These are synthetic cases, not live mission inputs.

Per-method limits: 3 s wall clock, 64 unique validator calls, 2048 classical QUBO
point evaluations, 256 draws, 24 quantum parameter evaluations, depth 1, at most
12 logical qubits. Exact statevector expectations are local simulator information,
not free device measurements. Record basis-state work separately; equal shot
counts are not equal computational cost. Preparation and every parameter attempt
count toward wall time. Solver calls cannot be forcibly interrupted; any result
arriving after the deadline is marked late and excluded from accepted outcomes.

Primary gate (held-out only, independently per application and quantum variant):
match the best eligible classical score on every paired case, mean assignment
checks to that score <=80% of the best classical reference, and mean total time
no greater. Missing or unsupported pairs cannot be dropped to manufacture GO.
All methods' full traces are retained. Development never changes this protocol;
any change requires a new experiment/version and fresh held-out seeds. Timing
is descriptive on this machine; no significance or universal advantage claims.
Passing the local gate only authorizes proposing a hardware experiment, not
submitting it. The expensive-physics hypothesis remains untested by this oracle.

## Algorithms and provenance

Sparse cost coefficients use x=(1-Z)/2 directly. Only the verification simulator
materializes basis energies. Warm QAOA uses product probabilities 0.2/0.8 around
the common classical seed and a matching rotated single-qubit mixer, following
the construction of Egger et al., https://arxiv.org/abs/2009.10095 (our seed is
greedy, not a relaxation and inherits no relaxation guarantee). Conflict QAOA
uses controlled RX moves from a feasible seed, following the feasible-subspace
principle of Hadfield et al., https://arxiv.org/abs/1709.03489. This preserves only
the explicitly certified constraints, not arbitrary flight physics. Neither
paper proves an advantage for these mission instances. Strong native references
follow the benchmarking principle in https://arxiv.org/abs/2504.03832.

No UI changes, no frontend tests, no new dependencies. Existing user notebook
edits are excluded from commits. Skeleton, frozen test and implementation commits
are separate under the repository's test-first workflow.
