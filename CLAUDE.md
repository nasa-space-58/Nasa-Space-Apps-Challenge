# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Lunar Landing Navigator** (Team 2gbram, NASA Space Apps Challenge "Lunar Landing Site Navigator"). The repo contains the completed Step 0 foundation and Task 1 RabbitMQ ingestion, persistent terrain/Horizons caches, Task 2 bounded terrain horizon computation, Task 3 sampled visibility metrics/refinement, and Task 4 persistent analysis API plus NASA-model scroll interface, and Task 5 saved A/B comparison with synchronized playback and tests. Local Docker runtime verification and frontend lock generation passed on 2026-11-14; Python transitive locks and a clean-clone Docker test also passed; terrain/site input definitions were also verified against NASA/USGS sources; Step 0 is complete; see docs/mvp-roadmap.md. The README is in Turkish; everything else (code, comments, commits, docs) must be in English.

`docs/` is the source of truth. Read the relevant file before working:
- `docs/mvp-roadmap.md`: first-release scope, release order, task checklist and acceptance criteria.
- `docs/technologies.md`: stack, service architecture, Docker Compose, planned repo layout. Changes need whole-team agreement.
- `docs/coding-conventions.md`: git, test-first workflow, code style.
- `docs/user-scenarios.md`: personas, modules, scenarios S1–S16, build order, acceptance criteria. Numeric thresholds there are example defaults.
- `docs/terrain-and-sites.md`, `docs/initial-sites.json`: verified initial terrain and observer inputs; runtime sourced presets are in shared/presets.json.
- `docs/data-sources.md`, `docs/2gbram_horizons_veri_notlari.md`: NASA data source catalog and JPL Horizons notes.

The first release focuses on site/date comparison, terrain-relative Sun/Earth visibility and understandable energy/communication metrics. Extended mission planning follows the phased roadmap.

## Latest verified extension

R1 adds explained candidate deficits, CSV/PNG/printable reports, saved-result links, portable input manifests with expected result fingerprints, and two bounded observer-based classroom lessons. See docs/release-r1.md. Surface-wide shadow visualization and human MVP acceptance remain open.

## Optional Q&A assistant

`backend/app/assistant.py` and `backend/app/doc_search.py` add an opt-in Claude-backed Q&A assistant (`/assistant/status`, `/assistant/ask`). It is off unless `ASSISTANT_ENABLED=true` and `ANTHROPIC_API_KEY` are set. Its tools are read-only, it never computes physics, and tests use a fake client, so they spend no credit. Never commit or print the key. Frozen tests: `6db6f82`. See docs/assistant.md.

## Planned architecture (see docs/technologies.md)

Four Docker Compose services; the only host requirement is Docker:
- `frontend`: React + TypeScript + Vite (port 5173), React Three Fiber for 3D, SVG horizon/timelines (Recharts planned for comparisons). **No scientific calculation in the frontend**; no business logic in UI code.
- `backend`: Python 3.12, FastAPI (port 8000), NumPy/SciPy, cached JPL Horizons ephemerides, Rasterio. Does all computation. **Never downloads external data**; mounts the data volume read-only.
- `data-ingestion`: separate Python worker that downloads and preprocesses NASA LRO/LOLA DEM and JPL Horizons observer data into the shared data volume (read-write). It never runs inside the backend process.
- `rabbitmq`: backend and ingestion communicate **only** through it (aio-pika). Messages are JSON Pydantic models shared by both services, carry references/metadata only (never large data), are durable, acked after full processing, and ingestion requests must be idempotent.

Data files are never committed to Git. Config via `.env` (commit `.env.example` only).

## Delivery instruction (user, 2026-11-14)

After completing each task, commit the verified work and push it to `origin/tahakrcc`. Integrate task branches into `tahakrcc` as needed, preserving remote work. Do not merge into or push to `main`. Continue to synchronize before pushing and run the required checks.

### Task 1 delivery override (user, 2026-11-14)

Develop and push Task 1 only on `feature/task1-ingestion-cache`. Do not merge it into `tahakrcc` or `main`; those branches are being tested independently. This explicit task instruction overrides the default delivery paragraph above.

## Commands (planned, per docs/technologies.md)

```bash
docker compose up --build                       # run everything
docker compose run --rm backend pytest          # backend tests
docker compose run --rm data-ingestion pytest
docker compose run --rm backend pytest path/to/test_file.py::test_name   # single test
```

Linting/formatting for Python is Ruff.

## Frontend: no tests, no screenshots (user, 2026-11-14)

