"""R7 alternative landing plans: reachable backup sites (scenario S11).

From the primary R4 descent, take the vehicle state at a divert point (start of
the approach phase = high gate, or start of terminal descent = low gate) and
check nearby backup sites:

- Candidates are 80 m DEM cells within a search radius that pass the R3 hard
  constraints (landing-area slope within the limit, no unknown cells), ranked
  by the R3 safety score and kept at least a minimum separation apart.
- Downrange retargeting is flown exactly with the R4 guidance model: shifting
  the target by d along the track is the same as rotating the vehicle state by
  -d / R about the Moon's centre (central gravity is rotation-symmetric).
- The planar model has no crossrange dynamics. A crossrange offset c is added
  as a decoupled lateral divert with the minimum-energy result for a double
  integrator starting and ending at zero lateral velocity in time T:
  delta-v = 3 |c| / T, peak acceleration 6 |c| / T^2. Its propellant is taken
  with the rocket equation after the in-plane flight (a conservative sum).
- A backup is reachable only if propellant is not exhausted, the R5 reserve is
  kept, the R4 touchdown tolerances are met and the engine can supply the
  combined in-plane and peak lateral acceleration at every sample.

Comparison metrics are the same for the primary and every backup.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import rasterio
from affine import Affine
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from rasterio.warp import transform
from rasterio.windows import Window

from app.descent import (
    FINAL_STEP_S,
    DescentError,
    DescentRequest,
    OrbitSettings,
    Tolerances,
    VehicleProfile,
    _state,
    fly_gates,
    gate_states,
    load_profiles,
    plan_descent,
    simulate_powered_descent,
    terrain_height_m,
)
from app.fuel_optimization import (
    DEFAULT_RESERVE_DELTA_V_MPS,
    _reason,
    hover_time_s,
    reserve_propellant_kg,
)
from app.horizon import _validate_dataset
from app.risk import _approach_basis, _local_unit, _site_xy
from app.terrain_safety import (
    DEM_PIXEL_M,
    SafetySettings,
    TerrainSafetyError,
    _cached_dem,
    safety_score,
    site_safety,
)
from shared.constants import (
    MOON_GM_M3_S2,
    MOON_REFERENCE_RADIUS_M,
    STANDARD_GRAVITY_M_S2,
)
from shared.contracts import ObserverSite

DIVERT_STEP_S = 0.1
DIVERT_PHASES = {"high_gate": "approach", "low_gate": "terminal"}
REMAINING_GATES = {"high_gate": ("approach", "terminal"), "low_gate": ("terminal",)}
MAX_SEARCH_RADIUS_M = 10000.0
GEOGRAPHIC = f"+proj=longlat +R={MOON_REFERENCE_RADIUS_M} +no_defs"
ASSUMPTIONS = (
    "The primary plan is the R4 descent; the divert starts from its state at high gate (approach) or low gate (terminal descent).",
    "Downrange retargeting is flown with the R4 ZEM/ZEV guidance to the backup's own terrain height (state rotated about the Moon's centre).",
    "Crossrange offsets use the analytic minimum-energy lateral divert (3|c|/T delta-v, 6|c|/T^2 peak), decoupled from the planar descent.",
    "The lateral divert starts once the engine leaves the fixed-throttle (maximum thrust) position; T is the time left after that.",
    "Lateral propellant is added after the in-plane flight with the rocket equation, which overstates the combined cost (conservative).",
    "Reachable means: no propellant exhaustion, the R5 reserve kept, the R4 touchdown tolerances met, and in-plane plus lateral acceleration within maximum thrust at every sample.",
    "Candidates must pass the R3 hard constraints on the 80 m DEM (landing-area slope within the limit, no unknown cells); lander-scale hazards are not resolved.",
    "Sunlight and communication at the backups are not evaluated here; use the site analysis for each backup.",
)


class AlternativesError(ValueError):
    """Invalid inputs, unusable terrain or a primary plan that cannot divert."""


class AlternativesRequest(BaseModel):
    """Primary plan (R4/R5 inputs) and backup search settings."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    profile_id: str = "apollo-lm-11"
    custom_profile: VehicleProfile | None = None
    orbit: OrbitSettings = Field(default_factory=OrbitSettings)
    tolerances: Tolerances = Field(default_factory=Tolerances)
    allow_plane_change: bool = False
    reserve_delta_v_mps: float = Field(
        default=DEFAULT_RESERVE_DELTA_V_MPS, ge=0, le=500
    )
    divert_from: Literal["high_gate", "low_gate"] = "high_gate"
    search_radius_m: float = Field(default=3000.0, ge=200, le=MAX_SEARCH_RADIUS_M)
    candidate_spacing_m: float = Field(default=160.0, ge=80, le=1000)
    min_separation_m: float = Field(default=400.0, ge=0, le=5000)
    max_candidates: int = Field(default=6, ge=1, le=12, strict=True)
    approach_azimuth_deg: float = Field(default=180.0, ge=0, lt=360)
    safety: SafetySettings = Field(default_factory=SafetySettings)


