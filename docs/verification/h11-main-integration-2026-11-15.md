# H8–H11 and R10–R13 integration — 2026-11-15

The user authorized publishing the combined version to main after verification. The integration combines H10/H11 branch head `88668ff` (including H8/H9) with main `7554cab` (including R10–R13 and PR #18).

## Conflict resolution

- Retain both teams' delivery notes and roadmap entries.
- Keep R11 translations under `campaign`; move H9 translations to `sharedAntenna` in both English and Turkish.
- Give the H9 panel the unique `shared-antenna` ID and preserve its `#/shared-antenna` URL. R11 keeps its `campaign` panel ID.
- Prefix H9 CSS/DOM identifiers with `antenna-` to prevent collisions with R11 campaign styles.
- Preserve both route groups: H9 `/campaign/*` and R11 `/campaigns/*`.
- Archive the separately authorized H11 hardware result in `h11-hardware-2026-11-15.json`. No provider job was submitted during integration. R10/R13 classical-default execution decisions remain unchanged.

## Verification

- Backend: **1716 passed** in a freshly built backend image with the combined dependency lock; Ruff passed for app, tests and shared modules.
- Ingestion: **218 passed**; Ruff passed.
- Optional quantum: **91 passed** in the existing pinned optional environment.
- Frontend: TypeScript/Vite production build passed. No frontend tests or screenshots were run, per the user's standing instruction.
- Combined API smoke check: unique method/path registrations; HTTP 200 from `/health`, `/arena/presets`, `/arena/hardware`, `/campaign/catalog`, `/campaign/hardware`, `/campaigns/examples`, `/recovery/examples`, `/research/scaling`, and `/research/relays`.
- Archived H11 raw counts reproduce 129/256 feasible and 51/256 optimal samples, zero best-plan gap, and agreement with independent geometry validation over all 16 encoded assignments.
- All Python test files match their respective parent commits (111 comparisons); no frozen test was modified for integration.
- The unrelated local `notebooks/H6_hybrid_recovery.ipynb` edit was preserved byte-for-byte and excluded from the commit.

Existing warnings remain: Starlette/AnyIO deprecation, large frontend bundles, and the existing externally created terrain-volume notice. Visual acceptance remains with the user. This integration delivers both sets of features together; it does not implement the newly discussed unified mission/quantum coordinator or claim quantum advantage.
