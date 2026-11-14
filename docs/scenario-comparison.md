# Task 5 — Compare locations and dates

Branch `feature/task5-scenario-comparison` starts from current tahakrcc, merge `c29086d`. Delivery is to this new branch only; tahakrcc/main integration remains a separate user action.

## Interface

Run the existing Compose preview and open http://localhost:5174. Complete a single-site analysis, then select **Add comparison**. That saved result becomes the immutable A baseline. B copies its submitted duration, observer height, sampling, refinement and horizon settings.

- **Same place, different date:** edit B's UTC start or select **Use +48-hour delay**. Its end shifts by the same amount, preserving duration. The API still enforces the supported date range.
- **Same date, different place:** choose a sourced preset or enter supported coordinates. Dates and above-ground observer height stay fixed; the backend resolves the new site's reference-sphere altitude from its own terrain pixel.
- **Run scenario B:** submits a normal durable analysis job through the existing ingestion pipeline. A stays available if B fails. Pending, failed, incompatible and insufficient-data outcomes remain explicit.
- Desktop shows A and B side by side. Mobile provides keyboard-operable A/B tabs. The shared time control, input draft and saved results survive tab changes. Closing/reopening comparison restores the B draft and job ID from localStorage keyed by A's job ID.
- Editing B after a completed run displays a stale-results notice until it is run again. The cards themselves retain their actual submitted coordinates and UTC dates. The main single-site form remains independent.

The introductory NASA scene remains unchanged. A short glossary explains horizon, elevation, partial sunlight and Earth-center visibility. Results remain geometric estimates, not landing or communication guarantees.

## API and compatibility

`POST /comparisons` takes existing completed analysis IDs:

```json
{"a_id": "<saved A job ID>", "b_id": "<saved B job ID>"}
```

It returns `mode` (sites, dates or identical), `alignment`, duration, start offset, B-minus-A metric deltas, differing state segments, synchronized cursor indices, analysis/job IDs and factual explanations. The endpoint reads saved results and does not download data, alter jobs or run scientific calculations in the browser.

- 404: a job is missing.
- 409: either job is pending/running/failed and has no completed result.
- 422: invalid body or incompatible result parameters.
- 200 with `status=insufficient_data`: a completed result has unknown coverage. Deltas remain inspectable; the explanation explicitly disallows treating them as a reliable ranking.

Compatibility requires equal positive duration, sampling step, horizon settings/model, terrain source hash, visibility model, refinement status, sensitivity offset and observer AGL height (absolute floating-point tolerance 1e-6 m). A site change must preserve the UTC interval; a date change must preserve the coordinates/reference frame. Changing both at once is rejected. Different sites may have different sphere altitudes while retaining the same AGL height. Identical cases are supported with zero deltas.

## Time and metric semantics

Deltas are **B minus A**, including visible hours, longest darkness/blockage, longest continuous visibility, unknown seconds and visible percentage points. These use the full observation window, not the current cursor. No automatic winner or safety score is produced.

The cursor combines the union of elapsed sample times in both results. At each cursor position, each view references its nearest preceding calculated sample, with the original timestamp and sample age displayed. No synthetic/interpolated sky angles are claimed. If a preceding sample is absent, the view says unknown. Cursor playback follows samples, not uniform wall-clock acceleration.

Both timelines span the same elapsed duration. Site comparisons therefore share UTC; date comparisons show different UTC dates at the same elapsed offset. The two horizon plots share elevation bounds. Changed-state segments are computed from the union of the existing interval boundaries and list A/B states and elapsed offsets. Unknown/boundary states retain their meaning from Task 3. These comparisons do not improve the underlying terrain/sampling accuracy.

## Verification

Non-UI skeleton: `c0688e1`. Original tests: `3a5478f`. The user explicitly approved a formatting-only test correction; `27d92be` is the final frozen baseline. Expectations were not changed. Tests verify arithmetic, 48-hour offset, deterministic/no-mutation behavior, compatibility failures, unknown coverage, irregular sample alignment, missing initial sample and endpoint status handling. Four new UI tests cover B submission, preserved shared settings, saved result restoration, synchronized views, tab preservation and incompatible-result errors.

All 128 backend + 75 ingestion + 9 frontend tests passed (212 total), as did Ruff, pip check, production build and npm audit. The existing dependency deprecation/build-size advisories remain unchanged.

Real local cases on 2026-11-14:

1. Malapert, 2026-11-04 for 24 h versus the same site starting 48 h later: +19.0727 h modeled sunlight, −19.0727 h longest darkness, no Earth-visible-duration difference.
2. Malapert versus Mons Mouton, same 2026-11-04 interval: B has −4.9273 h modeled sunlight and +6.0768 h Earth-center visibility.
3. Two 300 km horizon searches with incomplete terrain coverage: comparison returns insufficient_data and retains unknown coverage rather than ranking the sites/dates.

The first two use 2 m AGL, default 100 km horizon, hourly base samples and transition refinement. These values describe those specific model windows only. See [machine-readable evidence](verification/task5-comparison-2026-11-14.json).

Chromium checks covered desktop 1440×1000, mobile 390×844, synchronized playback, preserved baseline/draft after closing and reopening, hidden mobile panels and no horizontal overflow or page errors. Broader teammate/device review remains in Task 6.
