"""Lunar reference surface shared by Horizons and LOLA processing.

Source: NASA PGDA product 90 / Barker et al. 2023 (doi:10.3847/PSJ/acf3e1),
and NASA M2M-30044 Lunar Surface Data Book, p.73. This is not terrain height.
"""

MOON_REFERENCE_RADIUS_KM = 1737.4
MOON_REFERENCE_RADIUS_M = MOON_REFERENCE_RADIUS_KM * 1000

# Lunar gravitational parameter, m^3/s^2. Source: JPL Solar System Dynamics,
# Astrodynamic Parameters (DE440/DE441), GM(Moon) = 4902.800118 km^3/s^2,
# https://ssd.jpl.nasa.gov/astro_par.html
MOON_GM_M3_S2 = 4902.800118e9

# Standard gravity for specific impulse, m/s^2 (exact by definition; BIPM SI Brochure).
STANDARD_GRAVITY_M_S2 = 9.80665

# Exact unit conversions (international foot, avoirdupois pound-force, nautical mile).
FOOT_M = 0.3048
POUND_FORCE_N = 4.4482216152605
POUND_MASS_KG = 0.45359237
NAUTICAL_MILE_M = 1852.0

# Earth Rotation Angle (radians/turns) as a linear function of UT1, IERS
# Conventions (2010), TN36, eq. 5.15: ERA = 2*pi*(0.7790572732640 +
# 1.00273781191135448 * (JD_UT1 - 2451545.0)). https://iers-conventions.obspm.fr/
ERA_AT_J2000_TURNS = 0.7790572732640
ERA_RATE_TURNS_PER_UT1_DAY = 1.00273781191135448
J2000_JULIAN_DATE = 2451545.0
UNIX_EPOCH_JULIAN_DATE = 2440587.5
# |UT1 - UTC| is kept below 0.9 s by leap seconds (IERS); using UTC for UT1
# shifts Earth's displayed rotation by at most ~0.004 deg.
