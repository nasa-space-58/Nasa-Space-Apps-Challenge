# Horizons reference responses

Captured directly from https://ssd.jpl.nasa.gov/api/horizons.api on 2026-11-14.
Public NASA/JPL scientific output, retained verbatim for offline regression tests.

Parameters: format=json, COMMAND='10' or '399', MAKE_EPHEM=YES, OBJ_DATA=NO,
EPHEM_TYPE=OBSERVER, CENTER=coord@301, COORD_TYPE=GEODETIC,
SITE_COORD='0,-89.5,0', START_TIME='2026-11-14 00:00:00',
STOP_TIME='2026-11-14 02:00:00', STEP_SIZE='60 m', QUANTITIES='4,13',
APPARENT=AIRLESS, CSV_FORMAT=YES, EXTRA_PREC=YES, ELEV_CUT=-90,
SKIP_DAYLT=NO, TIME_TYPE=UT, TIME_DIGITS=SECONDS.

The 1e-6 degree absolute tolerance checks the six-decimal reference values in
`docs/2gbram_horizons_veri_notlari.md`; it is a parsing regression tolerance,
not a claim about physical prediction accuracy. Angular diameter is in arcseconds.
The site is a spherical-reference test observer, not a certified landing site.

## DSN station responses (R2)

`dsn_DSS-14.json`, `dsn_DSS-43.json` and `dsn_DSS-63.json` were captured from the
same API on 2026-11-14 with: format=json, COMMAND='301', MAKE_EPHEM=YES,
OBJ_DATA=NO, EPHEM_TYPE=OBSERVER, CENTER=coord@399, COORD_TYPE=GEODETIC,
START_TIME='2026-11-04 00:00:00', STOP_TIME='2026-11-04 12:00:00',
STEP_SIZE='5 m', QUANTITIES='4', APPARENT=AIRLESS, CSV_FORMAT=YES,
EXTRA_PREC=YES, ELEV_CUT=-90, SKIP_DAYLT=NO, TIME_TYPE=UT, TIME_DIGITS=SECONDS.

SITE_COORD is the WGS84 geodetic position from DSN 810-005 module 301 Rev. P,
Table 5 (`shared/dsn_stations.json`), as east longitude, latitude (degrees) and
ellipsoid height (km), formatted with 15 significant digits:

- DSS-14 Goldstone: '243.110461788889,35.4259008666667,1.00139'
- DSS-43 Canberra: '148.981267308333,-35.4024242333333,0.688867'
- DSS-63 Madrid: '355.751991455556,40.43120975,0.864816'

Horizons echoes the site rounded to 7 decimals, so site checks use a 1e-6 degree
and 1e-5 km absolute tolerance. Each window contains one 6-degree Moon elevation
crossing per station, used as a reference case for crossing interpolation.

## NOAA SWPC space weather products

`swpc_solar_probabilities.json`, `swpc_xrays.json`, `swpc_protons.json` and
`swpc_solar_regions.json` were fetched from https://services.swpc.noaa.gov on
2026-11-14 at about 20:45 UTC (public US government data):

- `/json/solar_probabilities.json`: the four newest entries (2026-11-14 back to 2026-11-11).
- `/json/goes/primary/xrays-1-day.json`: the last 40 rows (20 minutes, both channels, GOES-18).
- `/json/goes/primary/integral-protons-1-day.json`: the last 48 rows (six 5-minute samples x 8 channels).
- `/json/solar_regions.json`: all rows observed on 2026-11-13 and 2026-11-14.

Rows are unchanged; the files were trimmed and re-serialized as compact JSON.
The SWPC 3-day forecast text issued 2026 Nov 14 1230 UTC lists the same
R1-R2 25 %, R3+ 5 % and S1+ 5 % for Nov 14-16, which fixes the meaning of the
probability `date` as the first forecast UTC day.
## Flight ephemeris vectors

`flight_vectors_301.json` (Moon) and `flight_vectors_10.json` (Sun) were
captured verbatim from https://ssd.jpl.nasa.gov/api/horizons.api on
2026-11-15 with EPHEM_TYPE=VECTORS, CENTER='500@399', REF_PLANE=ECLIPTIC,
REF_SYSTEM=ICRF, VEC_TABLE=1, VEC_LABELS=NO, OUT_UNITS=KM-S, CSV_FORMAT=YES,
TIME_TYPE=UT, START_TIME='2026-11-03 22:00:00', STOP_TIME='2026-11-04
00:00:00', STEP_SIZE='30 m'. Public NASA/JPL output (DE441).
