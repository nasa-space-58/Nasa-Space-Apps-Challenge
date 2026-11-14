# Cinematic lunar interface

## Design and implementation

The introductory experience follows four chapters: Moon and LRO, orbital reconnaissance, Apollo approach, and surface contact. Native scrolling controls the scene; the page does not capture or replace wheel/touch scrolling. A brief dark transition bridges the globe and local scene. Scientific work remains in the existing observation workspace.

- Existing NASA Apollo and LRO GLBs remain local. A small NASA CGI Moon Kit relief preview adds globe surface detail.
- Original procedural crater geometry, surface grain, directional shadows, a restrained engine plume and radial dust provide the illustrative landing environment. The mesh has 131,072 triangles, rather than importing the proposed multi-million-triangle commercial terrains.
- The terrain is not a DEM, a named landing site, or a scientific elevation model. Scene coordinates and camera motion are artistic. Apollo is an illustrative vehicle, not a historical south-pole mission. No fuel, velocity, trajectory or landing-safety data is fabricated.
- The existing API remains responsible for terrain-relative scientific results. API contracts, processing, presets, comparisons, DSN communication, classroom lessons, exports and window search are unchanged.
- A warm neutral accent, clearer type hierarchy, larger controls and consistent panel spacing carry the visual treatment into the workspace, search and classroom.

## Assets

The proposed Desertsage Flat 1/2/3 and Mountain 4 assets were reviewed but not downloaded: free use/download rights were not confirmed. Mountain 4 redirects to a Fab purchase listing. No purchase was made and no commercial model is bundled.

Sources:
- NASA CGI Moon Kit: https://svs.gsfc.nasa.gov/4720/
- Apollo Lunar Module: https://science.nasa.gov/3d-resources/apollo-lunar-module/
- LRO: https://science.nasa.gov/3d-resources/lunar-reconnaissance-orbiter-a/
- Reviewed Mountain 4: https://www.fab.com/listings/68e89742-b141-44aa-aa95-1f50320cb7ca

The new 1K relief preview is only a decorative globe bump map. Its credit, original URL, size and SHA-256 are recorded in `frontend/public/nasa/manifest.json`. Reproduce its download from the repository root:

```sh
curl -L --fail --max-time 30 \
  https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/ldem_3_8bit.jpg \
  -o frontend/public/nasa/moon-relief.jpg
shasum -a 256 frontend/public/nasa/moon-relief.jpg
```

## Motion, accessibility and rendering

- Ambient motion can be paused without blocking scroll or observation controls. System reduced motion takes precedence.
- The animation loop stops when the introduction is off screen, the document is hidden, or ambient motion is paused. The scene updates on demand for scroll/resize in reduced-motion mode.
- Mobile uses its own camera composition, pixel ratio 1 and 1024-pixel shadow maps; desktop caps pixel ratio at 1.5 and uses 2048-pixel shadows.
- A keyboard skip button opens and focuses mission control. Navigation and the introductory calls to action also bypass the introduction.
- Missing WebGL, scene-loading errors and WebGL context loss retain a static Moon fallback and usable scientific controls.
- The 3D bundle remains lazy loaded. Vite still reports a large 3D chunk (about 270 KB gzip); no new runtime dependency was introduced. React Three Fiber emits a Three.Clock deprecation warning with the current pinned Three.js version; it did not prevent rendering.

## Verification — 2026-11-14

- TypeScript and production build passed.
- 24 frontend tests passed, including three new presentation/navigation tests. Existing frozen tests were not changed.
- Desktop 1440×1000 and mobile 390×844 scenes inspected; approach framing corrected after visual review.
- No horizontal overflow at 320, 390, 768 and 1440 pixels. Mission control, time search and classroom additionally checked at 390 and 1440 pixels.
- Browser checks passed for pause (identical successive canvas screenshots), reduced motion, source-dialog Escape dismissal, simulated WebGL context loss and continued access to analysis.
- A real local Malapert analysis completed through the API: 20.5% sunlight, 0.0% Earth-center visibility, 19.1 h longest darkness for the default 24-hour example. The playback slider updated UTC to 04 Nov, 04:05. These are example results, not site-wide claims.
- DSN request initiation was exercised. The subsequent NASA station-data fetch is an external operation, separate from the verified UI rendering.

Preview uses the existing isolated `nasa-task4` project at http://localhost:5174/ and API port 8001. The separate team preview on 5173 is not restarted.
