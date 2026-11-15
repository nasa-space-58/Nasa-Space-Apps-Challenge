"""Mission run: physics-gated mission outcome with injectable fault scenarios.

A mission run replays one landing attempt through ordered gates, each computed
from existing physics rather than from a random failure rate:

1. descent: the R4 planar powered descent (ZEM/ZEV guidance, TN D-6846 gates)
   flown again with time-activated vehicle faults (thrust loss, propellant leak,
   specific-impulse loss). Ground contact is detected on the site sphere and
   judged against touchdown speed limits.
2. touchdown terrain: the R3 Horn slope of the cached LOLA 80 m DEM at the
   actual touchdown point, shifted by guidance miss and navigation bias along an
   explicit approach azimuth (the planar model has no MOON_ME heading).
3. surface power: an ideal battery propagated exactly over the analysis' sunlit,
   partial and dark intervals with constant loads and surface faults.
4. communication: Earth-centre visibility from the analysis, minus fault
   outages, against a first-contact deadline and a maximum contact gap.

Unknown sunlight or Earth intervals are bracketed by pessimistic and optimistic
propagations, so an outcome that depends on them is reported as unknown, never
guessed. The run is a deterministic educational simulation: it is not landing
safety certification, a battery or thermal model, a DSN booking or a link budget.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.descent import VehicleProfile
from app.terrain_safety import SafetySettings

SCENARIOS_FILE = Path(__file__).resolve().parents[2] / "shared/mission_faults.json"

# Apollo Experience Report - Lunar Module Landing Gear Subsystem (NASA TN D-6850,
# 1972): the gear was designed for touchdown up to 10 ft/s vertical and 4 ft/s
# horizontal velocity. Used as configurable structural limits, not certification.
DEFAULT_CRASH_VERTICAL_MPS = 3.048
DEFAULT_CRASH_HORIZONTAL_MPS = 1.2192
# Soft-landing goal, matching the R4 touchdown tolerances.
DEFAULT_SOFT_VERTICAL_MPS = 1.0
DEFAULT_SOFT_HORIZONTAL_MPS = 0.5
# End-of-schedule altitudes at or below this count as contact (TN D-6846: the
# LM probes signal contact about 5.6 ft above the surface; 1 m is conservative).
CONTACT_ALTITUDE_M = 1.0
# Bounded post-schedule descent when the vehicle is still airborne.
EXTENSION_LIMIT_S = 600.0
EXTENSION_DESCENT_RATE_MPS = 0.9144  # TN D-6846 touchdown rate, 3 ft/s
EXTENSION_GAIN_PER_S = 0.5
# Touchdowns farther than this from the target are reported as a warning.
TARGET_MISS_WARNING_M = 50.0
MAX_DESCENT_SAMPLES = 400
MAX_FAULTS = 12

ASSUMPTIONS = (
    "Descent: R4 planar point-mass model and ZEM/ZEV guidance; faults act only from their activation time and are known to no guidance logic.",
    "A propellant leak removes mass without producing thrust; the leak's momentum is neglected.",
    "Ground contact is evaluated on the site sphere; terrain along the descent path is not checked.",
    "Touchdown point: along-track guidance miss along the explicit approach azimuth plus navigation bias, on the 1737.4 km sphere.",
    "Tip-over: Horn slope of the 80 m LOLA pixel containing the touchdown point against the slope limit; lander-scale boulders are not resolved.",
    "Power: ideal battery (no efficiency, temperature or ageing), full array output in full sun, a fixed fraction in partial sun, none in darkness.",
    "Communication: geometric Earth-centre visibility only; no DSN schedule, antenna pointing or link budget.",
    "Unknown Sun/Earth intervals are bracketed by pessimistic and optimistic runs; a result that depends on them is unknown.",
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# --- Faults ---------------------------------------------------------------


class EngineThrustLoss(Contract):
    """From activation, thrust is capped at a fraction of maximum thrust."""

    kind: Literal["engine_thrust_loss"] = "engine_thrust_loss"
    start_after_pdi_s: float = Field(ge=0, le=1200)
    thrust_fraction: float = Field(gt=0, lt=1)


class PropellantLeak(Contract):
    """Propellant is lost at a constant rate during the leak window."""

    kind: Literal["propellant_leak"] = "propellant_leak"
    start_after_pdi_s: float = Field(ge=0, le=1200)
    leak_rate_kg_s: float = Field(gt=0, le=50)
    duration_s: float = Field(gt=0, le=1200)


class IspDegradation(Contract):
    """From activation, specific impulse is reduced to a fraction of nominal."""

    kind: Literal["isp_degradation"] = "isp_degradation"
    start_after_pdi_s: float = Field(ge=0, le=1200)
    isp_fraction: float = Field(gt=0.3, lt=1)


class NavigationBias(Contract):
    """The navigation estimate is offset; guidance lands that far from the target."""

    kind: Literal["navigation_bias"] = "navigation_bias"
    offset_m: float = Field(gt=0, le=5000)
    bearing_deg: float = Field(ge=0, lt=360)


class LandingDelay(Contract):
    """Wave-off: the landing happens later inside the analysis interval."""

    kind: Literal["landing_delay"] = "landing_delay"
    delay_h: float = Field(gt=0, le=720)


class BatteryCapacityLoss(Contract):
    """From activation, a fraction of battery capacity (and its charge) is lost."""

    kind: Literal["battery_capacity_loss"] = "battery_capacity_loss"
    start_after_landing_h: float = Field(ge=0, le=720)
    capacity_fraction_lost: float = Field(gt=0, lt=1)


class PowerLoadIncrease(Contract):
    """An additional constant load, e.g. a stuck-on heater, for a window."""

    kind: Literal["power_load_increase"] = "power_load_increase"
    start_after_landing_h: float = Field(ge=0, le=720)
    duration_h: float = Field(gt=0, le=720)
    extra_load_w: float = Field(gt=0, le=5000)


class SolarArrayDegradation(Contract):
    """From activation, array output is reduced, e.g. by dust deposition."""

    kind: Literal["solar_array_degradation"] = "solar_array_degradation"
    start_after_landing_h: float = Field(ge=0, le=720)
    output_fraction: float = Field(ge=0, lt=1)


class CommunicationOutage(Contract):
    """No Earth contact is possible during the outage window."""

    kind: Literal["communication_outage"] = "communication_outage"
    start_after_landing_h: float = Field(ge=0, le=720)
    duration_h: float = Field(gt=0, le=720)


Fault = Annotated[
    EngineThrustLoss
    | PropellantLeak
    | IspDegradation
    | NavigationBias
    | LandingDelay
    | BatteryCapacityLoss
    | PowerLoadIncrease
    | SolarArrayDegradation
    | CommunicationOutage,
    Field(discriminator="kind"),
]
DESCENT_FAULTS = ("engine_thrust_loss", "propellant_leak", "isp_degradation")


class FaultScenario(Contract):
    """A curated, named fault combination from shared/mission_faults.json."""

    scenario_id: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9-]+$")
    summary: str = Field(min_length=1, max_length=300)
    faults: tuple[Fault, ...] = Field(max_length=MAX_FAULTS)


# --- Settings -------------------------------------------------------------


class TouchdownLimits(Contract):
    """Soft-landing goal and structural crash limits (m/s)."""

    soft_vertical_mps: float = Field(default=DEFAULT_SOFT_VERTICAL_MPS, gt=0, le=10)
    soft_horizontal_mps: float = Field(default=DEFAULT_SOFT_HORIZONTAL_MPS, gt=0, le=10)
    crash_vertical_mps: float = Field(default=DEFAULT_CRASH_VERTICAL_MPS, gt=0, le=20)
    crash_horizontal_mps: float = Field(
        default=DEFAULT_CRASH_HORIZONTAL_MPS, gt=0, le=20
    )


class PowerSystem(Contract):
    """Illustrative lander power system (not a sourced NASA vehicle)."""

    battery_capacity_wh: float = Field(default=4000.0, gt=0, le=200000)
    initial_state_of_charge: float = Field(default=0.9, gt=0, le=1)
    solar_array_power_w: float = Field(default=900.0, ge=0, le=50000)
    partial_sun_output_fraction: float = Field(default=0.5, ge=0, le=1)
    base_load_w: float = Field(default=350.0, gt=0, le=20000)


class CommunicationRules(Contract):
    """Touchdown confirmation deadline and longest tolerated silence (hours)."""

    first_contact_deadline_h: float = Field(default=1.0, gt=0, le=240)
    max_contact_gap_h: float = Field(default=72.0, gt=0, le=720)


class MissionRunRequest(Contract):
    """Vehicle, optional curated scenario plus custom faults, and gate settings."""

    profile_id: str = "apollo-lm-11"
    scenario_id: str | None = Field(default=None, max_length=60)
    faults: tuple[Fault, ...] = Field(default=(), max_length=MAX_FAULTS)
    approach_azimuth_deg: float = Field(default=0.0, ge=0, lt=360)
    touchdown: TouchdownLimits = Field(default_factory=TouchdownLimits)
    terrain: SafetySettings = Field(default_factory=SafetySettings)
    power: PowerSystem = Field(default_factory=PowerSystem)
    communication: CommunicationRules = Field(default_factory=CommunicationRules)


class MissionRunError(ValueError):
    """Invalid run inputs, e.g. an unknown scenario or a delay past the window."""


# --- Functions ------------------------------------------------------------


def load_scenarios(path: Path = SCENARIOS_FILE) -> dict[str, FaultScenario]:
    """Curated scenarios by ID; ValueError on duplicates or invalid entries."""
    raise NotImplementedError


def effective_faults(
    request: MissionRunRequest, scenarios: dict[str, FaultScenario]
) -> tuple:
    """Scenario faults followed by custom faults; MissionRunError on unknown ID,
    more than MAX_FAULTS in total or more than one landing delay."""
    raise NotImplementedError


def destination_point(
    latitude_deg: float,
    longitude_deg_east: float,
    distance_m: float,
    bearing_deg: float,
    radius_m: float,
) -> tuple[float, float]:
    """Great-circle destination (MOON_ME degrees, longitude in [0, 360))."""
    raise NotImplementedError


def touchdown_offset(
    downrange_m: float, approach_azimuth_deg: float, faults: tuple
) -> tuple[float, float]:
    """(distance m, bearing deg from north) of the touchdown from the target.

    Along-track miss (positive = overshoot) lies along the approach azimuth;
    navigation biases add as local east/north vectors.
    """
    raise NotImplementedError


def fly_faulted_descent(
    profile: VehicleProfile,
    site_radius_m: float,
    pdi_range_m: float,
    periapsis_speed_mps: float,
    faults: tuple,
    dt_s: float = 0.1,
) -> dict:
    """Fly the R4 gate schedule from PDI with time-activated vehicle faults.

    Without faults this matches simulate_powered_descent step for step. The run
    stops at ground contact (interpolated inside the step), extends a bounded
    controlled descent when still airborne after the schedule, and returns
    samples, events, contact state, propellant use and exhaustion.
    """
    raise NotImplementedError


def power_trace(
    sun_intervals: list[dict],
    start_utc: datetime,
    end_utc: datetime,
    power: PowerSystem,
    faults: tuple,
    unknown_generation: Literal["none", "full"],
) -> dict:
    """Exact piecewise-linear battery energy (Wh) from landing to end_utc.

    Returns the trace, minimum energy and the depletion time (None if never).
    """
    raise NotImplementedError


def contact_check(
    earth_intervals: list[dict],
    start_utc: datetime,
    end_utc: datetime,
    rules: CommunicationRules,
    faults: tuple,
    unknown_visible: bool,
) -> dict:
    """Contact windows after outages, first contact, longest gap and the time
    the first rule is violated (None if never)."""
    raise NotImplementedError


def run_mission(
    request: MissionRunRequest,
    analysis: dict,
    terrain_height_m: float,
    terrain_at: Callable[[float, float], dict],
    scenarios: dict[str, FaultScenario] | None = None,
) -> dict:
    """Evaluate all gates in order and report the first failure with its cause.

    `analysis` is a completed visibility analysis (request, sun, earth);
    `terrain_at(lat, lon)` returns R3 terrain safety for a touchdown point and
    may raise TerrainSafetyError when the point is outside coverage.
    """
    raise NotImplementedError


def register_mission_run_routes(app, analyses, data_dir: Path) -> None:
    """GET /mission-faults and POST /analyses/{analysis_id}/mission-run."""
    raise NotImplementedError


def _document(path: Path) -> dict:
    return json.loads(Path(path).read_bytes().decode("utf-8"))
