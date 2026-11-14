# Data Sources — Lunar Landing Site Navigator

> Challenge: **[Lunar Landing Site Navigator](https://www.spaceappschallenge.org/2026/challenges/lunar-landing-site-navigator/)** — NASA Space Apps Challenge 2026 (Sivas local event, 14–15 November 2026)
>
> This document ranks the challenge's official resources and additional APIs / datasets we researched **by how much value they add to the project**.
> Last checked: 14 November 2026. ✅ = page opened and verified · 🔎 = from search results, still needs to be opened and checked · 🧪 = tested by the team against the live API.
>
> Horizons query details, the data schema and the visibility-window algorithm are summarized here from [2gbram_horizons_veri_notlari.md](2gbram_horizons_veri_notlari.md); see that file for the full details.

## Implementation decision

JPL Horizons is selected for the MVP. The ingestion worker fetches and caches ephemerides and LOLA terrain data. The backend reads the shared volume and computes visibility. React displays API results. The catalog below preserves researched alternatives for future evaluation. Release priorities and tasks are tracked in [mvp-roadmap.md](mvp-roadmap.md).

## ⚠️ Competition rule (use of resources)

According to the [NASA Space Apps GO Team announcement](https://github.com/nasa/spaceapps/discussions/1827), to be eligible for **Global Awards** a team **must use at least one NASA data source from the challenge's Resources list and name it in the submission**. For us this is **JPL Horizons** (on the official list). The announcement also states that development started at **09:00 local time on 14 November**; only research was allowed before that.

---

## What the challenge asks for, in one sentence

For a chosen **landing site + date**, show **the positions of the Sun and Earth relative to the horizon**, derive **power (solar energy)** and **direct-to-Earth communication** windows from them, and let users compare multiple sites/dates quickly.

This requires three kinds of data:

| Need | Provides | Best source |
|---|---|---|
| **Sky positions** | Sun and Earth azimuth + elevation over time | JPL Horizons / Skyfield + SPICE |
| **Terrain** | Surrounding mountain/crater heights → real horizon | LOLA elevation models (PGDA) |
| **Site list** | Apollo, CLPS, Artemis and other nations' landing sites + coordinates | NASA CLPS page, Artemis announcements, USGS |

---

## Ranking by value

| Rank | Source | Role | Value | On the official Resources list? |
|---|---|---|---|---|
| 1 | JPL Horizons API | Sun/Earth positions | ⭐⭐⭐⭐⭐ | ✅ Yes |
| 2 | PGDA – LOLA South Pole DEMs | Real horizon computation | ⭐⭐⭐⭐⭐ | No (LOLA is suggested in the challenge text) |
| 3 | PGDA – Lunar Polar Illumination | Precomputed sunlight + Earth visibility maps | ⭐⭐⭐⭐⭐ | No |
| 4 | Skyfield + NAIF SPICE kernels | Offline alternative to Horizons | ⭐⭐⭐⭐ | No |
| 5 | Moon Trek WMTS API | 2D map layers (web) | ⭐⭐⭐⭐ | ✅ Yes (Moon Trek) |
| 6 | Cesium Moon Terrain | Ready-made 3D Moon globe + terrain | ⭐⭐⭐⭐ | No |
| 7 | Quickmap (LROC) | Exploration, finding coordinates | ⭐⭐⭐ | ✅ Yes |
| 8 | Landing site lists (CLPS, Artemis, Apollo, USGS) | Ready-made site catalog | ⭐⭐⭐ | Partially |
| 9 | NASA SVS CGI Moon Kit | Texture + elevation for a 3D globe | ⭐⭐⭐ | No |
| 10 | Other high-resolution DEMs (PGDA 78/81/104, LROC NAC DTM) | Close-up of a single site | ⭐⭐ | No |
| 11 | CSA Moon Exploration | Presentation / background content | ⭐ | ✅ Yes |

---

## 1. JPL Horizons API ⭐⭐⭐⭐⭐ ✅

- **What:** NASA JPL's ephemeris system. From **a user-defined point on the lunar surface** it returns the **azimuth and elevation (AZ & EL)** of the Sun and Earth over any date range and step size. Lunar libration is already part of the model.
- **Links:**
  - API documentation: https://ssd-api.jpl.nasa.gov/doc/horizons.html
  - Manual (output quantities, observer site definition): https://ssd.jpl.nasa.gov/horizons/manual.html
  - Web interface: https://ssd.jpl.nasa.gov/horizons/
- **Endpoint:** `GET https://ssd.jpl.nasa.gov/api/horizons.api`
- **Parameters** (🧪 tested live by the team):

  | Parameter | Value | Description |
  |---|---|---|
  | `COMMAND` | `10` / `399` | Sun / Earth center |
  | `EPHEM_TYPE` | `OBSERVER` | Observer ephemeris |
  | `CENTER` | `coord@301` | User-defined observer on the Moon (301 = Moon) |
  | `COORD_TYPE` | `GEODETIC` | Geodetic coordinates |
  | `SITE_COORD` | `'0,-89.5,0'` | East longitude (°), latitude (°), altitude (km) |
  | `START_TIME` / `STOP_TIME` | `'2026-11-14 00:00'` / `'2026-11-15 00:00'` | UTC |
  | `STEP_SIZE` | `1h` | Hourly sampling |
  | `QUANTITIES` | `4` (`4,13` for partial disk) | 4 = apparent AZ & EL · 13 = target angular diameter |
  | `APPARENT` | `AIRLESS` | Atmospheric refraction off (the Moon has no atmosphere) |
  | `ELEV_CUT` / `SKIP_DAYLT` | `-90` / `NO` | Include below-horizon and daylight samples |
  | `format` / `CSV_FORMAT` / `EXTRA_PREC` | `text` / `YES` / `YES` | CSV table inside text, extra precision |
  | `MAKE_EPHEM` / `OBJ_DATA` | `YES` / `NO` | Ephemeris table on, object summary off |

- **Working example queries:**
  - [Sun](https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND=%2710%27&MAKE_EPHEM=YES&OBJ_DATA=NO&EPHEM_TYPE=OBSERVER&CENTER=coord%40301&COORD_TYPE=GEODETIC&SITE_COORD=%270%2C-89.5%2C0%27&START_TIME=%272026-11-14%2000%3A00%27&STOP_TIME=%272026-11-15%2000%3A00%27&STEP_SIZE=1h&QUANTITIES=4&APPARENT=AIRLESS&CSV_FORMAT=YES&EXTRA_PREC=YES&ELEV_CUT=-90&SKIP_DAYLT=NO)
  - [Earth](https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND=%27399%27&MAKE_EPHEM=YES&OBJ_DATA=NO&EPHEM_TYPE=OBSERVER&CENTER=coord%40301&COORD_TYPE=GEODETIC&SITE_COORD=%270%2C-89.5%2C0%27&START_TIME=%272026-11-14%2000%3A00%27&STOP_TIME=%272026-11-15%2000%3A00%27&STEP_SIZE=1h&QUANTITIES=4&APPARENT=AIRLESS&CSV_FORMAT=YES&EXTRA_PREC=YES&ELEV_CUT=-90&SKIP_DAYLT=NO)
- **🧪 Live check values** (0° E, 89.5° S, 0 km · 25 hourly samples). Response header: ephemeris `DE441`, lunar orientation `MEAN_ME (high precision)`, reference sphere radius 1737.4 km.

  | Target | Azimuth at 14 Nov 2026 00:00 UTC | Elevation | Elevation range over 25 samples |
  |---|---:|---:|---:|
  | Sun | 126.627333° | +1.258335° | +1.25833° … +1.34951° |
  | Earth | 358.773423° | −4.148545° | −4.14855° … −3.05352° |

  → Against the flat (reference) horizon, the Sun's center is visible for all 24 hours and Earth is never visible. Terrain obstruction is **not** included in this result.
- **Angle conventions:** Azimuth is measured clockwise from north (N 0°, E 90°, S 180°, W 270°). Elevation is measured from the plane perpendicular to the local zenith. Apparent positions include light-time and aberration corrections. `UT` in the response means UTC for these dates.
- **Parsing the response:**
  - Rows are between `$$SOE` and `$$EOE`. Take the angles from the `Azimuth_(a-app)` and `Elevation_(a-app)` CSV columns (there are extra flag columns in between).
  - **An HTTP 200 response can still contain a Horizons error.** Verify the target, center, time scale and expected row count; mark `n.a.` values as unknown.
  - Store the full request URL, the raw response, the fetch time, the API version and the ephemeris/orientation headers (reproducibility).
- **Caveats:**
  - Cannot be called directly from the browser (blocked by CORS — we tested this). → The ingestion worker fetches and caches responses; the frontend reads results through FastAPI.
  - Horizons elevation is relative to a **flat horizon**; mountains and craters are not taken into account. Sources 2 and 3 are needed for the real horizon.
- **Role in the project:** The core of the application. Visibility metrics combine these positions with the LOLA terrain horizon.

## 2. PGDA – LOLA South Pole Digital Elevation Models (DEM) ⭐⭐⭐⭐⭐ ✅

- **What:** South pole elevation maps produced by NASA Goddard from LOLA laser altimeter data. Down to **20 m/pixel** for 80–90° S; wider areas (75° S, 60° S) at lower resolution.
- **Links:**
  - https://pgda.gsfc.nasa.gov/products/90 ("A New View of the Lunar South Pole from LOLA")
  - 5 m/pixel data folder: https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/ (from the team's data notes)
- **Format:** Cloud-Optimized GeoTIFF (COG); south polar stereographic X/Y (meters), MOON_ME reference frame.
- **Example file:** `LDEM_80S_20MPP_ADJ.TIF` (surface height, meters). Error (`_ERR`), slope and other layers are available alongside it.
- **Role in the project:** **Horizon mask** computation. For each landing site, look around 360° and answer "how many degrees above flat is the horizon in this direction?". If the Sun is below that angle → shadow; if Earth is below it → no communication.
- **Tip:** Don't download whole files. Because they are COGs, you can read only the window around each landing site (`rasterio`/GDAL windowed reads). For the hackathon, a 60–120 m/pixel version is enough and much smaller.

## 3. PGDA – Lunar Polar Illumination (precomputed illumination + Earth visibility) ⭐⭐⭐⭐⭐ ✅

- **What:** **Average solar illumination** and **average Earth visibility** maps for the polar regions, computed using LOLA terrain (over a full nutation cycle, with an hourly time step).
- **Links:**
  - https://pgda.gsfc.nasa.gov/products/69 (Mazarico)
  - 🔎 PDS archive (GDRVIS product): https://ode.rsl.wustl.edu/moon/pagehelp/Content/Missions_Instruments/LRO/LOLA/GDR/GDRVIS.htm
  - 🔎 MIT LOLA server: https://imbrium.mit.edu/BROWSE/EXTRAS/ILLUMINATION
- **Resolution:** 240 m/pixel and 60 m/pixel (south + north pole).
- **Role in the project:** A heat-map layer on the map: **"how much sunlight does this area usually get / how often does it see Earth"**. Shows good vs. bad regions from NASA's own precomputed results without running our own computation. Also an excellent reference for **validating** our own calculations.

## 4. Future alternative — Skyfield + NAIF SPICE kernels ⭐⭐⭐⭐ ✅

- **What:** A Python astronomy library. Computes Sun/Earth altitude and azimuth **offline**, "relative to the astronaut's horizon" for an observer standing on the lunar surface (`altaz()`).
- **Links:**
  - Skyfield planetary reference frames (includes a view-from-the-Moon example): https://rhodesmill.org/skyfield/planetary.html
  - NAIF generic kernels (lunar orientation files under `pck/`): https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/
- **Why in addition to Horizons:** No network or rate-limit concerns; computes thousands of site × date combinations in seconds. A future alternative to evaluate if needed; the MVP uses cached Horizons responses.
- **Alternative:** `spiceypy` (Python wrapper for SPICE) — more powerful but harder to learn.

## 5. Moon Trek WMTS API ⭐⭐⭐⭐ ✅

- **What:** An API that serves NASA Moon Trek's map layers as a standard **WMTS tile** service. Layers include LRO image mosaics, elevation, slope and more.
- **Link:** https://trek.nasa.gov/tiles/apidoc/trekAPI.html?body=moon
- **URL template:** `https://{WMTS endpoint}/1.0.0/{Style}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.png|jpg`
  Each layer's `WMTSCapabilities.xml` contains what a client library (Leaflet / OpenLayers / Cesium) needs.
- **Role in the project:** The **2D landing site picker map** in the web app. Real NASA imagery without downloading anything.
- **Note:** For the polar regions, pick polar-projected layers (poles look distorted in equirectangular projection).

## 6. Cesium Moon Terrain ⭐⭐⭐⭐ ✅

- **What:** A ready-made terrain + imagery tileset of the whole Moon in 3D Tiles format. Built from LRO data, with **the highest resolution at the poles** (polar terrain from 60 m down to 5 m).
- **Links:**
  - https://cesium.com/platform/cesium-ion/content/cesium-moon/
  - 🔎 Announcement: https://cesium.com/blog/2024/08/06/introducing-cesium-moon-terrain/
- **Access:** Free Cesium ion account (free for non-commercial / educational use — check current terms). Runs in the browser with CesiumJS.
- **Role in the project:** The "wow" factor: zoom into the landing site in 3D and show the Sun and Earth in the sky. Much faster than building terrain from scratch in Three.js.
- **Caveat:** 🔎 One user reported that Cesium heights appear ~2.5 km different from LOLA (possibly a reference surface difference). **Compute the horizon from the LOLA DEM, not from Cesium**; use Cesium for visuals only.

## 7. Quickmap (LROC) ⭐⭐⭐ ✅ (official resource)

- **What:** Arizona State University's interactive Moon map with LRO imagery, topography and other layers.
- **Links:** https://quickmap.lroc.asu.edu/ · https://quickmap.lroc.im-ldi.com/ (current domain, from the team's data notes)
- **Role in the project:** A team tool during development to **explore landing sites, read coordinates and visually check our results** — not an API to embed in the app.

## 8. Landing site lists ⭐⭐⭐

The challenge suggests a list of "past Apollo missions, future CLPS targets, other commercial missions and other nations' landing sites".

| Source | Provides | Link |
|---|---|---|
| NASA CLPS Deliveries ✅ | All CLPS deliveries and targets (Malapert A, Mare Crisium, Mons Mouton, Reiner Gamma, Schrödinger, Gruithuisen Domes, far side…) | https://science.nasa.gov/lunar-science/clps-deliveries/ |
| Artemis III candidate regions 🔎 | The 9 south pole regions announced in 2024: Peak near Cabeus B, Haworth, Malapert Massif, Mons Mouton Plateau, Mons Mouton, Nobile Rim 1, Nobile Rim 2, de Gerlache Rim 2, Slater Plain | https://www.upi.com/Science_News/2024/10/28/nasa-artemis-moon-landing-regions/5971730152372/ (coordinates should come from NASA's official page) |
| USGS Gazetteer of Planetary Nomenclature ✅ | All IAU-approved lunar crater/mountain names + coordinates (downloadable as CSV/KML) | https://planetarynames.wr.usgs.gov/ |
| LROC IM-2 landing region 🔎 | Mons Mouton imagery and location | https://www.lroc.asu.edu/images/1401 |

**Starter list (approximate coordinates — verify in Quickmap before use):**

| Site | Latitude | Longitude (East +) | Source / note |
|---|---|---|---|
| Apollo 11 – Mare Tranquillitatis | 0.67° N | 23.47° E | General knowledge |
| Apollo 15 – Hadley Rille | 26.13° N | 3.63° E | General knowledge |
| Apollo 17 – Taurus-Littrow | 20.19° N | 30.77° E | General knowledge |
| IM-1 Odysseus – Malapert A | 80.13° S | 1.44° E | 🔎 Search result |
| IM-2 Athena – Mons Mouton | 84.78° S | 29.13° E | 🔎 Search result (target point) |
| Blue Ghost 1 – Mare Crisium | 18.56° N | 61.81° E | 🔎 Secondary source |
| Chandrayaan-3 (India) | ~69.4° S | ~32.3° E | General knowledge |
| Chang'e 4 (China) – Von Kármán | ~45.4° S | ~177.6° E | General knowledge (far side → Earth never visible; a good teaching example) |
| SLIM (Japan) | ~13.3° S | ~25.2° E | General knowledge |

## 9. NASA SVS CGI Moon Kit ⭐⭐⭐ ✅

- **What:** Whole-Moon **color map** (2025 version, more detail at the poles) and **elevation map** prepared for 3D software. EXR and 16-bit TIFF in several sizes.
- **Link:** https://svs.gsfc.nasa.gov/4720/
- **Role in the project:** Texture and displacement if we build our own Moon globe in Three.js. (Not needed if we use Cesium.)

## 10. High-resolution close-up DEMs ⭐⭐

For looking very closely at a single landing site. Files are large — only worth it for 1–2 demo sites.

| Source | What | Link |
|---|---|---|
| PGDA 78 🔎 | High-resolution LOLA topography for selected south pole sites | https://pgda.gsfc.nasa.gov/products/78 |
| PGDA 81 🔎 | South pole LOLA DEM mosaic | https://pgda.gsfc.nasa.gov/products/81 |
| PGDA 104 🔎 | Topography for selected south pole regions enhanced with shape-from-shading | https://pgda.gsfc.nasa.gov/products/104 |
| LROC NAC DTM 🔎 | Sub-meter orthophotos + DTMs for Apollo sites (hundreds of MB to several GB) | e.g. https://data.lroc.im-ldi.com/lroc/view_rdr_product/NAC_DTM_APOLLO11_M150368601_50CM |

## 10b. Competition references

- [HackAthlone copy of the challenge and its resources](https://hackathlone.com/challenges/lunar-landing-site-navigator) (from the team's data notes)
- [NASA Space Apps GO Team announcement](https://github.com/nasa/spaceapps/discussions/1827) — Global Awards resource rule and start time

## 11. CSA Moon Exploration ⭐ ✅ (official resource)

- **What:** The Canadian Space Agency's Moon exploration portal: its contributions to Artemis, past and future missions.
- **Link:** https://www.asc-csa.gc.ca/eng/astronomy/moon-exploration/
- **Role in the project:** Not technical data; context for the presentation and a "we used a partner agency resource" point.

---

## MVP data flow

1. Frontend submits a site and UTC analysis interval to FastAPI.
2. Backend checks cached data and publishes any missing-data request through RabbitMQ.
3. The ingestion worker retrieves Horizons responses and LOLA data, validates them, stores raw and processed files, and publishes ready/failed events.
4. Backend reads the data volume and computes the horizon, visibility windows and summary metrics.
5. Frontend displays the map, sky view, timeline and comparison. It exposes pending, failed and insufficient-coverage states.

The selected rendering libraries are React Three Fiber and Recharts, as specified in technologies.md.

### How to compute the horizon mask (summary)
1. For each azimuth direction around the landing site (e.g. 360 directions at 1° steps), march outward across the DEM (e.g. up to 50–150 km).
2. At each sample compute the elevation angle: `angle = atan((h − h0) / d) − d / (2R)` (R = 1737.4 km; the second term accounts for lunar curvature).
3. The **maximum angle** in that direction is the horizon for that azimuth.
4. Then, for each time step: `sun_visible = sun_el > horizon(sun_az)`, `earth_visible = earth_el > horizon(earth_az)`.

### Normalized data schema (from the team's data notes)

Unique row key: `(site_id, sampled_at_utc)`. Sun and Earth tables are joined on the UTC timestamp.

| Field | Type / unit | Description |
|---|---|---|
| `site_id` | Text | Stable candidate identifier |
| `longitude_deg_east` | Degrees | East positive, [0, 360) |
| `latitude_deg` | Degrees | South negative, [−90, 90] |
| `altitude_km` | km | Observer height above the reference surface |
| `sampled_at_utc` | ISO 8601 UTC | e.g. `2026-11-14T00:00:00Z` |
| `sun_azimuth_deg`, `earth_azimuth_deg` | Degrees or null | [0, 360) |
| `sun_elevation_deg`, `earth_elevation_deg` | Degrees or null | [−90, 90]; keep negative values |
| `source` | Text | NASA/JPL Horizons |
| `sun_request_id`, `earth_request_id` | Text | Raw query record |

### Visibility windows (from the team's data notes)

1. **Clearance:** `clearance = target_elevation − horizon_angle(target_azimuth)`. Positive → visible, negative → below the horizon, 0 → boundary. For a flat horizon `horizon_angle = 0`.
2. **Terrain horizon:** Use **circular interpolation across the 359°–0° azimuth wrap** in the horizon profile. If DEM coverage is insufficient, mark the result as unknown.
3. **Crossing time:** If clearance changes sign, estimate the crossing by linear interpolation; re-query intervals near the boundary at **5 min** (or 1 min if needed) steps. There can be more than one crossing between two hourly samples.
4. **Window record:** `site_id`, `target`, `start_utc`, `end_utc`, `duration_s`, `horizon_model`, `refinement_step_s`, `quality_status`. Windows touching the analysis boundary are marked as clipped; never merge windows across missing data.
5. **Candidate comparison:** total visible time, visibility percentage, longest continuous window, longest gap, unknown time.

### Metrics
- **Sun elevation angle** and **fraction of hours in sunlight** (%).
- **Power estimate:** At the pole the Sun is very low (~0–2°), so a horizontal panel is nearly useless; with a **vertical panel** assumption `power ∝ cos(sun_el)` (when the Sun is visible), and for a horizontal panel `∝ sin(sun_el)`. Comparing the two is a strong teaching point.
- **Time remaining until sunset / Earthset** (specifically emphasized in the challenge).
- **Communication window:** continuous intervals when Earth is above the horizon.
- **Partial solar disk:** Get the angular diameter with `QUANTITIES='4,13'` (arcseconds → degrees, divide by two = radius). `center + radius > horizon` → partially visible; `center − radius > horizon` → fully visible.

### Computation limits (state these honestly in the presentation)
- **Solar power:** Visibility only indicates *potential access* to energy. Actual generation depends on panel orientation, incidence angle, panel efficiency and eclipses.
- **Earth communication:** `COMMAND=399` is Earth's *center*. A real link also requires a ground station (DSN: Goldstone, Madrid, Canberra) to see the Moon, a minimum elevation angle, antenna pointing and a link budget.
- **Terrain accuracy:** DEM resolution, height uncertainty, coverage and panel/antenna height affect near-horizon crossings. The DEM and Horizons coordinate frames and height references must be matched. The number of decimal places returned by the API is not the accuracy of the window.

---

## Helper libraries

| Tool | Used for |
|---|---|
| `requests` / `astroquery.jplhorizons` (Python) | Horizons queries |
| `skyfield`, `spiceypy` (Python) | Offline position computation |
| `rasterio` / GDAL (Python) | Reading and clipping LOLA GeoTIFFs |
| `numpy` | Horizon mask computation |
| CesiumJS or Three.js | 3D view |
| Leaflet / OpenLayers | 2D WMTS map |
| Chart.js / D3 | Time series, power/communication charts |
| `geotiff.js` | Reading GeoTIFFs directly in the browser if needed |

---

## Open checks (to do in the first hours)

- [x] Run the Horizons example query and confirm the `coord@301` + `SITE_COORD` syntax. (🧪 done by the team)
- [x] Select JPL Horizons for Sun/Earth positions. Fetch through data-ingestion and cache results.
- [ ] Download a 60–120 m/pixel DEM from PGDA 90 and compute a horizon mask for one site.
- [ ] Compare our computed sunlight fraction with the PGDA 69 map.
- [ ] Get the coordinates of the 9 Artemis III regions from NASA's official page.
- [ ] Open and verify the links marked 🔎.
- [ ] Pick a polar-projected layer in Moon Trek and get its `WMTSCapabilities.xml`.
