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
