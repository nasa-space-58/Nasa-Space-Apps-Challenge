# Flight ephemeris: real Moon, Sun and Earth motion during the flight

Developed on `fix/healthinfo`. Skeleton: `28c0375`. Frozen test baseline: `2f01b74` (`backend/tests/test_flight_ephemeris.py`, `backend/tests/test_flight_ephemeris_contracts.py`, `data-ingestion/tests/test_flight_ephemeris.py`).

## What it answers

Where are the Moon and the Sun, and how far has Earth turned, at each moment of the illustrative flight that lands at the chosen time?

Before this change, mission control drew the Earth, Moon and Sun fixed in one line during the whole flight. Now the flight is placed in real time:

- **Landing** is the observation start chosen in the Plan tab.
- **Launch** is 102 h 45 min 39.9 s earlier, following Apollo 11's elapsed times.

During those ~4.3 days:
- the Moon moves about 60° along its real orbit;
- Earth turns about 4.3 times;
- the Sun direction shifts about 4°.

## Timeline

`shared/mission_timeline.json` holds the Apollo 11 ground elapsed times, from the Apollo 11 Mission Report (MSC-00171), Table 3-I:

| Event | Elapsed time | Phase that ends |
|---|---|---|
| Earth orbit insertion | 000:11:49.9 | launch |
| Translunar injection | 002:44:16.2 | parking orbit |
| Lunar orbit insertion | 075:49:50.4 | translunar coast |
| Powered descent initiation | 102:33:05.0 | lunar orbit |
| Landing | 102:45:39.9 | descent |

Example: a landing at 2026-11-04 00:00:00 UTC gives a launch at 2026-10-30 17:14:20.1 UTC.

## Source and flow

JPL Horizons VECTORS tables for the Moon (301) and the Sun (10):
- Earth-centred (500@399), ecliptic of J2000.0;
- position only, km;
- `TIME_TYPE=UT`, so sample times are UTC (a `JDUT` column).

The flow:

1. `POST /flight-ephemeris {"landing_utc": "...Z"}` (202) stores a job.
   - A pending, running or ready job for the same landing is reused.
   - A failed job is retried as a new job.
2. A dedicated backend loop sends a `FlightEphemerisRequest` on `lunar.flight-ephemeris.requests.v1`. The request covers launch to landing on a 30-minute grid (207 samples). Flight jobs do not wait behind long analyses.
3. The ingestion worker downloads both tables and checks them:
   - target, centre (Earth 399), reference frame, `KM-S` units and the `JDUT` column;
   - finite values and the exact request grid.

   It writes an atomic, checksummed `flight_ephemeris/<sha256>` bundle (raw responses, `samples.json`, manifest). Existing bundles are verified and reused; corrupt ones raise.
4. The backend verifies the hashes and builds the result. `GET /flight-ephemeris/{job_id}` returns it:
   - the mission window and its phases;
   - aligned samples, each with `moon_geocentric_km`, `sun_geocentric_km` and `earth_rotation_deg`;
   - the frame, the source and the limitations.

Errors: 422 for a missing offset, fractional seconds, a landing outside 2020–2029 or extra fields; 404 for an unknown job; 429 for four active jobs. Failed acquisitions persist the error.

## Model

- **Earth rotation:** the IERS Conventions (2010) Earth Rotation Angle, `ERA = 360° · frac(0.7790572732640 + 1.00273781191135448 · (JD_UT1 − 2451545.0))`, with UTC used for UT1. Because |UT1 − UTC| < 0.9 s, this is within about 0.004°.
- **Moon distance check:** a Moon distance outside 350 000–410 000 km is rejected as corrupt data.
- **What the interface does:**
  - Samples are interpolated linearly for display.
  - The Moon is drawn in synchronous rotation, with its 0° meridian facing Earth.
  - The spacecraft path stays illustrative: it now leaves from the real Earth orientation and meets the Moon where the Moon will be at lunar orbit insertion.

## Verification

- 57 backend tests and 27 worker tests. Fixtures are verbatim Horizons captures (`data-ingestion/tests/fixtures/README.md`).
- Live check for a landing at 2026-11-04 00:00:00 UTC:
  - the Moon moved from ecliptic longitude 98.63° to 158.34° during the flight;
  - its distance went from 366 618 km to 380 047 km;
  - the landing-time value matches the independently captured Horizons fixture.

## Not modelled

- A computed transfer trajectory: no patched conics or n-body propagation.
- Lunar libration and the Moon's exact body orientation.
- UT1 − UTC.
- Mission timelines other than Apollo 11.
