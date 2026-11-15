# Unified mission planner — stage 2 native contracts

Stage 2 adds a versioned native-input envelope and bounded identity adapters on `codex/unified-quantum-stage1`. It does not run the common coordinator, enable a quantum optimizer, submit hardware jobs or modify existing API/UI behavior. The full implementation sequence remains in [the integration plan](unified-mission-planner.md).

## Why the envelope retains two models

R11 `CampaignProblem` is authoritative for mission requirements, cancellation/defer policies, dependencies, station calendars/capacities, rates, delivered data and relay rules. H6 `HybridRecoveryRequest` is authoritative for its estimated checkpoint, native planar flight model, uncertainty, target bindings and H0 proposal model. A generic dictionary or flattened QUBO would hide differences between these contracts.

`app.unified_planning.UnifiedPlanningEnvelope` embeds both complete typed models, plus `IntegrationContext`, explicit `OptionLink` records and optional `ModuleEvidence` references from `shared.unified_planning`. JSON round trips preserve both models. No existing H0/H6/R11 schema is rewritten. The envelope lives in the backend because the existing H4–H6 native types are backend-owned; common metadata and proposal types live in `shared/` without backend/provider dependencies.

The new layer has no optimizer/provider import of its own. Importing the existing H6 request module retains its existing local solver dependencies; adapter calls neither optimize nor submit a provider job.

## Supported bridge and deliberate rejections

The delivered example is a **synthetic nominal identity bridge**. It uses three H6 landing targets and one R11 lander with a declared station, capacity, 60-bit demand and 10 bit/s rate. The R11 campaign uses 60-second slots, while the original H6 sub-second dynamics and second-scale option windows are retained. The example is not a real site registration, flight replay or proof that the two models' descent/energy estimates agree.

Each H0 option must map to exactly one distinct generated R11 candidate. Mission and vehicle IDs and MOON_ME site identity/angles must agree. The R11 nominal landing instant must lie in the H0 choice window, and a landing target's deadline must permit it. No time rounding, interpolated rates or coordinate conversion occurs. Additional R11 missions may remain outside the H0 subset; their constraints stay in the full original problem, and they are absent (not cancelled) in a partial proposal.

Stage 2 explicitly rejects:

- Missing, duplicate or ambiguous option mappings; unknown or natively rejected R11 candidates.
- Different mission/vehicle/site identities or an unsupported coordinate-conversion request.
- A nominal landing outside the H0 window or after the H6 target deadline.
- Mapped missions demanding data without an explicit positive direct-link rate. Relay-only mapped missions need a later adapter; unrelated native R11 relay semantics remain preserved.
- Missing provenance, duplicate module/source IDs, unknown/cyclic source lineage, non-synthetic correspondence evidence, truth/observation state attachments to the H0 planner and non-UTC timestamps.
- A decision window before its estimated checkpoint or after a mapped landing; it must fit both horizons.

`PLANAR_MOON_CENTERED_NONROTATING` remains the H6 state frame. `MOON_ME` remains the site-label frame. The declared synthetic correspondence associates nominal choices; it does not convert positions or assert physical equivalence. Real registration remains unsupported until a separately verified frame/origin/epoch transform exists.

## Snapshot identity and stale inputs

`seal_inputs(envelope)` reparses and detaches the native models and returns `PlanningSnapshot` with SHA-256 identities for the full R11 campaign, H6 request and complete envelope. The envelope hash also covers context, deadline, mapping evidence and optional module references. Semantically unordered metadata is normalized; vectors, trajectories and physically ordered data are not sorted.

Every adapter requires both the sealed snapshot and the **current authoritative envelope**, plus explicit `now_utc`. It reparses nested data, checks hashes and compares current inputs. This catches changes even inside mutable dictionaries held by otherwise frozen Pydantic models. Returned native models are detached, so a caller cannot alter the stored snapshot by changing an output dictionary.

The caller must supply genuinely current inputs; hashes do not fetch telemetry, authenticate evidence, certify safety or replace persistence/concurrency control. The decision window is half-open: creation is accepted, the deadline itself is rejected. These are logical UTC admission checks, not a guarantee of computation/actuator delivery within a real-time deadline.

Stable adapter error codes are `invalid_time`, `snapshot_tampered`, `stale_inputs`, `outside_decision_window`, and `invalid_selection`. Structural/native-model failures remain Pydantic/ValueError diagnostics. No rejection silently falls back, changes input assumptions or spends credits.

## Adapters and proposal semantics

