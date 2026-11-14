"""Moon-fixed observer coordinates and UTC analysis requests."""
from datetime import datetime
from pydantic import BaseModel


class ObserverSite(BaseModel):
    """Coordinates in degrees and altitude in km above the lunar reference sphere."""

    site_id: str
    longitude_deg_east: float
    latitude_deg: float
    altitude_km: float
    reference_frame: str = "MOON_ME"


class AnalysisRequest(BaseModel):
    """Inclusive UTC interval and sampling step in seconds."""

    site: ObserverSite
    start_utc: datetime
    end_utc: datetime
    step_s: int = 3600
