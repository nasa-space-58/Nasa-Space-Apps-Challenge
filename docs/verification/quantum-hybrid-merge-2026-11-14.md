# Quantum hybrid integration into tahakrcc — 2026-11-14

The user explicitly authorized merging `codex/quantum-hybrid` into current `tahakrcc`, resolving conflicts, testing the combined tree and pushing only `tahakrcc`. No main merge or push is part of this operation.

Inputs:
- `tahakrcc`: `e6ee9c09e4821c087ed9e983b9427e70c6232499`, including the collaborator's R6–R9 work.
- `codex/quantum-hybrid`: `0fecf0af094ab8218e3b2cd25c565413d88c8940`, including H0–H7 and the recorded OpenQuantum hardware experiment.

There was one code conflict in `backend/app/descent.py`. Resolution retains R7's `fly_gates` extraction and final planar-state output, while applying H4's shared `advance_burn` implementation to the actual flight loop. The engine stops at fuel exhaustion and the remaining integration time coasts without thrust. The obsolete exhaust-speed local and duplicate dry-mass assignment were removed. Both branches' original frozen tests are preserved.

Checks on the combined checkout:
- Backend: **970 tests passed**; backend/shared Ruff passed. One existing Starlette/AnyIO deprecation warning remains.
- Data ingestion: **112 tests passed**; Ruff passed.
- Optional quantum package: **60 tests passed**; Ruff passed.
- H7 notebook: **12 cells executed in memory without errors**, including recorded hardware evidence and offline flight replay. No notebook output was rewritten, no new provider job was submitted and no credit was spent.
- Frontend: TypeScript and Vite production build passed. The existing large-chunk advisory remains; no frontend tests or screenshots were used.
- No frozen test was modified during conflict resolution. No frontend source change was made relative to the target branch.

The working quantum checkout's uncommitted H6 notebook edits are kept local and excluded from this merge. H8 remains planned only; its scope is the side-by-side classical/QAOA solver arena in `docs/mvp-roadmap.md`.