- `to_r11_problem(...)` restores the complete native campaign, including constraints not represented by H0.
- `to_h6_request(...)` restores the native H6 request for its existing physical preparation/coordinator. It does not claim that H6 enforces the full R11 campaign.
- `map_h6_proposal(...)` translates one unique known option per H0 mission into partial R11 selections. It accepts option identities as **untrusted proposals**, whether they came from an H6 prepared result, a local experiment or another caller. It does not validate the prepared scenario's solver provenance or certify an H0/H6 outcome. Mapping requires no assumption that an H6 prepared scenario has the raw H0 fingerprint.

`CampaignProposal` permanently labels its output `unvalidated`, `requires_classical_completion=true`, and `requires_physical_revalidation=true`. It has no allocations or accepted-plan status. Unknown/duplicate/missing selections are rejected. A later coordinator must apply the proposal to the original R11 problem, complete remaining choices and allocations, run `validate_campaign_plan`, and re-screen the physical continuation against current state. A successful mapping alone cannot authorize flight or cancellation.

The verification artifact's independently solved R11 plan is a baseline, not completion of the displayed H6 proposal. Removing that baseline plan's communication allocations makes the original validator reject it. This confirms that crossing the interface does not erase the data demand.

## All H modules in the common architecture

The long-term design is one coordinator with multiple classical and quantum components, not one circuit performing every physical calculation. `module_catalog()` enumerates all H0–H11 roles:

| Module | Role | Stage 2 status |
| --- | --- | --- |
| H0 | Mission contracts and provenance | Native input |
| H1 | Classical reference and independent validation | Reference only |
| H2 | Bounded QUBO/QAOA proposals | Reference only |
| H3 | Resource/calendar/storage constraints | Reference only |
| H4 | Terminal descent | Native input via H6 |
| H5 | Fault scenario and estimated state | Native input via H6 |
| H6 | Recovery and option identities | Native input/identity bridge |
| H7 | Optional quoted hardware execution | Separate approval required |
| H8 | Comparison and solver provenance views | Reference only |
| H9 | Shared-antenna scheduling evidence | Reference only |
| H10 | Scaling and matched benchmarks | Reference only |
| H11 | Orbital-relay selection evidence | Reference only; distinct frame/model |

`ModuleEvidence` can carry a versioned contract name, revision, content hash and source references for any of these roles. It is a provenance declaration, not a payload loader, signature verification, executable plugin registry or proof that a future adapter exists. It cannot turn on a solver or bypass R10/R13 NO-GO policies. H7 has no credential, spending or dispatch field in this layer.

## Reproduce locally

The fixture is `shared/examples/unified/r11-h6.json`. In the backend Python environment:

```python
from datetime import datetime, timezone
from pathlib import Path
from app.unified_planning import (
    UnifiedPlanningEnvelope, seal_inputs, to_r11_problem,
    to_h6_request, map_h6_proposal,
)

current = UnifiedPlanningEnvelope.model_validate_json(
    Path('/workspace/shared/examples/unified/r11-h6.json').read_text()
)
snapshot = seal_inputs(current)
now = datetime(2030, 1, 1, tzinfo=timezone.utc)  # synthetic replay clock
campaign = to_r11_problem(snapshot, current=current, now_utc=now)
recovery = to_h6_request(snapshot, current=current, now_utc=now)
proposal = map_h6_proposal(
    snapshot, ('land-backup',), current=current, now_utc=now,
)
assert proposal.requires_classical_completion
```

Run `pytest -q tests/test_unified_planning.py` in the backend. The 55 new tests cover native round trips and physical-preparation compatibility, original R11 allocation validation, unrepresented mission/dependency preservation, stale inputs, nested mutation, ordering, deadline boundaries, all module declarations and unsupported bridges. Skeleton commit: `408dbf7`; frozen test/fixture commit: `73cdc6e`. Tests and fixture were unchanged during implementation. See [recorded stage 2 evidence](verification/unified-planner-stage2-2026-11-15.json).

Stage 3 remains pending: a multi-mission classical pilot that completes and physically revalidates the proposal after an event, including stale results and no-continuation cases. The stage 2 fixture is deliberately smaller and is not that pilot.

## Full verification

1,771 backend, 218 ingestion and 91 optional-quantum tests passed (2,080 total, including 55 new contract tests). Backend/ingestion Ruff checks and the TypeScript/Vite production build passed. Existing Starlette/AnyIO deprecation and large frontend bundle warnings remain. No frontend tests/screenshots or live provider jobs were run. The local H6 notebook remains untouched and excluded from commits.

## Stage 3 follow-up

The previously pending classical pilot is now delivered in [stage 3](unified-classical-pilot.md). It uses this unchanged native envelope and explicitly rederives full R11 arrival/fuel evidence; it does not turn stage 2 identity mappings into automatic coordinate transforms.
