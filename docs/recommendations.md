# Explained site recommendations — R3 (scenarios S8 and S9)

Start from `temel` `fdc4020` (R2); developed on `feature/r3-explained-recommendations`.
Skeleton: `af61c30`. Frozen test baseline: `cee01e4` (unchanged; `git diff cee01e4 -- backend/tests` is empty).
Implementation: `611e58f`. Interface: `4807832`.

## What it answers

Given 2–6 candidate sites and one UTC window, the app ranks the candidates and explains:

- **Each ranking:** the score per criterion, its weighted contribution, and the strongest and weakest criterion.
- **Each rejection:** which hard constraint failed, with the actual value and the limit.
- **Each unranked candidate:** which input has unknown coverage.

The ranking is a planning aid for comparing sites under stated assumptions. It is not a landing-site certification.

## User flow

Open **Recommend** in the navigation. Then:

1. Pick presets or add custom coordinates (80–90° S).
2. Set the window (24 h, 3 d or 7 d) and the hard constraints.
3. Set the priority sliders.
4. Select **Rank candidate sites**.

Each candidate runs the existing Sun/Earth analysis, then the R2 DSN communication job, then the terrain screen below. The recommendation ID is remembered in the browser; results persist in the `analysis-state` volume.

- **Apply new priorities to this result (S9):** rescoring from the stored criterion scores. It runs no new analysis. The previous and new rankings appear side by side, with each rank change and the criterion that caused it.
- **Re-rank after this delay:** re-runs the same candidates and settings shifted by a schedule slip (for example 48 h). The app then shows each candidate's old and new status and rank, and suggests the best site at the new date.

## Terrain safety screen

| Item | Definition |
|---|---|
| DEM | Cached NASA PGDA product 90 `LDEM_80S_80MPP_ADJ` (LOLA, 80 m polar stereographic). It is checksum-verified and read-only in the backend. |
| Slope | Horn (1981) 3×3 finite difference in projected metres. A cell with unknown height, or next to one, has unknown slope. The stereographic scale factor (< 0.8 % at 80–90° S) is not corrected. |
| Landing area | Pixel centres within **100 m** of the site pixel (5 pixels at 80 m). Configurable from 40 to 500 m. |
| Steep terrain | Pixels steeper than the slope limit within **2 km**. The reported distance is centre to centre; none within 2 km counts as full clearance. |
| Slope limit | **10°** default (configurable 1–45°). |

NASA has not published a numeric slope limit for these regions. Its [Artemis III candidate region announcement](https://www.nasa.gov/news-release/nasa-identifies-candidate-regions-for-landing-next-americans-on-Moon/) lists slope as a criterion without a value. Published lunar lander studies commonly screen at 10°, for example the [ESA Lunar Lander site characterisation](https://arxiv.org/pdf/1208.5587) and [PROSPECT/CLPS slope classes](https://meetingorganizer.copernicus.org/EPSC2022/EPSC2022-530.html) (< 10°, < 5°). Treat the default as a planning assumption.

**Resolution limit:** an 80 m grid smooths slopes. It cannot see craters or boulders at lander scale (metres), which are the dominant local hazards in the ESA study above. A site that passes this screen still needs higher-resolution hazard analysis.

## Criteria and scores

Each criterion score lies between 0 and 1.

| Criterion | Score rule | Source |
|---|---|---|
| Energy | sunlight % / 100 (full + partial disk) | Task 3 analysis |
| Darkness | 1 − longest terrain-blocked sunlight / window | Task 3 analysis |
| Communication | combined DSN direct-to-Earth % / 100 at the chosen station limit (6° default) | R2 |
| Terrain safety | 0.5 · (1 − min(max landing slope / limit, 1)) + 0.5 · min(steep distance / 2 km, 1) | this screen |

The total is a weight-normalized mean: total = Σ wᵢ·sᵢ / Σ wᵢ. The contributions wᵢ·sᵢ / Σ wᵢ sum to the total. Default weights are equal (each 1, range 0–10, at least one positive). Ties break by candidate ID.

**Not modeled yet:**

- **Fuel and reachability:** after the R4/R5 descent trajectories.
- **Science value:** no validated dataset is integrated.

These are listed with every result.

## Hard constraints and uncertainty

A candidate is **rejected** only when a constraint fails for certain. All failures are listed in this order:

1. Terrain coverage is missing.
2. Maximum landing-area slope > limit, among the known pixels.
3. The optional minimum sunlight % is not met, even if all unknown time were sunlit.
4. The optional minimum DSN communication hours is not met, even if all unknown time were available.

A candidate is **insufficient_data** (not ranked) when any of these is unknown and no certain failure exists:

- sunlight duration;
- Earth visibility or communication duration;
- landing-area slope.

Unknown coverage is never treated as darkness, blocked Earth or flat terrain.

Recommendation statuses:

- `ready`: every candidate was evaluated.
- `partial`: some candidates failed or have insufficient data.
- `failed`: no candidate could be evaluated.

At most 2 recommendations run at once. Each one queues its analysis jobs (shared limit 8) and communication jobs (shared limit 4).

## API

- `POST /recommendations`: body `{candidates[2..6], start_utc, end_utc, step_s, refine, min_elevation_deg, constraints, weights}`. Returns `202`. Returns `422` for invalid input and `429` when the queue is full.
- `GET /recommendations/{id}`: returns the candidates with their analysis and communication job IDs, terrain safety and evaluation, plus the `ranking` (`ranked`, `rejected`, `insufficient_data`, `failed`, normalized `weights`, `not_modeled`). A rescheduled run also includes `schedule_change`.
- `POST /recommendations/{id}/rescore`: body = weights. Returns `{previous, current, changes[{candidate_id, previous_rank, new_rank, reason}]}`. Returns `409` when the recommendation is not finished.
- `POST /recommendations/{id}/reschedule`: body `{"delay_hours": 0 < h ≤ 720}`. Returns `202` with the new recommendation linked by `previous_id`. Returns `409` when not finished and `422` when the shifted dates fall outside 2026–2027.

## Verification (2026-11-14, local Docker)

**Tests:** backend 379, ingestion 112 and frontend 23. Ruff and the production build pass.

**Malapert R23 versus Mons Mouton feature center, 2026-11-04, 24 h (refined):**

- Malapert has a site slope of 3.9° and a 5.6° maximum in the landing area. Steep terrain is 288 m away. It ranks first with a total score of 0.18 (sunlight 20.5 %, no DSN window).
- Mons Mouton's feature center (a summit) has a site slope of 9.4° and an 11.1° landing-area maximum. It is **rejected**: slope 11.1° exceeds the 10° limit.
- Rescoring toward energy kept the order and reported no rank change.

**48 h slip:** Malapert reaches 100 % sunlight and its total rises to 0.57. Mons Mouton is still rejected. The suggestion is that Malapert remains the best option.