- Never write, run, restore or plan frontend tests (no Vitest, React Testing Library or any other frontend test). The test-first rule in docs/coding-conventions.md applies to backend and data-ingestion code only; frontend code, including non-UI helpers such as `frontend/src/missionPath.ts`, is exempt.
- Verify frontend changes with `docker compose run --rm frontend npm run build` (TypeScript check + Vite build) only.
- Do not take or inspect screenshots or other images of the app to verify changes; the user checks visuals personally.

## Key conventions (see docs/coding-conventions.md)

- **Test-first for all non-UI code, three separate commits:** (1) skeleton with stubs, (2) comprehensive tests, committed alone and then **frozen**, (3) implementation. Never edit, delete, skip or loosen tests during implementation; if a frozen test is wrong, follow the process in section 3 of the conventions. PRs must cite the test commit SHA.
- Git: branch per task (`feature/`, `fix/`, `ui/`, `docs/`, `chore/`, `refactor/`), no direct commits to `main`, pull before every push, no force push or history rewrite, Conventional Commits, squash-merge with at least one approval from another member.
- Every physical quantity carries its unit in its name, every coordinate an explicit frame, every timestamp an explicit time scale (no naive timestamps). Radians internally, degrees only at I/O boundaries. Physical constants live in one constants module with units and sources.
- Fail loudly; no catch-all error handling. Seeded randomness for Monte Carlo.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- Keep graphify-out/ local. The directory is ignored by Git and its generated graph, report and visualization are not committed or pushed.
- ALWAYS read graphify output (graphify-out/GRAPH_REPORT.md, then `graphify query`) FIRST, before looking at any other file or source in this project.
- ALWAYS update the graphify output after EVERY code change: run `graphify update .` right after writing or editing code, before moving on.
- ALWAYS follow the documents under docs/ (docs/coding-conventions.md, docs/user-scenarios.md, and any others added there). Act according to them in every task; if a request conflicts with them, point out the conflict before proceeding.

### Task 2 delivery override (user, 2026-11-14)

Task 2 starts from Task 1 on `feature/task2-terrain-horizon`. Push only this new branch. Do not merge into or push to `tahakrcc` or `main`; the user performs merges manually.

### Task 3 delivery override (user, 2026-11-14)

Task 3 starts from Task 2 on `feature/task3-visibility-metrics`. Push only this new branch. Do not merge into or push to `tahakrcc` or `main`; the user performs merges manually.

### Task 4 delivery override (user, 2026-11-14)

Task 4 starts from Task 3 on `feature/task4-mission-interface`. Push only this new branch; the user manually merges. Do not merge into or push to tahakrcc or main.

### Task 5 delivery override (user, 2026-11-14)

Task 5 starts from current tahakrcc (`c29086d`) on `feature/task5-scenario-comparison`. Push only this new branch. Do not merge into or push to tahakrcc or main. Task 4 was merged into tahakrcc separately at the user's explicit request.

### Task 6 delivery override (user, 2026-11-14)

Task 6 starts from Task 5 (`c1d6348`) on `feature/task6-demo-verification`. Push only this new branch; do not merge into or push to tahakrcc or main. Verification uses an isolated fresh clone and new Docker volumes. Teammate acceptance must be recorded by a human, not inferred from automated checks.

### Task 7 delivery override (user, 2026-11-14)

Start from Task 6 on `feature/task7-window-search`. Push only this new branch; do not merge into or push to tahakrcc/main. Time search is a bounded candidate-grid exploration, not a guarantee of all feasible windows or landing safety.

### R1 delivery override (user, 2026-11-14)

Complete remaining R1 work from Task 7 (`28554ff`) on `feature/r1-exports-sharing-classroom`.
Push only this new branch. Do not merge into or push to tahakrcc/main. The user merges manually.

### R2 delivery override (user, 2026-11-14)

R2 (DSN ground-station communication) starts from tahakrcc `a37e83c` on `feature/r2-ground-station-communication`. Push that branch and, at the user's explicit request, the same commits to `temel`. Do not merge into or push to tahakrcc or main.

### R3 delivery override (user, 2026-11-14)

R3 (explained recommendations) starts from `temel` `fdc4020` on `feature/r3-explained-recommendations`. Push that branch and the same commits to `temel`. Do not merge into or push to tahakrcc or main.

### R4 delivery override (user, 2026-11-14)

R4 (descent trajectories) starts from `temel` `a0b7b8d` on `feature/r4-descent-trajectories`. Push that branch and the same commits to `temel`. Do not merge into or push to tahakrcc or main.

### R5 delivery override (user, 2026-11-14)

R5 (fuel optimization) starts from `temel` `f497f21` on `feature/r5-fuel-optimization`. Push that branch and the same commits to `temel`. Do not merge into or push to tahakrcc or main.

