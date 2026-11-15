"""Durable synthetic planning sessions shared across mission workspaces."""

from typing import Annotated, Literal

from pydantic import Field

from shared.hybrid import Contract, Identifier

from .unified_quantum import ProposalBudget


class PlanCommand(Contract):
    request_id: Identifier
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    mode: Literal["classical", "local_experiment"] = "classical"
    ttl_s: Annotated[float, Field(strict=True, gt=0, le=120)] = 30.0
    budget: ProposalBudget = ProposalBudget()


class EventCommand(PlanCommand):
    event_id: Identifier
    elapsed_s: Annotated[float, Field(strict=True, gt=0, lt=10)]
    start_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    end_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]


class CoordinationError(ValueError):
    """An invalid transition, missing session, or conflicting command."""


class UnifiedCoordinator:
    def __init__(self, path, *, clock=None):
        raise NotImplementedError

    def create(self):
        raise NotImplementedError

    def get(self, session_id):
        raise NotImplementedError

    def recent(self):
        raise NotImplementedError

    def plan(self, session_id, command: PlanCommand):
        raise NotImplementedError

    def event(self, session_id, command: EventCommand):
        raise NotImplementedError

    def tick(self):
        raise NotImplementedError

    def recover_interrupted(self):
        raise NotImplementedError


def register_unified_routes(app, coordinator):
    raise NotImplementedError
