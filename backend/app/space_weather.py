"""Solar flare and proton storm context for a saved analysis (NOAA SWPC).

The worker stores a checksummed SWPC snapshot; this module reports the current
GOES conditions and the SWPC 3-day forecast days that overlap the analysis
interval. Nothing is extrapolated beyond the forecast horizon.

Thresholds: NOAA Space Weather Scales (https://www.swpc.noaa.gov/noaa-scales-explanation).
Flare classes use the GOES 0.1-0.8 nm X-ray flux in W/m²; the radiation storm
scale uses >=10 MeV integral proton flux in pfu.
"""

from datetime import datetime
from pathlib import Path

from shared.space_weather import SpaceWeatherRequest

from app.submit_space_weather import submit_space_weather

# Lower bounds of the GOES 0.1-0.8 nm flare classes in W/m².
FLARE_CLASS_BASES_W_M2 = (("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7), ("A", 1e-8))
# NOAA R1..R5 lower bounds in W/m² (M1, M5, X1, X10, X20).
RADIO_BLACKOUT_THRESHOLDS_W_M2 = (1e-5, 5e-5, 1e-4, 1e-3, 2e-3)
# NOAA S1..S5 lower bounds in pfu at >=10 MeV.
RADIATION_STORM_THRESHOLDS_PFU = (1e1, 1e2, 1e3, 1e4, 1e5)
MAX_ACTIVE_REGIONS = 5
MAX_ACTIVE_JOBS = 4
ACTIVE = ("pending", "acquiring", "computing")
STATUSES = (*ACTIVE, "ready", "failed")
LIMITATIONS = (
    "Solar flares cannot be predicted with certainty; SWPC gives daily probabilities for at most 3 days.",
    "Analysis time outside the SWPC forecast days has no forecast; nothing is extrapolated.",
    "Current conditions describe the Sun when the snapshot was fetched, not at the landing time.",
    "GOES measures near Earth; proton flux is a proxy for the lunar surface, not a dose estimate.",
    "The radio blackout scale describes HF radio on Earth's sunlit side, not DSN links.",
)


def flare_class(flux_w_m2: float) -> str:
    """GOES flare class such as 'B6.7' or 'X12.3' (one decimal, floored)."""
    raise NotImplementedError


def radio_blackout_level(flux_w_m2: float) -> int:
    """NOAA R level 0-5 for a 0.1-0.8 nm X-ray flux in W/m²."""
    raise NotImplementedError


def radiation_storm_level(flux_pfu: float) -> int:
    """NOAA S level 0-5 for a >=10 MeV integral proton flux in pfu."""
    raise NotImplementedError


def load_snapshot(data_dir: Path, snapshot_path: str) -> dict:
    """Read a verified space-weather/<sha256> bundle; ValueError on any defect."""
    raise NotImplementedError


def assess_space_weather(
    snapshot: dict, start_utc: datetime, end_utc: datetime
) -> dict:
    """Current GOES conditions and forecast days overlapping [start, end).

    Result keys: acquired_at_utc, current (xray, xray_peak, protons_10mev,
    protons_10mev_peak), forecast (forecast_start_utc, forecast_end_utc,
    coverage 'full'/'partial'/'none', covered_duration_s, days and the
    max_daily_* percents, None without overlap), active_regions, limitations.
    """
    raise NotImplementedError


def space_weather_request_for(job_id: str) -> SpaceWeatherRequest:
    """Request whose UUID derives from the job ID (replay-safe recovery)."""
    raise NotImplementedError


class SpaceWeatherStore:
    """Space weather jobs in the analysis SQLite database."""

    def __init__(self, jobs):
        raise NotImplementedError

    def create(self, analysis_id: str) -> dict:
        raise NotImplementedError

    def get(self, job_id: str) -> dict | None:
        raise NotImplementedError

    def update(self, job_id: str, status: str, result=None, error=None) -> None:
        raise NotImplementedError

    def pending(self) -> list[dict]:
        raise NotImplementedError

    def recover(self) -> None:
        raise NotImplementedError


async def execute_space_weather(store, analyses, job, data_dir, broker_url) -> None:
    """Acquire a snapshot via RabbitMQ and persist the assessment or the failure."""
    raise NotImplementedError


def register_space_weather_routes(app, analyses, store) -> None:
    """POST /analyses/{id}/space-weather and GET /space-weather/{id}."""
    raise NotImplementedError


__all__ = ["submit_space_weather"]
