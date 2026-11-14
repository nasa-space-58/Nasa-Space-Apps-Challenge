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
