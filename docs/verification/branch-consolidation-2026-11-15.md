# Branch consolidation and scene refresh — 2026-11-15

## Scope and preservation

Integrate the latest unified planner (including C2 physics reuse) and documentation assistant with the current mission UI. Preserve local candidate/rover work before refining the layout. The assistant remains disabled by default; no paid calls or quantum submissions were made.

PR #19 already integrated H8–H11, and PR #18 integrated healthinfo. Ahead counts alone were misleading because these were squash merges. The earlier R12 branch replaces incompatible `self_healing.py` and twin contracts; its work is preserved under an archive tag instead of reverting the current H5/H6/R12 implementation. It is not claimed as a source merge.

Before removing a completed/superseded remote branch, publish `archive/2026-11-15/<original-branch>` at its exact recorded tip. Retain contributor branches and the unified planner branch because it is checked out in another active worktree. Do not rewrite any history. Local untracked frontend test files and preview config remain outside the delivery commit; the original complete working tree is also preserved in a named Git stash.

| Branch | Recorded tip | Disposition |
|---|---|---|
| `codex/h0-hybrid-contracts` | `ac14ec8cc811` | Already contained in main/integration history |
| `codex/h10-h11-scaling-relays` | `dc95002869fb` | Already integrated through squash PR #19 |
| `codex/h8-solver-arena` | `74bcfba9ef16` | Already integrated through squash PR #19 |
| `codex/h9-shared-antenna` | `fb59791ef607` | Already integrated through squash PR #19 |
| `codex/quantum-hybrid` | `0fecf0af094a` | Already contained in main/integration history |
| `codex/unified-quantum-stage1` | `7549aca6f7cc` | Retained (main, contributor branch, or active worktree) |
| `feature/assistant-qa` | `e82dbcf4a3a2` | Merged in this integration |
| `feature/r1-exports-sharing-classroom` | `481c3205abe5` | Already contained in main/integration history |
| `feature/r10-hybrid-optimization` | `5f9a65b911f0` | Already contained in main/integration history |
| `feature/r11-multi-mission-planning` | `90d6561edeb9` | Already contained in main/integration history |
| `feature/r12-event-panel` | `456f4efb201e` | Already contained in main/integration history |
| `feature/r12-self-healing` | `85ce6d421f50` | Superseded implementation; preserve under archive tag, do not replace H5/H6 |
| `feature/r13-quantum-application-benchmarks` | `7554cab21338` | Already contained in main/integration history |
| `feature/r2-ground-station-communication` | `fdc4020f9a11` | Already contained in main/integration history |
| `feature/r3-explained-recommendations` | `a0b7b8db4952` | Already contained in main/integration history |
| `feature/r4-descent-trajectories` | `f497f2192fe0` | Already contained in main/integration history |
| `feature/r5-fuel-optimization` | `6250fe29e134` | Already contained in main/integration history |
| `feature/task1-ingestion-cache` | `e96dd628e45f` | Already contained in main/integration history |
| `feature/task2-terrain-horizon` | `5019c01218f4` | Already contained in main/integration history |
| `feature/task3-visibility-metrics` | `f25d94522b11` | Already contained in main/integration history |
| `feature/task4-mission-interface` | `825a0008952d` | Already contained in main/integration history |
| `feature/task5-scenario-comparison` | `c1d6348bfe1d` | Already contained in main/integration history |
| `feature/task6-demo-verification` | `16d59d05b5ff` | Already contained in main/integration history |
| `feature/task7-window-search` | `28554ff4b45f` | Already contained in main/integration history |
| `fix/healthinfo` | `0595abc9a9a8` | Already integrated through squash PR #18 |
| `main` | `5d032c0d0c9a` | Retained (main, contributor branch, or active worktree) |
| `sinan` | `4aae354019f2` | Retained (main, contributor branch, or active worktree) |
| `tahakrcc` | `cb17bdda4c8c` | Retained (main, contributor branch, or active worktree) |
| `temel` | `a5171d4db0fb` | Retained (main, contributor branch, or active worktree) |
| `ui/mission-launch-animation` | `8874894560d4` | Already contained in main/integration history |

## Interface changes

- Shared 12px control, 16px card and 20px panel corner tokens.
- Compact phase chip and camera selector, clean view and fullscreen controls.
- Flight readouts, coordinates, source notes, candidate scores/reasons and surface operations in a single selected side inspector.
- Compact flight scrubber and rover shortcut below the scene.
- Independent hazard, search-coverage, rover-route and landing-candidate visibility controls; visibility never changes planning or route playback.
- Surface operations remain mounted while hidden, preserving scan and drive progress.
- Retain real LOLA versus demo labels and the original 3D assets.

## Verification

Frontend TypeScript/Vite build passed in the preview container. No new frontend tests or screenshot verification were performed. Backend full-suite and ingestion results are recorded below when complete. Visual acceptance remains with the user, per repository instructions.

- Backend clean-image full suite: **2,029 passed** (261.98 s); one upstream AnyIO deprecation warning.
- Data ingestion full suite: **218 passed**.
- Changed integration modules: Ruff check passed.
- Final frontend TypeScript/Vite production build passed; existing large-bundle advisory remains.
- Local rebuilt backend `/health` returned OK; `/assistant/status` confirms disabled-by-default operation. Existing data/state volumes were preserved.
