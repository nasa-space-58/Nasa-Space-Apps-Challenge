# H3 — Explicit communication and energy resource integration

H3 connects R2-shaped geometric contact evidence, separately supplied station calendars/capacities, and ideal constant-power battery/activity profiles to the shared H0 planning problem. H1 and H2 receive the same revised scenario and fingerprint. The integration is offline: it does not book DSN time, download data, submit quantum jobs, or infer a radio link or real battery model.

Implementation: `backend/app/hybrid_resources.py`. Notebook: `notebooks/H3_resource_integration.ipynb`. Branch: `codex/quantum-hybrid`, with manual merges by the user. Skeleton commit `839c167`; frozen tests `87d7d95` (41 tests). Existing H0/H1/H2 modules and tests remain unchanged.

## Inputs and interface

| Type/function | Meaning |
|---|---|
| `StationCalendar` | Station ID, explicitly provided channel capacity (nullable), booked/available UTC windows, provenance |
| `ContactRequirement` | Candidate ID, station ID, required UTC interval, channel demand and geometric state intervals with provenance |
| `contact_from_r2` | Adapter for the actual `communication_windows` result shape: `stations[station_id].intervals` |
| `BatteryProfile` | Initial energy, storage capacity, fixed reserve (J), piecewise constant generation (W), provenance |
| `ActivityLoad` | Candidate ID, battery ID, UTC activity interval, constant consumption (W), provenance |
| `integrate_resources` | Return a new H0 scenario with revision +1 and explicit resource/demand constraints; preserve the input |
| `battery_trace` | Independently propagate a selected plan with the supplied ideal battery/load profiles and report energy, spill and deficits |

Contact/load windows must lie inside their candidate's window. Generation must lie inside the scenario horizon. Profiles use explicit UTC and unit-bearing fields. IDs, source collisions, duplicate requirements/activities, unknown candidate/station/battery references, invalid windows and overlapping generation segments fail loudly. Generation segments describe a single aggregate power profile; combine parallel generators explicitly before calling.

The caller must supply complete numerical battery/power assumptions; H3 does not turn missing battery data into free energy. Unknown station capacity is preserved. Any unapplied H0 event is rejected, rather than silently deleted or applied.

## Geometry, calendar and capacity are separate

R2 calculates Earth/station geometric visibility. Its own limitations exclude scheduling, station horizon masks, keyholes and link budgets. H3 consumes those station intervals without reinterpreting them as a booking.

For each contact, H3 adds a candidate-specific geometry gate resource plus demand on the shared station-calendar resource. This prevents visibility for one candidate/site from accidentally making another candidate/site available. Station availability and concurrent capacity remain independent constraints checked by H1.

Geometry intervals must be ordered, contiguous, nonoverlapping and cover the required contact. `available` permits geometry, `unavailable` blocks it, and `unknown` overlapping positive requested demand produces an unknown gate capacity. The gate's potentially available intervals include available and unknown time; this avoids mislabeling an unknown segment as a proven unavailable segment. Known unavailable gaps remain excluded. H1 reports incomplete input when unknown geometry is needed. Unknown time outside the requested contact does not block it.

Zero channel demand needs no capacity; no radio throughput, transmitted data volume, link margin or automatic station choice is inferred. A saved real R2 result must match the candidate's site and dates and carry traceable source metadata; the adapter does not magically establish that external association.

## Ideal storage model

Explicit assumptions:

- Constant generation/consumption power within each supplied interval, ideal unit efficiency.
- Storage capacity C, fixed reserve R, known initial energy E0 with `0 <= R <= E0 <= C`.
- Energy above capacity is spilled; there is no credit for production that could not be stored.
- No charge/discharge power limit, temperature effect, degradation, conversion loss, illumination-to-power inference or stochastic load.
- Activity timing is fixed within each candidate. Different activity/load records add their consumption.

Collect cuts at horizon, generation and activity boundaries. For an interval `[a,b)`, define G(a,b) as supplied generated joules and D(a,b) as consumption by selected activities. Add a budget constraint for **every pair of cuts**:

```text
D(a,b) <= E0 - R + G(a,b), when a is the horizon start
D(a,b) <= C  - R + G(a,b), otherwise
```

Each activity contributes power times its overlap duration to each relevant interval budget. These auxiliary budgets are multiple checks of the **same physical energy**, not separate batteries or additional energy consumption.

Why all intervals are needed: at each cut, the ideal recurrence is `E_next = min(C, E + G - D)`. Expanding the recurrence expresses energy as the minimum of the initial-energy prefix and capacity-based suffix sums. Every such expression must be at least R, giving the inequalities above. Constant power makes endpoint checking sufficient between cuts. The independent trace uses the recurrence directly; the planner uses interval inequalities.