### H0 delivery override (user, 2026-11-14)

H0 starts from current tahakrcc `d67a32f` on `codex/h0-hybrid-contracts`. Push only the new branch; do not merge into or push to tahakrcc/main. The user merges manually. Shared contracts are in `shared/hybrid.py`, synthetic cases in `shared/examples/hybrid/`, and the runnable notebook in `notebooks/H0_shared_contracts.ipynb`. See `docs/hybrid-contracts.md`; skeleton `a4437db`, frozen tests `774a50f`. H0 does not implement an optimizer or physical simulator.


### H1 and continuing hybrid delivery override (user, 2026-11-14)

H1 starts from H0 `ac14ec8`. The user requested one continuing quantum-development branch: `codex/quantum-hybrid`. Push only that branch; do not merge into or push to tahakrcc/main. The user merges manually. H1 is the tested classical reference, with no quantum calls. See `docs/classical-planning.md`; skeleton `33901c3`, frozen tests `1bc2ce8`. For later quantum experiments, start with a small circuit and bounded execution budget; keep unsuccessful experimental implementations uncommitted and preserve their evidence locally. Agree on measurable success criteria before quantum experiments; do not equate a successful hardware invocation with a useful optimization result.


### H2 continuation (user, 2026-11-14)

The user authorized continuing later H stages on `codex/quantum-hybrid`. H2 uses a bounded local simulator only; no paid/provider job is authorized by a local simulation run. Skeleton `00c6801`, frozen tests `7c26cfc`; see `docs/quantum-mapping.md`. Low-qubit encoding must report its gate/locality tradeoff. Do not label simulator expectations as hardware observations or per-sample gains as end-to-end advantage.


### H3 continuation (user, 2026-11-14)

H3 remains on `codex/quantum-hybrid`; no tahakrcc/main merge or push. Skeleton `839c167`, frozen tests `87d7d95`; see `docs/resource-integration.md`. Geometry is not a station booking or radio-link guarantee. Storage is an explicitly ideal constant-power model. Unsupported quantum mappings retain the classical answer and report the limit, never silently remove constraints.


### H4 continuation (user, 2026-11-14)

Continue on `codex/quantum-hybrid`; push only that branch, without tahakrcc/main merges. Skeleton `6292085`, frozen tests `3436c14` and `aa07539`; see `docs/descent-foundation.md`. Shared R4/H4 integration must stop thrust exactly at fuel depletion. H4's restartable terminal model uses an explicit nonrotating planar frame, sampled corridor clearance and two-resolution candidate screening. Never imply automatic MOON_ME conversion, continuous collision certification, a global reachability envelope or quantum execution from these checks.


### H5 continuation (user, 2026-11-14)

Continue on `codex/quantum-hybrid`; push only this branch without tahakrcc/main merges. Skeleton `9fd35a7`, frozen tests `9c9a6d1`, `1a4fc36` and `432cd67`; see `docs/self-healing.md`. H5 uses exact-time planar fault replay, explicit truth/observation/estimate separation, seeded shared uncertainty draws and classical retargeting. Never present finite Monte Carlo passes as certified probabilities, a landing exclusion as volumetric obstacle geometry, infeasibility as a safe abort, or synchronous planning as a real-time/hardware guarantee. H6 async quantum/fallback integration remains open.


### H6 continuation (user, 2026-11-14)

Continue on `codex/quantum-hybrid`; push only this branch, without tahakrcc/main merges. Skeleton `5b9252a`, frozen tests `d872606` and `bfe96d5`; see `docs/hybrid-recovery.md`. H6 connects H5 screened targets and downstream schedules to the local H2 QAOA worker. Classical preparation precedes the quantum acceptance deadline; every resolution rechecks current physical and campaign inputs. Offline flight time is paused during planning, and running Python work is not forcibly killed. Do not present this as live control, hardware execution or quantum advantage. H7 hardware/provider experiments remain separate work.

### H7 continuation (user, 2026-11-14)

Continue on `codex/quantum-hybrid` and push only that branch; do not merge into tahakrcc/main. OpenQuantum free Spark experiments are authorized; ask before paid credit use. SDK dependencies stay in the separate optional quantum environment. Preserve local credentials and submission receipts outside Git. Never retry ambiguous submissions automatically, label simulators as hardware, or infer quantum advantage from this tiny benchmark. Frozen tests: `db20168`, `45f4dc7`, `e85e5e7`, `1b48779`. See `docs/openquantum-hardware.md` for evidence and remaining boundaries.

### H0–H7 integration authorization (user, 2026-11-14)

