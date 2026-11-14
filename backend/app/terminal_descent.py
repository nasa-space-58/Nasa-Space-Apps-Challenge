"""H4 bounded restartable terminal descent in an explicit nonrotating 2D frame."""

import hashlib
import json
import math
from bisect import bisect_left
from datetime import datetime, timedelta
from typing import Annotated, Literal

import numpy as np
from pydantic import Field, model_validator

from .descent_dynamics import advance_burn, zem_zev_acceleration
from shared.constants import MOON_REFERENCE_RADIUS_M, MOON_GM_M3_S2
from shared.hybrid import Contract, Identifier, NonNegative, Number, Positive, Source

Vector2 = tuple[Number, Number]


class TerminalState(Contract):
    epoch_utc: datetime
    vehicle_id: Identifier
    revision: Annotated[int, Field(strict=True, ge=0)] = 0
    reference_frame: Literal["PLANAR_MOON_CENTERED_NONROTATING"] = (
        "PLANAR_MOON_CENTERED_NONROTATING"
    )
    position_m: Vector2
    velocity_mps: Vector2
    propellant_kg: NonNegative

    @model_validator(mode="after")
    def valid_radius(self):
        radius = math.hypot(*self.position_m)
        if not 1600000 <= radius <= 2000000:
            raise ValueError("Terminal state requires a lunar-neighborhood radius")
        if math.hypot(*self.velocity_mps) > 5000:
            raise ValueError("Terminal state exceeds the 5000 m/s model bound")
        return self


class TerminalVehicle(Contract):
    vehicle_id: Identifier
    dry_mass_kg: Annotated[float, Field(strict=True, gt=0, le=100000)]
    propellant_capacity_kg: Annotated[float, Field(strict=True, ge=0, le=90000)]
    max_thrust_n: Annotated[float, Field(strict=True, gt=0, le=1e6)]
    design_thrust_n: Annotated[float, Field(strict=True, gt=0, le=1e6)]
    min_throttle_fraction: Annotated[float, Field(strict=True, ge=0, lt=1)] = 0.0
    max_throttle_fraction: Annotated[float, Field(strict=True, gt=0, le=1)] = 1.0
    isp_s: Annotated[float, Field(strict=True, gt=50, le=500)]
    source: Source

    @model_validator(mode="after")
    def valid_engine(self):
        if self.min_throttle_fraction > self.max_throttle_fraction:
            raise ValueError("Minimum throttle exceeds throttleable maximum")
        if self.max_thrust_n < self.max_throttle_fraction * self.design_thrust_n:
            raise ValueError("Fixed maximum thrust is below the throttleable range")
        return self


class TerrainPoint(Contract):
    downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    height_m: Annotated[float, Field(strict=True, ge=-10000, le=10000)]


class TerrainCorridor(Contract):
    reference_radius_m: Annotated[float, Field(strict=True, ge=1700000, le=1800000)] = (
        MOON_REFERENCE_RADIUS_M
    )
    points: Annotated[tuple[TerrainPoint, ...], Field(min_length=2, max_length=4096)]
    source: Source

    @model_validator(mode="after")
    def ordered_points(self):
        if any(
            a.downrange_m >= b.downrange_m for a, b in zip(self.points, self.points[1:])
        ):
            raise ValueError("Terrain samples must be strictly increasing")
        return self


class TerminalRequest(Contract):
    initial_state: TerminalState
    vehicle: TerminalVehicle
    terrain: TerrainCorridor
    target_downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)] = 0.0
    duration_s: Annotated[float, Field(strict=True, ge=5, le=300)] = 30.0
    dt_s: Annotated[float, Field(strict=True, ge=0.02, le=1)] = 0.1
    reserve_kg: NonNegative = 0.0
    target_vertical_speed_mps: Annotated[float, Field(strict=True, ge=-5, lt=0)] = -0.5
    position_tolerance_m: Positive = 5.0
    vertical_speed_limit_mps: Positive = 2.0
    horizontal_speed_limit_mps: Positive = 1.0
    slope_limit_deg: Annotated[float, Field(strict=True, ge=0, lt=90)] = 15.0

    @model_validator(mode="after")
    def consistent_state(self):
        if self.initial_state.vehicle_id != self.vehicle.vehicle_id:
            raise ValueError("State and vehicle identity differ")
        if self.initial_state.propellant_kg > self.vehicle.propellant_capacity_kg:
            raise ValueError("State propellant exceeds tank capacity")
        return self


