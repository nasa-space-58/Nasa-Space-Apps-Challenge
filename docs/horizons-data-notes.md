# 2gbram · Lunar Landing Site Navigator

## NASA/JPL Horizons data and query notes

The goal is to compute the positions of the Sun and Earth relative to the local horizon for candidate sites at the Moon's south pole and to compare their visibility windows.

## 1. Example API requests

- [Sun · COMMAND=10](https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND=%2710%27&MAKE_EPHEM=YES&OBJ_DATA=NO&EPHEM_TYPE=OBSERVER&CENTER=coord%40301&COORD_TYPE=GEODETIC&SITE_COORD=%270%2C-89.5%2C0%27&START_TIME=%272026-11-14%2000%3A00%27&STOP_TIME=%272026-11-15%2000%3A00%27&STEP_SIZE=1h&QUANTITIES=4&APPARENT=AIRLESS&CSV_FORMAT=YES&EXTRA_PREC=YES&ELEV_CUT=-90&SKIP_DAYLT=NO)
- [Earth · COMMAND=399](https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND=%27399%27&MAKE_EPHEM=YES&OBJ_DATA=NO&EPHEM_TYPE=OBSERVER&CENTER=coord%40301&COORD_TYPE=GEODETIC&SITE_COORD=%270%2C-89.5%2C0%27&START_TIME=%272026-11-14%2000%3A00%27&STOP_TIME=%272026-11-15%2000%3A00%27&STEP_SIZE=1h&QUANTITIES=4&APPARENT=AIRLESS&CSV_FORMAT=YES&EXTRA_PREC=YES&ELEV_CUT=-90&SKIP_DAYLT=NO)

| Parameter | Value | Description |
|---|---|---|
| `COMMAND` | `10` or `399` | Center of the Sun and of Earth |
| `EPHEM_TYPE` | `OBSERVER` | Observer ephemeris |
| `CENTER` | `coord@301` | User-defined observer on the Moon |
| `COORD_TYPE` | `GEODETIC` | Geodetic coordinates |
| `SITE_COORD` | `0,-89.5,0` | East longitude and latitude in degrees, observer altitude in km |
| `START_TIME` | `2026-11-14 00:00` | UTC start |
| `STOP_TIME` | `2026-11-15 00:00` | UTC stop |
| `STEP_SIZE` | `1h` | Hourly sampling |
| `QUANTITIES` | `4` | Apparent azimuth and elevation |
| `APPARENT` | `AIRLESS` | Atmospheric refraction off |
| `ELEV_CUT`, `SKIP_DAYLT` | `-90`, `NO` | Below-horizon and daylight samples included |
| `format`, `CSV_FORMAT`, `EXTRA_PREC` | `text`, `YES`, `YES` | CSV table inside text, with extra digits |
| `MAKE_EPHEM`, `OBJ_DATA` | `YES`, `NO` | Ephemeris table on, object property summary off |

Azimuth is measured clockwise from north: north 0°, east 90°, south 180°, west 270°. Elevation is measured from the plane perpendicular to the local zenith. The apparent position includes corrections such as light-travel time and aberration. In the live response, the `UT` label means UTC for these dates.

## 2. Live API check values

The example site is at 0° east, 89.5° south and 0 km altitude. This coordinate was chosen to test the query. The Horizons response reported the ephemeris as `DE441`, the lunar orientation model as `MEAN_ME (high precision)` and the reference sphere radius as 1737.4 km.

| Target | Azimuth at 14 Nov 2026 00:00 UTC | Elevation at the same time | Elevation range over 25 samples |
|---|---:|---:|---:|
| Sun | 126.627333° | +1.258335° | +1.25833° to +1.34951° |
| Earth | 358.773423° | −4.148545° | −4.14855° to −3.05352° |

The 25 samples per target span a 24-hour analysis period. The reference-horizon result derived from the hourly samples is 24 hours for the Sun's center and 0 hours for Earth's center. Terrain obstructions are not included in this computation.

## 3. Normalized data schema

The unique row key is `(site_id, sampled_at_utc)`. Match the Sun and Earth tables by UTC timestamp.

| Field | Type and unit | Description |
|---|---|---|
| `site_id` | Text | Stable candidate identifier |
| `longitude_deg_east` | Decimal degrees | East positive, [0, 360) |
| `latitude_deg` | Decimal degrees | South negative, [−90, 90] |
| `altitude_km` | Decimal km | Observer altitude relative to the reference surface |
| `sampled_at_utc` | ISO 8601 UTC | Example `2026-11-14T00:00:00Z` |
| `sun_azimuth_deg`, `earth_azimuth_deg` | Decimal degrees or null | [0, 360) |
| `sun_elevation_deg`, `earth_elevation_deg` | Decimal degrees or null | [−90, 90]. Keep negative values. |
| `source` | Text | NASA/JPL Horizons |
| `sun_request_id`, `earth_request_id` | Text | Raw query records for reproduction |

