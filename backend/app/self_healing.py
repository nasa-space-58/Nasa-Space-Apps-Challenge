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

import math
from pathlib import Path
from time import perf_counter
from typing import Callable

import numpy as np
from fastapi import HTTPException
from pydantic import Field

from app.alternatives import (
    AlternativesError,
    AlternativesRequest,
    LocalTerrain,
    _site_pixel,
    find_candidates,
    lateral_divert,
    load_local_terrain,
    rotate_state,
)
from app.descent import (
    DescentError,
    DescentRequest,
    fly_gates,
    gate_states,
    plan_descent,
    simulate_powered_descent,
    terrain_height_m,
)
from app.fuel_optimization import _reason, reserve_propellant_kg
from app.terrain_safety import DEM_PIXEL_M, SafetySettings, safety_score, site_safety
from app.twin import (
    TwinError,
    TwinFlight,
    TwinRequest,
    _downsample,
    _touchdown_check,
    _vehicle,
    canonical_hash,
    estimate_sigma,
    fly_twin,
)
from shared.constants import MOON_REFERENCE_RADIUS_M, STANDARD_GRAVITY_M_S2

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


ASSUMPTIONS = (
    "The vehicle is the R9 twin flying the R4 descent; replanning sees only the estimated state, never the truth.",
    "A decision is triggered when an event invalidates the active plan; continuing to the current target is evaluated first, then backups.",
    "Backups are R7-style DEM cells passing the R3 hard constraints within the search radius, ranked by safety score.",
    "Options are flown from the estimate with R4 guidance (downrange by state rotation) plus the analytic lateral divert (3|c|/T) after the fixed-throttle block.",
    "An option must keep the R5 reserve after the divert and a k-sigma correction allowance, meet R4 tolerances, stay within maximum thrust and keep k-sigma clearance from obstacles and R3 hazards.",
    "Sigma is the navigation uncertainty the planner must assume: the estimator's error with a working sensor, or, with the sensor lost, a bound on drift to touchdown from a one-sigma engine bias. The bias itself is unknown to the planner and to validation.",
    "The chosen option is validated independently from the true state at 0.05 s before it is applied; a failed validation moves to the next option.",
    "The evaluation budget is a count (deterministic); wall-clock solver time is reported but excluded from the result ID.",
    "abort_required means no evaluated option is feasible: abort-to-orbit is not simulated and the vehicle keeps flying its old plan; hover/defer is not modelled.",
    "Hybrid or quantum selection, asynchronous quantum paths and propagation into a multi-mission schedule are not part of this classical prototype.",
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
    if clearance_m is None:
        return True
    return clearance_m >= k_sigma * estimate_std_m


def convergence_check(
    profile, site_radius_m: float, pdi_range_m: float, periapsis_speed_mps: float
) -> list[dict]:
    """R4 touchdown error, speeds and final mass at each CONVERGENCE_STEPS_S."""
    rows = []
    for step in CONVERGENCE_STEPS_S:
        run = simulate_powered_descent(
            profile, site_radius_m, pdi_range_m, periapsis_speed_mps, step, record=False
        )
        final = run["final"]
        rows.append(
            {
                "step_s": step,
                "position_error_m": final["position_error_m"],
                "vertical_speed_mps": final["vertical_speed_mps"],
                "horizontal_speed_mps": final["horizontal_speed_mps"],
                "final_mass_kg": final["mass_kg"],
            }
        )
    return rows


def default_rank(options: list[dict], state: dict) -> list[dict]:
    """Classical order: highest safety score, then least divert delta-v, then id."""
    return sorted(
        options,
        key=lambda o: (
            -(o.get("safety_score") or 0.0),
            o.get("total_delta_v_mps", 0.0),
            o["id"],
        ),
    )


def _gates_for(template: list[dict], radius_m: float) -> list[dict]:
    """Gate targets for a target at radius_m, keeping the template durations."""
    full = {g["name"]: g for g in gate_states(radius_m)}
    return [full[t["name"]] | {"duration_s": t["duration_s"]} for t in template]


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
    `gates` gives the remaining gate names and durations; their targets are
    rebuilt for the option's height. Returns reachable, reasons, delta-v,
    propellant, reserve margin, lateral propellant and touchdown values.
    """
    radius = MOON_REFERENCE_RADIUS_M + option["target_height_m"]
    flown_gates = _gates_for(gates, radius)
    position, velocity = rotate_state(
        state["position_m"], state["velocity_mps"], option["down_m"] / radius
    )
    engine = state["engine"]
    run = fly_gates(
        engine,
        radius,
        np.array(position),
        np.array(velocity),
        state["mass_kg"],
        flown_gates,
        step_s,
        record=True,
    )
    total_s = sum(g["duration_s"] for g in flown_gates)
    flight = run["samples"][:-1]
    saturated = engine.max_thrust_n * (1 - 1e-9)
    lateral_start = total_s
    for sample in flight:
        if sample["thrust_n"] < saturated:
            lateral_start = sample["t_s"]
            break
    std = state["estimate_std_m"]
    margin = k_sigma * std
    reasons = []
    cross = option["cross_m"]
    lateral_dv = 0.0
    if cross != 0.0:
        window = total_s - lateral_start
        if window <= 0:
            reasons.append(
                _reason(
                    "thrust",
                    "The engine stays at full thrust; nothing is left for a lateral move.",
                    abs(cross),
                    0.0,
                    "m",
                )
            )
        else:
            lateral = lateral_divert(cross, window)
            lateral_dv = lateral["delta_v_mps"]
            worst = 0.0
            for sample in flight:
                if sample["t_s"] < lateral_start:
                    continue
                fraction = (sample["t_s"] - lateral_start) / window
                a_lateral = lateral["peak_acceleration_mps2"] * abs(1 - 2 * fraction)
                a_plane = sample["thrust_n"] / sample["mass_kg"]
                worst = max(
                    worst,
                    math.hypot(a_plane, a_lateral)
                    / (engine.max_thrust_n / sample["mass_kg"]),
                )
            if worst > 1 + 1e-9:
                reasons.append(
                    _reason(
                        "thrust",
                        f"Needs {100 * worst:.0f} % of maximum thrust for a {abs(cross):.0f} m sideways move.",
                        worst,
                        1.0,
                        "fraction of maximum thrust",
                    )
                )
    correction_dv = 3 * margin / total_s if margin > 0 and total_s > 0 else 0.0
    ve = engine.isp_s * STANDARD_GRAVITY_M_S2
    final = run["final"]
    after_lateral = final["mass_kg"] * math.exp(-lateral_dv / ve)
    landing_mass = after_lateral * math.exp(-correction_dv / ve)
    dry = engine.mass_at_pdi_kg - engine.usable_propellant_kg
    remaining = landing_mass - dry
    reserve = reserve_propellant_kg(
        landing_mass, request.reserve_delta_v_mps, engine.isp_s
    )
    if run["propellant_exhausted"] or remaining < 0:
        reasons.insert(
            0,
            _reason(
                "propellant",
                "Not enough propellant to reach this target.",
                -remaining,
                0.0,
                "kg",
            ),
        )
    elif remaining - reserve < 0:
        reasons.insert(
            0,
            _reason(
                "reserve",
                f"Leaves {remaining:.0f} kg; the reserve (with a {margin:.0f} m correction allowance) needs {reserve:.0f} kg.",
                remaining,
                reserve,
                "kg",
            ),
        )
    tol = request.tolerances
    misses = []
    if final["position_error_m"] > tol.position_m:
        misses.append(f"position error {final['position_error_m']:.1f} m")
    if abs(final["vertical_speed_mps"]) > tol.vertical_speed_mps:
        misses.append(f"vertical speed {abs(final['vertical_speed_mps']):.2f} m/s")
    if final["horizontal_speed_mps"] > tol.horizontal_speed_mps:
        misses.append(f"horizontal speed {final['horizontal_speed_mps']:.2f} m/s")
    if misses:
        reasons.append(
            _reason(
                "touchdown_tolerance",
                "Touchdown outside tolerance: " + "; ".join(misses) + ".",
                final["position_error_m"],
                tol.position_m,
                "m",
            )
        )
    absolute = (
        target_offset_m[0] + option["down_m"],
        target_offset_m[1] + option["cross_m"],
    )
    for obstacle in obstacles:
        distance = math.hypot(
            absolute[0] - obstacle["down_m"], absolute[1] - obstacle["cross_m"]
        )
        if distance < obstacle["radius_m"]:
            reasons.append(
                _reason(
                    "obstacle",
                    "The target lies inside a detected obstacle.",
                    distance,
                    obstacle["radius_m"],
                    "m",
                )
            )
        elif not margin_ok(distance - obstacle["radius_m"], std, k_sigma):
            reasons.append(
                _reason(
                    "uncertainty_margin",
                    f"Only {distance - obstacle['radius_m']:.0f} m from an obstacle; {k_sigma:g} sigma of navigation error is {margin:.0f} m.",
                    distance - obstacle["radius_m"],
                    margin,
                    "m",
                )
            )
    if not margin_ok(option.get("hazard_clearance_m"), std, k_sigma):
        reasons.append(
            _reason(
                "uncertainty_margin",
                f"Steep terrain {option['hazard_clearance_m']:.0f} m away; {k_sigma:g} sigma of navigation error is {margin:.0f} m.",
                option["hazard_clearance_m"],
                margin,
                "m",
            )
        )
    return {
        "id": option["id"],
        "down_m": option["down_m"],
        "cross_m": option["cross_m"],
        "target_height_m": option["target_height_m"],
        "hazard_clearance_m": option.get("hazard_clearance_m"),
        "safety_score": option.get("safety_score"),
        "distance_m": option.get("distance_m", math.hypot(option["down_m"], cross)),
        "reachable": not reasons,
        "reasons": reasons,
        "in_plane_delta_v_mps": float(sum(p["delta_v_mps"] for p in run["phases"])),
        "lateral_delta_v_mps": float(lateral_dv),
        "total_delta_v_mps": float(
            sum(p["delta_v_mps"] for p in run["phases"]) + lateral_dv
        ),
        "lateral_propellant_kg": float(final["mass_kg"] - after_lateral),
        "propellant_remaining_kg": float(remaining),
        "reserve_margin_kg": float(remaining - reserve),
        "time_to_touchdown_s": float(total_s),
        "touchdown": {
            "position_error_m": float(final["position_error_m"]),
            "vertical_speed_mps": float(final["vertical_speed_mps"]),
            "horizontal_speed_mps": float(final["horizontal_speed_mps"]),
        },
    }


def _expected_std(flight: TwinFlight) -> float:
    """Navigation uncertainty the planner must assume for the rest of the descent.

    With a working sensor: the estimator's steady-state error for the current
    noise. Without one, the error at touchdown is bounded by the speed term
    plus the drift a one-sigma engine bias would cause over the remaining
    time: 0.5 * bias * (max thrust / mass) * T^2. This is the planner's
    conservative bound, not the twin's own (naive) filter estimate.
    """
    if flight.health != "lost":
        return max(flight.est_std, estimate_sigma(flight._sigmas()[0], flight.gain))
    remaining = sum(g["duration_s"] for g in flight.remaining_gates())
    speed = estimate_sigma(flight.speed_sigma, flight.gain) * remaining
    acceleration = flight.engine.max_thrust_n / flight.mass
    drift = 0.5 * flight.sensor.thrust_noise_fraction * acceleration * remaining**2
    return flight.est_std + speed + drift


def _outcome(run: dict, request, profile, target_score, abort: bool) -> dict:
    check = _touchdown_check(run, request, profile)
    return {
        "success": check["success"],
        "reasons": check["reasons"],
        "final": run["final"],
        "target_offset_m": run["target_offset_m"],
        "target_safety_score": target_score,
        "reserve_margin_kg": check["reserve_margin_kg"],
        "propellant_remaining_kg": run["propellant_remaining_kg"],
        "abort_required": abort,
        "plan_valid": run["plan"]["valid"],
    }


def run_self_healing(
    request: HealingRequest,
    terrain_height_m: float,
    terrain: LocalTerrain,
    select_fn: Selector | None = None,
) -> dict:
    """Fly the events with and without replanning and report every decision.

    Raises HealingError for an unknown vehicle or a plan the R4 model rejects.
    """
    try:
        profile = _vehicle(request)
    except TwinError as exc:
        raise HealingError(str(exc)) from exc
    try:
        nominal = plan_descent(
            DescentRequest(
                site_id=request.site_id,
                latitude_deg=request.latitude_deg,
                longitude_deg_east=request.longitude_deg_east,
                profile_id=request.profile_id,
                custom_profile=request.custom_profile,
                orbit=request.orbit,
                tolerances=request.tolerances,
            ),
            terrain_height_m,
        )
    except (DescentError, ValueError) as exc:
        raise HealingError(str(exc)) from exc
    site_radius = nominal["site"]["radius_m"]
    pdi = nominal["pdi_range_m"]
    speed = nominal["deorbit"]["periapsis_speed_mps"]
    selector = select_fn or default_rank
    heights = np.asarray(terrain.heights_m, dtype=float)
    try:
        primary = site_safety(
            heights, terrain.pixel_m, *_site_pixel(terrain), request.safety
        )
    except ValueError:
        primary = None
    primary_score = (
        float(safety_score(primary, request.safety))
        if primary and primary["max_landing_slope_deg"] is not None
        else None
    )
    try:
        search = AlternativesRequest(
            site_id=request.site_id,
            latitude_deg=request.latitude_deg,
            longitude_deg_east=request.longitude_deg_east,
            search_radius_m=request.search_radius_m,
            candidate_spacing_m=request.candidate_spacing_m,
            min_separation_m=request.min_separation_m,
            max_candidates=request.max_candidates,
            approach_azimuth_deg=request.approach_azimuth_deg,
            safety=request.safety,
        )
        found = find_candidates(terrain, search)
    except (AlternativesError, ValueError) as exc:
        raise HealingError(str(exc)) from exc
    candidates = [
        {
            "id": f"B{i + 1}",
            "down_m": c["down_m"],
            "cross_m": c["cross_m"],
            "target_height_m": c["terrain_height_m"],
            "hazard_clearance_m": c["safety"]["hazard_distance_m"],
            "safety_score": c["score"],
            "distance_m": c["distance_m"],
        }
        for i, c in enumerate(found)
    ]
    nominal_gates = gate_states(site_radius)
    deadline = (
        request.decision_deadline_s
        if request.decision_deadline_s is not None
        else nominal_gates[0]["duration_s"]
        + nominal_gates[1]["duration_s"]
        + DEADLINE_AFTER_LOW_GATE_S
    )
    common = dict(
        seed=request.seed,
        step_s=request.step_s,
        battery=request.battery,
        plan_dependencies=tuple(request.plan_dependencies),
    )
    original = fly_twin(
        profile, site_radius, pdi, speed, request.sensor, list(request.events), **common
    )
    flight = TwinFlight(
        profile, site_radius, pdi, speed, request.sensor, list(request.events), **common
    )
    current = {
        "down_m": 0.0,
        "cross_m": 0.0,
        "target_height_m": terrain_height_m,
        "hazard_clearance_m": primary["hazard_distance_m"] if primary else None,
        "safety_score": primary_score,
    }
    decisions: list[dict] = []
    backups: list[dict] = []
    aborted = False

    def options_now():
        offset = (flight.target_down_m, flight.target_cross_m)
        listed = []
        for c in candidates:
            down, cross = c["down_m"] - offset[0], c["cross_m"] - offset[1]
            if math.hypot(down, cross) < 1.0:
                continue
            listed.append(c | {"down_m": down, "cross_m": cross})
        return listed

    def state_now():
        state = flight.estimated_state()
        state["estimate_std_m"] = _expected_std(flight)
        return state

    def count_backups(label):
        state = state_now()
        template = flight.remaining_gates()
        offset = (flight.target_down_m, flight.target_cross_m)
        results = [
            evaluate_option(
                state, template, o, request, flight.obstacles, offset,
                request.k_sigma, EVALUATION_STEP_S,
            )
            for o in options_now()
        ]  # fmt: skip
        backups.append(
            {
                "t_s": flight.clock,
                "label": label,
                "reachable": sum(r["reachable"] for r in results),
                "evaluated": len(results),
            }
        )

    def decide(trigger):
        started = perf_counter()
        state = state_now()
        template = flight.remaining_gates()
        offset = (flight.target_down_m, flight.target_cross_m)
        options = [current | {"id": "CURRENT", "down_m": 0.0, "cross_m": 0.0}]
        options += sorted(
            options_now(), key=lambda o: (-(o["safety_score"] or 0.0), o["distance_m"])
        )
        considered, evaluations, action = [], 0, None
        for option in options:
            if evaluations >= request.max_evaluations:
                break
            result = evaluate_option(
                state, template, option, request, flight.obstacles, offset,
                request.k_sigma, EVALUATION_STEP_S,
            )  # fmt: skip
            evaluations += 1
            considered.append(result)
            if option["id"] == "CURRENT" and result["reachable"]:
                action = "continue"
                break
        ranked = []
        if action is None:
            acceptable = [
                c for c in considered if c["reachable"] and c["id"] != "CURRENT"
            ]
            if acceptable:
                action = "retarget"
                ranked = selector(acceptable, state)
            elif evaluations >= request.max_evaluations and len(considered) < len(
                options
            ):
                action = "budget_exhausted"
            else:
                action = "abort_required"
        explanation = {
            "continue": "The current target is still reachable with the changed vehicle state; the plan is re-validated.",
            "retarget": "The current target is no longer acceptable; the best reachable backup is validated from the true state and flown.",
            "budget_exhausted": "The evaluation budget ran out before an acceptable option was found; the current plan is kept without validation.",
            "abort_required": "No evaluated option is feasible. Abort-to-orbit would be required (not simulated); the old plan keeps flying.",
        }[action]
        return {
            "t_s": flight.clock,
            "applied_at_s": None,
            "trigger": trigger,
            "state_revision": flight.revision,
            "phase": flight.phase,
            "estimate_std_m": state["estimate_std_m"],
            "margin_m": request.k_sigma * state["estimate_std_m"],
            "action": action,
            "evaluations": evaluations,
            "budget": request.max_evaluations,
            "solver_wall_ms": (perf_counter() - started) * 1000,
            "considered": considered,
            "chosen": None,
            "validation": [],
            "explanation": explanation,
            "_ranked": ranked,
        }

    def finalize(decision):
        nonlocal current, aborted
        ranked = decision.pop("_ranked")
        if decision["action"] == "continue":
            flight.revision += 1
            flight.invalidated = []
            flight.note(
                {
                    "kind": "plan_revalidated",
                    "plan_revision": flight.revision,
                    "reason": "current target still reachable",
                }
            )
        elif decision["action"] == "retarget":
            truth = flight.true_state() | {"estimate_std_m": 0.0}
            template = flight.remaining_gates()
            offset = (flight.target_down_m, flight.target_cross_m)
            for option in ranked:
                check = evaluate_option(
                    truth, template, option, request, flight.obstacles, offset,
                    0.0, VALIDATION_STEP_S,
                )  # fmt: skip
                decision["validation"].append(
                    {
                        "id": option["id"],
                        "passed": check["reachable"],
                        "reasons": check["reasons"],
                    }
                )
                if check["reachable"]:
                    flight.retarget(
                        option["down_m"],
                        option["cross_m"],
                        option["target_height_m"],
                        check["lateral_propellant_kg"],
                        f"replanned to {option['id']} after {', '.join(decision['trigger'])}",
                    )
                    decision["chosen"] = option["id"]
                    current = {
                        "down_m": 0.0,
                        "cross_m": 0.0,
                        "target_height_m": option["target_height_m"],
                        "hazard_clearance_m": option.get("hazard_clearance_m"),
                        "safety_score": option.get("safety_score"),
                    }
                    break
            else:
                decision["action"] = "abort_required"
                decision["explanation"] = (
                    "Every acceptable option failed independent validation from the "
                    "true state. Abort-to-orbit would be required (not simulated)."
                )
        if decision["action"] == "abort_required":
            aborted = True
        decision["applied_at_s"] = flight.clock
        flight.note(
            {
                "kind": "decision",
                "action": decision["action"],
                "chosen": decision["chosen"],
                "state_revision": decision["state_revision"],
            }
        )
        decisions.append(decision)

    pending = None
    while not flight.done:
        if flight.has_due_events():
            applied = flight.apply_due()
            trigger = [event.kind for event in applied]
            if pending is not None:
                stale, _ = pending
                stale.pop("_ranked", None)
                stale.update(
                    action="stale_result_rejected",
                    applied_at_s=None,
                    explanation="New events arrived before the result was ready; it was computed for an older state revision and is discarded.",
                )
                decisions.append(stale)
                pending = None
            if flight.invalidated:
                if flight.clock > deadline:
                    decisions.append(
                        {
                            "t_s": flight.clock,
                            "applied_at_s": flight.clock,
                            "trigger": trigger,
                            "state_revision": flight.revision,
                            "phase": flight.phase,
                            "estimate_std_m": _expected_std(flight),
                            "margin_m": request.k_sigma * _expected_std(flight),
                            "action": "no_time_to_replan",
                            "evaluations": 0,
                            "budget": request.max_evaluations,
                            "solver_wall_ms": 0.0,
                            "considered": [],
                            "chosen": None,
                            "validation": [],
                            "explanation": f"The event came after the {deadline:.0f} s decision deadline; the current plan keeps flying.",
                        }
                    )
                else:
                    decision = decide(trigger)
                    if decision["action"] in ("continue", "retarget") and (
                        request.solver_latency_s > 0
                    ):
                        pending = (decision, flight.clock + request.solver_latency_s)
                    else:
                        finalize(decision)
        if pending is not None and flight.clock >= pending[1] - 1e-9:
            finalize(pending[0])
            pending = None
        phase = flight.phase
        flight.step()
        if not flight.done and flight.phase != phase:
            count_backups("high_gate" if flight.phase == "approach" else "low_gate")
        for decision in decisions:
            if (
                decision.get("_counted") is None
                and decision["action"] != "stale_result_rejected"
            ):
                decision["_counted"] = True
                considered = [c for c in decision["considered"] if c["id"] != "CURRENT"]
                backups.append(
                    {
                        "t_s": decision["t_s"],
                        "label": "decision",
                        "reachable": sum(c["reachable"] for c in considered),
                        "evaluated": len(considered),
                    }
                )
    replanned = flight.finish()
    for decision in decisions:
        decision.pop("_counted", None)
        decision.pop("_ranked", None)
    backups.sort(key=lambda b: (b["t_s"], b["label"]))
    output = {
        "settings": request.model_dump(mode="json"),
        "nominal": {"feasible": nominal["feasible"], "pdi_range_m": pdi},
        "decision_deadline_s": deadline,
        "convergence": convergence_check(profile, site_radius, pdi, speed),
        "original": _outcome(original, request, profile, primary_score, False),
        "replanned": _outcome(
            replanned, request, profile, current["safety_score"], aborted
        ),
        "decisions": decisions,
        "backups_over_time": backups,
        "candidates": candidates,
        "timeline": _downsample(replanned["rows"]),
        "original_timeline": _downsample(original["rows"]),
        "obstacles": replanned["obstacles"],
        "log": replanned["log"],
        "log_hash": replanned["log_hash"],
        "assumptions": list(ASSUMPTIONS),
    }
    identity = output | {
        "decisions": [
            {k: v for k, v in d.items() if k != "solver_wall_ms"} for d in decisions
        ]
    }
    output["result_id"] = canonical_hash(identity)
    return output


def register_self_healing_routes(app, data_dir: Path) -> None:
    """POST /descents/self-healing: 409 without cached terrain, 422 otherwise."""

    @app.post("/descents/self-healing")
    def self_healing(body: HealingRequest):
        try:
            height = terrain_height_m(
                data_dir, body.latitude_deg, body.longitude_deg_east
            )
            terrain = load_local_terrain(
                data_dir,
                body.latitude_deg,
                body.longitude_deg_east,
                body.search_radius_m + body.safety.hazard_search_m + 3 * DEM_PIXEL_M,
            )
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (DescentError, AlternativesError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return run_self_healing(body, height, terrain)
        except HealingError as exc:
            raise HTTPException(422, str(exc)) from exc
