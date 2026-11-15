# R6 runtime integration and surrogate-readiness boundary

Recorded before implementation/measurements. C1 and C2 evidence is immutable.

## Local API delivery

Enable bounded physics reuse in `POST /descents/risk`, already used by the risk
panel. Each app worker owns one computation thread, at most two admitted
requests (running plus waiting), 128 cache entries and 32 MiB retained encoded
payloads. The payload limit is not a promise about total Python process RSS.
Make cache enablement and byte/entry limits configurable at app startup.

Every request reloads verified terrain and re-evaluates hazards, reserves,
touchdown and uncertainty. Keys include every dynamics input and resolved
vehicle. No whole risk response or terrain decision is cached. Source/model
code changes require a worker restart; entries never survive that restart.
Explicit invalidation clears a namespace. Configuration changes take effect at
startup. Process reuse after fork without a new app factory must fail closed.

Admission is bounded and excess requests return 429; shutdown refuses new work.
Disconnect or cancellation sets a cooperative stop flag. Check before nominal
planning, between full integrations and before delivering results. Running
integration is not forcibly killed. Completed deterministic cache entries may
be retained after cancellation, but partial risk responses must never publish.
Shutdown sets every stop flag, cancels queued jobs, joins and clears cache.

Freeze tests for real API cold/warm/off equality, fresh terrain and settings,
input/model invalidation, byte/entry limits, mutation isolation, concurrent
requests, queue overflow, exceptions, cancellation and lifecycle cleanup.
Measure actual HTTP calls through a separate local server on synthetic verified
DEM fixtures: seeds 831/832, 24 draws, cold/warm/off, fixed 32 MiB cap. Report
exact outcome equality and retained memory; no global application speed claim.
Before release, additionally measure the unchanged default 200-draw workload
at seed 833 (cold/warm/off). This size exceeds the entry cap, so freeze additive
scan-resistance tests before implementing request-scoped admission protection:
entries hit or admitted during a request cannot be evicted by its later scan.
New requests may replace unprotected old entries within the same byte limit.
These are resource-policy checks, not changes to any physics or quantum gate.
Do not recreate any shared Docker environment or deploy production.

## Next quantum step: readiness audit, not a trained surrogate

Before creating another algorithm, count independent support in the C2 evidence.
Repeated methods, warm calls and different cost limits do not create new terrain
or uncertainty groups. Group on the source scenario seed and candidate identity;
retain differences between labels and independent input-only features. C2 has
already been inspected, so none of its records is a new blind test set.

Potential cheap features are input orbit, direction, vehicle, uncertainty scales
and terrain summaries. Such features can define a research surrogate, but they
do not certify touchdown or full joint risk. Nominal trajectories and learned
labels are not free input features: generation/training cost must be recorded.

Stop this iteration before training or QAOA when there is no untouched group
holdout and no validated surrogate under the deployment uncertainty domain.
Also report the full finite pair space: a small space exhaustible by the
classical reference is not evidence for a quantum candidate-generation need.
Do not select a model or threshold using C1/C2 test outcomes. The audit must not
fit a model, execute physics, generate a circuit or submit a provider job.

If future work supplies an independently validated surrogate, pre-register
separate scenario-group training/development/test seeds before generating data.
Give its entire feature/model table to exact enumeration/MILP and diverse
classical selection too. Hold paid physics calls and total preparation/training/
selection budget equal. Charge dataset construction, learning, encoding,
simulation/compilation, queue and readout; distinguish reusable offline cost from
new-instance cost. Require native physical quality on unseen groups and a
meaningful reduction in expensive validations before considering hardware.
This document does not assert that those gates can be passed.
