"""Causal offline mission resilience replay; classical decisions only."""
from typing import Annotated, Literal
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import math

from pydantic import Field, model_validator

from shared.hybrid import Contract, Identifier, Source
from .terminal_descent import TerminalRequest, TerminalState, propagate_terminal
from .self_healing import RecoveryTarget, _screen

Phase = Literal["launch", "cruise", "descent", "surface"]
Scalar = Annotated[float, Field(strict=True, ge=0, le=100000)]
Seconds = Annotated[int, Field(strict=True, ge=0, le=600)]


class Route(Contract):
    route_id: Identifier
    duration_s: Annotated[float, Field(strict=True, ge=1, le=120)]
    fuel_kg: Scalar
    min_thrust_n: Scalar
    requires_link: bool = False


class SurfaceTask(Contract):
    task_id: Identifier
    duration_s: Annotated[int, Field(strict=True, ge=1, le=120)]
    power_w: Scalar
    priority: Annotated[int, Field(strict=True, ge=1, le=100)]
    hardware: Identifier
    requires_link: bool = False
    deadline_met_s: Seconds = 240


class MissionConfig(Contract):
    scenario_id: Identifier
    source: Source
    initial_phase: Phase = "launch"
    terminal: TerminalRequest
    targets: Annotated[dict[Identifier, float], Field(min_length=1, max_length=5)]
    routes: Annotated[tuple[Route, ...], Field(min_length=1, max_length=4)]
    tasks: Annotated[tuple[SurfaceTask, ...], Field(min_length=1, max_length=8)]
    launch_windows_s: tuple[tuple[Seconds, Seconds], ...] = ((0, 5), (10, 20))
    launch_duration_s: Annotated[int, Field(strict=True, ge=1, le=10)] = 2
    launch_fuel_kg: Scalar = 2.0
    horizon_s: Annotated[int, Field(strict=True, ge=10, le=300)] = 100
    arrival_deadline_met_s: Seconds = 80
    battery_capacity_wh: Annotated[float, Field(strict=True, gt=0, le=100000)] = 20.0
    initial_energy_wh: Scalar = 15.0
    reserve_energy_wh: Scalar = 0.1
    idle_power_w: Scalar = 10.0
    solar_power_w: Scalar = 12.0
    initial_temperature_c: Annotated[float, Field(strict=True, ge=-100, le=100)] = 20.0
    temperature_limits_c: tuple[float, float] = (-20.0, 50.0)
    thermal_drift_cps: Annotated[float, Field(strict=True, ge=-2, le=2)] = -0.05
    thermal_power_w: Scalar = 30.0
    thermal_rate_cps: Annotated[float, Field(strict=True, gt=0, le=2)] = 0.5
    planning_delay_s: Annotated[int, Field(strict=True, ge=0, le=10)] = 1
    plan_ttl_s: Annotated[int, Field(strict=True, ge=1, le=30)] = 6
    navigation_bound_m: Annotated[float, Field(strict=True, ge=0, le=50)] = 0.0
    require_link_for_retarget: bool = False

    @model_validator(mode="after")
    def bounded_inputs(self):
        if not 0 <= self.reserve_energy_wh <= self.initial_energy_wh <= self.battery_capacity_wh:
            raise ValueError("Energy must satisfy reserve <= initial <= capacity")
        low, high = self.temperature_limits_c
        if not -100 <= low < self.initial_temperature_c < high <= 100:
            raise ValueError("Initial temperature must lie strictly within bounds")
        if not self.launch_windows_s or any(a >= b for a, b in self.launch_windows_s):
            raise ValueError("Launch windows must have positive duration")
        for items, key in ((self.routes, "route_id"), (self.tasks, "task_id")):
            if len({getattr(x, key) for x in items}) != len(items):
                raise ValueError("Duplicate route or task identity")
        if any(not math.isfinite(x) or abs(x) > 50000 for x in self.targets.values()):
            raise ValueError("Target outside supported corridor coordinates")
        if self.terminal.target_downrange_m not in self.targets.values():
            raise ValueError("Targets must include the predeclared nominal target")
        return self