@dataclass(frozen=True)
class LocalTerrain:
    """Height grid (m above the 1737.4 km sphere, NaN unknown) around a site.

    `transform` maps (col, row) to projected (x, y); `north_unit_xy` and
    `east_unit_xy` give the local directions at the site; `crs` is the
    projection (PROJ string or WKT) used to report candidate coordinates.
    """

    heights_m: np.ndarray
    transform: Affine
    site_xy_m: tuple[float, float]
    north_unit_xy: tuple[float, float]
    east_unit_xy: tuple[float, float]
    pixel_m: float
    crs: str


def lateral_divert(cross_m: float, time_to_go_s: float) -> dict:
    """Minimum-energy lateral divert: delta-v 3|c|/T and peak 6|c|/T^2.

    Raises ValueError for a non-positive or non-finite time to go.
    """
    if not (math.isfinite(time_to_go_s) and time_to_go_s > 0):
        raise ValueError("Time to go must be a positive finite duration")
    offset = abs(float(cross_m))
    return {
        "delta_v_mps": 3 * offset / time_to_go_s,
        "peak_acceleration_mps2": 6 * offset / time_to_go_s**2,
    }


def lateral_offset_m(
    cross_m: float, t_s: float, lateral_start_s: float, window_s: float
) -> float:
    """Crossrange position (m) of the minimum-energy lateral divert at t_s.

    c (3 s^2 - 2 s^3) with s = (t_s - lateral_start_s) / window_s clipped to
    [0, 1]: zero before the divert starts and c once it ends. A non-positive
    window means no lateral move is possible, so the offset stays zero.
    """
    if not window_s > 0 or cross_m == 0.0:
        return 0.0
    s = min(max((t_s - lateral_start_s) / window_s, 0.0), 1.0)
    return float(cross_m) * (3 * s**2 - 2 * s**3)


def rotate_state(position_m, velocity_mps, angle_rad: float):
    """Rotate planar state vectors counter-clockwise by angle_rad.

    Rotating by +d / R moves the vehicle d metres uprange relative to the site,
    i.e. it retargets the site d metres further downrange.
    """
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    rotation = np.array([[c, -s], [s, c]])
    position = rotation @ np.asarray(position_m, dtype=float)
    velocity = rotation @ np.asarray(velocity_mps, dtype=float)
    return position.tolist(), velocity.tolist()