class LandingTarget(Contract):
    target_id: Identifier
    downrange_m: Annotated[float, Field(strict=True, ge=-50000, le=50000)]
    duration_s: Annotated[float, Field(strict=True, ge=5, le=300)]


def vehicle_from_r4(profile: dict, *, source: Source) -> TerminalVehicle:
    """Adapt the R4 SI profile without importing FastAPI or terrain I/O."""
    return TerminalVehicle(
        vehicle_id=profile["profile_id"],
        dry_mass_kg=profile["mass_at_pdi_kg"] - profile["usable_propellant_kg"],
        propellant_capacity_kg=profile["usable_propellant_kg"],
        **{
            key: profile[key]
            for key in (
                "max_thrust_n",
                "design_thrust_n",
                "min_throttle_fraction",
                "max_throttle_fraction",
                "isp_s",
            )
        },
        source=source,
    )


def terrain_at(corridor: TerrainCorridor, downrange_m: float) -> tuple[float, float]:
    """Return height (m), conservative adjacent slope (degrees); never extrapolate."""
    if not math.isfinite(downrange_m):
        raise ValueError("Terrain coverage requires finite downrange")
    points = corridor.points
    xs = [point.downrange_m for point in points]
    if not xs[0] <= downrange_m <= xs[-1]:
        raise ValueError("Terrain coverage does not include downrange")
    right = bisect_left(xs, downrange_m)
    index = max(0, min(len(points) - 2, right - 1))

    def slope(i):
        a, b = points[i : i + 2]
        return (b.height_m - a.height_m) / (b.downrange_m - a.downrange_m)

    gradient = slope(index)
    height = points[index].height_m + gradient * (downrange_m - xs[index])
    magnitude = abs(gradient)
    if right < len(points) - 1 and xs[right] == downrange_m:
        magnitude = max(magnitude, abs(slope(right)))
    return float(height), math.degrees(math.atan(magnitude))


def _frame_state(position, velocity, terrain):
    radius = float(np.linalg.norm(position))
    up = position / radius
    east = np.array([up[1], -up[0]])
    downrange = terrain.reference_radius_m * math.atan2(position[0], position[1])
    altitude = radius - terrain.reference_radius_m
    if terrain.points[0].downrange_m <= downrange <= terrain.points[-1].downrange_m:
        height, slope = terrain_at(terrain, downrange)
        clearance = altitude - height
    else:
        height, slope, clearance = None, None, None
    return dict(
        downrange_m=downrange,
        altitude_m=altitude,
        terrain_height_m=height,
        terrain_slope_deg=slope,
        clearance_m=clearance,
        vertical_speed_mps=float(velocity @ up),
        horizontal_speed_mps=float(velocity @ east),
    )


def simulate_terminal(job_request: TerminalRequest) -> dict:
    """Integrate from the supplied state to first sampled terrain contact or timeout."""
    # Revalidate at the execution boundary: model_copy does not validate updates.
    job = TerminalRequest.model_validate(job_request.model_dump())
    return _simulate(job, job.dt_s)


