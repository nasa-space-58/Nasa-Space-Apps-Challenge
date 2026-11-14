# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Lunar Landing Navigator** (Team 2gbram, NASA Space Apps Challenge "Lunar Landing Site Navigator"). The repo currently contains **documentation only** (`docs/`, `README.md`); no application code, `docker-compose.yml` or tests exist yet. The README is in Turkish; everything else (code, comments, commits, docs) must be in English.

`docs/` is the source of truth. Read the relevant file before working:
- `docs/technologies.md`: stack, service architecture, Docker Compose, planned repo layout. Changes need whole-team agreement.
- `docs/coding-conventions.md`: git, test-first workflow, code style.
- `docs/user-scenarios.md`: personas, modules, scenarios S1–S16, build order, acceptance criteria. Numeric thresholds there are example defaults.
- `docs/data-sources.md`, `docs/2gbram_horizons_veri_notlari.md`: NASA data source catalog and JPL Horizons notes.

## Planned architecture (see docs/technologies.md)

Four Docker Compose services; the only host requirement is Docker:
- `frontend`: React + TypeScript + Vite (port 5173), React Three Fiber for 3D, Recharts. **No scientific calculation in the frontend**; no business logic in UI code.
- `backend`: Python 3.12, FastAPI (port 8000), NumPy/SciPy, SpiceyPy, Rasterio. Does all computation. **Never downloads external data**; mounts the data volume read-only.
- `data-ingestion`: separate Python worker that downloads and preprocesses NASA LRO/LOLA DEM and NAIF SPICE data into the shared data volume (read-write). It never runs inside the backend process.
- `rabbitmq`: backend and ingestion communicate **only** through it (aio-pika). Messages are JSON Pydantic models shared by both services, carry references/metadata only (never large data), are durable, acked after full processing, and ingestion requests must be idempotent.

Data files are never committed to Git. Config via `.env` (commit `.env.example` only).

## Commands (planned, per docs/technologies.md)

```bash
docker compose up --build                       # run everything
docker compose run --rm backend pytest          # backend tests
docker compose run --rm data-ingestion pytest
docker compose run --rm frontend npm test
docker compose run --rm backend pytest path/to/test_file.py::test_name   # single test
```

Linting/formatting for Python is Ruff; frontend tests are Vitest + React Testing Library.

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
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
- ALWAYS read graphify output (graphify-out/GRAPH_REPORT.md, then `graphify query`) FIRST, before looking at any other file or source in this project.
- ALWAYS update the graphify output after EVERY code change: run `graphify update .` right after writing or editing code, before moving on.
- ALWAYS follow the documents under docs/ (docs/coding-conventions.md, docs/user-scenarios.md, and any others added there). Act according to them in every task; if a request conflicts with them, point out the conflict before proceeding.
