"""Bounded durable searches over candidate observation start times."""
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.analysis_api import Submission


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    template: Submission
    last_start_utc: datetime
    start_step_hours: int = Field(default=24, ge=1, le=168, strict=True)
    min_sunlight_percent: float = Field(default=70, ge=0, le=100)
    max_darkness_hours: float = Field(default=6, ge=0, le=744)
    min_earth_hours: float = Field(default=4, ge=0, le=744)


def candidate_submissions(request: SearchRequest) -> list[Submission]:
    """Inclusive start grid, no more than 16 candidates or 31 days of start span."""
    raise NotImplementedError


def assess_result(result: dict, request: SearchRequest) -> dict:
    """Evaluate estimated metrics; unknown coverage can never become a match."""
    raise NotImplementedError


class SearchStore:
    def __init__(self, jobs):
        self.jobs = jobs

    def create(self, request: SearchRequest):
        raise NotImplementedError

    def get(self, search_id: str):
        raise NotImplementedError

    def tick(self):
        """Advance persisted searches, transactionally scheduling at most one job each."""
        raise NotImplementedError


def register_search_routes(app, searches):
    raise NotImplementedError
