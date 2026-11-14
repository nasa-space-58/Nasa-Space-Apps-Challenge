"""H8 bounded classical/QAOA comparison and recorded experiment replay."""

from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import Field

from shared.hybrid import Contract, Scenario


class ArenaRequest(Contract):
    preset_id: Literal["recovery", "shared-conflict", "independent", "infeasible", "missing-capacity"] = "recovery"
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 2
    evaluations: Annotated[int, Field(strict=True, ge=1, le=256)] = 80
    shots: Annotated[int, Field(strict=True, ge=1, le=8192)] = 512
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17


def load_arena_scenario(preset_id: str) -> Scenario:
    raise NotImplementedError


def compare_solvers(request: ArenaRequest) -> dict:
    raise NotImplementedError


def recorded_hardware() -> dict:
    raise NotImplementedError


def register_arena_routes(app):
    raise NotImplementedError


router = APIRouter(prefix="/arena", tags=["solver-arena"])
