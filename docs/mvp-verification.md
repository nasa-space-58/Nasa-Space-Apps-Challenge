# Task 6 — Local MVP verification

Technical verification completed on 2026-11-14 on `feature/task6-demo-verification`, based on Task 5 `c1d6348`. No changes from this task are merged into tahakrcc or main. Human teammate acceptance remains open; this is not a public deployment or independent mission-design certification.

## Clean installation and final source

A fresh `git clone --no-local` was created at `/tmp/nasa-task6-clean`. The initial no-cache build used the Task 5 base with entirely new `nasa-task6` volumes for RabbitMQ, terrain, frontend modules and analysis state. No previous terrain cache was copied. The clone was fast-forwarded to the Task 6 fixes (`682a731`); affected backend/frontend images were rebuilt with `--no-cache`. All final image tests ran without source bind mounts and with networking disabled. Previously pulled base images were reused; installing Docker itself was not tested.

Temporary overrides exposed only loopback 8002/5175 and no broker port. Production UI verification used the locked Vite 7.3.8 preview on 5176, rather than the development server. No deployment or new runtime dependency was introduced.

| Final check | Result |
|---|---|
| Backend image | 136 tests, Ruff and pip check passed |
| Ingestion image | 75 tests, Ruff and pip check passed |
| Frontend image | 11 tests and TypeScript/production build passed |
| Total | 222 passing tests |
| Dependencies | Fresh hashed Python installs, npm ci; npm audit reports zero findings |
| Existing tests | Unchanged; new broker tests frozen at `b3cc525` |
| Runtime | Cold acquisition, cached replay, site/date comparison and 30-day refinement completed |

The existing Starlette/AnyIO deprecation and lazy 3D chunk-size advisory remain. Python package vulnerability auditing was not performed; pip check validates dependency compatibility, not vulnerabilities.

## Reproduce the live checks

Use a new Compose project name so the data/state volumes are **empty**. From this branch's checkout, create an isolated override and run:

```bash
cat > /tmp/lunar-check.yml <<'YAML'
services:
  rabbitmq:
    ports: !reset []
  backend:
    ports: !override ["127.0.0.1:8002:8000"]
  frontend:
    ports: !override ["127.0.0.1:5175:5173"]
YAML
docker compose -p nasa-mvp-check --env-file .env.example \
  -f docker-compose.yml -f /tmp/lunar-check.yml up -d --build --wait
python3 scripts/verify-mvp.py http://localhost:8002 /tmp/mvp-live.json
```

Stop only this project afterward with the same Compose arguments and `down --remove-orphans`; volumes remain. For another cold run, choose a new project name. The Task 6 verification services were stopped after testing; their volumes were retained and the existing 5174 preview was left running.

This opt-in script creates real NASA-backed jobs and checks an empty terrain resolver, invalid coverage/date/duration, cold and warm results, both comparison modes, interval continuity, total accounted duration and provenance. It intentionally expects empty terrain at the beginning and is not a repeated smoke check for an already populated environment. The chosen port must belong to your isolated instance.

The test requests 24 h from Malapert on 2026-11-04, the same request again, a 48 h delay and same-date Mons Mouton. All use 2 m AGL and refinement. Full evidence, IDs and measured results are in [the verification JSON](verification/task6-mvp-2026-11-14.json).

## Defects found and fixed

1. The ready example evaluated an invalid/empty editable date before applying its own preset. It now constructs the example independently. A regression test failed before the fix and passes after it.
2. Reload restored a saved result but left the editable scenario at its default coordinates/date/height. The submitted inputs now restore with the job. A regression test covers non-default coordinates, height, date, duration and refinement.
3. Simultaneous backend/broker restart could fail a recovered job before RabbitMQ became ready. Initial connection now has ten bounded attempts, a five-second per-attempt timeout and two-second delays (up to approximately 68 seconds). Configuration errors and cancellation are not swallowed. Existing persistent request IDs and ingestion deduplication remain unchanged. New tests cover immediate success, refusal/timeout recovery, exhaustion, cancellation and invalid retry settings. The same real restart experiment failed before and completed after the fix.

Completed results stayed identical across restart. A fault-injected unreachable HTTPS proxy produced a persisted failed job; restoring the worker and submitting a new request succeeded. Failed jobs are not silently relabeled successful. Browser-injected API 503 displayed an error and Reconnect restored the interface.

## Performance observations

Single observations on this local Mac/Docker ARM64 environment, not benchmark guarantees or SLA measurements. Other local services and some verification work were running.

| Case | Observed time |
|---|---|
| Empty terrain + 24 h Malapert, with refinement | 268.0 s (includes approximately 189 MB NASA terrain download) |
| Same request, verified cache | 4.0 s |
| +48 h case, terrain already cached | 5.1 s |
| Mons Mouton, terrain already cached | 8.1 s |
| 30 days, with real transition refinement | 57.6 s; 1,005 displayed frames, 38 source bundles |

The 30-day analysis accounted for all duration and its detected event brackets were 60 s. Its slider and playback advanced correctly in the production UI. These checks do not guarantee all transitions have been discovered or remove spatial uncertainty.

During initial acquisition, 20 health requests had a 3.5 ms median and 11.5 ms maximum. Cache-disabled production UI loading transferred about 3.05 MB of same-origin resources; DOMContentLoaded was about 96 ms. External font bytes are excluded. A 120-frame desktop animation sample had a 16.7 ms median frame interval, not a physical-mobile performance measurement. No fixed time budget was used to pass/fail these observations.

## Scientific verification and external product compatibility

- The freshly acquired 24 h Malapert numerical metrics matched the stored Task 4 reference within 1e-5 seconds/percentage points. Cold/warm result objects were exactly identical. This is a reproducibility check, not an independent ephemeris validation.
- The independent Cartesian horizon check was rerun against the new terrain cache: maximum angle difference about 4.4e-11 degrees, with repeat-identical output. Existing frozen tests cover reference geometry, curvature, boundaries, interval accounting and refinement.
- [NASA PGDA product 90](https://pgda.gsfc.nasa.gov/products/90) documents the terrain product family and MOON_ME / DE421 projection. [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/manual.html) supplies observer ephemerides. Acquisition provenance and terrain checksums are retained by the pipeline.
- [NASA PGDA illumination product 69](https://pgda.gsfc.nasa.gov/products/69) was reviewed for comparability. Its published maps average approximately 18.6 years and use different pixel scales; Earth is visible when any part of its disk clears terrain. Our demo uses 24 h/30 d windows, an 80 m piecewise-cell horizon and Earth-center visibility. Those percentages are **not comparable numerical acceptance targets**. No independent NASA illumination accuracy match is claimed; no product-69 imagery or values are used in application results.

An 80 m containing-pixel terrain model can produce steep near-field horizon angles. Subpixel topography, terrain/model uncertainty, missing terrain beyond the finite radius, eclipses, station geometry, link budgets and landing safety remain outside the validated scope. See the Task 2/3 model documentation and the on-screen limitations.

## Browser and demo acceptance

Chromium desktop 1440×1000 and mobile layout 390×844 checks covered actual 3D assets, reduced motion, API outage/reconnect, failed-job input restoration, UTC comparison, scrubbing/playback, mobile overflow and the long-window scenario. Earlier Task 4/5 WebGL fallback and keyboard/tab tests remain in the suite and verification history. Physical devices and other browser engines were not claimed tested.

The [demo runbook](demo-runbook.md) includes prewarming, a three-minute walkthrough, NASA attribution and an explicit teammate checklist. **Teammate planner/public-user acceptance is pending.** The overall MVP release gate remains open until that human review is recorded.
