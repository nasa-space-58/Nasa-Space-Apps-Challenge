# Initial terrain coverage and source-backed sites

Verified on 2026-11-14. This closes the Step 0 input-definition item. Operational
terrain ingestion, horizon computation and UI preset selection remain Step 1/2/4
work. These are demonstration observers, not certified safe landing sites.

## Selected terrain product

Use NASA GSFC PGDA product 90, `LDEM_80S_80MPP_ADJ.TIF`, for initial regional
terrain acquisition. The source describes 80–90° south coverage, height in meters,
and a DE421 MOON_ME south-polar stereographic coordinate system. Its 80 m cells
are an initial development choice; finer products can be evaluated later.

- [Product and data policy](https://pgda.gsfc.nasa.gov/products/90)
- [Direct COG](https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_80MPP_ADJ.TIF)
- [Dataset DOI](https://doi.org/10.60903/gsfcpgda-lola-spole)
- Required scientific attribution: Barker et al. (2023), PSJ 4, 183,
  [doi:10.3847/PSJ/acf3e1](https://doi.org/10.3847/PSJ/acf3e1).

Live file inspection found 7,600 × 7,600 float32 cells, 80 × 80 m spacing,
NaN NoData, scale 1 and offset 0. Projected bounds are −304,000 to +304,000 m
on both axes. It is a DEFLATE-compressed COG. The square includes corners beyond
the nominal latitude boundary; those corners do not enlarge the declared domain.
HTTP range access returned 206. Source size: 189,158,392 bytes; Last-Modified:
2023-07-07 22:03:02 GMT. Weak ETag: `W/"b4653f8-189326141f0"` (not a checksum).

## Initial observers

| Stable ID | Latitude | East longitude [0,360) | Coordinate source |
|---|---:|---:|---|
| mons-mouton-feature-center | −84.60° | 31.00° | [IAU/USGS feature 16070](https://planetarynames.wr.usgs.gov/Feature/16070), center, LOLA 2011 |
| malapert-massif-r23 | −85.9948° | 359.76422° | [NASA Lunar Surface Data Book](https://ntrs.nasa.gov/api/citations/20250002845/downloads/M2M-30044%20Lunar%20Surface%20Data%20Book%20Baseline_Final.pdf#page=75), Table A2.1-1, p.75, R23 |

USGS's Mons Mouton coordinate describes the named feature's center, rounded to
0.01 degree. It is not the IM-2 landing coordinate or a surveyed summit.
NASA's R23 is a DEM-derived region/polygon center. Its published longitude
−0.23578° is normalized by adding 360°. Malapert Massif is distinct from
Malapert crater and Malapert A. No coordinates from the old approximate mission
list in data-sources.md are activated as presets.

The machine-readable catalog is [initial-sites.json](initial-sites.json).
`runtime_preset_enabled: false` records that this task defines inputs; UI/API
selection has not yet been implemented.

## Actual coverage checks

The source was accessed through HTTP ranges; the whole 189 MB raster was not
required. For each point a 501 × 501 pixel window was read at native resolution,
centered on its containing pixel. Each window spans 40.08 × 40.08 km in projected
coordinates. Every one of the 251,001 cells in each window was finite and unmasked.

| Observer | Pixel row / column (zero-based) | Surface height at containing pixel | Window min / max height |
|---|---|---:|---:|
| Mons Mouton center | 2044 / 4854 | 5039.157 m | 2850.276 / 7026.331 m |
| Malapert Massif R23 | 2281 / 3793 | 4784.625 m | −1467.259 / 5172.486 m |

Verified window bounds (left, bottom, right, top), in projected meters:

- Mons Mouton: (64320, 120400, 104400, 160480).
- Malapert Massif: (−20560, 101440, 19520, 141520).

Initial **verified data availability** is limited to these two windows. The
source's wider nominal coverage is an acquisition target, not a blanket guarantee
that every arbitrary input point and its required surroundings are valid.
Terrain ingestion must check bounds, masks and finite values again after download.
An analysis requiring terrain outside its validated window must report incomplete
coverage or fetch a wider area. This task does not set a scientifically sufficient
horizon-search radius; distant occluders and resolution convergence belong to
Step 2. Valid DEM values do not prove flatness, safety or direct LOLA shot coverage.

## Frames, vertical reference and Horizons compatibility

[The product paper](https://ntrs.nasa.gov/api/citations/20230014419/downloads/BarkernewviewSTIupdated.pdf),
pp.3–4, specifies the DE421 MOON_ME frame and a 1737.4 km projection radius.
The [NASA data book](https://ntrs.nasa.gov/api/citations/20250002845/downloads/M2M-30044%20Lunar%20Surface%20Data%20Book%20Baseline_Final.pdf#page=73),
p.73, defines LDEM elevation relative to the 1737.4 km lunar radius; product 90's
height values are meters. The inspected GeoTIFF also uses a 1,737,400 m sphere.
Do not treat its generic `Moon (2015)` CRS name alone as proof of the rotational
frame; that provenance comes from the product documentation.

The [JPL Horizons manual](https://ssd.jpl.nasa.gov/horizons/manual.html#reference-frames)
identifies the high-precision lunar cartographic frame as DE421 Mean-Earth.
Consequently the live `MEAN_ME (high precision)` observer metadata is compatible
with this mapping frame. `DE441` in the target ephemeris header does not imply
that the supplied surface longitude is in a different cartographic frame.
No additional frame rotation is introduced for these inputs. On the adopted
sphere, planetographic and planetocentric latitude coincide. Longitudes remain
east-positive and are normalized to [0,360).

For the proposed 2 m observer height, use:

```text
altitude_km = (terrain_surface_height_m + observer_height_above_terrain_m) / 1000
```

This gives provisional altitudes 5.0411572265625 km and 4.78662451171875 km,
respectively. These use the containing 80 m cell, not interpolated or surveyed
ground elevations. Preserve that uncertainty; do not claim millimeter accuracy
from the stored decimals. The old 0° E / 89.5° S / 0 km Horizons fixture remains a
spherical-reference parser test, not a terrain-adjusted third preset.

## Reproducibility and retained evidence

[Inspection results](verification/terrain-2026-11-14.json) record CRS WKT,
transforms via projected coordinates, masks, statistics and SHA-256 of each local
crop. The crop checksums identify generated subsets, not the full remote file.
The full file was not checksummed or ingested into the application data volume.

The disposable inspection environment used Python 3.12.12 (Linux amd64),
Rasterio 1.4.3 / GDAL 3.9.3, NumPy and libexpat1. Existing application dependency
locks were not changed. Source PDFs, HTTP metadata, cropped rasters and the
executed inspection script are retained locally, outside Git, under
`data/terrain-verification/2026-11-14/`.

The read procedure was: open `/vsicurl/<source-url>`, transform each source
longitude/latitude using a lunar longlat CRS with R=1737400 into the dataset CRS,
locate the containing cell, read a 501-cell square centered on that cell, verify
bounds and all finite/unmasked values, save crops and hash them. This is a
research verification procedure, not the production ingestion implementation.

## Artemis III candidate region presets (2026-11-15)

`shared/presets.json` now lists one runtime preset for each of the nine
Artemis III candidate landing regions in
[NASA release 24-136](https://www.nasa.gov/news-release/nasa-provides-update-on-artemis-iii-moon-landing-regions/)
(2024-10-28). NASA's [LPSC 2026 update](https://ntrs.nasa.gov/citations/20250011660)
lists the same nine regions. Each preset carries `artemis_iii_region` and
`coordinate_basis`. None of them is a certified landing site.

NASA publishes the 2024 regions as outlines on a map, with no point
coordinates. Each representative point was chosen as follows, in this order
of preference:

1. `nasa_region_center`: a DEM-derived polygon center from the NASA
   [Lunar Surface Data Book](https://ntrs.nasa.gov/api/citations/20250002845/downloads/M2M-30044%20Lunar%20Surface%20Data%20Book%20Baseline_Final.pdf#page=75),
   Table A2.1-1. These are the 2022 region centers, retained because the
   2024 list kept these region names.
2. `lroc_region_mosaic_center`: the center of the LROC NAC controlled
   mosaic that the LRO camera team produced for the named Artemis III
   candidate region. Centers are published to 0.1°. For Haworth, Nobile
   Rim 2 and Malapert, these mosaic centers agree with the Data Book
   centers within about 0.1°.
3. `iau_feature_center` / `iau_feature_anchor`: the center of the named
   feature in the USGS/IAU gazetteer.

| Region | Stable ID | Latitude | East lon [0,360) | Basis and source | Containing-pixel height |
|---|---|---:|---:|---|---:|
| Peak near Cabeus B | peak-near-cabeus-b | −83.6° | 291.2° | LROC [NAC_ROI_CABEUSPKLO1](https://data.lroc.im-ldi.com/lroc/view_rdr/NAC_ROI_CABEUSPKLO1) | 4534.785 m |
| Haworth | haworth | −86.7639° | 337.223° | Data Book, Haworth | 1631.480 m |
| Malapert Massif | malapert-massif-r23 | −85.9948° | 359.76422° | Data Book, R23 | 4784.625 m |
| Mons Mouton Plateau | mons-mouton-plateau | −84.3° | 30.6° | LROC [NAC_ROI_MOUTNPLTLO1](https://data.lroc.im-ldi.com/lroc/view_rdr/NAC_ROI_MOUTNPLTLO1) | 5289.486 m |
| Mons Mouton | mons-mouton-feature-center | −84.6° | 31.0° | IAU feature [16070](https://planetarynames.wr.usgs.gov/Feature/16070) | 5039.157 m |
| Nobile Rim 1 | nobile-rim-1-r06 | −85.4381° | 37.36667° | Data Book, R06 | 5932.139 m |
| Nobile Rim 2 | nobile-rim-2-dm2 | −83.954° | 58.82206° | Data Book, DM2 | 1009.322 m |
| de Gerlache Rim 2 | de-gerlache-rim-2-sl2 | −88.23° | 295.3716° | Data Book, SL2 (−64.6284°) | −92.201 m |
| Slater Plain | slater-plain-crater-anchor | −88.08° | 111.29° | IAU feature [15325](https://planetarynames.wr.usgs.gov/Feature/15325), anchor | −1745.477 m |

**Slater Plain** has no published NASA or LROC point. At the user's decision
of 2026-11-15, the preset uses the IAU center of Slater crater as an
anchor. It is not the center of the plain. The point is likely in or near
permanent shadow, so its Sun results do not represent the region's best
sites. Replace it when NASA publishes region coordinates or outlines.

Heights come from the containing 80 m cell of `LDEM_80S_80MPP_ADJ.TIF`.
They were read from the application's raw terrain cache. Every 501 × 501
window around each point was finite. The two Step 0 presets reproduced
their recorded heights and windows exactly. Evidence:
[artemis3-regions-2026-11-15.json](verification/artemis3-regions-2026-11-15.json).

No ingestion change is needed. All points lie within the single 80–90° S
product that the worker already downloads. The default 100 km horizon
search stays inside the product's nominal 80° S boundary. The closest case
is Peak near Cabeus B, which reaches about 294 km against a boundary at
about 304 km. `Malapert Massif` stays last in the catalog because the
analysis job bootstraps the raw terrain cache with the last preset.
