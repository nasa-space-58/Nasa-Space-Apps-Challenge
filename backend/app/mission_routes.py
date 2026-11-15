"""Mission-run route set: every computed landing route with its role and reasons.

When a mission run starts, the interface also asks for the route set around
the analysis site. It reuses the R7 divert model (app.alternatives) from the
nominal R4 descent and labels each route:

- primary: the planned landing target of the analysis, flown from the divert
  point with the same metrics as every backup.
- backup: a backup site that passed the R3 hard constraints and is reachable
  from the divert point, ranked like R7 (safety score, then distance).
- rejected: either a backup that was flown but is not reachable (the R7
  rejection reasons: propellant, reserve, thrust, touchdown tolerance), or a
  cell in the search area that fails the R3 hard constraints (slope above the
  limit or unknown terrain) and was therefore never flown.

Paths are given in one local planar frame at the primary target: down_m along
the approach azimuth, cross_m to its right, altitude_m above the primary site
sphere, t_s since powered descent initiation. The route set is computed for the
nominal (unfaulted) vehicle: it is the pre-flight plan set, not a replay of the
run's faults. It is not landing-safety certification.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.alternatives import (
    MAX_SEARCH_RADIUS_M,
    AlternativesRequest,
    LocalTerrain,
)
from app.descent import VehicleProfile
from app.fuel_optimization import DEFAULT_RESERVE_DELTA_V_MPS
from app.terrain_safety import SafetySettings

MAX_ROUTE_SAMPLES = 120
FRAME = (
    "Local planar frame at the primary target: down_m along the approach "
    "azimuth, cross_m to its right, altitude_m above the primary site sphere, "
    "t_s since powered descent initiation."
)
ASSUMPTIONS = (
    "Routes come from the R7 divert model from the nominal R4 descent; the mission run's faults are not applied to them.",
    "Downrange flight uses the R4 ZEM/ZEV guidance to each site's own terrain height; crossrange uses the analytic minimum-energy lateral divert profile c(3s^2 - 2s^3).",
    "A backup's altitude is shifted by its terrain height difference to the primary so that all paths share the primary site sphere.",
    "Terrain-rejected cells fail the R3 hard constraints on the 80 m DEM (landing-area slope above the limit or unknown cells); they are not flown.",
    "Backups are ranked like R7: reachable first, then R3 safety score and distance.",
)
DISCLAIMER = (
    "Pre-flight route set from sampled physics on an 80 m DEM; not landing-safety "
    "certification, a guaranteed divert capability or a replay of injected faults."
)


class MissionRoutesRequest(BaseModel):
    """Vehicle, approach and backup search settings for the route set."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    profile_id: str = "apollo-lm-11"
    custom_profile: VehicleProfile | None = None
    approach_azimuth_deg: float = Field(default=0.0, ge=0, lt=360)
    divert_from: Literal["high_gate", "low_gate"] = "high_gate"
    search_radius_m: float = Field(default=3000.0, ge=200, le=MAX_SEARCH_RADIUS_M)
    max_candidates: int = Field(default=8, ge=1, le=12, strict=True)
    max_terrain_rejections: int = Field(default=6, ge=0, le=12, strict=True)
    reserve_delta_v_mps: float = Field(
        default=DEFAULT_RESERVE_DELTA_V_MPS, ge=0, le=500
    )
    terrain: SafetySettings = Field(default_factory=SafetySettings)


class MissionRoutesError(ValueError):
    """Invalid inputs or a site the route set cannot be computed for."""


def downsample(points: list, max_samples: int) -> list:
    """Evenly strided subset that always keeps the first and last point.

    Returns the list unchanged when it is short enough. Raises ValueError for
    max_samples below 2.
    """
    raise NotImplementedError


def alternatives_request(
    request: MissionRoutesRequest, site: dict
) -> AlternativesRequest:
    """R7 request for the analysis site (site_id, latitude_deg, longitude_deg_east)."""
    raise NotImplementedError


def terrain_rejections(
    terrain: LocalTerrain, request: AlternativesRequest, limit: int
) -> list[dict]:
    """Search-area cells that fail the R3 hard constraints, nearest first.

    Uses the R7 candidate grid (spacing, search radius, minimum separation from
    the primary and from each other). Cells without enough terrain around them
    for the check are skipped, never reported. Each entry has down_m, cross_m,
    distance_m, bearing_deg, latitude_deg, longitude_deg_east,
    terrain_height_m, safety and reasons (code "slope" or "unknown_terrain").
    """
    raise NotImplementedError


def build_routes(
    plan: dict, rejected_cells: list[dict], max_samples: int = MAX_ROUTE_SAMPLES
) -> list[dict]:
    """Role-labelled routes from an R7 plan with trajectories and rejected cells.

    Order: primary, backups by rank, flown rejections, terrain rejections.
    """
    raise NotImplementedError


def plan_mission_routes(
    request: MissionRoutesRequest,
    site: dict,
    terrain: LocalTerrain,
    terrain_height_m: float,
) -> dict:
    """Route set for one analysis site, deterministic with a result_id.

    Raises MissionRoutesError when the R7 plan cannot be computed.
    """
    raise NotImplementedError


def register_mission_route_routes(app, analyses, data_dir: Path) -> None:
    """POST /analyses/{analysis_id}/mission-routes."""

    @app.post("/analyses/{analysis_id}/mission-routes")
    def mission_routes(analysis_id: str, body: MissionRoutesRequest):
        raise NotImplementedError
