"""H6 bounded asynchronous QAOA selection with fresh classical recovery fallback."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from pydantic import Field

from shared.hybrid import Contract, Identifier, NonNegative, Scenario
from .self_healing import NavigationUncertainty, RecoveryScenario
from .terminal_descent import TerminalState


class RecoveryCheckpoint(Contract):
    estimated_state: TerminalState
    available_thrust_n: NonNegative
    landing_hazards: tuple[tuple[float, float], ...] = ()
    outage_end_utc: datetime
    uncertainty: NavigationUncertainty
    previous_target_id: Identifier


class LandingBinding(Contract):
    target_id: Identifier
    option_id: Identifier


class LandingDependency(Contract):
    follower_option_id: Identifier
    minimum_delay_s: Annotated[float, Field(strict=True, ge=0, le=3600)] = 0.0


class HybridRecoveryRequest(Contract):
    recovery: RecoveryScenario
    checkpoint: RecoveryCheckpoint
    campaign: Scenario
    landing_mission_id: Identifier
    bindings: Annotated[tuple[LandingBinding, ...], Field(min_length=1, max_length=8)]
    dependencies: tuple[LandingDependency, ...] = ()


class QuantumBudget(Contract):
    deadline_s: Annotated[float, Field(strict=True, gt=0, le=30)] = 5.0
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    evaluations: Annotated[int, Field(strict=True, ge=1, le=512)] = 64
    shots: Annotated[int, Field(strict=True, ge=1, le=8192)] = 256
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17
    max_qubits: Annotated[int, Field(strict=True, ge=1, le=6)] = 4


@dataclass
class RecoveryTicket:
    request_sha256: str
    prepared: dict
    fallback: object
    future: object
    deadline: float
    launch_reason: str


def checkpoint_from_decision(decision: dict) -> RecoveryCheckpoint:
    """Extract H5's estimate and active constraints; ignore claimed acceptance flags."""
    raise NotImplementedError


def prepare_problem(job_request: HybridRecoveryRequest) -> dict:
    """Recheck H5 physics, bind explicit sites, shift landing service windows and add dependencies."""
    raise NotImplementedError


class RecoveryCoordinator:
    def __init__(self, *, runner=None, clock=None):
        raise NotImplementedError

    def submit(
        self,
        job_request: HybridRecoveryRequest,
        budget: QuantumBudget = QuantumBudget(),
    ) -> RecoveryTicket:
        """Prepare a verified classical incumbent before launching one bounded worker."""
        raise NotImplementedError

    def resolve(
        self, ticket: RecoveryTicket, current_request: HybridRecoveryRequest
    ) -> dict:
        """Revalidate current physics/schedule; reject pending, late, stale or invalid results."""
        raise NotImplementedError

    def wait(
        self, ticket: RecoveryTicket, current_request: HybridRecoveryRequest
    ) -> dict:
        """Wait within the quantum decision budget, then resolve against current inputs."""
        raise NotImplementedError

    def close(self):
        """Drain the bounded local worker; no provider job or live actuator exists."""
        raise NotImplementedError


def replay_hybrid(
    recovery: RecoveryScenario,
    campaign: Scenario,
    landing_mission_id: str,
    bindings: tuple[LandingBinding, ...],
    dependencies: tuple[LandingDependency, ...],
    budget: QuantumBudget = QuantumBudget(),
) -> dict:
    """Offline H5 replay with guarded joint scheduling at each frozen planning checkpoint."""
    raise NotImplementedError