def _simulate(job, dt_s):
    vehicle, terrain = job.vehicle, job.terrain
    initial = job.initial_state
    position = np.array(initial.position_m)
    velocity = np.array(initial.velocity_mps)
    mass = vehicle.dry_mass_kg + initial.propellant_kg
    start_mass = mass
    elapsed, delta_v = 0.0, 0.0
    exhausted = initial.propellant_kg == 0
    trace = []

    def sample(thrust=0.0, burn_s=0.0, coast_s=0.0):
        # The same state can start another simulation; UTC advances with elapsed time.
        state = initial.model_copy(
            update={
                "epoch_utc": initial.epoch_utc + timedelta(seconds=elapsed),
                "position_m": tuple(float(x) for x in position),
                "velocity_mps": tuple(float(x) for x in velocity),
                "propellant_kg": max(0.0, mass - vehicle.dry_mass_kg),
            }
        )
        row = dict(
            t_s=elapsed,
            state=state.model_dump(mode="json"),
            thrust_n=thrust,
            burn_s=burn_s,
            coast_s=coast_s,
            **_frame_state(position, velocity, terrain),
        )
        trace.append(row)
        return row

    current = sample()
    reasons = []
    target_known = (
        terrain.points[0].downrange_m
        <= job.target_downrange_m
        <= terrain.points[-1].downrange_m
    )
    if current["clearance_m"] is None or not target_known:
        termination = "terrain_unknown"
    elif current["clearance_m"] < -1e-7:
        termination = "initial_below_terrain"
    elif current["clearance_m"] <= 0:
        termination = "contact"
    else:
        height, _ = terrain_at(terrain, job.target_downrange_m)
        angle = job.target_downrange_m / terrain.reference_radius_m
        up = np.array([math.sin(angle), math.cos(angle)])
        target_position = (terrain.reference_radius_m + height) * up
        target_velocity = job.target_vertical_speed_mps * up
        command = np.zeros(2)
        termination = "timeout"
        while elapsed < job.duration_s - 1e-9:
            step_s = min(dt_s, job.duration_s - elapsed)
            time_to_go = job.duration_s - elapsed
            # Hold the last finite command in the final 0.2 s, avoiding singular gains.
            if time_to_go > max(0.2, dt_s) or elapsed == 0:
                gravity = -MOON_GM_M3_S2 * position / np.linalg.norm(position) ** 3
                command = zem_zev_acceleration(
                    position,
                    velocity,
                    target_position,
                    target_velocity,
                    time_to_go,
                    gravity,
                )
            norm = float(np.linalg.norm(command))
            requested = mass * norm
            if requested > vehicle.max_throttle_fraction * vehicle.design_thrust_n:
                thrust = vehicle.max_thrust_n
            else:
                thrust = max(
                    requested, vehicle.min_throttle_fraction * vehicle.design_thrust_n
                )
            if mass <= vehicle.dry_mass_kg:
                thrust = 0.0
            direction = (
                command / norm if norm > 0 else position / np.linalg.norm(position)
            )
            force = thrust * direction

            def advance(duration):
                return advance_burn(
                    position,
                    velocity,
                    mass,
                    vehicle.dry_mass_kg,
                    force,
                    vehicle.isp_s,
                    duration,
                )

            advanced = advance(step_s)
            next_description = _frame_state(
                advanced.position_m, advanced.velocity_mps, terrain
            )
            if next_description["clearance_m"] is None:
                termination = "terrain_unknown"
            elif next_description["clearance_m"] <= 0:
                # Locate the first crossing bracketed by this integration step.
                # This is sampled clearance, not a proof against sub-step obstacles.
                low, high = 0.0, step_s
                for _ in range(30):
                    middle = (low + high) / 2
                    probe = advance(middle)
                    clearance = _frame_state(
                        probe.position_m, probe.velocity_mps, terrain
                    )["clearance_m"]
                    if clearance is None:
                        raise ValueError("Contact refinement left terrain coverage")
                    if clearance <= 0:
                        high = middle
                    else:
                        low = middle
                step_s = high
                advanced = advance(step_s)
                termination = "contact"
            position, velocity, mass = (
                advanced.position_m,
                advanced.velocity_mps,
                advanced.mass_kg,
            )
            elapsed += step_s
            delta_v += advanced.delta_v_mps
            exhausted = exhausted or advanced.propellant_exhausted
            current = sample(thrust, advanced.burn_s, advanced.coast_s)
            if termination != "timeout":
                break
    if termination == "terrain_unknown":
        reasons.append("terrain_coverage")
    elif termination == "initial_below_terrain":
        reasons.append("initial_below_terrain")
    elif termination != "contact":
        reasons.append("no_contact")
    if termination == "contact":
        if (
            abs(current["downrange_m"] - job.target_downrange_m)
            > job.position_tolerance_m
        ):
            reasons.append("position")
        if not -job.vertical_speed_limit_mps <= current["vertical_speed_mps"] <= 0:
            reasons.append("vertical_speed")
        if abs(current["horizontal_speed_mps"]) > job.horizontal_speed_limit_mps:
            reasons.append("horizontal_speed")
        if current["terrain_slope_deg"] > job.slope_limit_deg:
            reasons.append("terrain_slope")
    remaining = current["state"]["propellant_kg"]
    if remaining < job.reserve_kg:
        reasons.append("reserve")
    identity = {
        "model_version": "h4-planar-v1",
        "request": job.model_dump(mode="json"),
        "integration_dt_s": dt_s,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            identity, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return dict(
        feasible=not reasons,
        reasons=reasons,
        termination=termination,
        request_sha256=fingerprint,
        integration_dt_s=dt_s,
        propellant_used_kg=start_mass - mass,
        propellant_remaining_kg=remaining,
        reserve_margin_kg=remaining - job.reserve_kg,
        delta_v_mps=delta_v,
        propellant_exhausted=exhausted,
        trace=trace,
    )


def evaluate_targets(
    job_request: TerminalRequest, targets: tuple[LandingTarget, ...]
) -> dict:
    """Check at most 16 explicit targets at dt and dt/2, retaining every verdict.

    Agreement limits: 0.5 m position, 0.1 m/s velocity, 0.05 kg propellant and
    0.2 s elapsed time. These are numerical checks, not flight tolerances.
    """
    job = TerminalRequest.model_validate(job_request.model_dump())
    if not 1 <= len(targets) <= 16:
        raise ValueError("Provide 1 to 16 explicit targets")
    targets = tuple(LandingTarget.model_validate(item.model_dump()) for item in targets)
    if len({item.target_id for item in targets}) != len(targets):
        raise ValueError("Target identifiers must be unique")
    candidates = []
    for target in targets:
        data = job.model_dump()
        data.update(target_downrange_m=target.downrange_m, duration_s=target.duration_s)
        candidate = TerminalRequest.model_validate(data)
        coarse, fine = (
            _simulate(candidate, candidate.dt_s),
            _simulate(candidate, candidate.dt_s / 2),
        )
        a, b = coarse["trace"][-1], fine["trace"][-1]
        position_difference = float(
            np.linalg.norm(
                np.array(a["state"]["position_m"]) - b["state"]["position_m"]
            )
        )
        velocity_difference = float(
            np.linalg.norm(
                np.array(a["state"]["velocity_mps"]) - b["state"]["velocity_mps"]
            )
        )
        fuel_difference = abs(a["state"]["propellant_kg"] - b["state"]["propellant_kg"])
        time_difference = abs(a["t_s"] - b["t_s"])
        consistent = (
            coarse["termination"] == fine["termination"]
            and coarse["reasons"] == fine["reasons"]
            and position_difference <= 0.5
            and velocity_difference <= 0.1
            and fuel_difference <= 0.05
            and time_difference <= 0.2
        )
        candidates.append(
            dict(
                target_id=target.target_id,
                coarse=coarse,
                fine=fine,
                position_difference_m=position_difference,
                velocity_difference_mps=velocity_difference,
                fuel_difference_kg=fuel_difference,
                time_difference_s=time_difference,
                numerically_consistent=consistent,
                reachable=coarse["feasible"] and fine["feasible"] and consistent,
            )
        )
    feasible = [item for item in candidates if item["reachable"]]
    best = min(
        feasible,
        key=lambda item: (item["fine"]["propellant_used_kg"], item["target_id"]),
        default=None,
    )
    return dict(
        candidates=candidates,
        recommended_target_id=best["target_id"] if best else None,
        scope="Explicit candidates only; sampled planar terrain clearance, no global reachability proof",
    )


def propagate_terminal(
    job_request: TerminalRequest,
    *,
    stop_after_s: float | None = None,
    position_bias_m=(0.0, 0.0),
    velocity_bias_mps=(0.0, 0.0),
    thrust_limit_n: float | None = None,
) -> dict:
    """H5 propagation with persistent navigation bias, thrust cap and exact pause time."""
    raise NotImplementedError
