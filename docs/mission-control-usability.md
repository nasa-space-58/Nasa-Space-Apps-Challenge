# Mission control usability review

Branch: `ui/mission-control-usability`, based on main `36cf387`.

## Changes

- The Moon overview camera no longer co-rotates with the Moon. Dated ephemeris orientation remains authoritative. The illustrative fallback now uses a shared progress-driven lunar transform for the body, sites, paths and spacecraft. Pausing and scrubbing use the same progress value.
- Descent, Monte Carlo landing dispersion, backups, fault replay, terrain recovery and surface-route API results can open a local-coordinate 3D result view. This is a uniform-scale plot, explicitly separate from the terrain scene. API metres and sample timestamps are preserved; no flight solver was added to the frontend.
- Backup and surface-route table selections highlight the corresponding 3D result. Optimized descent choices select the existing illustrative candidate corridor. Recovery decisions select their chosen target. Timed trajectories have playback, scrubbing and event-time shortcuts.
- Each analysis records its input snapshot and request generation. Changed inputs mark old results stale; late responses cannot replace the current scenario. Descent calculations and optimization cannot submit concurrently.
- Orbit/fuel settings and scene-inspection tools are collapsed by default. Core controls and existing advanced capabilities remain available. New result cards and buttons follow the existing rounded interface.
- Turkish explanations cover the advanced panels' fixed assumptions and recovery labels. Common numeric diagnostics are translated while preserving their numbers. Unknown technical messages remain available verbatim through an explicitly labelled disclosure.
- Starting a descent calculation updates the mission's selected coordinates.

## Boundaries

No model assets, backend solvers, ingestion code or existing tests were changed. The original terrain/rover scene is available through “Back to mission”. Local surface-route plots display north/east plan coordinates; they do not invent terrain heights or replace the existing DEM rover view. Vehicle artwork remains illustrative and does not claim to represent a selected vehicle profile exactly.

The synchronous fallback orbit is illustrative, not a new scientific ephemeris. Surface-following cameras intentionally keep the landing area stable; use the Moon overview to observe rotation.

Unknown server prose is retained in its original language rather than assigned an invented translation. Backend validation of inputs remains authoritative.

## Human visual acceptance (pending)

Open `http://localhost:5174/#/mission-control`:

1. Select Turkish, play the mission in Moon overview, then pause and scrub. Check texture motion and alignment of markers and spacecraft.
2. Open Landing and surface. Check the collapsed orbit/fuel controls and all deeper-analysis panels, including terrain recovery.
3. Calculate a descent; inspect the 3D path, playback and return-to-mission control.
4. Run risk, backups, fault replay, recovery and surface routes. Select targets/routes and event times; inspect the highlighted result.
5. Change an input after a calculation; confirm the old-result warning and recalculate.
6. Confirm the existing landing models, DEM rover exploration, mission navigation and narrow-screen panel layout remain usable.

Frontend verification follows the repository policy: TypeScript + Vite build only; no frontend tests or screenshots. Backend regression suite and runtime HTTP checks are recorded separately. Visual acceptance remains with the user. Do not merge to main before that review.
