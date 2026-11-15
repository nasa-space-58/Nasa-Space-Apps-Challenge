"""R12 self-healing landing: event-triggered classical replanning (S6/S11/S13).

The R9 twin flies the R4 descent. When an event invalidates the active plan,
the vehicle replans from what it *believes* (its estimated state):

1. Evaluate continuing to the current target, then R7-style backup sites from
   the cached DEM (R3 hard constraints), in ranked order, until an evaluation
   budget is spent. Each option is flown from the estimate with R4 guidance
   (downrange by state rotation, crossrange by the analytic minimum-energy
   lateral divert) and must keep the R5 reserve, meet the R4 tolerances,
   stay within maximum thrust and keep k-sigma clearance from detected
   obstacles and R3 hazards, where sigma is the navigation uncertainty.
2. A selector orders the acceptable options (classical default; a hybrid or
   quantum selector can be plugged in). The first that passes independent
   validation - flown again from the *true* state at 0.05 s - is applied.
3. If none passes, the result is `abort_required`: abort-to-orbit is reported,
   not simulated, and the vehicle keeps flying the old plan so its outcome
   stays visible. Hover/defer is not modelled.

Decisions after the deadline are not made (`no_time_to_replan`). With a
solver latency, a result computed for an older state revision is rejected as
stale and recomputed. The same events are also flown without replanning so
the two outcomes can be compared.
"""

from pathlib import Path
from typing import Callable

from pydantic import Field

from app.alternatives import LocalTerrain
from app.terrain_safety import SafetySettings
from app.terrain_twin import TwinRequest

EVALUATION_STEP_S = 0.2
VALIDATION_STEP_S = 0.05
DEADLINE_AFTER_LOW_GATE_S = 10.0
CONVERGENCE_STEPS_S = (0.5, 0.2, 0.1, 0.05)
ACTIONS = (
    "continue",
    "retarget",
    "abort_required",
    "budget_exhausted",
    "no_time_to_replan",
    "stale_result_rejected",
)


class HealingError(ValueError):
    """Invalid inputs, unusable terrain or a plan the R4 model rejects."""


class HealingRequest(TwinRequest):
    """R9 twin inputs plus replanning budget, deadline and margins."""

    search_radius_m: float = Field(default=2000.0, ge=400, le=5000)
    candidate_spacing_m: float = Field(default=160.0, ge=80, le=1000)
    min_separation_m: float = Field(default=400.0, ge=0, le=3000)
    max_candidates: int = Field(default=8, ge=1, le=12, strict=True)
    max_evaluations: int = Field(default=12, ge=1, le=50, strict=True)
    solver_latency_s: float = Field(default=0.0, ge=0, le=30)
    decision_deadline_s: float | None = Field(default=None, ge=0, le=3600)
    k_sigma: float = Field(default=3.0, ge=0, le=6)
    approach_azimuth_deg: float = Field(default=180.0, ge=0, lt=360)
    safety: SafetySettings = Field(default_factory=SafetySettings)


Selector = Callable[[list[dict], dict], list[dict]]


def margin_ok(clearance_m: float | None, estimate_std_m: float, k_sigma: float) -> bool:
    """True when a hazard clearance covers k-sigma navigation uncertainty.

    None means no hazard was found within the search radius (always acceptable).
    """
    raise NotImplementedError


def convergence_check(
    profile, site_radius_m: float, pdi_range_m: float, periapsis_speed_mps: float
) -> list[dict]:
    """R4 touchdown error, speeds and final mass at each CONVERGENCE_STEPS_S."""
    raise NotImplementedError


def default_rank(options: list[dict], state: dict) -> list[dict]:
    """Classical order: highest safety score, then least divert delta-v, then id."""
    raise NotImplementedError


def evaluate_option(
    state: dict,
    gates: list[dict],
    option: dict,
    request: HealingRequest,
    obstacles: list[dict],
    target_offset_m: tuple[float, float],
    k_sigma: float,
    step_s: float,
) -> dict:
    """Fly one option (offset from the current target) from a state snapshot.

    `option` has id, down_m, cross_m (relative to the current target),
    target_height_m and hazard_clearance_m. Obstacles are in the original
    target frame; `target_offset_m` locates the current target in it.
    Returns reachable, reasons, delta-v, propellant, reserve margin, lateral
    propellant and touchdown values.
    """
    raise NotImplementedError


def run_self_healing(
    request: HealingRequest,
    terrain_height_m: float,
    terrain: LocalTerrain,
    select_fn: Selector | None = None,
) -> dict:
    """Fly the events with and without replanning and report every decision.

    Raises HealingError for an unknown vehicle or a plan the R4 model rejects.
    """
    raise NotImplementedError


def register_self_healing_routes(app, data_dir: Path) -> None:
    """POST /descents/self-healing: 409 without cached terrain, 422 otherwise."""

    @app.post("/descents/self-healing")
    def self_healing(body: HealingRequest):
        raise NotImplementedError
