"""R7 alternative landing plans: reachable backup sites (scenario S11).

From the primary R4 descent, take the vehicle state at a divert point (start of
the approach phase = high gate, or start of terminal descent = low gate) and
check nearby backup sites:

- Candidates are 80 m DEM cells within a search radius that pass the R3 hard
  constraints (landing-area slope within the limit, no unknown cells), ranked
  by the R3 safety score and kept at least a minimum separation apart.
- Downrange retargeting is flown exactly with the R4 guidance model: shifting
  the target by d along the track is the same as rotating the vehicle state by
  -d / R about the Moon's centre (central gravity is rotation-symmetric).
- The planar model has no crossrange dynamics. A crossrange offset c is added
  as a decoupled lateral divert with the minimum-energy result for a double
  integrator starting and ending at zero lateral velocity in time T:
  delta-v = 3 |c| / T, peak acceleration 6 |c| / T^2. Its propellant is taken
  with the rocket equation after the in-plane flight (a conservative sum).
- A backup is reachable only if propellant is not exhausted, the R5 reserve is
  kept, the R4 touchdown tolerances are met and the engine can supply the
  combined in-plane and peak lateral acceleration at every sample.

Comparison metrics are the same for the primary and every backup.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from affine import Affine
from pydantic import BaseModel, ConfigDict, Field

from app.descent import OrbitSettings, Tolerances, VehicleProfile
from app.fuel_optimization import DEFAULT_RESERVE_DELTA_V_MPS
from app.terrain_safety import SafetySettings

DIVERT_STEP_S = 0.1
DIVERT_PHASES = {"high_gate": "approach", "low_gate": "terminal"}
MAX_SEARCH_RADIUS_M = 10000.0


class AlternativesError(ValueError):
    """Invalid inputs, unusable terrain or a primary plan that cannot divert."""


class AlternativesRequest(BaseModel):
    """Primary plan (R4/R5 inputs) and backup search settings."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    profile_id: str = "apollo-lm-11"
    custom_profile: VehicleProfile | None = None
    orbit: OrbitSettings = Field(default_factory=OrbitSettings)
    tolerances: Tolerances = Field(default_factory=Tolerances)
    allow_plane_change: bool = False
    reserve_delta_v_mps: float = Field(
        default=DEFAULT_RESERVE_DELTA_V_MPS, ge=0, le=500
    )
    divert_from: Literal["high_gate", "low_gate"] = "high_gate"
    search_radius_m: float = Field(default=3000.0, ge=200, le=MAX_SEARCH_RADIUS_M)
    candidate_spacing_m: float = Field(default=160.0, ge=80, le=1000)
    min_separation_m: float = Field(default=400.0, ge=0, le=5000)
    max_candidates: int = Field(default=6, ge=1, le=12, strict=True)
    approach_azimuth_deg: float = Field(default=180.0, ge=0, lt=360)
    safety: SafetySettings = Field(default_factory=SafetySettings)


@dataclass(frozen=True)
class LocalTerrain:
    """Height grid (m above the 1737.4 km sphere, NaN unknown) around a site.

    `transform` maps (col, row) to projected (x, y); `north_unit_xy` and
    `east_unit_xy` give the local directions at the site; `crs` is the
    projection (PROJ string or WKT) used to report candidate coordinates.
    """

    heights_m: np.ndarray
    transform: Affine
    site_xy_m: tuple[float, float]
    north_unit_xy: tuple[float, float]
    east_unit_xy: tuple[float, float]
    pixel_m: float
    crs: str


def lateral_divert(cross_m: float, time_to_go_s: float) -> dict:
    """Minimum-energy lateral divert: delta-v 3|c|/T and peak 6|c|/T^2.

    Raises ValueError for a non-positive or non-finite time to go.
    """
    raise NotImplementedError


def rotate_state(position_m, velocity_mps, angle_rad: float):
    """Rotate planar state vectors counter-clockwise by angle_rad.

    Rotating by +d / R moves the vehicle d metres uprange relative to the site,
    i.e. it retargets the site d metres further downrange.
    """
    raise NotImplementedError


def divert_state(samples: list[dict], site_radius_m: float, divert_from: str) -> dict:
    """Planar state at the first sample of the divert phase.

    Returns position_m, velocity_mps (lists), mass_kg, t_s (since PDI),
    altitude_m and downrange_m. Raises AlternativesError if the phase is absent.
    """
    raise NotImplementedError


def evaluate_divert(
    profile: VehicleProfile,
    state: dict,
    target_height_m: float,
    down_m: float,
    cross_m: float,
    divert_from: str,
    request: AlternativesRequest,
) -> dict:
    """Fly from the divert state to a target offset (down_m, cross_m).

    Returns reachable, rejection_reasons, in-plane and lateral delta-v (m/s),
    total delta-v from the divert, propellant remaining (kg), reserve margin
    (kg), hover margin (s), time to touchdown (s) and the touchdown state.
    """
    raise NotImplementedError


def find_candidates(terrain: LocalTerrain, request: AlternativesRequest) -> list[dict]:
    """Backup cells passing the R3 hard constraints, best safety score first.

    Cells are sampled every candidate_spacing_m within search_radius_m, at
    least min_separation_m from the primary site and from each other, at most
    max_candidates. Each has down_m, cross_m, distance_m, bearing_deg,
    latitude_deg, longitude_deg_east, terrain_height_m, safety and score.
    """
    raise NotImplementedError


def plan_alternatives(
    request: AlternativesRequest, terrain: LocalTerrain, terrain_height_m: float
) -> dict:
    """Primary row plus ranked backups with the same metrics, deterministic.

    Raises AlternativesError for an unknown vehicle, a plan the R4 model
    rejects or a primary site without terrain margin for the safety check.
    """
    raise NotImplementedError


def load_local_terrain(
    data_dir: Path, latitude_deg: float, longitude_deg_east: float, half_width_m: float
) -> LocalTerrain:
    """Verified cached DEM heights around the site (window clipped to the raster).

    FileNotFoundError means terrain must be acquired first; AlternativesError
    marks a site outside coverage.
    """
    raise NotImplementedError


def register_alternative_routes(app, data_dir: Path) -> None:
    """POST /descents/alternatives: 409 without cached terrain, 422 otherwise."""

    @app.post("/descents/alternatives")
    def descent_alternatives(body: AlternativesRequest):
        raise NotImplementedError
