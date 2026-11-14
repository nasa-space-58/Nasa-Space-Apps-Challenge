"""R12 event-panel view over H5 fault replay and H6 guarded hybrid recovery.

This module only reshapes existing H5/H6 evidence for display: event list,
original/classical/hybrid trajectories, decision explanations, sampled landing
dispersion and solver timing. It adds no new physics, estimator or solver.
"""

from pathlib import Path

from fastapi import HTTPException

import shared.hybrid as hybrid_contracts
from shared.hybrid import Contract, Identifier, Scenario

from .hybrid_recovery import LandingBinding, LandingDependency, QuantumBudget
from .self_healing import RecoveryScenario

EXAMPLES_DIR = Path(hybrid_contracts.__file__).parent / "examples" / "recovery"
VIEW_VERSION = "r12-replay-view-v1"
MAX_TRAJECTORY_POINTS = 400


class HybridReplayInputs(Contract):
    campaign: Scenario
    landing_mission_id: Identifier
    bindings: tuple[LandingBinding, ...]
    dependencies: tuple[LandingDependency, ...] = ()
    quantum_budget: QuantumBudget = QuantumBudget()


class RecoveryReplayRequest(Contract):
    recovery: RecoveryScenario
    hybrid: HybridReplayInputs | None = None


def example_requests() -> dict[str, dict]:
    """Return every recovery example as a ready RecoveryReplayRequest body."""
    raise NotImplementedError


def build_replay_view(request: RecoveryReplayRequest) -> dict:
    """Replay identical events under original, classical and optional hybrid plans."""
    raise NotImplementedError


def register_recovery_routes(app) -> None:
    """GET /recovery/examples[/{name}] and POST /recovery/replays."""

    @app.get("/recovery/examples")
    def recovery_examples():
        return [
            {
                "name": name,
                "scenario_id": body["recovery"]["scenario_id"],
                "hybrid": body.get("hybrid") is not None,
            }
            for name, body in example_requests().items()
        ]

    @app.get("/recovery/examples/{name}")
    def recovery_example(name: str):
        body = example_requests().get(name)
        if body is None:
            raise HTTPException(404, "Unknown recovery example")
        return body

    @app.post("/recovery/replays")
    def recovery_replay(body: RecoveryReplayRequest):
        try:
            return build_replay_view(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
