"""R5 fuel optimization across descent routes (scenario S7).

Generates route variants of the R4 descent (initial orbit altitude, approach
duration, terminal-descent duration), estimates propellant from descent orbit
insertion to touchdown, rejects routes that violate the touchdown propellant
reserve or the R4 vehicle limits, and reports the fuel-versus-safety
trade-off. The default reserve is the NASA TN D-6846 Table II contingency
allowance: landing-site redesignation 60 fps plus manual hover 85 fps.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.descent import Tolerances, VehicleProfile
from shared.constants import FOOT_M, NAUTICAL_MILE_M

DEFAULT_RESERVE_DELTA_V_MPS = (60 + 85) * FOOT_M
DEFAULT_ORBIT_ALTITUDES_KM = tuple(n * NAUTICAL_MILE_M / 1000 for n in (40, 60, 80))
DEFAULT_APPROACH_DURATIONS_S = (60.0, 100.0, 140.0)
DEFAULT_TERMINAL_DURATIONS_S = (60.0, 108.0, 150.0)
REFERENCE_ORBIT_ALTITUDE_KM = 60 * NAUTICAL_MILE_M / 1000


class RouteRequest(BaseModel):
    """Site, vehicle, variant grid, propellant reserve and touchdown limits."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    profile_id: str = "apollo-lm-11"
    custom_profile: VehicleProfile | None = None
    inclination_deg: float = Field(default=90.0, ge=0, le=180)
    allow_plane_change: bool = False
    orbit_altitudes_km: list[float] = Field(
        default_factory=lambda: list(DEFAULT_ORBIT_ALTITUDES_KM),
        min_length=1,
        max_length=4,
    )
    approach_durations_s: list[float] = Field(
        default_factory=lambda: list(DEFAULT_APPROACH_DURATIONS_S),
        min_length=1,
        max_length=4,
    )
    terminal_durations_s: list[float] = Field(
        default_factory=lambda: list(DEFAULT_TERMINAL_DURATIONS_S),
        min_length=1,
        max_length=4,
    )
    reserve_delta_v_mps: float = Field(
        default=DEFAULT_RESERVE_DELTA_V_MPS, ge=0, le=500
    )
    tolerances: Tolerances = Field(default_factory=Tolerances)


def mass_at_doi_kg(profile: VehicleProfile, reference_doi_delta_v_mps: float) -> float:
    """Back out the mass before DOI from the PDI mass (rocket equation)."""
    raise NotImplementedError


def reserve_propellant_kg(
    landing_mass_kg: float, reserve_delta_v_mps: float, isp_s: float
) -> float:
    """Propellant that must remain at touchdown to fly the reserve delta-v."""
    raise NotImplementedError


def hover_time_s(
    propellant_kg: float, landing_mass_kg: float, isp_s: float, gravity_mps2: float
) -> float:
    """Seconds of hover the given propellant buys at the landing mass."""
    raise NotImplementedError


def pareto_front(routes: list[dict]) -> list[str]:
    """IDs of feasible routes not dominated in (lower fuel, longer assessment)."""
    raise NotImplementedError


def optimize_routes(request: RouteRequest, terrain_height_m: float) -> dict:
    """Simulate the variant grid and classify, rank and explain each route."""
    raise NotImplementedError


def register_fuel_routes(app, data_dir: Path) -> None:
    """POST /descents/optimize."""
    raise NotImplementedError