class MissionEvent(Contract):
    event_id: Identifier
    phase: Phase
    at_s: Seconds
    notice_delay_s: Seconds = 0
    kind: Literal[
        "weather", "thrust", "fuel_loss", "link", "hazard", "sensor_bias",
        "solar", "thermal", "hardware", "energy_loss",
    ]
    value: Annotated[float, Field(strict=True, ge=-100000, le=100000)]
    subject: Identifier = "vehicle"


class MissionScenario(Contract):
    config: MissionConfig
    events: Annotated[tuple[MissionEvent, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def valid_events(self):
        if len({x.event_id for x in self.events}) != len(self.events):
            raise ValueError("Duplicate event identity")
        hardware = {t.hardware for t in self.config.tasks} | {"heater", "cooler"}
        for event in self.events:
            if event.kind in ("link", "weather", "hazard", "hardware") and event.value not in (0, 1):
                raise ValueError("Boolean events require 0 or 1")
            if event.kind in ("fuel_loss", "energy_loss", "solar", "thrust") and event.value < 0:
                raise ValueError("Resource and thrust events must be nonnegative")
            if event.kind == "thrust" and event.value > self.config.terminal.vehicle.max_thrust_n:
                raise ValueError("Thrust exceeds declared vehicle capacity")
            if event.kind == "hazard" and event.subject not in self.config.targets:
                raise ValueError("Unknown landing target")
            if event.kind == "hardware" and event.subject not in hardware:
                raise ValueError("Unknown equipment")
            if event.kind == "sensor_bias" and abs(event.value) > 100:
                raise ValueError("Navigation bias exceeds supported 100 m range")
            if event.kind == "thermal" and abs(event.value) > 2:
                raise ValueError("Thermal rate exceeds illustrative model bounds")
        return self


class PlannerView(Contract):
    """Only observations and declared inputs, never future events or latent truth."""
    phase: Phase
    met_s: Scalar
    phase_elapsed_s: Scalar
    revision: Annotated[int, Field(strict=True, ge=0)]
    fuel_kg: Scalar
    energy_wh: Scalar
    temperature_c: float
    available_thrust_n: Scalar
    solar_power_w: Scalar
    thermal_drift_cps: float
    link: bool
    weather_ok: bool
    hardware: tuple[Identifier, ...]
    blocked_targets: tuple[Identifier, ...] = ()
    navigation_bound_m: Scalar = 0.0
    sensor_conflict: bool = False
    landing_state: TerminalState | None = None
    cruise_progress: Annotated[float, Field(strict=True, ge=0, le=1)] = 0.0
    task_progress_s: dict[str, float] = {}
    current_target: str | None = None


def fingerprint(scenario):
    return hashlib.sha256(json.dumps(scenario.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _candidate(identity, kind, duration_s=1.0, **kw):
    return dict(id=identity, kind=kind, duration_s=duration_s, reasons=[], feasible=False,
                fuel_kg=0.0, power_w=0.0, priority=0, **kw)


def _energy_reason(c, v, power_w, duration_s):
    projected = v.energy_wh + (v.solar_power_w - c.idle_power_w - power_w) * duration_s / 3600
    return projected < c.reserve_energy_wh - 1e-10


def plan_mission(config: MissionConfig, view: PlannerView) -> dict:
    """Explicit finite classical candidates; no future events or simulator truth."""
    c = MissionConfig.model_validate(config.model_dump())
    v = PlannerView.model_validate(view.model_dump())
    rows = []
    remaining = c.horizon_s - v.met_s
    low, high = c.temperature_limits_c
    if v.phase == "launch":
        launch = _candidate("launch", "launch", float(c.launch_duration_s))
        launch["fuel_kg"] = c.launch_fuel_kg
        if not v.weather_ok:
            launch["reasons"].append("weather_not_clear")
        if not any(a <= v.met_s and v.met_s + c.launch_duration_s <= b for a, b in c.launch_windows_s):
            launch["reasons"].append("outside_launch_window")
        if v.met_s + c.launch_duration_s + min(r.duration_s for r in c.routes) > c.arrival_deadline_met_s:
            launch["reasons"].append("arrival_deadline")
        rows.append(launch)
        hold = _candidate("hold", "hold")
        if not any(v.met_s + 1 + c.launch_duration_s <= b for _, b in c.launch_windows_s):
            hold["reasons"].append("no_future_launch_window")
        rows.append(hold)
    elif v.phase == "cruise":
        fraction = 1 - v.cruise_progress
        for route in c.routes:
            duration = route.duration_s * fraction
            row = _candidate(route.route_id, "route", max(0.01, duration))
            row.update(fuel_kg=route.fuel_kg * fraction, requires_link=route.requires_link,
                       min_thrust_n=route.min_thrust_n, full_duration_s=route.duration_s)
            if v.available_thrust_n < route.min_thrust_n:
                row["reasons"].append("insufficient_thrust")
            if route.requires_link and not v.link:
                row["reasons"].append("link_required")
            if v.met_s + duration > c.arrival_deadline_met_s:
                row["reasons"].append("arrival_deadline")
            rows.append(row)
    elif v.phase == "descent":
        duration = c.terminal.duration_s - v.phase_elapsed_s
        for name, downrange in c.targets.items():
            row = _candidate(name, "landing", max(0.0, duration))
            row["downrange_m"] = downrange
            reasons = row["reasons"]
            if name in v.blocked_targets:
                reasons.append("landing_hazard")
            if v.sensor_conflict:
                reasons.append("navigation_sensor_conflict")
            if c.require_link_for_retarget and not v.link and name != v.current_target:
                reasons.append("link_required")
            if v.available_thrust_n <= 0 or v.fuel_kg <= c.terminal.reserve_kg:
                reasons.append("insufficient_propulsion")
            if duration < 5 or v.landing_state is None:
                reasons.append("outside_terminal_model_horizon")
            if not reasons:
                state = v.landing_state.model_copy(update={"propellant_kg": v.fuel_kg})
                target = RecoveryTarget(target_id=name, downrange_m=downrange,
                    touchdown_deadline_utc=state.epoch_utc + timedelta(seconds=duration))
                bound = v.navigation_bound_m
                probes = [dict(position_offset_m=offset, velocity_offset_mps=[0.0, 0.0],
                               thrust_fraction=1.0, fuel_shortfall_kg=0.0)
                          for offset in ([0.0, 0.0], [bound, 0.0], [-bound, 0.0], [0.0, bound], [0.0, -bound])]
                hazards = [(c.targets[x] - c.terminal.position_tolerance_m,
                            c.targets[x] + c.terminal.position_tolerance_m) for x in v.blocked_targets]
                physical = _screen(c.terminal, state, target, v.available_thrust_n, hazards, probes, None)
                row["physics"] = physical
                reasons.extend(physical["reasons"])
                if "fine" in physical:
                    row["fuel_kg"] = physical["fine"]["propellant_used_kg"]
            rows.append(row)
    else:
        for task in c.tasks:
            left = task.duration_s - v.task_progress_s.get(task.task_id, 0.0)
            row = _candidate(task.task_id, "task", max(0.0, left))
            row.update(power_w=task.power_w, priority=task.priority, hardware=task.hardware, requires_link=task.requires_link)
            if left <= 1e-9:
                row["reasons"].append("already_completed")
            if task.hardware not in v.hardware:
                row["reasons"].append("hardware_unavailable")
            if task.requires_link and not v.link:
                row["reasons"].append("link_required")
            if v.met_s + left > min(task.deadline_met_s, c.horizon_s):
                row["reasons"].append("task_deadline")
            projected = v.temperature_c + v.thermal_drift_cps * left
            if not low + 1 <= projected <= high - 1:
                row["reasons"].append("thermal_margin")
            rows.append(row)
        for name, sign, equipment in (("heat", 1, "heater"), ("cool", -1, "cooler")):
            row = _candidate(name, "thermal")
            row.update(power_w=c.thermal_power_w, thermal_rate_cps=sign * c.thermal_rate_cps)
            if equipment not in v.hardware:
                row["reasons"].append("hardware_unavailable")
            if (sign == 1 and v.temperature_c >= low + 3) or (sign == -1 and v.temperature_c <= high - 3):
                row["reasons"].append("thermal_control_not_needed")
            rows.append(row)
        rows.append(_candidate("standby", "standby"))
    for row in rows:
        reasons = row["reasons"]
        dt = row["duration_s"]
        if dt <= 0 or dt > remaining:
            reasons.append("mission_horizon")
        if v.fuel_kg - row["fuel_kg"] < c.terminal.reserve_kg - 1e-9 and row["kind"] != "landing":
            reasons.append("fuel_reserve")
        if _energy_reason(c, v, row["power_w"], dt):
            reasons.append("energy_reserve")
        thermal = v.temperature_c + (v.thermal_drift_cps + row.get("thermal_rate_cps", 0.0)) * dt
        if not low <= thermal <= high:
            reasons.append("temperature_limit")
        row["feasible"] = not reasons
    def rank(row):
        if row["kind"] == "landing":
            return (abs(row["downrange_m"] - c.terminal.target_downrange_m), row["fuel_kg"], row["id"])
        return ({"launch": 0, "route": 0, "thermal": 0, "task": 1, "hold": 2, "standby": 3}.get(row["kind"], 4),
                -row["priority"], row["duration_s"], row["id"])
    chosen = min((r for r in rows if r["feasible"]), key=rank, default=None)
    return dict(selected=deepcopy(chosen), candidates=rows, based_on_met_s=v.met_s,
                based_on_revision=v.revision, phase=v.phase,
                conditions=dict(fuel_reserve_kg=c.terminal.reserve_kg, energy_reserve_wh=c.reserve_energy_wh,
                                temperature_limits_c=c.temperature_limits_c,
                                valid_until_met_s=min(c.horizon_s, v.met_s + c.plan_ttl_s),
                                revalidate_on="observation, phase, resource or deadline change"),
                solver="classical_finite_enumeration")


def _initial(c):
    return dict(fuel_kg=c.terminal.initial_state.propellant_kg, energy_wh=c.initial_energy_wh,
                temperature_c=c.initial_temperature_c, available_thrust_n=c.terminal.vehicle.max_thrust_n,
                solar_power_w=c.solar_power_w, thermal_drift_cps=c.thermal_drift_cps,
                link=True, weather_ok=True, hardware=sorted({t.hardware for t in c.tasks} | {"heater", "cooler"}),
                blocked_targets=[], sensor_bias_m=0.0, landing_state=None,
                cruise_progress=0.0, task_progress_s={t.task_id: 0.0 for t in c.tasks})


def _effect(state, e):
    key = {"weather": "weather_ok", "link": "link", "thrust": "available_thrust_n",
           "solar": "solar_power_w", "thermal": "thermal_drift_cps", "sensor_bias": "sensor_bias_m"}.get(e.kind)
    if key:
        state[key] = bool(e.value) if e.kind in ("weather", "link") else e.value
    elif e.kind in ("fuel_loss", "energy_loss"):
        key = "fuel_kg" if e.kind == "fuel_loss" else "energy_wh"
        state[key] = max(0.0, state[key] - e.value)
    else:
        key = "blocked_targets" if e.kind == "hazard" else "hardware"
        enabled = bool(e.value) if e.kind == "hazard" else not bool(e.value)
        values = set(state[key])
        if enabled:
            values.add(e.subject) if key == "blocked_targets" else values.discard(e.subject)
        else:
            values.discard(e.subject) if key == "blocked_targets" else values.add(e.subject)
        state[key] = sorted(values)


def replay_mission(scenario: MissionScenario, *, cancelled=None) -> dict:
    """Offline one-second causal clock, independent of workstation runtime."""
    s = MissionScenario.model_validate(scenario.model_dump())
    c = s.config
    truth, known = _initial(c), _initial(c)
    phase, entered, met, revision = c.initial_phase, 0.0, 0.0, 0
    fired, notices, observations, truth_events = set(), [], [], []
    frames, decisions, transitions = [], [], []
    active, pending, trajectory = None, None, None
    target_name = next(k for k, x in c.targets.items() if x == c.terminal.target_downrange_m)
    phase_progress, status, reasons = 0.0, "partial", []
    needs_plan = True

    def stop_check():
        if cancelled and cancelled():
            raise ReplayCancelled("Offline replay cancelled; no vehicle command issued")

    def enter(next_phase):
        nonlocal phase, entered, revision, active, pending, trajectory, phase_progress, needs_plan
        phase, entered = next_phase, met
        revision += 1
        active, pending, trajectory, phase_progress, needs_plan = None, None, None, 0.0, True
        if phase == "descent":
            raw = c.terminal.initial_state.model_dump(mode="json")
            raw.update(epoch_utc=(c.terminal.initial_state.epoch_utc + timedelta(seconds=met)).isoformat(),
                       propellant_kg=truth["fuel_kg"])
            truth["landing_state"] = TerminalState.model_validate(raw).model_dump(mode="json")
        transitions.append(dict(phase=phase, met_s=met, fuel_kg=truth["fuel_kg"], energy_wh=truth["energy_wh"]))

    def observe():
        nonlocal revision, needs_plan
        # Declared perfect resource meters, two synthetic navigation channels.
        # Engine/equipment/weather changes reach the estimator only by notices.
        for key in ("fuel_kg", "energy_wh", "temperature_c", "cruise_progress", "task_progress_s"):
            known[key] = deepcopy(truth[key])
        landing = deepcopy(truth["landing_state"])
        bias = truth["sensor_bias_m"]
        sensor_conflict = abs(bias) > 2 * c.terminal.position_tolerance_m
        if landing:
            landing["position_m"][0] += bias / 2
            landing["propellant_kg"] = known["fuel_kg"]
        if sensor_conflict != known.get("sensor_conflict", False):
            revision += 1
            needs_plan = True
        known["sensor_conflict"] = sensor_conflict
        return PlannerView(phase=phase, met_s=met, phase_elapsed_s=met-entered, revision=revision,
             **{k: known[k] for k in ("fuel_kg", "energy_wh", "temperature_c", "available_thrust_n", "solar_power_w",
                "thermal_drift_cps", "link", "weather_ok", "hardware", "blocked_targets", "cruise_progress", "task_progress_s")},
             navigation_bound_m=c.navigation_bound_m + abs(bias)/2,
             sensor_conflict=sensor_conflict, landing_state=landing, current_target=target_name)

    def record(proposal, verdict, current, fresh=None):
        row = deepcopy(proposal)
        row.update(met_s=met, revision=current.revision, status=verdict)
        if fresh is not None:
            row.update(selected=fresh["selected"], candidates=fresh["candidates"], conditions=fresh["conditions"])
        decisions.append(row)

    enter(phase)
    while met <= c.horizon_s:
        stop_check()
        # Environment alone sees event schedule. Planner gets only view below.
        for e in s.events:
            if e.event_id not in fired and e.phase == phase and met-entered >= e.at_s:
                fired.add(e.event_id)
                _effect(truth, e)
                truth_events.append(dict(event_id=e.event_id, met_s=met, kind=e.kind, subject=e.subject, value=e.value))
                notices.append((met+e.notice_delay_s, e))
                if e.kind in ("fuel_loss", "thrust"):
                    trajectory = None
        for at, e in list(notices):
            if at <= met:
                # Resource losses are already accounted by meters; never apply twice.
                if e.kind not in ("fuel_loss", "energy_loss", "sensor_bias"):
                    _effect(known, e)
                observations.append(dict(event_id=e.event_id, met_s=met, kind=e.kind, subject=e.subject, value=e.value))
                notices.remove((at, e))
                revision += 1
                needs_plan = True
        current = observe()
        frames.append(dict(met_s=met, phase=phase, estimate=current.model_dump(mode="json"), truth=deepcopy(truth),
                           executing=active["id"] if active else None,
                           planning_pending=pending is not None,
                           navigation_channels_m=[truth["landing_state"]["position_m"][0], truth["landing_state"]["position_m"][0]+truth["sensor_bias_m"]] if truth["landing_state"] else []))
        low, high = c.temperature_limits_c
        if truth["energy_wh"] <= 0 or truth["fuel_kg"] <= 0 and phase != "surface":
            status, reasons = "no_feasible_continuation", ["resource_depleted"]
            break
        if not low <= truth["temperature_c"] <= high:
            status, reasons = "no_feasible_continuation", ["temperature_limit"]
            break
        if phase == "surface" and all(truth["task_progress_s"][t.task_id] >= t.duration_s for t in c.tasks):
            status = "completed"
            break
        if met >= c.horizon_s:
            reasons = ["mission_horizon_reached"]
            break
        # A prepared decision must survive time, observed changes and full recheck.
        if pending is not None and met >= pending["ready_s"]:
            proposal = pending["proposal"]
            if met - proposal["based_on_met_s"] >= c.plan_ttl_s:
                record(proposal, "expired", current)
                needs_plan = True
            elif proposal["based_on_revision"] != revision or proposal["phase"] != phase:
                record(proposal, "stale", current)
                needs_plan = True
            else:
                fresh = plan_mission(c, current)
                proposed_id = proposal["selected"]["id"] if proposal["selected"] else None
                selected = next((r for r in fresh["candidates"] if r["id"] == proposed_id and r["feasible"]), None)
                fresh["selected"] = selected
                if selected is None:
                    record(proposal, "rejected_current_state", current, fresh)
                    active = None
                    needs_plan = True
                else:
                    record(proposal, "accepted", current, fresh)
                    active = deepcopy(selected)
                    active["accepted_s"] = met
                    if phase == "descent":
                        target_name = active["id"]
                        trajectory = None
                    needs_plan = False
            pending = None
        if active and phase == "surface" and active["kind"] == "task":
            task = next(t for t in c.tasks if t.task_id == active["id"])
            if truth["task_progress_s"][task.task_id] >= task.duration_s:
                active, needs_plan = None, True
        if active and phase != "descent" and met - active["accepted_s"] >= min(c.plan_ttl_s, active["duration_s"]):
            needs_plan = True
        if (needs_plan or active is None) and pending is None:
            proposal = plan_mission(c, current)
            stop_check()
            if proposal["selected"] is None:
                record(proposal, "no_feasible_continuation", current)
                status, reasons = "no_feasible_continuation", sorted({r for row in proposal["candidates"] for r in row["reasons"]})
                break
            # Initial stage decision is an explicitly offline preflight check.
            if met == entered or c.planning_delay_s == 0:
                record(proposal, "accepted", current)
                active = deepcopy(proposal["selected"])
                active["accepted_s"] = met
                if phase == "descent":
                    target_name, trajectory = active["id"], None
                needs_plan = False
            else:
                pending = dict(ready_s=met+c.planning_delay_s, proposal=proposal)
                needs_plan = False
        # During latency retain current controls, but inhibit known unsafe task work.
        action = active or _candidate("pending", "standby")
        if phase == "surface" and action["kind"] == "task":
            task = next(t for t in c.tasks if t.task_id == action["id"])
            if task.hardware not in current.hardware or task.requires_link and not current.link:
                action = _candidate("pending", "standby")
        dt, next_phase = 1.0, None
        actual_power = action["power_w"]
        if phase == "launch" and action["kind"] == "launch":
            if current.weather_ok and truth["weather_ok"]:
                truth["fuel_kg"] -= c.launch_fuel_kg / c.launch_duration_s
                phase_progress += dt
                if phase_progress >= c.launch_duration_s:
                    next_phase = "cruise"
            else:
                needs_plan = True
        elif phase == "cruise" and action["kind"] == "route":
            route = next(r for r in c.routes if r.route_id == action["id"])
            if truth["available_thrust_n"] >= route.min_thrust_n:
                step = min(1-truth["cruise_progress"], dt/route.duration_s)
                truth["cruise_progress"] += step
                truth["fuel_kg"] -= route.fuel_kg * step
                if truth["cruise_progress"] >= 1-1e-9:
                    next_phase = "descent"
        elif phase == "descent":
            remaining = c.terminal.duration_s - (met-entered)
            if trajectory is None:
                if remaining < 5:
                    status, reasons = "no_feasible_continuation", ["outside_terminal_model_horizon"]
                    break
                raw = c.terminal.model_dump()
                st = deepcopy(truth["landing_state"])
                st["propellant_kg"] = truth["fuel_kg"]
                raw.update(initial_state=st, target_downrange_m=c.targets[target_name], duration_s=remaining)
                physical = propagate_terminal(TerminalRequest.model_validate(raw),
                    thrust_limit_n=truth["available_thrust_n"], integration_dt_s=c.terminal.dt_s/2,
                    position_bias_m=(truth["sensor_bias_m"]/2, 0.0))
                trajectory = dict(start=met, run=physical)
            run = trajectory["run"]
            samples = run["trace"]
            reached = min(met+dt-trajectory["start"], samples[-1]["t_s"])
            row = max((x for x in samples if x["t_s"] <= reached+1e-8), key=lambda x: x["t_s"])
            dt = max(0.0, trajectory["start"]+row["t_s"]-met)
            truth["landing_state"] = deepcopy(row["state"])
            truth["fuel_kg"] = row["state"]["propellant_kg"]
            if reached >= samples[-1]["t_s"]-1e-8:
                hazards = any(abs(row["downrange_m"]-c.targets[name]) <= c.terminal.position_tolerance_m for name in truth["blocked_targets"])
                if not run["feasible"] or hazards:
                    status, reasons = "no_feasible_continuation", list(run["reasons"]) + (["actual_landing_hazard"] if hazards else [])
                    # Account for the final physical interval before publishing failure.
                else:
                    next_phase = "surface"
        elif phase == "surface" and action["kind"] == "task":
            task = next(t for t in c.tasks if t.task_id == action["id"])
            if task.hardware in truth["hardware"] and (truth["link"] or not task.requires_link):
                truth["task_progress_s"][task.task_id] = min(task.duration_s, truth["task_progress_s"][task.task_id]+dt)
        thermal_rate = action.get("thermal_rate_cps", 0.0)
        if action["id"] == "heat" and "heater" not in truth["hardware"] or action["id"] == "cool" and "cooler" not in truth["hardware"]:
            thermal_rate = 0.0
        energy = truth["energy_wh"] + (truth["solar_power_w"]-c.idle_power_w-actual_power)*dt/3600
        fuel = truth["fuel_kg"]
        truth["energy_wh"] = max(0.0, min(c.battery_capacity_wh, energy))
        truth["fuel_kg"] = max(0.0, fuel)
        truth["temperature_c"] += (truth["thermal_drift_cps"]+thermal_rate)*dt
        met = round(met+dt, 8)
        if energy < c.reserve_energy_wh-1e-10 or fuel < c.terminal.reserve_kg-1e-8:
            status, reasons = "no_feasible_continuation", ["resource_reserve_breached"]
        if status == "no_feasible_continuation":
            frames.append(dict(met_s=met, phase=phase, truth=deepcopy(truth), estimate=observe().model_dump(mode="json"), executing=action["id"], planning_pending=False, navigation_channels_m=[]))
            break
        if next_phase:
            enter(next_phase)
    stop_check()
    completed = sorted(t.task_id for t in c.tasks if truth["task_progress_s"][t.task_id] >= t.duration_s)
    return dict(input_sha256=fingerprint(s), scenario_id=c.scenario_id, status=status, reasons=reasons,
        source=c.source.model_dump(mode="json"), decisions=decisions, frames=frames, transitions=transitions,
        observations=observations, truth_events=truth_events, completed_tasks=completed,
        unfinished_tasks=[dict(task_id=t.task_id, progress_s=truth["task_progress_s"][t.task_id],
            reason="hardware_unavailable" if t.hardware not in known["hardware"] else "deadline_or_replay_ended") for t in c.tasks if t.task_id not in completed],
        quantum_calls=0, limitations=LIMITATIONS)


LIMITATIONS = [
    "Offline synthetic replay; not flight control, certification or exhaustive hazard coverage.",
    "Launch and cruise use declared windows, duration and resource accounting; no atmospheric launch or orbital dynamics.",
    "Cruise enters a declared H4 terminal state; geometry is not propagated from an interplanetary trajectory.",
    "Descent uses nonrotating planar H4/H5 physics, sampled terrain and finite navigation probes; no 3D or continuous safety proof.",
    "Surface energy is constant-power accounting; temperature is a declared linear model, not calibrated spacecraft thermal physics.",
    "Resource meters are ideal; navigation uses two declared synthetic channels. Observed bounds are not calibrated probabilities.",
    "Planning latency is declared simulation time. Wall-clock job deadlines separately fence late offline results.",
    "Failure means no validated continuation among declared options, not a safe hover, abort or vehicle shutdown.",
]


def resilience_examples() -> dict[str, dict]:
    base = json.loads((Path(__file__).resolve().parents[2] / "shared/examples/recovery/combined_recovery.json").read_text())
    config = MissionConfig(scenario_id="mission-chain", source=base["uncertainty"]["source"],
        terminal=base["initial_request"], targets={"primary": 0.0, "backup": 40.0, "spare": -40.0},
        routes=(Route(route_id="direct", duration_s=10.0, fuel_kg=3.0, min_thrust_n=2000.0, requires_link=True),
                Route(route_id="autonomous", duration_s=16.0, fuel_kg=2.0, min_thrust_n=1000.0)),
        tasks=(SurfaceTask(task_id="science", duration_s=6, power_w=40.0, priority=3, hardware="camera"),
               SurfaceTask(task_id="transmit", duration_s=3, power_w=20.0, priority=2, hardware="radio", requires_link=True)))
    def e(name, phase, at, kind, value, **kw):
        return MissionEvent(event_id=name, phase=phase, at_s=at, kind=kind, value=float(value), **kw)
    specifications = {
        "Nominal four-phase mission": ("launch", ()),
        "Weather delay and cruise outage": ("launch", (e("weather", "launch", 0, "weather", 0), e("clear", "launch", 7, "weather", 1), e("lost", "cruise", 2, "link", 0), e("restored", "cruise", 8, "link", 1))),
        "Cruise thrust loss and fuel leak": ("cruise", (e("thrust", "cruise", 2, "thrust", 1500), e("leak", "cruise", 4, "fuel_loss", 5))),
        "Landing fallback then fallback failure": ("descent", (e("primary", "descent", 1, "hazard", 1, subject="primary"), e("backup", "descent", 5, "hazard", 1, subject="backup"), e("spare", "descent", 9, "hazard", 1, subject="spare"))),
        "Conflicting landing sensors": ("descent", (e("bias", "descent", 1, "sensor_bias", 30, notice_delay_s=4),)),
        "Surface power and communications recovery": ("surface", (e("lost", "surface", 0, "link", 0), e("shade", "surface", 2, "solar", 0), e("back", "surface", 10, "link", 1))),
        "Surface equipment and energy exhaustion": ("surface", (e("broken", "surface", 1, "hardware", 0, subject="camera"), e("empty", "surface", 5, "energy_loss", 100))),
    }
    return {name: MissionScenario(config=MissionConfig.model_validate(config.model_dump() | {"initial_phase": phase}), events=events).model_dump(mode="json") for name, (phase, events) in specifications.items()}


class ReplayCancelled(RuntimeError):
    """Offline replay stopped; not a safe vehicle stop command."""
