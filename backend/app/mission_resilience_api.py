"""Durable bounded mission replay jobs, with cancellation and expiry fences."""
from typing import Annotated

from pydantic import Field
from shared.hybrid import Contract, Identifier
from .mission_resilience import MissionScenario


class ReplayCommand(Contract):
    request_id: Identifier
    scenario: MissionScenario
    ttl_s: Annotated[float, Field(strict=True, gt=0, le=600)] = 120.0


class ReplayConflict(ValueError):
    pass


class MissionReplayStore:
    def __init__(self, path, *, clock=None):
        raise NotImplementedError

    def submit(self, command: ReplayCommand):
        raise NotImplementedError

    def get(self, run_id):
        raise NotImplementedError

    def recent(self):
        raise NotImplementedError

    def cancel(self, run_id):
        raise NotImplementedError

    def tick(self, *, runner=None):
        raise NotImplementedError

    def recover_interrupted(self):
        raise NotImplementedError


def register_resilience_routes(app, store):
    raise NotImplementedError
