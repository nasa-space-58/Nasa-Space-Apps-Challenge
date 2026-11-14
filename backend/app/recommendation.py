"""R3 explained site recommendations (S8) and priority weights (S9).

Candidates reuse the existing analysis (Sun/Earth) and R2 communication jobs and
the R3 terrain safety screen. Hard constraints reject candidates with recorded
reasons; uncertain inputs are reported as insufficient data, never silently
ranked. Rescoring reuses stored criterion scores without new computation.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.terrain_safety import SafetySettings

CRITERIA = ("energy", "darkness", "communication", "terrain_safety")
NOT_MODELED = {
    "fuel": "Fuel and reachability are added after R4/R5 descent trajectories.",
    "science": "No validated science-value dataset is integrated yet.",
}
MAX_ACTIVE_RECOMMENDATIONS = 2


class Weights(BaseModel):
    """Relative criterion importance; normalized by their sum (must be > 0)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    energy: float = Field(default=1.0, ge=0, le=10)
    darkness: float = Field(default=1.0, ge=0, le=10)
    communication: float = Field(default=1.0, ge=0, le=10)
    terrain_safety: float = Field(default=1.0, ge=0, le=10)

    @model_validator(mode="after")
    def require_positive_total(self) -> "Weights":
        raise NotImplementedError


class Constraints(SafetySettings):
    """Terrain screen plus optional minimum sunlight (%) and communication (h)."""

    min_sunlight_percent: float | None = Field(default=None, ge=0, le=100)
    min_communication_h: float | None = Field(default=None, ge=0, le=744)


class Candidate(BaseModel):
    """One candidate observer: MOON_ME degrees and height above terrain (m)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    observer_height_m: float = Field(default=2.0, ge=0.1, le=100)


class RecommendationRequest(BaseModel):
    """2-6 candidates over one shared UTC window, settings and weights."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    candidates: list[Candidate] = Field(min_length=2, max_length=6)
    start_utc: datetime
    end_utc: datetime
    step_s: int = 3600
    refine: bool = True
    min_elevation_deg: float = Field(default=6.0, ge=6, le=90)
    constraints: Constraints = Field(default_factory=Constraints)
    weights: Weights = Field(default_factory=Weights)

    @model_validator(mode="after")
    def validate_candidates(self) -> "RecommendationRequest":
        raise NotImplementedError


def candidate_ids(request: RecommendationRequest) -> list[str]:
    """Stable IDs c1..cN in request order."""
    raise NotImplementedError


def criterion_scores(
    analysis: dict, communication: dict, safety: dict, constraints: Constraints
) -> dict:
    """Score each criterion 0..1 with its raw value, unit and rule."""
    raise NotImplementedError


def evaluate(
    candidate_id: str,
    analysis: dict,
    communication: dict,
    safety: dict,
    constraints: Constraints,
) -> dict:
    """Classify as ranked, rejected (with failures) or insufficient_data."""
    raise NotImplementedError


def rank(evaluations: list[dict], weights: Weights) -> dict:
    """Weighted ranking with contributions and explanations for each candidate."""
    raise NotImplementedError


def rescore(evaluations: list[dict], previous: Weights, current: Weights) -> dict:
    """Previous and new rankings side by side with rank changes and reasons."""
    raise NotImplementedError


def compare_schedules(previous: dict, current: dict) -> dict:
    """Compare two finished recommendations for the same sites at shifted dates."""
    raise NotImplementedError


class RecommendationStore:
    """Persistent recommendations orchestrating analysis and communication jobs."""

    def __init__(self, jobs, communications):
        raise NotImplementedError

    def create(self, request: RecommendationRequest, previous_id: str | None = None):
        raise NotImplementedError

    def get(self, recommendation_id: str) -> dict | None:
        raise NotImplementedError

    def tick(self, data_dir) -> None:
        raise NotImplementedError

    def reschedule(self, recommendation_id: str, delay_hours: float) -> dict:
        raise NotImplementedError


def register_recommendation_routes(app, recommendations) -> None:
    """Recommendation create/get, rescore and reschedule routes."""
    raise NotImplementedError