The default is at most 16 cuts per battery; callers may request 2–32. Constraint growth is quadratic in cuts. This bounded exact encoding is for small examples, not a production-scale energy scheduler.

Energy arithmetic uses Fraction values from supplied decimal quantities and exact microsecond interval durations. H0 ultimately stores numeric floats: H3 rejects conversions that change the exact decimal energy, or exceed finite range. It does not silently round a hard budget. Inputs needing finer precision must be explicitly rounded/reformulated upstream, with that choice recorded. The diagnostic trace displays finite float values and can show negative energy to explain an invalid plan; that is a deficit report, not a physically executed discharge below zero.

## Provenance, identity and diagnostic limits

Each input record has an explicit H0 Source. H3 preserves these and adds a versioned derived source whose parents identify the input evidence/assumptions. Same source IDs with different content are rejected. The resulting constraints and source lineage participate in the H0 fingerprint. Stable IDs and sorted inputs make input-order permutations deterministic. Reintegrating generated resource IDs fails rather than duplicating demands.

The `battery_trace` checks plan/scenario identity, then evaluates the explicitly supplied battery/load profiles. It does not prove that independently supplied profiles are the same ones used to construct that scenario. Callers must retain and reuse those exact inputs when presenting a trace as an explanation of an integrated plan. Alternative profiles may be explored explicitly, but their output must not be presented as original-plan evidence.

## Matched classical/quantum experiment

The notebook uses synthetic R2-shaped all-day geometry and a separately assumed single booked channel. Tests also call the real R2 `communication_windows` function and feed its output into the adapter.

The binding energy example gives each of three missions a 3600 J battery, no generation, and one-hour activities. Option 1 consumes 2 W (7200 J); options 2–4 consume 1 W (3600 J). These are toy assumptions, not spacecraft specifications. Option 1 is individually infeasible. Sharing one station then leaves six feasible assignments, with raw science score 22 and normalized optimum `11/5`.

The same integrated fingerprint reaches H1 exact enumeration and H2 QAOA (six qubits, one layer, 80 simulator objective evaluations, 256 final shots, seed 17). Both obey the same source, station and energy constraints. Raw quantum outcomes retain invalid samples; no repair is applied. Runtime includes the previously documented mapping/audit overhead and is not a hardware or performance-advantage measurement.

A second example starts a shared battery full at 7200 J, with 1 W generation during hour one and three one-hour 1 W activities. If all activities start later, 3600 J production spills and only 7200 J remains for 10800 J consumption. At least one mission must consume during the first hour: exactly 37 of 64 assignments are feasible. A naive total-generation budget would incorrectly accept late-only plans.

This second case has a three-way constraint that H2's current no-slack mapping cannot represent. H3 keeps the exact classical answer and reports the quantum encoding limit; it never removes the energy constraint or changes the scenario to force a comparison. General slack-variable encodings and larger quantum subproblem strategies remain open.

## Jupyter and verification

Open with the existing **Lunar H0 (Python 3.12)** kernel; no new dependencies:

http://localhost:8888/lab/tree/quantum-hybrid-workspace/notebooks/H3_resource_integration.ipynb

The eight code cells cover input setup, R2/calendar integration, closed/unknown contact cases, binding energy constraints, matched quantum/classical results, energy traces, storage spillage and explicit encoding limits.

On 2026-11-14:

- All 41 H3 tests initially failed only with `NotImplementedError` against the skeleton, then passed.
- Tests include the real R2 result shape, candidate-specific geometry, unknown handling, calendars, energy/reserve boundaries, spillage, late generation, input/provenance failures and eight seeded cases compared against a separate hourly storage oracle over all 64 assignments.
- Full backend suite: 684 passed. Ruff and formatting checks passed. Frozen H0/H1/H2/H3 tests are unchanged.
- H3 notebook: eight code cells executed without errors. Zero hardware jobs submitted.
- H3 changes no frontend, ingestion or shared contracts. Those components passed their H1 checks; the backend suite continues to cover existing communication behavior.

```sh
docker exec nasa-task4-backend-1 pytest -q
docker exec nasa-task4-backend-1 ruff check app tests /workspace/shared
git diff 87d7d95 HEAD -- backend/tests/test_hybrid_resources.py
git diff cc6c9fa HEAD -- backend/app/hybrid_quantum.py backend/tests/test_hybrid_quantum.py
```

Real mission calibration, arbitrary activity precedence, optional tasks, physical battery limits, robust uncertainty, general quantum resource encodings and automatic state/event updates are not completed by this bounded H3 integration.
