# Unified mission planner — stage 4 bounded local proposals

Stage 4 connects H2/H6 quantum target proposals to the stage 3 full R11 completion and physical admission path. The default remains classical. Only an explicit `mode="local_experiment"` runs the local noiseless simulator. There is no hardware mode, provider dispatch, credential access, automatic retry or credit budget in this interface.

Implementation: `backend/app/unified_quantum.py`. [Recorded local comparison](verification/unified-planner-stage4-2026-11-15.json). Existing H0–H11 modules and R10/R13 production NO-GO decisions remain unchanged.

## Supported scope and actual results

The proposal domain is the stage 3 single-active-lander H0/H6 target set. Other R11 missions remain present in every completion. This is a bounded neighborhood around a complete classical incumbent, not a lossless quantum encoding of every R11 constraint, a universal optimizer or an integration of all H modules into one circuit.

| Case | Outcome |
| --- | --- |
| Nominal three-target pilot | Compact H2 mapping rejects the non-power-of-two option count. Return the classical plan and the explicit unsupported reason. No target is silently removed and no padded solution is invented. |
| Pilot after the landing exclusion | Two surviving targets, one logical qubit, depth 1, 32 tuning evaluations, 64 measurements, seed 17. Local QAOA proposed `remote` 64 times. |
| Matched random proposals | `remote` 40 times and `backup` 24 times; both unique targets completed and admitted. |
| Matched greedy proposals | Both top local-energy targets examined within the same two-completion cap; both completed and admitted. |
| Selected final plan | The classical incumbent remains selected: none of the proposals gives a strict full-plan improvement. |

QAOA's recorded local sampled feasibility and local optimum rates are both 100% for this one-qubit example. Those describe the **small H0 target model**, not an independently quantum-computed three-mission plan or a scalable success guarantee. Classical MILP supplies the remaining choices/communication allocations, and classical physics validates the flight. The one-qubit circuit has zero CX gates; this run does not demonstrate an entanglement-based performance gain. Hardware jobs and credits spent are zero.

## Pipeline and preserved constraints

`compare_proposals(snapshot, current=..., now_utc=..., mode=..., budget=..., completed=...)`:

1. Validate the mode and strict finite budgets, then run the existing stage 3 full classical baseline. In ordinary/classical mode return it immediately without a QAOA call. If no full incumbent exists, report that there is no supported neighborhood to freeze; do not conceal no-continuation or solver-limit outcomes.
2. For a local experiment, recheck snapshot identity and prepare the current H6 physical/H0 model. Use the existing H2 QUBO compiler/audit, compact encoder and seeded QAOA implementation. Follow R10's bounded-subproblem and matched-budget approach; no new quantum optimizer or alternative physics implementation is added. Unsupported variable/qubit/group/constraint shapes stop before sampling.
3. Freeze every outside mission's **selection**, including cancellation. Retain the full original R11 requirements, calendars, capacities, data demands/rates, dependencies and relay rules in each derived completion. Outside selections continue consuming their modeled resources; they are never removed from the campaign to make a proposal feasible. Future link allocations may be recomputed by MILP; the executed-action ledger must still match exactly.
4. Treat raw sampled option IDs as untrusted proposals. Check local references/constraints independently and translate the active target using the same stage 3 exact-arrival/fuel derivation. Unknown IDs are rejected, never repaired into another target. Native QAOA reports zero-count basis states as part of its probability table; these are not proposals and consume no completion slot. Nonnegative integer counts must still total the requested measurements.
5. Force the proposed active target and all fixed outside choices in the full R11 solver. A fixed cancellation excludes that mission's generated choices while retaining its native cancellation policy. Check complete data delivery and every original native constraint. Reject an infeasible/limited completion or one that alters outside choices or executed actions.
6. Call `accept_classical` for each completed candidate: repeat physical screening, rebuild the expected derived full campaign and independently validate it. Store raw sample counts, local feasibility and admitted **completed** plans separately. A high local feasible rate can coexist with zero admitted full plans.
7. Compare the best admitted QAOA-completed plan against the full classical incumbent and matched random/greedy completions. QAOA gets selection credit only for a strict independently checked full-plan score improvement. Equal or worse proposals preserve the classical plan. A feasible time-limited incumbent never becomes a proved optimum by passing through this layer.

No unconstrained target repair is performed. A completed plan's extra allocations are attributed to classical completion, not raw quantum output. The original R11 campaign and physically derived arrival/fuel evidence remain authoritative, and the stage 3 wait-cost correction remains active when comparing target branches.

## Matched budgets and timing

`ProposalBudget` bounds depth (1–3), tuning evaluations (1–128), measurements (1–1,024), seed, logical qubits (1–6), unique classical completions per method (1–8), and per-completion native solver time (greater than zero, at most 30 s). Defaults are depth 1, 32 evaluations, 64 measurements, seed 17, four-qubit ceiling, two completions and five seconds per solver call.