def divert_state(samples: list[dict], site_radius_m: float, divert_from: str) -> dict:
    """Planar state at the first sample of the divert phase.

    Returns position_m, velocity_mps (lists), mass_kg, t_s (since PDI),
    altitude_m and downrange_m. Raises AlternativesError if the phase is absent.
    """
    phase = DIVERT_PHASES.get(divert_from)
    if phase is None:
        raise AlternativesError(f"Unknown divert point {divert_from}")
    sample = next((s for s in samples if s["phase"] == phase), None)
    if sample is None:
        raise AlternativesError(f"The primary descent has no {phase} phase")
    position, velocity = _state(
        site_radius_m,
        sample["downrange_m"],
        sample["altitude_m"],
        sample["horizontal_speed_mps"],
        sample["vertical_speed_mps"],
    )
    return {
        "position_m": position.tolist(),
        "velocity_mps": velocity.tolist(),
        "mass_kg": float(sample["mass_kg"]),
        "t_s": float(sample["t_s"]),
        "altitude_m": float(sample["altitude_m"]),
        "downrange_m": float(sample["downrange_m"]),
        "speed_mps": float(sample["speed_mps"]),
    }


def _remaining_gates(site_radius_m: float, divert_from: str) -> list[dict]:
    names = REMAINING_GATES[divert_from]
    return [g for g in gate_states(site_radius_m) if g["name"] in names]


