"""Causal offline mission resilience replay; classical decisions only."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from shared.hybrid import Contract, Identifier, Source
from .terminal_descent import TerminalRequest, TerminalState

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
        raise NotImplementedError


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
        raise NotImplementedError


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


def plan_mission(config: MissionConfig, view: PlannerView) -> dict:
    """Enumerate explicit classical choices with hard constraints and reasons."""
    raise NotImplementedError


def replay_mission(scenario: MissionScenario, *, cancelled=None) -> dict:
    """Advance truth/observations/time separately and revalidate delayed plans."""
    raise NotImplementedError


def resilience_examples() -> dict[str, dict]:
    raise NotImplementedError


class ReplayCancelled(RuntimeError):
    """Offline replay stopped; not a safe vehicle stop command."""