The user explicitly authorized merging `codex/quantum-hybrid` into current `tahakrcc`, preserving collaborator changes, resolving conflicts and pushing only `tahakrcc` after tests pass. This one-time integration overrides the preceding no-merge delivery instructions for H0–H7. Keep `main` untouched, update local checkouts, and explain H8 before implementation. See `docs/verification/quantum-hybrid-merge-2026-11-14.md`.

### Main push exception (user, 2026-11-14)

At the user's explicit request, tahakrcc (`28f8380`) was fast-forwarded directly onto `main` without a PR, overriding the no-direct-push rule for this one push only. The default rules for `main` still apply afterwards.

### H8 delivery (user, 2026-11-14)

Start from current main `cb17bdd` on `codex/h8-solver-arena`. Push only this new branch; do not merge into or push to main/tahakrcc. Skeleton `8bfd5be`, frozen tests `4071a81`. H8 records actual classical/QAOA computation history for progress-normalized replay; it is not a live hardware race. The two-qubit benchmark preserves the exact H7 input snapshot across platforms. Show H7 hardware only as archived evidence with its original budget and cost. No new provider submission is part of H8. See `docs/solver-arena.md`.

### H9 delivery (user, 2026-11-15)

H9 proceeds from completed H8 `74bcfba` on `codex/h9-shared-antenna`; push only the new branch for the user's manual merge. Current main `cb17bdd` is already an ancestor. Campaign skeleton `071e1fa`, frozen tests `945a718`; optional physical-layout skeleton `84dac54`, frozen tests `a366a5c` and user-approved reader-only correction `d66685f`. Preserve the user's local H6 notebook changes. The user authorized one free-credit hardware trial capped at 1 Spark; no paid credits or automatic retries. H9 uses actual archived R2/Sun evidence with explicit synthetic mission/calendar preferences, and distinguishes scheduling feasibility from landing/energy certification. See `docs/shared-antenna-campaign.md`.
### Space weather main push exception (user, 2026-11-15)

At the user's explicit request, `feature/space-weather-noaa` (NOAA SWPC solar activity; frozen tests `be593c9`, see docs/space-weather.md) was fast-forwarded directly onto `main` and `temel` from `cb17bdd` without a PR, overriding the no-direct-push rule for this one push only. The default rules for `main` still apply afterwards.


### H10/H11 delivery (user, 2026-11-15)

Start from H9 `fb59791` on `codex/h10-h11-scaling-relays`. Complete H10 then H11, run intermediate and combined tests, and push only this new branch for the user to merge. Never merge into or push main/tahakrcc. Ask before any real quantum credit expenditure, including free credits. H10 records exact/MILP benchmarks, not a quantum speedup; H11 supports k=1/2 relay selection on explicitly approximate, sampled CR3BP/terrain geometry. Preserve the local H6 notebook edit. Test baselines and two narrowly approved test corrections are documented in docs/problem-scaling.md and docs/relay-orbit-selection.md.

### R10 delivery (user, 2026-11-15)

R10 (classical-versus-hybrid benchmark) starts from `main` `a5171d4` on `feature/r10-hybrid-optimization`; push only that branch, no merge into or push to tahakrcc/main. Frozen tests `6c96376` and `411d549`; see `docs/hybrid-benchmark.md`. SciPy (HiGHS MILP) is now locked in the backend. The pre-registered held-out decision is NO-GO for both direct QAOA and QAOA-solved subproblems, so the MILP solver is retained. Do not tune on held-out cases after the fact, present the illustrative noise model as calibrated, or claim quantum advantage.

### R11 delivery (user, 2026-11-15)

R11 (multi-mission planning) starts from `feature/r10-hybrid-optimization` `5f9a65b` on `feature/r11-multi-mission-planning`; push only that branch, no merge into or push to tahakrcc/main. Frozen tests `8504f81`; see `docs/multi-mission-planning.md`. Campaign contracts live in `shared/campaign.py` (H0 V1 is unchanged). Station geometry, calendar, capacity, data rate and exclusivity stay separate inputs; relay credit needs equipment, line-of-sight evidence, range and the relay's own station link. Do not present synthetic example campaigns as NASA plans or DSN bookings, or passing candidates as landing-safety certification.

### R10/R11 main push exception (user, 2026-11-15)

At the user's explicit request, `feature/r11-multi-mission-planning` (which contains R10) was fast-forwarded directly onto `main` from `a5171d4` without a PR, overriding the no-direct-push rule for this one push only. The default rules for `main` still apply afterwards.

### PR #18 merge exception (user, 2026-11-15)

