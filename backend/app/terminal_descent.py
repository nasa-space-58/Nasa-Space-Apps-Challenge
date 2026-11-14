"""H4 bounded restartable terminal descent in an explicit nonrotating 2D frame."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from shared.constants import MOON_REFERENCE_RADIUS_M
from shared.hybrid import Contract, Identifier, NonNegative, Number, Positive, Source

Vector2 = tuple[Number, Number]


class TerminalState(Contract):
    epoch_utc: datetime
    vehicle_id: Identifier
    revision: Annotated[int, Field(strict=True, ge=0)] = 0
    reference_frame: Literal["PLANAR_MOON_CENTERED_NONROTATING"] = (
        "PLANAR_MOON_CENTERED_NONROTATING"
    )
    position_m: Vector2
    velocity_mps: Vector2
    propellant_kg: NonNegative


class TerminalVehicle(Contract):
    vehicle_id: Identifier
    dry_mass_kg: Annotated[float, Field(strict=True, gt=0, le=100000)]
    propellant_capacity_kg: Annotated[float, Field(strict=True, ge=0, le=90000)]
    max_thrust_n: Annotated[float, Field(strict=True, gt=0, le=1e6)]
    design_thrust_n: Annotated[float, Field(strict=True, gt=0, le=1e6)]
    min_throttle_fraction: Annotated[float, Field(strict=True, ge=0, lt=1)] = 0.0
    max_throttle_fraction: Annotated[float, Field(strict=True, gt=0, le=1)] = 1.0
    isp_s: Annotated[float, Field(strict=True, gt=50, le=500)]
    source: Source


class TerrainPoint(Contract):
    downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    height_m: Annotated[float, Field(strict=True, ge=-10000, le=10000)]


class TerrainCorridor(Contract):
    reference_radius_m: Annotated[float, Field(strict=True, ge=1700000, le=1800000)] = (
        MOON_REFERENCE_RADIUS_M
    )
    points: Annotated[tuple[TerrainPoint, ...], Field(min_length=2, max_length=4096)]
    source: Source


class TerminalRequest(Contract):
    initial_state: TerminalState
    vehicle: TerminalVehicle
    terrain: TerrainCorridor
    target_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)] = 0.0
    duration_s: Annotated[float, Field(strict=True, ge=5, le=300)] = 30.0
    dt_s: Annotated[float, Field(strict=True, ge=0.02, le=1)] = 0.1
    reserve_kg: NonNegative = 0.0
    target_vertical_speed_mps: Annotated[float, Field(strict=True, ge=-5, lt=0)] = -0.5
    position_tolerance_m: Positive = 5.0
    vertical_speed_limit_mps: Positive = 2.0
    horizontal_speed_limit_mps: Positive = 1.0
    slope_limit_deg: Annotated[float, Field(strict=True, ge=0, lt=90)] = 15.0


class LandingTarget(Contract):
    target_id: Identifier
    downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    duration_s: Annotated[float, Field(strict=True, ge=5, le=300)]


def vehicle_from_r4(profile: dict, *, source: Source) -> TerminalVehicle:
    """Adapt the R4 SI profile without importing FastAPI or terrain I/O."""
    raise NotImplementedError


def terrain_at(corridor: TerrainCorridor, downrange_m: float) -> tuple[float, float]:
    """Return height (m), conservative adjacent slope (degrees); never extrapolate."""
    raise NotImplementedError


def simulate_terminal(job_request: TerminalRequest) -> dict:
    """Integrate from the supplied state to first sampled terrain contact or timeout."""
    raise NotImplementedError


def evaluate_targets(
    job_request: TerminalRequest, targets: tuple[LandingTarget, ...]
) -> dict:
    """Check at most 16 explicit targets at dt and dt/2, retaining every verdict."""
    raise NotImplementedError
