"""Moon-fixed observer coordinates and UTC analysis requests."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ObserverSite(BaseModel):
    """MOON_ME angles in degrees and altitude in km above the 1737.4 km sphere.

    Invalid coordinates or non-finite numbers raise ValidationError. Geometric
    validity does not imply that a downloaded terrain product covers the site.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(min_length=1, pattern=r"\S", strict=True)
    longitude_deg_east: float = Field(ge=0, lt=360, strict=True)
    latitude_deg: float = Field(ge=-90, le=90, strict=True)
    altitude_km: float = Field(strict=True)
    reference_frame: Literal["MOON_ME"] = "MOON_ME"


class AnalysisRequest(BaseModel):
    """UTC interval and sample spacing for cached Horizons requests.

    Raises ValidationError for non-UTC times, unsupported sampling or requests
    beyond the MVP date, duration and sample-count limits in assumptions.md.
    """

    model_config = ConfigDict(extra="forbid")
    site: ObserverSite
    start_utc: datetime
    end_utc: datetime
    step_s: int = Field(default=3600, strict=True)

    @field_validator("start_utc", "end_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Require explicit zero-offset UTC input."""
        if value.utcoffset() != timedelta(0):
            raise ValueError("Timestamp must include UTC (Z or +00:00)")
        return value

    @field_validator("step_s")
    @classmethod
    def require_supported_step(cls, value: int) -> int:
        """Accept hourly, five-minute and one-minute sampling."""
        if value not in (60, 300, 3600):
            raise ValueError("step_s must be 60, 300 or 3600 seconds")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> "AnalysisRequest":
        """Bound work per request and reject reversed intervals."""
        lower = datetime(2026, 1, 1, tzinfo=timezone.utc)
        upper = datetime(2028, 1, 1, tzinfo=timezone.utc)
        if not lower <= self.start_utc < self.end_utc <= upper:
            raise ValueError("Require 2026-01-01 <= start < end <= 2028-01-01 UTC")
        duration_s = (self.end_utc - self.start_utc).total_seconds()
        if duration_s > 31 * 86400:
            raise ValueError("Analysis duration must be at most 31 days")
        if int(duration_s // self.step_s) + 1 > 10000:
            raise ValueError("Request exceeds 10000 samples; shorten the interval")
        return self