At the user's explicit request, PR #18 (`fix/healthinfo`: rover drive and surface panel, planet spin, real Moon/Sun/Earth flight positions from JPL Horizons; frozen tests `2f01b74`, see docs/flight-ephemeris.md) was squash-merged into `main` without another member's approval, overriding the approval rule for this one PR only. The default rules for `main` still apply afterwards.

### R12 completion and main push exception (user, 2026-11-15)

R12's last item (visual event panel) was built from `main` `90d6561` on `feature/r12-event-panel`. Skeleton `e9600d0`, frozen tests `7332c3e`; see `docs/r12-event-panel.md`. The panel is built on H5/H6; the conflicting `feature/r12-self-healing` branch was not merged. At the user's explicit request, the branch (after merging PR #18 from `main` `8c3dff5`) was pushed directly onto `main` as a fast-forward without a PR, overriding the no-direct-push rule for this one push only. The default rules for `main` still apply afterwards.

### R13 delivery and main merge (user, 2026-11-15)

R13 (additional quantum application benchmarks) starts from `main` `456f4ef` on `feature/r13-quantum-application-benchmarks`. Pre-registration/skeleton `0bcb830`, frozen tests `351c1e1`; see `docs/quantum-applications.md`. All five applications (communication slots, science activities, relay selection, R8 rover routes, R6 robust plans) are NO-GO on held-out instances, so `solve_application` keeps the HiGHS MILP and runs QAOA only for a held-out GO. At the user's explicit request the branch was merged into `main` and pushed, overriding the PR/approval rule for this one merge only. Do not present the raw-feasibility gain of noiseless QAOA as an end-to-end contribution, and do not tune on held-out cases after the fact.


### H11 and current main integration (user, 2026-11-15)

The user explicitly authorized integrating the latest main with the completed H10/H11 branch and publishing the combined version to main after resolving conflicts and passing checks. This one-time authorization supersedes the earlier H10/H11 manual-merge boundary. Preserve both teams' features and the unrelated local H6 notebook edit. No new quantum submission or credit spending is authorized by this integration.

### UI simplification and rover scan main push exception (user, 2026-11-15)

At the user's explicit request, `feature/rover-scan-route` (which contains `ui/simplify-interface`) was fast-forwarded directly onto `main` from `e92174e` without a PR, overriding the no-direct-push rule for this one push only. It adds the simplified mission-control menu, the R8 route scene payload (skeleton `6b3d52c`, frozen tests `93583cf`), the real-DEM rover scan and the CC BY 4.0 Sketchfab rover model (attribution in `frontend/public/models/manifest.json`). The default rules for `main` still apply afterwards.
### Unified planner stage 1 (user, 2026-11-15)

Start from current main `e92174e` on `codex/unified-quantum-stage1`. Complete only the first agreed stage: integration inventory, baseline observations and tests. Push only this new branch; do not merge into or push main/tahakrcc. Ask before any real quantum credit spending, including Spark. The new common solver, adapters and live coordinator remain subsequent stages. Preserve the unrelated H6 notebook edit and existing frozen tests. See docs/unified-mission-planner.md and its stage 1 evidence.

### Unified planner stage 2 (user, 2026-11-15)

Continue on `codex/unified-quantum-stage1`, implement the R11/H6 common contracts/adapters, test and push only this branch. Do not merge into main/tahakrcc. All H0–H11 roles must remain in the common architecture; stage 2 does not claim all adapters or a universal solver are implemented. Ask before any real quantum credits, including Spark. Preserve the local H6 notebook. Skeleton `408dbf7`, frozen tests/fixture `73cdc6e`; see docs/unified-planner-contracts.md.

### Unified planner stage 3 (user, 2026-11-15)

Continue on `codex/unified-quantum-stage1`; implement the full classical mission pilot and event/physical revalidation, test and push only this branch. Do not merge into main/tahakrcc or spend real quantum credits. Preserve the local H6 notebook. Skeleton `4c4110b`; frozen tests/fixture `bdeeb7c` and independent admission tests `6dbc897`. See docs/unified-classical-pilot.md for exact limits, including offline paused-clock operation and immutable-allocation rejection.

### Unified planner stage 4 (user, 2026-11-15)

Continue on `codex/unified-quantum-stage1`; connect bounded local quantum proposals to full campaign/physical admission, test and push only this branch. No main/tahakrcc merges or real quantum credits. Preserve the local H6 notebook. Skeleton `8f45b95`; frozen tests `94fb31a`, `d90ddd9`, `bb3df22`. Default mode stays classical, experiments are explicitly local, and unchanged R10/R13 NO-GO policy cannot be bypassed. See docs/unified-quantum-proposals.md for supported scope and attribution rules.