- Read the rows between `$$SOE` and `$$EOE`. There are extra flag columns between the timestamp and the angle columns. Take the angles from the `Azimuth_(a-app)` and `Elevation_(a-app)` fields in the CSV header.
- An HTTP 200 response can still contain a Horizons error. Validate the target, center, time scale, time series and expected row count. Mark missing or `n.a.` values as unknown.
- Store the full request URL, the raw response, the retrieval time, the API version and the ephemeris and orientation headers.

## 4. Visibility windows

1. **Reference horizon.** The clearance angle is computed as the target elevation minus 0°. A positive clearance indicates center visibility, a negative clearance below the horizon, and zero the boundary.
2. **Terrain horizon.** From the LOLA digital elevation model (DEM), derive an azimuth-dependent horizon profile for each candidate and observer height. Compute the highest obstruction angle in each direction, including lunar curvature. Subtract the corresponding obstruction angle from the target elevation. Use circular interpolation across the 359°–0° azimuth wrap. If coverage is insufficient, mark the result as unknown.
3. **Transition time.** If the clearance changes sign, estimate the transition with linear interpolation. Re-query regions near the boundary at 5-minute and, if needed, 1-minute steps. More than one transition can occur between two consecutive hourly samples. Sample the analysis interval more densely around narrow obstructions.
4. **Window fields.** `site_id`, `target`, `start_utc`, `end_utc`, `duration_s`, `horizon_model`, `refinement_step_s` and `quality_status`. Mark windows that touch the analysis boundary as clipped. Do not merge windows across missing data.
5. **Candidate comparison.** For the same analysis period, report the total visible duration, visibility percentage, the longest continuous window, the longest outage and the unknown duration.

## 5. Computation limits

- **Solar power.** Center visibility indicates potential access to energy. Real generation additionally depends on panel orientation, incidence angle, panel efficiency and eclipses. For partial solar-disk visibility, the angular diameter can be obtained with `QUANTITIES='4,13'`. Convert the diameter from arcseconds to degrees and divide by two. If the horizon elevation is roughly constant across the disk, center elevation plus radius is used to check partial visibility and center elevation minus radius to check full visibility.
- **Earth communication.** `COMMAND=399` represents Earth's center. A real link additionally depends on the selected station's location and availability, line of sight from both ends, the minimum elevation angle, antenna pointing and the link budget.
- **Terrain accuracy.** DEM resolution, elevation uncertainty, coverage and panel or antenna height affect near-horizon transitions. Match the coordinate frame and height reference of the DEM and Horizons. The number of decimal places the API displays does not determine the window's accuracy.

## 6. Sources and the challenge

### Tools in the challenge's resources tab

| Source | Use in the project |
|---|---|
| [LROC QuickMap](https://quickmap.lroc.im-ldi.com/) | Inspect candidate landing areas at the lunar south pole with LRO imagery and topography layers. |
| [NASA Moon Trek](https://trek.nasa.gov/moon/) | Interpret terrain and the local horizon by visualizing candidate sites and surrounding elevations in 3D. |
| [NASA/JPL Horizons System](https://ssd.jpl.nasa.gov/horizons/) | Obtain time-dependent Sun and Earth azimuth and elevation angles for each candidate site, and compare them with the terrain horizon to compute visibility windows for solar power and direct-to-Earth communication. |
| [Canadian Space Agency (CSA) · Moon exploration](https://www.asc-csa.gc.ca/eng/astronomy/moon-exploration/) | Background on lunar exploration missions, Canada's contributions and the Artemis program. |

### Technical documents and challenge references

- [NASA Lunar Landing Site Navigator challenge page](https://www.spaceappschallenge.org/2026/challenges/lunar-landing-site-navigator/)
- [NASA/JPL Horizons API documentation](https://ssd-api.jpl.nasa.gov/doc/horizons.html)
- [NASA/JPL Horizons user manual](https://ssd.jpl.nasa.gov/horizons/manual.html)
- [NASA South Pole LOLA elevation maps](https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/)
- [NASA South Pole LOLA DEM mosaic](https://pgda.gsfc.nasa.gov/products/81)
- [HackAthlone challenge resources](https://hackathlone.com/challenges/lunar-landing-site-navigator)
- [NASA Space Apps GO Team announcement](https://github.com/nasa/spaceapps/discussions/1827)

The HackAthlone copy of the challenge lists the Horizons System among the NASA resources. In the earlier review, the Resources tab of the NASA challenge page could not be verified directly. According to the GO Team announcement, Global Awards entries must use at least one NASA data source from the challenge's Resources list and state the sources used in the submission. The announcement says development started at 09:00 local event time on 14 November and that research was allowed beforehand.
