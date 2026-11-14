"""H5 deterministic planar fault replay and classical candidate replanning."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from shared.hybrid import Contract, Identifier, NonNegative, Source
from .terminal_descent import TerminalRequest

Sigma2 = tuple[
    Annotated[float, Field(strict=True, ge=0, le=100)],
    Annotated[float, Field(strict=True, ge=0, le=100)],
]


class NavigationUncertainty(Contract):
    position_std_m: Sigma2 = (0.0, 0.0)
    velocity_std_mps: Sigma2 = (0.0, 0.0)
    thrust_loss_fraction_bound: Annotated[float, Field(strict=True, ge=0, le=1)] = 0.0
    fuel_shortfall_bound_kg: Annotated[float, Field(strict=True, ge=0, le=1000)] = 0.0
    samples: Annotated[int, Field(strict=True, ge=1, le=32)] = 8
    source: Source


class ThrustFault(Contract):
    kind: Literal["thrust_loss"]
    available_thrust_n: NonNegative


class FuelFault(Contract):
    kind: Literal["propellant_loss"]
    amount_kg: NonNegative


class SensorFault(Contract):
    kind: Literal["sensor_degradation"]
    sensor_id: Identifier
    position_std_m: Sigma2
    velocity_std_mps: Sigma2 = (0.0, 0.0)


class LandingHazard(Contract):
    kind: Literal["landing_hazard"]
    start_downrange_m: float
    end_downrange_m: float


class CommunicationFault(Contract):
    kind: Literal["communication_outage"]
    end_utc: datetime


class RecoveryEvent(Contract):
    event_id: Identifier
    vehicle_id: Identifier
    epoch_utc: datetime
    sequence: Annotated[int, Field(strict=True, ge=0)]
    payload: Annotated[
        ThrustFault | FuelFault | SensorFault | LandingHazard | CommunicationFault,
        Field(discriminator="kind"),
    ]
    source: Source


class RecoveryTarget(Contract):
    target_id: Identifier
    downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    touchdown_deadline_utc: datetime


class RecoveryScenario(Contract):
    scenario_id: Identifier
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17
    initial_request: TerminalRequest
    nominal_target_id: Identifier
    targets: Annotated[tuple[RecoveryTarget, ...], Field(min_length=1, max_length=8)]
    events: Annotated[tuple[RecoveryEvent, ...], Field(max_length=8)] = ()
    uncertainty: NavigationUncertainty
    require_link_for_retarget: bool = True


def replay_recovery(scenario: RecoveryScenario, *, replan: bool = True) -> dict:
    """Execute timestamped faults, compare estimates and truth, and retain decisions."""
    raise NotImplementedError


def compare_recovery(scenario: RecoveryScenario) -> dict:
    """Replay the same inputs under the fixed original target and classical recovery."""
    raise NotImplementedError
