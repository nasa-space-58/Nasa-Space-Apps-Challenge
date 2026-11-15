"""Mission-run route set: every computed landing route with its role and reasons.

When a mission run starts, the interface also asks for the route set around
the analysis site. It reuses the R7 divert model (app.alternatives) from the
nominal R4 descent and labels each route:

- primary: the planned landing target of the analysis, flown from the divert
  point with the same metrics as every backup.
- backup: a backup site that passed the R3 hard constraints and is reachable
  from the divert point, ranked like R7 (safety score, then distance).
- rejected: either a backup that was flown but is not reachable (the R7
  rejection reasons: propellant, reserve, thrust, touchdown tolerance), or a
  cell in the search area that fails the R3 hard constraints (slope above the
  limit or unknown terrain) and was therefore never flown.

Paths are given in one local planar frame at the primary target: down_m along
the approach azimuth, cross_m to its right, altitude_m above the primary site
sphere, t_s since powered descent initiation. The route set is computed for the
nominal (unfaulted) vehicle: it is the pre-flight plan set, not a replay of the
run's faults. It is not landing-safety certification.
"""

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.alternatives import (
    MAX_SEARCH_RADIUS_M,
    AlternativesError,
    AlternativesRequest,
    LocalTerrain,
    _coordinates,
    _site_pixel,
    load_local_terrain,
    plan_alternatives,
)
from app.descent import DescentError, VehicleProfile, terrain_height_m
from app.fuel_optimization import DEFAULT_RESERVE_DELTA_V_MPS
from app.risk import _approach_basis
from app.terrain_safety import DEM_PIXEL_M, SafetySettings, site_safety

MAX_ROUTE_SAMPLES = 120
FRAME = (
    "Local planar frame at the primary target: down_m along the approach "
    "azimuth, cross_m to its right, altitude_m above the primary site sphere, "
    "t_s since powered descent initiation."
)
ASSUMPTIONS = (
    "Routes come from the R7 divert model from the nominal R4 descent; the mission run's faults are not applied to them.",
    "Downrange flight uses the R4 ZEM/ZEV guidance to each site's own terrain height; crossrange uses the analytic minimum-energy lateral divert profile c(3s^2 - 2s^3).",
    "A backup's altitude is shifted by its terrain height difference to the primary so that all paths share the primary site sphere.",
    "Terrain-rejected cells fail the R3 hard constraints on the 80 m DEM (landing-area slope above the limit or unknown cells); they are not flown.",
    "Backups are ranked like R7: reachable first, then R3 safety score and distance.",
)
DISCLAIMER = (
    "Pre-flight route set from sampled physics on an 80 m DEM; not landing-safety "
    "certification, a guaranteed divert capability or a replay of injected faults."
)


