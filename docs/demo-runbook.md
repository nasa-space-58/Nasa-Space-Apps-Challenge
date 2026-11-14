# MVP demo runbook and teammate acceptance

Task 6 delivers local technical verification, not a public deployment or scientific certification. Work on `feature/task6-demo-verification` is not merged into tahakrcc/main by this task.

## Before the demo

1. Start Docker Desktop, copy `.env.example` to `.env`, choose local RabbitMQ credentials, and run `docker compose up -d --build --wait`.
2. If reusing an older frontend dependency volume, run `docker compose run --rm --no-deps frontend npm ci`.
3. Open http://localhost:5173. For a separate development preview, use `docker-compose.preview.yml` with project `nasa-task4` (5174/8001).
4. Precompute the Malapert 2026-11-04 / 24 h example, its +48 h scenario and the same-date Mons Mouton scenario. First terrain acquisition is about 189 MB and needs network access; it is unsuitable for a live stage wait. Do not delete named volumes after prewarming.
5. Keep NASA attribution and model limitations visible. Verify the actual branch, ports and API health before presenting.

## Three-minute walkthrough

| Time | Action | Explain |
|---|---|---|
| 0:00–0:25 | Scroll from LRO/Moon to Apollo | NASA display assets introduce the perspective; descent is illustrative, not a flight simulation. |
| 0:25–1:00 | Open mission control and the Malapert example | Coordinates are MOON_ME/east longitude; times are UTC; height is above the terrain pixel. |
| 1:00–1:35 | Scrub the horizon and visibility timelines | Terrain, Sun and Earth use backend results. Partial sunlight differs from full sunlight; Earth-center visibility is not a radio link. |
| 1:35–2:20 | Add comparison, choose +48-hour delay, run B | Same site, duration and model; show both UTC dates and B-minus-A differences. Results are specific to this interval. |
| 2:20–2:45 | Switch to same-date Mons Mouton | Same observation window, different terrain/site. A remains the baseline. |
| 2:45–3:00 | Open model limits / sources | Explain unknown coverage, 80 m piecewise terrain, bounded horizon, sampling and no eclipse/link budget/landing-safety model. |

On a slow or unavailable network, show an already completed saved analysis; do not substitute mock results. A failed new request stays visibly failed. Browser storage retains the most recent baseline ID and per-baseline B draft/ID, not a full user account/history. Comparison is reopened with Add comparison after page reload.

## Teammate acceptance — still requires a human

Automated browser checks do not replace this section. A teammate should test without implementation guidance and record name/date/device, pass/fail and comments below. No messages were sent to teammates by this task.

- [ ] Planner: select a site/date, explain the displayed units, find a sunset and compare a 48 h delay.
- [ ] Public user: use the ready example and glossary; explain what partial sunlight and Earth visibility mean.
- [ ] Mobile user: switch A/B, scrub time, reopen comparison and verify the original inputs/results remain.
- [ ] Fault handling: recognize an unavailable API, reconnect and avoid interpreting unknown coverage as darkness.
- [ ] Identify any confusing wording, unreadable chart labels or interaction failures.

Reviewer / date / device: **pending**

Findings / disposition: **pending**

## Release limits

The local API is single-process and unauthenticated, with loopback bindings. Production hosting, authentication, distributed job management, broad device profiling and teammate acceptance are separate gates. The 3D bundle remains relatively large and loads lazily. Visibility estimates are not independent mission-design validation. A full public MVP release must not be marked approved until the human review above is complete.