QAOA, random and deterministic local-energy/greedy proposals use the same supported compact states, outside-choice snapshot, completion cap, per-completion solver budget and physical admission path. Greedy distributes its proposal count over its best bounded set; random uses a separately seeded uniform draw. Each method receives its own completion calls—no cached optimum or repair is gifted to QAOA. Repeated samples are deduplicated for completion; unused cap is not filled with unobserved QAOA states. Unexamined sampled targets are explicitly listed.

The complete classical baseline is a separate reference, not a matched small-proposal sampler. Its computation is included in the overall experiment elapsed time. The report also records shared preparation, each method's proposal generation, each unique completion and admission times. Overall comparison time includes all three methods and bookkeeping. A subsequent external `accept_hybrid` call has additional validation cost. These local timings are illustrative measurements, not a controlled speed benchmark; no end-to-end advantage is claimed.

## Final admission and provenance

`accept_hybrid(result, snapshot, current=..., now_utc=..., completed=...)` first rejects stale/expired inputs and changed result payloads. It re-admits the baseline and final candidate through the stage 3 physical/full-campaign validator, checks fixed outside choices, and recomputes comparison scores from freshly validated native data. Stored score labels are not trusted even if a caller recomputes the checksum.

A result labelled as quantum-derived must match the actually admitted QAOA completion and strictly beat the independently rechecked classical references. Equal-plan reattribution and inflated-score claims are rejected. This check does not authenticate a user or a provider; hashes remain change-detection identities, and callers must supply genuinely current inputs. No accepted result here dispatches a vehicle or writes an operational plan.

The execution remains an offline paused-clock synthetic demonstration with one physically propagated lander. The existing stage 3 limits remain: no inferred real coordinate conversion, no unmodeled future fault replay, no general partially executed allocation scheduler, and no battery-trajectory or certified uncertainty guarantee. Admission failures never become a safe-abort claim.

## Relationship to all H modules

This stage executes H0/H1 contracts/validation, H2 local QAOA, H4/H5 physics and H6 preparation alongside full R11 completion. H3 constraints carried by the H0 model are preserved and checked. It retains the common H0–H11 architecture: H7 remains separately authorized hardware, H8 presentation is later work, and H9/H10/H11 scheduling/scaling/orbital evidence require their declared domain adapters. The existing `module_catalog()` field is explicitly a **stage 2 status** snapshot, not a live all-modules-enabled flag.

R10/R13 NO-GO results are neither retuned nor overridden. Production defaults stay classical. New application domains and any real-device evaluation must receive their own model checks, budgets and (for credits) user approval.

## Reproduce in the backend environment

```python
from datetime import datetime, timezone
from pathlib import Path
from app.unified_planning import UnifiedPlanningEnvelope, seal_inputs
from app.unified_classical import LandingExclusion, apply_landing_exclusion
from app.unified_quantum import ProposalBudget, compare_proposals, accept_hybrid

nominal = UnifiedPlanningEnvelope.model_validate_json(
    Path('/workspace/shared/examples/unified/classical-pilot.json').read_text()
)
now = datetime(2030, 1, 1, 0, 0, 8, tzinfo=timezone.utc)
current = apply_landing_exclusion(
    seal_inputs(nominal),
    LandingExclusion(event_id='obstacle-1', epoch_utc=now,
                     start_downrange_m=-5.0, end_downrange_m=5.0),
)
snapshot = seal_inputs(current)
result = compare_proposals(
    snapshot, current=current, now_utc=now,
    mode='local_experiment', budget=ProposalBudget(),
)
accepted = accept_hybrid(result, snapshot, current=current, now_utc=now)
assert result.quantum.hardware_jobs == 0
assert accepted.validation.status == 'feasible'
```

## Test provenance

Skeleton: `8f45b95`. Frozen tests: `94fb31a` (integration/budgets), `d90ddd9` (untrusted IDs/counts, cancellations and attribution), `bb3df22` (unsampled zero-count basis records). None were edited after their commits. The 31 new tests cover explicit opt-in, unsupported mappings, matched caps, full completion/physical admission, infeasible completions despite local feasibility, stale/expired/tampered results, deterministic sampling, cancellation preservation, independent score checks, malformed samples/no retries and the production classical default.

## Full verification

1,837 backend, 218 ingestion and 91 optional-quantum tests passed: **2,146 total**, including 31 new stage 4 tests. Backend/ingestion Ruff checks and the TypeScript/Vite production build passed. Existing Starlette/AnyIO deprecation and large Vite bundle warnings remain. No frontend tests/screenshots or live hardware jobs were run. The local H6 notebook edit remains byte-for-byte unchanged and excluded from commits.
