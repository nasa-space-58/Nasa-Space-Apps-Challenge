"""R4 powered descent trajectories (scenario S6).

Planar two-body model: spherical Moon, point-mass gravity (JPL GM), no mascons,
J2 or lunar rotation. Phase structure and gates follow NASA TN D-6846 (Apollo 11
premission profile): descent orbit insertion (Hohmann to 50 000 ft above the
site), coast, braking to high gate, approach to low gate, terminal descent.
Each powered phase uses closed-loop ZEM/ZEV guidance to its gate state and the
TN D-6846 throttle rule. Terrain clearance along the path is not checked.
"""

from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

PROFILES_FILE = Path(__file__).resolve().parents[2] / "shared/vehicle_profiles.json"
DEFAULT_ORBIT_ALTITUDE_KM = 111.12  # 60 nautical miles (TN D-6846, Figure 1)


class DescentError(ValueError):
    """Invalid descent inputs or unusable terrain."""


class VehicleProfile(BaseModel):
    """SI vehicle parameters at powered descent initiation."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    profile_id: str = "custom"
    name: str = "Custom vehicle"
    mass_at_pdi_kg: float = Field(gt=100, le=100000)
    usable_propellant_kg: float = Field(gt=0, le=90000)
    max_thrust_n: float = Field(gt=0, le=1e6)
    design_thrust_n: float = Field(gt=0, le=1e6)
    min_throttle_fraction: float = Field(ge=0, lt=1)
    max_throttle_fraction: float = Field(gt=0, le=1)
    isp_s: float = Field(gt=50, le=500)


class OrbitSettings(BaseModel):
    """Initial circular orbit: altitude above the reference sphere and inclination."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    altitude_km: float = Field(default=DEFAULT_ORBIT_ALTITUDE_KM, ge=30, le=500)
    inclination_deg: float = Field(default=90.0, ge=0, le=180)


class Tolerances(BaseModel):
    """Touchdown success limits."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    position_m: float = Field(default=5.0, gt=0, le=1000)
    vertical_speed_mps: float = Field(default=1.0, gt=0, le=10)
    horizontal_speed_mps: float = Field(default=0.5, gt=0, le=10)


class DescentRequest(BaseModel):
    """Site (MOON_ME degrees), vehicle profile ID or custom profile, orbit and limits."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    profile_id: str = "apollo-lm-11"
    custom_profile: VehicleProfile | None = None
    orbit: OrbitSettings = Field(default_factory=OrbitSettings)
    tolerances: Tolerances = Field(default_factory=Tolerances)
    allow_plane_change: bool = False


def load_profiles(path: Path = PROFILES_FILE) -> dict[str, VehicleProfile]:
    """Sourced profiles converted from document units to SI."""
    raise NotImplementedError


def apollo11_reference(path: Path = PROFILES_FILE) -> dict:
    """TN D-6846 Table I reference values in their published units."""
    raise NotImplementedError


def hohmann_deorbit(orbit_radius_m: float, periapsis_radius_m: float) -> dict:
    """Delta-v (m/s), periapsis speed (m/s) and coast time (s) of the transfer."""
    raise NotImplementedError


def plane_change(latitude_deg: float, inclination_deg: float, speed_mps: float) -> dict:
    """Required plane rotation (deg) and impulsive delta-v (m/s) to overfly the site."""
    raise NotImplementedError


def engine_thrust(command_n: float, profile: VehicleProfile) -> tuple[float, bool]:
    """Apply the TN D-6846 throttle rule; return (thrust N, saturated)."""
    raise NotImplementedError


def zem_zev_acceleration(
    position_m: np.ndarray,
    velocity_mps: np.ndarray,
    target_position_m: np.ndarray,
    target_velocity_mps: np.ndarray,
    time_to_go_s: float,
    gravity_mps2: np.ndarray,
) -> np.ndarray:
    """Commanded thrust acceleration: 6 ZEM / t^2 - 2 ZEV / t."""
    raise NotImplementedError


def gate_states(site_radius_m: float) -> list[dict]:
    """Apollo 11 gate targets as planar states relative to the site."""
    raise NotImplementedError


def simulate_powered_descent(
    profile: VehicleProfile,
    site_radius_m: float,
    pdi_range_m: float,
    periapsis_speed_mps: float,
    dt_s: float = 0.1,
) -> dict:
    """Integrate braking, approach and terminal phases with RK4."""
    raise NotImplementedError


def plan_descent(request: DescentRequest, terrain_height_m: float) -> dict:
    """Choose the PDI range, simulate and report feasibility and phase budgets."""
    raise NotImplementedError


def terrain_height_m(
    data_dir: Path, latitude_deg: float, longitude_deg_east: float
) -> float:
    """Height of the containing 80 m DEM pixel above the 1737.4 km sphere."""
    raise NotImplementedError


def register_descent_routes(app, data_dir: Path) -> None:
    """GET /vehicle-profiles and POST /descents."""
    raise NotImplementedError