class MissionRoutesRequest(BaseModel):
    """Vehicle, approach and backup search settings for the route set."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    profile_id: str = "apollo-lm-11"
    custom_profile: VehicleProfile | None = None
    approach_azimuth_deg: float = Field(default=0.0, ge=0, lt=360)
    divert_from: Literal["high_gate", "low_gate"] = "high_gate"
    search_radius_m: float = Field(default=3000.0, ge=200, le=MAX_SEARCH_RADIUS_M)
    max_candidates: int = Field(default=8, ge=1, le=12, strict=True)
    max_terrain_rejections: int = Field(default=6, ge=0, le=12, strict=True)
    reserve_delta_v_mps: float = Field(
        default=DEFAULT_RESERVE_DELTA_V_MPS, ge=0, le=500
    )
    terrain: SafetySettings = Field(default_factory=SafetySettings)


class MissionRoutesError(ValueError):
    """Invalid inputs or a site the route set cannot be computed for."""


def _reason(code, detail, value=None, limit=None, unit=None) -> dict:
    return {
        "code": code,
        "detail": detail,
        "value": value,
        "limit": limit,
        "unit": unit,
    }


def downsample(points: list, max_samples: int) -> list:
    """Evenly strided subset that always keeps the first and last point.

    Returns the list unchanged when it is short enough. Raises ValueError for
    max_samples below 2.
    """
    if max_samples < 2:
        raise ValueError("At least two samples are needed to keep both ends")
    if len(points) <= max_samples:
        return list(points)
    stride = math.ceil((len(points) - 1) / (max_samples - 1))
    kept = list(points[::stride])
    if len(points) - 1 > (len(kept) - 1) * stride:
        kept.append(points[-1])
    return kept


def alternatives_request(
    request: MissionRoutesRequest, site: dict
) -> AlternativesRequest:
    """R7 request for the analysis site (site_id, latitude_deg, longitude_deg_east)."""
    return AlternativesRequest(
        site_id=str(site.get("site_id") or "custom"),
        latitude_deg=site["latitude_deg"],
        longitude_deg_east=site["longitude_deg_east"],
        profile_id=request.profile_id,
        custom_profile=request.custom_profile,
        reserve_delta_v_mps=request.reserve_delta_v_mps,
        divert_from=request.divert_from,
        search_radius_m=request.search_radius_m,
        max_candidates=request.max_candidates,
        approach_azimuth_deg=request.approach_azimuth_deg,
        safety=request.terrain,
    )


def _terrain_reasons(safety: dict, settings: SafetySettings) -> list[dict]:
    reasons = []
    slope = safety["max_landing_slope_deg"]
    unknown = safety["unknown_landing_fraction"]
    if slope is None or unknown > 0:
        reasons.append(
            _reason(
                "unknown_terrain",
                f"{100 * unknown:.0f} % of the landing area has unknown slope on the 80 m DEM; unknown terrain is never treated as flat.",
                float(unknown),
                0.0,
                "fraction",
            )
        )
    if slope is not None and slope > settings.slope_limit_deg:
        reasons.append(
            _reason(
                "slope",
                f"Landing-area slope {slope:.1f} deg exceeds the {settings.slope_limit_deg:g} deg limit (tip-over risk).",
                float(slope),
                float(settings.slope_limit_deg),
                "deg",
            )
        )
    return reasons


def terrain_rejections(
    terrain: LocalTerrain, request: AlternativesRequest, limit: int
) -> list[dict]:
    """Search-area cells that fail the R3 hard constraints, nearest first.

    Uses the R7 candidate grid (spacing, search radius, minimum separation from
    the primary and from each other). Cells without enough terrain around them
    for the check are skipped, never reported. Each entry has down_m, cross_m,
    distance_m, bearing_deg, latitude_deg, longitude_deg_east,
    terrain_height_m, safety and reasons (code "slope" or "unknown_terrain").
    """
    if limit <= 0:
        return []
    heights = np.asarray(terrain.heights_m, dtype=float)
    rows, cols = heights.shape
    site_row, site_col = _site_pixel(terrain)
    travel, right = _approach_basis(
        terrain.north_unit_xy, terrain.east_unit_xy, request.approach_azimuth_deg
    )
    step = max(1, round(request.candidate_spacing_m / terrain.pixel_m))
    reach = math.ceil(request.search_radius_m / terrain.pixel_m)
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
                safety = site_safety(heights, terrain.pixel_m, row, col, request.safety)
            except ValueError:
                continue  # not enough terrain around the cell for the R3 check
            reasons = _terrain_reasons(safety, request.safety)
            if reasons:
                pool.append(
                    (
                        distance,
                        down,
                        cross,
                        float(x),
                        float(y),
                        row,
                        col,
                        safety,
                        reasons,
                    )
                )
    pool.sort(key=lambda c: (c[0], c[1], c[2]))
    chosen = []
    for cell in pool:
        if all(
            math.hypot(cell[1] - other[1], cell[2] - other[2])
            >= request.min_separation_m - 1e-9
            for other in chosen
        ):
            chosen.append(cell)
        if len(chosen) == limit:
            break
    result = []
    for distance, down, cross, x, y, row, col, safety, reasons in chosen:
        latitude, longitude = _coordinates(terrain, x, y)
        height = heights[row, col]
        result.append(
            {
                "down_m": down,
                "cross_m": cross,
                "distance_m": distance,
                "bearing_deg": (
                    request.approach_azimuth_deg + math.degrees(math.atan2(cross, down))
                )
                % 360,
                "latitude_deg": latitude,
                "longitude_deg_east": longitude,
                "terrain_height_m": float(height) if math.isfinite(height) else None,
                "safety": safety,
                "reasons": reasons,
            }
        )
    return result


def _metrics(row: dict | None, safety: dict, score) -> dict:
    flight = row is not None
    return {
        "safety_score": score,
        "max_landing_slope_deg": safety["max_landing_slope_deg"],
        "hazard_distance_m": safety["hazard_distance_m"],
        "divert_delta_v_mps": row["divert_delta_v_mps"] if flight else None,
        "propellant_remaining_kg": row["propellant_remaining_kg"] if flight else None,
        "reserve_margin_kg": row["reserve_margin_kg"] if flight else None,
        "hover_margin_s": row["hover_margin_s"] if flight else None,
        "time_to_touchdown_s": row["time_to_touchdown_s"] if flight else None,
        "touchdown_position_error_m": row["touchdown"]["position_error_m"]
        if flight
        else None,
        "touchdown_vertical_speed_mps": row["touchdown"]["vertical_speed_mps"]
        if flight
        else None,
        "touchdown_horizontal_speed_mps": row["touchdown"]["horizontal_speed_mps"]
        if flight
        else None,
    }


def _place(row: dict) -> dict:
    return {
        key: row[key]
        for key in (
            "latitude_deg",
            "longitude_deg_east",
            "down_m",
            "cross_m",
            "distance_m",
            "bearing_deg",
            "terrain_height_m",
        )
    }


def _r7_reasons(row: dict) -> list[dict]:
    return [
        _reason(r["reason"], r["detail"], r["value"], r["limit"], r["unit"])
        for r in row["rejection_reasons"]
    ]


def build_routes(
    plan: dict, rejected_cells: list[dict], max_samples: int = MAX_ROUTE_SAMPLES
) -> list[dict]:
    """Role-labelled routes from an R7 plan with trajectories and rejected cells.

    Order: primary, backups by rank, flown rejections, terrain rejections.
    """
    paths = plan["trajectories"]
    primary = plan["primary"]

    def flown(row, role, rank, reasons):
        return {
            "id": row["id"],
            "role": role,
            "rank": rank,
            "reachable": bool(row["reachable"]),
            "evaluated": "flown",
            **_place(row),
            "metrics": _metrics(row, row["safety"], row["safety_score"]),
            "reasons": reasons,
            "trajectory": downsample(paths[row["id"]], max_samples),
        }

    margin = primary["reserve_margin_kg"]
    if primary["reachable"]:
        detail = f"Planned landing target of the analysis; reachable from the divert point with {margin:.0f} kg above the propellant reserve."
    else:
        detail = "Planned landing target of the analysis; not reachable from the divert point with this vehicle."
    routes = [
        flown(
            primary,
            "primary",
            None,
            [
                _reason("planned_target", detail, float(margin), 0.0, "kg"),
                *_r7_reasons(primary),
            ],
        )
    ]
    reachable = [c for c in plan["candidates"] if c["reachable"]]
    for rank, row in enumerate(reachable, start=1):
        extra_dv = row["divert_delta_v_mps"]
        less_kg = primary["propellant_remaining_kg"] - row["propellant_remaining_kg"]
        score = row["safety_score"]
        routes.append(
            flown(
                row,
                "backup",
                rank,
                [
                    _reason(
                        "reachable",
                        f"Reachable from the divert point with {row['reserve_margin_kg']:.0f} kg above the propellant reserve and within the touchdown tolerances.",
                        float(row["reserve_margin_kg"]),
                        0.0,
                        "kg",
                    ),
                    _reason(
                        "backup_rank",
                        f"Backup {rank} of {len(reachable)}, ranked by R3 safety score ({score:.2f}) and then distance.",
                        rank,
                        len(reachable),
                        None,
                    ),
                    _reason(
                        "not_planned_target",
                        f"{row['distance_m']:.0f} m from the planned target; the divert needs {extra_dv:+.1f} m/s delta-v and {less_kg:+.0f} kg propellant compared with the primary.",
                        float(row["distance_m"]),
                        None,
                        "m",
                    ),
                ],
            )
        )
    for row in plan["candidates"]:
        if not row["reachable"]:
            routes.append(flown(row, "rejected", None, _r7_reasons(row)))
    for index, cell in enumerate(rejected_cells, start=1):
        routes.append(
            {
                "id": f"T{index}",
                "role": "rejected",
                "rank": None,
                "reachable": None,
                "evaluated": "terrain_only",
                **_place(cell),
                "metrics": _metrics(None, cell["safety"], None),
                "reasons": cell["reasons"],
                "trajectory": [],
            }
        )
    return routes


def plan_mission_routes(
    request: MissionRoutesRequest,
    site: dict,
    terrain: LocalTerrain,
    terrain_height_m: float,
) -> dict:
    """Route set for one analysis site, deterministic with a result_id.

    Raises MissionRoutesError when the R7 plan cannot be computed.
    """
    r7 = alternatives_request(request, site)
    try:
        plan = plan_alternatives(r7, terrain, terrain_height_m, trajectories=True)
    except AlternativesError as exc:
        raise MissionRoutesError(str(exc)) from exc
    cells = terrain_rejections(terrain, r7, request.max_terrain_rejections)
    routes = build_routes(plan, cells)
    roles = [r["role"] for r in routes]
    output = {
        "frame": FRAME,
        "divert_from": plan["divert_from"],
        "divert_state": plan["divert_state"],
        "nominal_feasible": plan["nominal_feasible"],
        "routes": routes,
        "counts": {
            role: roles.count(role) for role in ("primary", "backup", "rejected")
        },
        "settings": request.model_dump(mode="json"),
        "assumptions": list(ASSUMPTIONS),
        "disclaimer": DISCLAIMER,
    }
    identity = json.dumps(output, sort_keys=True, allow_nan=False).encode()
    output["result_id"] = hashlib.sha256(identity).hexdigest()
    return output


MAX_TERRAIN_CELLS = 160
TERRAIN_MARGIN_M = 600.0


def route_extent(routes: list[dict], margin_m: float) -> dict:
    """Bounds of every path point and route end point, widened by margin_m.

    Returns down_min_m, down_max_m, cross_min_m and cross_max_m in the route
    frame. Raises ValueError for an empty route list or a negative margin.
    """
    raise NotImplementedError


def route_terrain(
    terrain: LocalTerrain,
    approach_azimuth_deg: float,
    extent: dict,
    primary_height_m: float,
    max_cells: int = MAX_TERRAIN_CELLS,
) -> dict:
    """DEM heights resampled onto a regular grid in the route frame.

    Row i, column j is the cell centre at down0_m + i * spacing_m and
    cross0_m + j * spacing_m. Heights are bilinear between DEM cell centres
    and relative to the primary site height (m), so they share the paths'
    altitude reference; a sample is None when any of its four DEM neighbours
    is unknown or outside the window. The spacing is at least one DEM pixel
    and keeps rows and columns at most max_cells while covering the extent.
    Returns down0_m, cross0_m, spacing_m, rows, cols and heights_m.
    """
    raise NotImplementedError


def register_mission_route_routes(app, analyses, data_dir: Path) -> None:
    """POST /analyses/{analysis_id}/mission-routes."""

    @app.post("/analyses/{analysis_id}/mission-routes")
    def mission_routes(analysis_id: str, body: MissionRoutesRequest):
        job = analyses.get(analysis_id)
        if job is None:
            raise HTTPException(404, "Analysis not found")
        if job["status"] not in ("ready", "insufficient_data"):
            raise HTTPException(409, "Analysis is not complete")
        site = job["result"]["analysis"]["request"]["site"]
        try:
            height = terrain_height_m(
                data_dir, site["latitude_deg"], site["longitude_deg_east"]
            )
            terrain = load_local_terrain(
                data_dir,
                site["latitude_deg"],
                site["longitude_deg_east"],
                body.search_radius_m + body.terrain.hazard_search_m + 3 * DEM_PIXEL_M,
            )
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (DescentError, AlternativesError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            result = plan_mission_routes(body, site, terrain, height)
        except MissionRoutesError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"analysis_id": analysis_id, **result}
