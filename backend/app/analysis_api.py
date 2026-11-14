"""Persistent local analysis jobs and read-only terrain observer resolution."""

from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shared.contracts import AnalysisRequest, ObserverSite
from shared.horizon import HorizonSettings


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = "custom"
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    observer_height_m: float = Field(default=2, ge=0.1, le=100)
    start_utc: datetime
    end_utc: datetime
    step_s: int = 3600
    refine: bool = True
    horizon: HorizonSettings = Field(default_factory=HorizonSettings)

    @model_validator(mode="after")
    def validate_analysis(self):
        AnalysisRequest(site=ObserverSite(site_id=self.site_id,latitude_deg=self.latitude_deg,
                        longitude_deg_east=self.longitude_deg_east,altitude_km=0.0),
                        start_utc=self.start_utc,end_utc=self.end_utc,step_s=self.step_s)
        return self


class JobStore:
    def __init__(self, path: Path):
        raise NotImplementedError

    def create(self, submission: Submission) -> dict:
        raise NotImplementedError

    def get(self, job_id: str) -> dict | None:
        raise NotImplementedError

    def update(self, job_id: str, status: str, result=None, error=None):
        raise NotImplementedError

    def recover(self):
        raise NotImplementedError

    def pending(self) -> list:
        raise NotImplementedError


def presets() -> list:
    raise NotImplementedError


def resolve_observer(data_dir: Path, submission: Submission) -> ObserverSite:
    raise NotImplementedError


def presentation(data_dir: Path, result: dict, profile: dict) -> dict:
    raise NotImplementedError


async def execute_job(store, job, data_dir, broker_url):
    raise NotImplementedError


def create_app(state_dir=None, data_dir=None, broker_url=None, start_worker=True):
    raise NotImplementedError