def evaluate_divert(
    profile: VehicleProfile,
    state: dict,
    target_height_m: float,
    down_m: float,
    cross_m: float,
    divert_from: str,
    request: AlternativesRequest,
    record_path: bool = False,
) -> dict:
    """Fly from the divert state to a target offset (down_m, cross_m).

    Returns reachable, rejection_reasons, in-plane and lateral delta-v (m/s),
    total delta-v from the divert, propellant remaining (kg), reserve margin
    (kg), hover margin (s), time to touchdown (s) and the touchdown state.
    With record_path, also `path`: the flown samples as t_s (since the
    divert), downrange_m and altitude_m relative to the target, and cross_m
    from the lateral divert profile.
    """
    site_radius = MOON_REFERENCE_RADIUS_M + target_height_m
    gates = _remaining_gates(site_radius, divert_from)
    position, velocity = rotate_state(
        state["position_m"], state["velocity_mps"], down_m / site_radius
    )
    run = fly_gates(
        profile,
        site_radius,
        np.array(position),
        np.array(velocity),
        state["mass_kg"],
        gates,
        DIVERT_STEP_S,
        record=True,
    )
    total_s = sum(g["duration_s"] for g in gates)
    in_plane_dv = sum(p["delta_v_mps"] for p in run["phases"])
    flight = run["samples"][:-1]  # the last sample is the touchdown state
    saturated = profile.max_thrust_n * (1 - 1e-9)
    lateral_start = total_s
    for sample in flight:
        if sample["thrust_n"] < saturated:
            lateral_start = sample["t_s"]
            break
    reasons = []
    lateral_dv = lateral_peak = 0.0
    if cross_m != 0.0:
        window = total_s - lateral_start
        if window <= 0:
            reasons.append(
                _reason(
                    "thrust",
                    "The engine stays at the fixed-throttle position for the whole divert; no thrust is left for a lateral move.",
                    abs(cross_m),
                    0.0,
                    "m",
                )
            )
        else:
            lateral = lateral_divert(cross_m, window)
            lateral_dv = lateral["delta_v_mps"]
            lateral_peak = lateral["peak_acceleration_mps2"]
            worst = 0.0
            for sample in flight:
                if sample["t_s"] < lateral_start:
                    continue
                fraction = (sample["t_s"] - lateral_start) / window
                a_lateral = lateral_peak * abs(1 - 2 * fraction)
                a_plane = sample["thrust_n"] / sample["mass_kg"]
                available = profile.max_thrust_n / sample["mass_kg"]
                worst = max(worst, math.hypot(a_plane, a_lateral) / available)
            if worst > 1 + 1e-9:
                reasons.append(
                    _reason(
                        "thrust",
                        f"In-plane plus lateral acceleration needs {100 * worst:.0f} % of maximum thrust for a {abs(cross_m):.0f} m crossrange divert.",
                        worst,
                        1.0,
                        "fraction of maximum thrust",
                    )
                )
    ve = profile.isp_s * STANDARD_GRAVITY_M_S2
    final = run["final"]
    landing_mass = final["mass_kg"] * math.exp(-lateral_dv / ve)
    dry = profile.mass_at_pdi_kg - profile.usable_propellant_kg
    remaining = landing_mass - dry
    reserve = reserve_propellant_kg(
        landing_mass, request.reserve_delta_v_mps, profile.isp_s
    )
    margin = remaining - reserve
    gravity = MOON_GM_M3_S2 / site_radius**2
    hover = (
        hover_time_s(margin, landing_mass, profile.isp_s, gravity)
        if 0 < margin < landing_mass
        else 0.0
    )
    if run["propellant_exhausted"] or remaining < 0:
        reasons.insert(
            0,
            _reason(
                "propellant",
                f"The divert needs more propellant than the {profile.usable_propellant_kg:.0f} kg available.",
                -remaining,
                0.0,
                "kg",
            ),
        )
    elif margin < 0:
        reasons.insert(
            0,
            _reason(
                "reserve",
                f"Leaves {remaining:.0f} kg at touchdown; the reserve needs {reserve:.0f} kg.",
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
    result = {
        "reachable": not reasons,
        "rejection_reasons": reasons,
        "in_plane_delta_v_mps": float(in_plane_dv),
        "lateral_delta_v_mps": float(lateral_dv),
        "lateral_peak_acceleration_mps2": float(lateral_peak),
        "lateral_start_s": float(lateral_start),
        "total_delta_v_mps": float(in_plane_dv + lateral_dv),
        "propellant_remaining_kg": float(remaining),
        "reserve_required_kg": float(reserve),
        "reserve_margin_kg": float(margin),
        "hover_margin_s": float(hover),
        "time_to_touchdown_s": float(run["phases"][-1]["end_s"]),
        "touchdown": {
            "position_error_m": float(final["position_error_m"]),
            "vertical_speed_mps": float(final["vertical_speed_mps"]),
            "horizontal_speed_mps": float(final["horizontal_speed_mps"]),
        },
    }
    if record_path:
        # Without crossrange no lateral move is made, so no window applies.
        window = total_s - lateral_start if lateral_dv > 0 else 0.0
        result["path"] = [
            {
                "t_s": float(s["t_s"]),
                "downrange_m": float(s["downrange_m"]),
                "cross_m": lateral_offset_m(cross_m, s["t_s"], lateral_start, window),
                "altitude_m": float(s["altitude_m"]),
            }
            for s in run["samples"]
        ]
    return result


def _site_pixel(terrain: LocalTerrain) -> tuple[int, int]:
    inverse = ~terrain.transform
    col, row = inverse @ terrain.site_xy_m
    return int(math.floor(row)), int(math.floor(col))


def _coordinates(terrain: LocalTerrain, x: float, y: float) -> tuple[float, float]:
    lon, lat = transform(terrain.crs, GEOGRAPHIC, [x], [y])
    return float(lat[0]), float(lon[0]) % 360.0


def find_candidates(terrain: LocalTerrain, request: AlternativesRequest) -> list[dict]:
    """Backup cells passing the R3 hard constraints, best safety score first.

    Cells are sampled every candidate_spacing_m within search_radius_m, at
    least min_separation_m from the primary site and from each other, at most
    max_candidates. Each has down_m, cross_m, distance_m, bearing_deg,
    latitude_deg, longitude_deg_east, terrain_height_m, safety and score.
    """
    heights = np.asarray(terrain.heights_m, dtype=float)
    rows, cols = heights.shape
    site_row, site_col = _site_pixel(terrain)
    travel, right = _approach_basis(
        terrain.north_unit_xy, terrain.east_unit_xy, request.approach_azimuth_deg
    )
    step = max(1, round(request.candidate_spacing_m / terrain.pixel_m))
    reach = math.ceil(request.search_radius_m / terrain.pixel_m)
    settings = request.safety
    pool = []
    for d_row in range(-(reach // step) * step, reach + 1, step):
        for d_col in range(-(reach // step) * step, reach + 1, step):
            row, col = site_row + d_row, site_col + d_col
            if not (0 <= row < rows and 0 <= col < cols):
                continue
            x, y = terrain.transform @ (col + 0.5, row + 0.5)
            delta = np.array([x - terrain.site_xy_m[0], y - terrain.site_xy_m[1]])
            down, cross = float(delta @ travel), float(delta @ right)
            distance = math.hypot(down, cross)
            if (
                distance > request.search_radius_m + 1e-9
                or distance < max(request.min_separation_m, 1e-6) - 1e-9
            ):
                continue
            try:
                safety = site_safety(heights, terrain.pixel_m, row, col, settings)
            except ValueError:
                continue  # not enough terrain around the cell for the R3 check
            slope = safety["max_landing_slope_deg"]
            if (
                slope is None
                or slope > settings.slope_limit_deg
                or safety["unknown_landing_fraction"] > 0
            ):
                continue
            pool.append(
                {
                    "down_m": down,
                    "cross_m": cross,
                    "distance_m": distance,
                    "x": float(x),
                    "y": float(y),
                    "row": row,
                    "col": col,
                    "safety": safety,
                    "score": float(safety_score(safety, settings)),
                }
            )
    pool.sort(key=lambda c: (-c["score"], c["distance_m"], c["down_m"], c["cross_m"]))
    chosen = []
    for candidate in pool:
        if all(
            math.hypot(
                candidate["down_m"] - other["down_m"],
                candidate["cross_m"] - other["cross_m"],
            )
            >= request.min_separation_m - 1e-9
            for other in chosen
        ):
            chosen.append(candidate)
        if len(chosen) == request.max_candidates:
            break
    result = []
    for c in chosen:
        latitude, longitude = _coordinates(terrain, c["x"], c["y"])
        bearing = (
            request.approach_azimuth_deg
            + math.degrees(math.atan2(c["cross_m"], c["down_m"]))
        ) % 360
        result.append(
            {
                "down_m": c["down_m"],
                "cross_m": c["cross_m"],
                "distance_m": c["distance_m"],
                "bearing_deg": bearing,
                "latitude_deg": latitude,
                "longitude_deg_east": longitude,
                "terrain_height_m": float(heights[c["row"], c["col"]]),
                "safety": c["safety"],
                "score": c["score"],
            }
        )
    return result


def _vehicle(request: AlternativesRequest) -> VehicleProfile:
    if request.custom_profile is not None:
        return request.custom_profile
    profiles = load_profiles()
    if request.profile_id not in profiles:
        raise AlternativesError(f"Unknown vehicle profile {request.profile_id}")
    return profiles[request.profile_id]


def _row(identifier, role, place: dict, evaluation: dict, base_in_plane: float):
    return {
        "id": identifier,
        "role": role,
        "down_m": place["down_m"],
        "cross_m": place["cross_m"],
        "distance_m": place["distance_m"],
        "bearing_deg": place["bearing_deg"],
        "latitude_deg": place["latitude_deg"],
        "longitude_deg_east": place["longitude_deg_east"],
        "terrain_height_m": place["terrain_height_m"],
        "safety": place["safety"],
        "safety_score": place["score"],
        "reachable": evaluation["reachable"],
        "rejection_reasons": evaluation["rejection_reasons"],
        "in_plane_delta_v_mps": evaluation["in_plane_delta_v_mps"],
        "lateral_delta_v_mps": evaluation["lateral_delta_v_mps"],
        "divert_delta_v_mps": evaluation["in_plane_delta_v_mps"]
        - base_in_plane
        + evaluation["lateral_delta_v_mps"],
        "lateral_peak_acceleration_mps2": evaluation["lateral_peak_acceleration_mps2"],
        "propellant_remaining_kg": evaluation["propellant_remaining_kg"],
        "reserve_margin_kg": evaluation["reserve_margin_kg"],
        "hover_margin_s": evaluation["hover_margin_s"],
        "time_to_touchdown_s": evaluation["time_to_touchdown_s"],
        "touchdown": evaluation["touchdown"],
    }


def plan_alternatives(
    request: AlternativesRequest,
    terrain: LocalTerrain,
    terrain_height_m: float,
    trajectories: bool = False,
) -> dict:
    """Primary row plus ranked backups with the same metrics, deterministic.

    With trajectories, the output also maps each row id to its flown divert
    path in the primary frame (t_s since PDI, down_m, cross_m, altitude_m
    above the primary site sphere); `result_id` is unchanged by this.
    Raises AlternativesError for an unknown vehicle, a plan the R4 model
    rejects or a primary site without terrain margin for the safety check.
    """
    profile = _vehicle(request)
    descent = DescentRequest(
        site_id=request.site_id,
        latitude_deg=request.latitude_deg,
        longitude_deg_east=request.longitude_deg_east,
        profile_id=request.profile_id,
        custom_profile=request.custom_profile,
        orbit=request.orbit,
        tolerances=request.tolerances,
        allow_plane_change=request.allow_plane_change,
    )
    try:
        nominal = plan_descent(descent, terrain_height_m)
    except (DescentError, ValueError) as exc:
        raise AlternativesError(str(exc)) from exc
    vehicle = profile
    if nominal["plane_change"]["applied"]:
        used = nominal["plane_change"]["propellant_kg"]
        vehicle = profile.model_copy(
            update={
                "mass_at_pdi_kg": profile.mass_at_pdi_kg - used,
                "usable_propellant_kg": max(profile.usable_propellant_kg - used, 0.0),
            }
        )
    heights = np.asarray(terrain.heights_m, dtype=float)
    site_row, site_col = _site_pixel(terrain)
    try:
        primary_safety = site_safety(
            heights, terrain.pixel_m, site_row, site_col, request.safety
        )
    except ValueError as exc:
        raise AlternativesError(
            f"Primary site lacks terrain around it for the safety check: {exc}"
        ) from exc
    site_radius = nominal["site"]["radius_m"]
    run = simulate_powered_descent(
        vehicle,
        site_radius,
        nominal["pdi_range_m"],
        nominal["deorbit"]["periapsis_speed_mps"],
        FINAL_STEP_S,
    )
    state = divert_state(run["samples"], site_radius, request.divert_from)
    primary_eval = evaluate_divert(
        vehicle,
        state,
        terrain_height_m,
        0.0,
        0.0,
        request.divert_from,
        request,
        record_path=trajectories,
    )
    base = primary_eval["in_plane_delta_v_mps"]
    primary_score = (
        float(safety_score(primary_safety, request.safety))
        if primary_safety["max_landing_slope_deg"] is not None
        else None
    )
    primary = _row(
        "PRIMARY",
        "primary",
        {
            "down_m": 0.0,
            "cross_m": 0.0,
            "distance_m": 0.0,
            "bearing_deg": 0.0,
            "latitude_deg": request.latitude_deg,
            "longitude_deg_east": request.longitude_deg_east,
            "terrain_height_m": float(terrain_height_m),
            "safety": primary_safety,
            "score": primary_score,
        },
        primary_eval,
        base,
    )
    evaluated = []
    for candidate in find_candidates(terrain, request):
        result = evaluate_divert(
            vehicle,
            state,
            candidate["terrain_height_m"],
            candidate["down_m"],
            candidate["cross_m"],
            request.divert_from,
            request,
            record_path=trajectories,
        )
        evaluated.append((candidate, result))
    evaluated.sort(key=lambda pair: not pair[1]["reachable"])  # stable
    rows = [
        _row(f"B{i + 1}", "backup", candidate, result, base)
        for i, (candidate, result) in enumerate(evaluated)
    ]
    gates = _remaining_gates(site_radius, request.divert_from)
    output = {
        "settings": request.model_dump(mode="json"),
        "divert_from": request.divert_from,
        "divert_state": {
            "t_s": state["t_s"],
            "altitude_m": state["altitude_m"],
            "downrange_m": state["downrange_m"],
            "speed_mps": state["speed_mps"],
            "mass_kg": state["mass_kg"],
            "time_to_go_s": float(sum(g["duration_s"] for g in gates)),
        },
        "nominal_feasible": nominal["feasible"],
        "primary": primary,
        "candidates": rows,
        "reachable_ids": [r["id"] for r in rows if r["reachable"]],
        "rejected_ids": [r["id"] for r in rows if not r["reachable"]],
        "assumptions": list(ASSUMPTIONS),
    }
    identity = json.dumps(output, sort_keys=True, allow_nan=False).encode()
    output["result_id"] = hashlib.sha256(identity).hexdigest()
    if trajectories:
        # Outside the hashed identity: the paths add detail, not a new result.
        flown = [("PRIMARY", float(terrain_height_m), 0.0, primary_eval)]
        flown += [
            (row["id"], candidate["terrain_height_m"], candidate["down_m"], result)
            for row, (candidate, result) in zip(rows, evaluated)
        ]
        output["trajectories"] = {
            identifier: [
                {
                    "t_s": state["t_s"] + p["t_s"],
                    "down_m": p["downrange_m"] + down_m,
                    "cross_m": p["cross_m"],
                    "altitude_m": p["altitude_m"] + height - float(terrain_height_m),
                }
                for p in result["path"]
            ]
            for identifier, height, down_m, result in flown
        }
    return output


def load_local_terrain(
    data_dir: Path, latitude_deg: float, longitude_deg_east: float, half_width_m: float
) -> LocalTerrain:
    """Verified cached DEM heights around the site (window clipped to the raster).

    FileNotFoundError means terrain must be acquired first; AlternativesError
    marks a site outside coverage.
    """
    try:
        path = _cached_dem(data_dir)
    except TerrainSafetyError as exc:
        raise AlternativesError(str(exc)) from exc
    half = min(max(float(half_width_m), DEM_PIXEL_M), 2 * MAX_SEARCH_RADIUS_M)
    with rasterio.open(path) as dataset:
        try:
            _validate_dataset(dataset)
        except ValueError as exc:
            raise AlternativesError(str(exc)) from exc
        site_xy = _site_xy(dataset, longitude_deg_east, latitude_deg)
        row, col = dataset.index(*site_xy)
        if not (0 <= row < dataset.height and 0 <= col < dataset.width):
            raise AlternativesError("Site is outside terrain coverage")
        pixels = math.ceil(half / DEM_PIXEL_M) + 1
        row0, col0 = max(row - pixels, 0), max(col - pixels, 0)
        row1 = min(row + pixels + 1, dataset.height)
        col1 = min(col + pixels + 1, dataset.width)
        window = Window(col0, row0, col1 - col0, row1 - row0)
        heights = dataset.read(1, window=window, masked=True).astype(float)
        site = ObserverSite(
            site_id="alternatives-map",
            longitude_deg_east=float(longitude_deg_east),
            latitude_deg=float(latitude_deg),
            altitude_km=0.0,
        )
        north = _local_unit(dataset, site, site_xy, 0.0)
        east = _local_unit(dataset, site, site_xy, 90.0)
        window_transform = dataset.window_transform(window)
        crs = dataset.crs.to_wkt()
    return LocalTerrain(
        heights_m=np.ma.filled(heights, np.nan),
        transform=window_transform,
        site_xy_m=site_xy,
        north_unit_xy=north,
        east_unit_xy=east,
        pixel_m=DEM_PIXEL_M,
        crs=crs,
    )


def register_alternative_routes(app, data_dir: Path) -> None:
    """POST /descents/alternatives: 409 without cached terrain, 422 otherwise."""

    @app.post("/descents/alternatives")
    def descent_alternatives(body: AlternativesRequest):
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
            return plan_alternatives(body, terrain, height)
        except AlternativesError as exc:
            raise HTTPException(422, str(exc)) from exc
