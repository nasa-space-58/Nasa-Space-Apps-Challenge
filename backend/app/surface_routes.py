"""R8 post-landing exploration: terrain-constrained surface routes (scenario S12).

From a landing site to up to five science targets on the cached 80 m DEM:

- Traversability: a cell is passable when its Horn slope (R3 method) is known
  and at most the mobility slope limit (planning assumption, default 15 deg).
- Routing: A* over 8-connected passable cells; step cost is the 3-D distance
  between cell centres (horizontal 80 m or 113 m, plus the height change),
  with the admissible straight-line horizontal heuristic.
- Duration uses a constant traverse speed (planning assumption, default
  0.1 m/s including stops); no rover power or thermal model.
- Sunlight and Earth visibility along a route are evaluated only when a ready
  site analysis (Task 4 job) is supplied: at up to `waypoints_per_route`
  evenly spaced route cells, the local terrain horizon is computed for that
  cell and compared with the analysis' Sun/Earth direction at the arrival
  time (linear interpolation between analysis frames). Reusing the landing
  site's ephemeris shifts directions by at most distance / R (0.033 deg/km).
  The Sun uses the mean solar angular radius (959.63 arcsec at 1 au). Route
  stretches between sampled cells are not evaluated.
"""

import hashlib
import heapq
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import rasterio
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.alternatives import (
    AlternativesError,
    LocalTerrain,
    _coordinates,
    _site_pixel,
    load_local_terrain,
)
from app.horizon import HorizonError, compute_profile, interpolate_horizon
from app.terrain_safety import DEM_PIXEL_M, _cached_dem, slope_grid_deg
from shared.constants import MOON_REFERENCE_RADIUS_M
from shared.contracts import ObserverSite
from shared.horizon import HorizonSettings

DEFAULT_SLOPE_LIMIT_DEG = 15.0
DEFAULT_SPEED_MPS = 0.1
DEFAULT_OBSERVER_HEIGHT_M = 2.0
MAX_TARGET_DISTANCE_M = 8000.0
MAX_TARGETS = 5
MAX_PATH_POINTS = 200
DEFAULT_SCENE_MARGIN_M = 400.0
DEFAULT_MAX_EXPLORED_CELLS = 4000
MAX_ANALYSIS_OFFSET_M = 10000.0
SOLAR_RADIUS_DEG = 959.63 / 3600
NEIGHBOURS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
ASSUMPTIONS = (
    "Passable cells have a known R3 Horn slope at or below the mobility limit on the 80 m DEM; rocks and craters below 80 m are not resolved.",
    "A* finds the shortest 3-D path over 8-connected passable cells; the route follows cell centres.",
    "Duration uses a constant traverse speed including stops; rover power, thermal and wheel limits are not modelled.",
    "Sunlight and Earth are checked only at sampled route cells, using a local horizon computed for each cell and the site analysis' Sun/Earth directions at arrival.",
    "The landing site's ephemeris is reused along the route; directions shift by at most distance / R (about 0.033 deg per km).",
    "The Sun is a disk of the mean solar angular radius (959.63 arcsec at 1 au); Earth visibility is for its centre, not a usable radio link.",
    "Mission efficiency = reachable share, straight-line/route length and the sunlit share of sampled points; weights are documented and adjustable only in code.",
)


class RouteError(ValueError):
    """Invalid route inputs, unusable terrain or an unusable site analysis."""


class Target(BaseModel):
    """A science target by bearing (deg from north, clockwise) and distance (m)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    target_id: str = Field(min_length=1, max_length=40)
    bearing_deg: float = Field(ge=0, lt=360)
    distance_m: float = Field(gt=0, le=MAX_TARGET_DISTANCE_M)


class RouteRequest(BaseModel):
    """Landing site, targets, mobility assumptions and an optional analysis."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    targets: list[Target] = Field(min_length=1, max_length=MAX_TARGETS)
    slope_limit_deg: float = Field(default=DEFAULT_SLOPE_LIMIT_DEG, ge=1, le=35)
    speed_mps: float = Field(default=DEFAULT_SPEED_MPS, gt=0, le=2)
    observer_height_m: float = Field(default=DEFAULT_OBSERVER_HEIGHT_M, gt=0, le=10)
    analysis_job_id: str | None = Field(default=None, max_length=64)
    departure_utc: str | None = None
    waypoints_per_route: int = Field(default=5, ge=2, le=8, strict=True)
    # Optional 3-D scene payload: the terrain window around the routes and the
    # A* search order, for visualizing the planner on the real DEM.
    include_scene: bool = False
    scene_margin_m: float = Field(default=DEFAULT_SCENE_MARGIN_M, ge=0, le=2000)
    max_explored_cells: int = Field(
        default=DEFAULT_MAX_EXPLORED_CELLS, ge=100, le=20000, strict=True
    )

    @model_validator(mode="after")
    def unique_targets(self):
        ids = [t.target_id for t in self.targets]
        if len(set(ids)) != len(ids):
            raise ValueError("Target IDs must be unique")
        return self


def passable_grid(slope_deg: np.ndarray, slope_limit_deg: float) -> np.ndarray:
    """True where the slope is known and at most the limit."""
    slopes = np.asarray(slope_deg, dtype=float)
    with np.errstate(invalid="ignore"):
        return np.isfinite(slopes) & (slopes <= slope_limit_deg)


def astar(
    heights_m: np.ndarray,
    passable: np.ndarray,
    start: tuple[int, int],
    goal: tuple[int, int],
    pixel_m: float,
    explored: list[tuple[int, int]] | None = None,
) -> list[tuple[int, int]] | None:
    """Shortest 3-D path over 8-connected passable cells; None when unreachable.

    Includes start and goal. Deterministic for equal costs. Start or goal
    outside the grid or impassable returns None. When `explored` is a list,
    every expanded (closed) cell is appended to it in expansion order.
    """
    heights = np.asarray(heights_m, dtype=float)
    rows, cols = heights.shape

    def inside(cell):
        return 0 <= cell[0] < rows and 0 <= cell[1] < cols

    start, goal = tuple(start), tuple(goal)
    if not (inside(start) and inside(goal)):
        return None
    if not (passable[start] and passable[goal]):
        return None

    def heuristic(cell):
        return pixel_m * math.hypot(cell[0] - goal[0], cell[1] - goal[1])

    best = {start: 0.0}
    parent: dict = {}
    frontier = [(heuristic(start), 0.0, start)]
    closed = set()
    while frontier:
        _, cost, cell = heapq.heappop(frontier)
        if cell in closed:
            continue
        if cell == goal:
            path = [cell]
            while path[-1] in parent:
                path.append(parent[path[-1]])
            return path[::-1]
        closed.add(cell)
        if explored is not None:
            explored.append(cell)
        for d_row, d_col in NEIGHBOURS:
            nxt = (cell[0] + d_row, cell[1] + d_col)
            if not inside(nxt) or not passable[nxt] or nxt in closed:
                continue
            horizontal = pixel_m * math.hypot(d_row, d_col)
            step = math.hypot(horizontal, heights[nxt] - heights[cell])
            total = cost + step
            if total < best.get(nxt, math.inf) - 1e-12:
                best[nxt] = total
                parent[nxt] = cell
                heapq.heappush(frontier, (total + heuristic(nxt), total, nxt))
    return None


def path_metrics(
    path: list[tuple[int, int]],
    heights_m: np.ndarray,
    slope_deg: np.ndarray,
    pixel_m: float,
    speed_mps: float,
) -> dict:
    """length_m (3-D), horizontal_length_m, duration_s, max_slope_deg,
    elevation_gain_m (sum of climbs) and cumulative distance per cell."""
    heights = np.asarray(heights_m, dtype=float)
    cumulative = [0.0]
    horizontal_total = gain = 0.0
    for a, b in zip(path, path[1:]):
        horizontal = pixel_m * math.hypot(b[0] - a[0], b[1] - a[1])
        rise = float(heights[b] - heights[a])
        cumulative.append(cumulative[-1] + math.hypot(horizontal, rise))
        horizontal_total += horizontal
        gain += max(rise, 0.0)
    slopes = [float(slope_deg[cell]) for cell in path]
    return {
        "length_m": cumulative[-1],
        "horizontal_length_m": horizontal_total,
        "duration_s": cumulative[-1] / speed_mps,
        "max_slope_deg": max(slopes) if slopes else None,
        "elevation_gain_m": gain,
        "cumulative_m": cumulative,
    }


def sample_waypoints(cell_count: int, count: int) -> list[int]:
    """Evenly spaced path indices including first and last, without repeats."""
    if cell_count <= 0:
        return []
    points = np.round(np.linspace(0, cell_count - 1, min(count, cell_count)))
    return sorted({int(p) for p in points})


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RouteError("UTC timestamps need an explicit offset")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    text = value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        text += f".{value.microsecond:06d}"
    return text + "Z"


def interpolate_sky(frames: list[dict], utc: str, target: str) -> dict | None:
    """Linear azimuth (shortest way round) and elevation at a UTC instant.

    `frames` are Task 4 playback frames ({'utc', 'sun': {...}, 'earth': {...}}).
    None outside the frame span or when a neighbouring value is unknown.
    """
    if not frames:
        return None
    moment = _parse_utc(utc)
    times = [_parse_utc(f["utc"]) for f in frames]
    if moment < times[0] or moment > times[-1]:
        return None

    def value(frame):
        sky = frame.get(target) or {}
        az, el = sky.get("azimuth_deg"), sky.get("elevation_deg")
        return None if az is None or el is None else (float(az), float(el))

    for index, time in enumerate(times):
        if time == moment:
            exact = value(frames[index])
            return (
                None
                if exact is None
                else {"azimuth_deg": exact[0], "elevation_deg": exact[1]}
            )
        if time > moment:
            before, after = value(frames[index - 1]), value(frames[index])
            if before is None or after is None:
                return None
            span = (time - times[index - 1]).total_seconds()
            fraction = (moment - times[index - 1]).total_seconds() / span
            turn = (after[0] - before[0] + 180.0) % 360.0 - 180.0
            return {
                "azimuth_deg": (before[0] + fraction * turn) % 360.0,
                "elevation_deg": before[1] + fraction * (after[1] - before[1]),
            }
    return None


def classify_sun(clearance_deg: float | None) -> str:
    """full / partial / dark from clearance against the mean solar radius."""
    if clearance_deg is None:
        return "unknown"
    if clearance_deg > SOLAR_RADIUS_DEG:
        return "full"
    if clearance_deg < -SOLAR_RADIUS_DEG:
        return "dark"
    return "partial"


def classify_earth(clearance_deg: float | None) -> str:
    """visible / blocked / boundary for the Earth centre; unknown for None."""
    if clearance_deg is None:
        return "unknown"
    if clearance_deg > 0:
        return "visible"
    if clearance_deg < 0:
        return "blocked"
    return "boundary"


HorizonFunction = Callable[[float, float, float], dict]


def _sky_state(frames, arrival, target, profile) -> dict:
    position = interpolate_sky(frames, arrival, target) if arrival else None
    horizon = (
        interpolate_horizon(profile, position["azimuth_deg"]) if position else None
    )
    clearance = (
        position["elevation_deg"] - horizon
        if position is not None and horizon is not None
        else None
    )
    classify = classify_sun if target == "sun" else classify_earth
    return {
        "state": classify(clearance),
        "clearance_deg": clearance,
        "azimuth_deg": position["azimuth_deg"] if position else None,
        "elevation_deg": position["elevation_deg"] if position else None,
        "horizon_deg": horizon,
    }


def scene_window(
    rows: int,
    cols: int,
    cells: list[tuple[int, int]],
    margin_cells: int,
) -> tuple[int, int, int, int]:
    """Clipped (row0, col0, row1, col1) box, end-exclusive, around `cells`."""
    if not cells:
        raise ValueError("A scene window needs at least one cell")
    if margin_cells < 0:
        raise ValueError("Scene margin must be non-negative")
    row_values = [c[0] for c in cells]
    col_values = [c[1] for c in cells]
    return (
        max(0, min(row_values) - margin_cells),
        max(0, min(col_values) - margin_cells),
        min(rows, max(row_values) + margin_cells + 1),
        min(cols, max(col_values) + margin_cells + 1),
    )


def _scene(
    request: "RouteRequest",
    terrain: LocalTerrain,
    heights: np.ndarray,
    passable: np.ndarray,
    site: tuple[int, int],
    searches: dict[str, dict],
) -> dict:
    """Terrain window, traversability, affine and per-target A* order.

    `searches[target_id]` holds the target cell (or None), the full path (or
    None) and the expanded cells of that target's search, in terrain indices.
    """
    rows, cols = heights.shape
    cells = [site]
    for search in searches.values():
        if search["path"]:
            cells.extend(search["path"])
        elif search["target"] is not None:
            cells.append(search["target"])
    margin = math.ceil(request.scene_margin_m / terrain.pixel_m)
    r0, c0, r1, c1 = scene_window(rows, cols, cells, margin)

    def inside(cell):
        return r0 <= cell[0] < r1 and c0 <= cell[1] < c1

    def relative(cell):
        return [int(cell[0] - r0), int(cell[1] - c0)]

    base = float(heights[site])
    window = heights[r0:r1, c0:c1]
    t = terrain.transform
    a_matrix = np.array([[t.a, t.b], [t.d, t.e]])
    offset = a_matrix @ np.array([c0, r0]) + np.array([t.c, t.f])
    offset = offset - np.asarray(terrain.site_xy_m, dtype=float)

    def row_of(unit):
        unit = np.asarray(unit, dtype=float)
        return [
            float(unit @ a_matrix[:, 0]),
            float(unit @ a_matrix[:, 1]),
            float(unit @ offset),
        ]

    explored, truncated, path_cells = {}, {}, {}
    for target_id, search in searches.items():
        kept = [relative(c) for c in search["explored"] if inside(c)]
        truncated[target_id] = len(kept) > request.max_explored_cells
        explored[target_id] = kept[: request.max_explored_cells]
        path_cells[target_id] = [relative(c) for c in search["path"] or []]
    return {
        "pixel_m": float(terrain.pixel_m),
        "slope_limit_deg": request.slope_limit_deg,
        "rows": int(r1 - r0),
        "cols": int(c1 - c0),
        "window": {"row0": int(r0), "col0": int(c0), "row1": int(r1), "col1": int(c1)},
        "site_cell": relative(site),
        "heights_m": [
            [float(v - base) if np.isfinite(v) else None for v in line]
            for line in window
        ],
        "passable": [[bool(v) for v in line] for line in passable[r0:r1, c0:c1]],
        "local_from_cell": {
            "east": row_of(terrain.east_unit_xy),
            "north": row_of(terrain.north_unit_xy),
        },
        "explored": explored,
        "explored_truncated": truncated,
        "path_cells": path_cells,
    }


def plan_routes(
    request: RouteRequest,
    terrain: LocalTerrain,
    frames: list[dict] | None = None,
    horizon_at: HorizonFunction | None = None,
) -> dict:
    """Routes to every target with metrics, waypoints and a site efficiency score.

    `horizon_at(latitude_deg, longitude_deg_east, altitude_m_above_sphere)`
    returns a horizon profile (Task 2 format). Without frames, sunlight and
    communication are reported as not evaluated. Raises RouteError when the
    landing cell is impassable or outside the terrain.
    """
    heights = np.asarray(terrain.heights_m, dtype=float)
    rows, cols = heights.shape
    slopes = slope_grid_deg(heights, terrain.pixel_m)
    passable = passable_grid(slopes, request.slope_limit_deg)
    site = _site_pixel(terrain)
    if not (0 <= site[0] < rows and 0 <= site[1] < cols):
        raise RouteError("Landing site is outside the terrain window")
    if not passable[site]:
        raise RouteError(
            "Landing cell is not traversable at the mobility slope limit "
            f"({request.slope_limit_deg:g} deg)"
        )
    north = np.asarray(terrain.north_unit_xy, dtype=float)
    east = np.asarray(terrain.east_unit_xy, dtype=float)
    site_xy = np.asarray(terrain.site_xy_m, dtype=float)

    def centre(cell):
        x, y = terrain.transform @ (cell[1] + 0.5, cell[0] + 0.5)
        return np.array([x, y])

    def local(cell):
        delta = centre(cell) - site_xy
        return {"north_m": float(delta @ north), "east_m": float(delta @ east)}

    sky = bool(frames) and horizon_at is not None
    departure = request.departure_utc or (frames[0]["utc"] if frames else None)
    start_time = _parse_utc(departure) if departure else None
    site_centre = centre(site)
    routes = []
    searches: dict[str, dict] = {}
    for item in request.targets:
        bearing = math.radians(item.bearing_deg)
        xy = site_xy + item.distance_m * (
            math.cos(bearing) * north + math.sin(bearing) * east
        )
        latitude, longitude = _coordinates(terrain, float(xy[0]), float(xy[1]))
        col, row = ~terrain.transform @ (float(xy[0]), float(xy[1]))
        cell = (int(math.floor(row)), int(math.floor(col)))
        route = {
            "target_id": item.target_id,
            "bearing_deg": item.bearing_deg,
            "distance_m": item.distance_m,
            "latitude_deg": latitude,
            "longitude_deg_east": longitude,
            "straight_distance_m": None,
            "reachable": False,
            "reason": None,
            "detail": None,
            "length_m": None,
            "horizontal_length_m": None,
            "duration_s": None,
            "max_slope_deg": None,
            "elevation_gain_m": None,
            "detour_ratio": None,
            "path": [],
            "waypoints": [],
            "shadowed_waypoints": None,
            "no_earth_waypoints": None,
            "unknown_sky_waypoints": None,
        }
        routes.append(route)
        search = {"target": None, "path": None, "explored": []}
        searches[item.target_id] = search
        if not (0 <= cell[0] < rows and 0 <= cell[1] < cols):
            route["reason"] = "outside_terrain"
            route["detail"] = "The target lies outside the loaded terrain window."
            continue
        search["target"] = cell
        route["straight_distance_m"] = float(np.linalg.norm(centre(cell) - site_centre))
        if not passable[cell]:
            slope = slopes[cell]
            route["reason"] = "target_impassable"
            route["detail"] = (
                "The target cell slope is unknown."
                if not np.isfinite(slope)
                else f"The target cell slope is {slope:.1f} deg, above the {request.slope_limit_deg:g} deg limit."
            )
            continue
        path = astar(
            heights,
            passable,
            site,
            cell,
            terrain.pixel_m,
            explored=search["explored"] if request.include_scene else None,
        )
        search["path"] = path
        if path is None:
            route["reason"] = "no_path"
            route["detail"] = (
                f"No route stays at or below {request.slope_limit_deg:g} deg on known terrain."
            )
            continue
        metrics = path_metrics(
            path, heights, slopes, terrain.pixel_m, request.speed_mps
        )
        stride = max(1, math.ceil(len(path) / (MAX_PATH_POINTS - 1)))
        kept = path[::stride]
        if kept[-1] != path[-1]:
            kept.append(path[-1])
        route.update(
            {
                "reachable": True,
                "length_m": metrics["length_m"],
                "horizontal_length_m": metrics["horizontal_length_m"],
                "duration_s": metrics["duration_s"],
                "max_slope_deg": metrics["max_slope_deg"],
                "elevation_gain_m": metrics["elevation_gain_m"],
                "detour_ratio": (
                    metrics["horizontal_length_m"] / route["straight_distance_m"]
                    if route["straight_distance_m"] > 0
                    else 1.0
                ),
                "path": [local(c) for c in kept],
            }
        )
        waypoints = []
        for index in sample_waypoints(len(path), request.waypoints_per_route):
            point_cell = path[index]
            point_xy = centre(point_cell)
            point_lat, point_lon = _coordinates(
                terrain, float(point_xy[0]), float(point_xy[1])
            )
            along = metrics["cumulative_m"][index]
            elapsed = along / request.speed_mps
            arrival = (
                _format_utc(start_time + timedelta(seconds=elapsed))
                if start_time
                else None
            )
            height = float(heights[point_cell])
            point = {
                "index": index,
                "distance_along_m": along,
                "elapsed_s": elapsed,
                "arrival_utc": arrival,
                **local(point_cell),
                "latitude_deg": point_lat,
                "longitude_deg_east": point_lon,
                "terrain_height_m": height,
                "sun": None,
                "earth": None,
            }
            if sky:
                profile = horizon_at(
                    point_lat, point_lon, height + request.observer_height_m
                )
                point["sun"] = _sky_state(frames, arrival, "sun", profile)
                point["earth"] = _sky_state(frames, arrival, "earth", profile)
            waypoints.append(point)
        route["waypoints"] = waypoints
        if sky:
            route["shadowed_waypoints"] = sum(
                p["sun"]["state"] == "dark" for p in waypoints
            )
            route["no_earth_waypoints"] = sum(
                p["earth"]["state"] == "blocked" for p in waypoints
            )
            route["unknown_sky_waypoints"] = sum(
                p["sun"]["state"] == "unknown" or p["earth"]["state"] == "unknown"
                for p in waypoints
            )

    reachable = [r for r in routes if r["reachable"]]
    reachable_fraction = len(reachable) / len(routes)
    detour_efficiency = (
        float(np.mean([1 / r["detour_ratio"] for r in reachable])) if reachable else 0.0
    )
    sunlit_fraction = None
    if sky:
        known = [
            p["sun"]["state"]
            for r in reachable
            for p in r["waypoints"]
            if p["sun"]["state"] in ("full", "partial", "dark")
        ]
        if known:
            sunlit_fraction = sum(s != "dark" for s in known) / len(known)
    weights = (
        {"reachable": 0.5, "detour": 0.25, "sunlit": 0.25}
        if sunlit_fraction is not None
        else {"reachable": 2 / 3, "detour": 1 / 3, "sunlit": 0.0}
    )
    score = (
        weights["reachable"] * reachable_fraction
        + weights["detour"] * detour_efficiency
        + weights["sunlit"] * (sunlit_fraction or 0.0)
    )
    output = {
        "site": {
            "site_id": request.site_id,
            "latitude_deg": request.latitude_deg,
            "longitude_deg_east": request.longitude_deg_east,
            "terrain_height_m": float(heights[site]),
        },
        "settings": request.model_dump(mode="json"),
        "sky_evaluated": sky,
        "departure_utc": _format_utc(start_time) if start_time else None,
        "routes": routes,
        "efficiency": {
            "score": score,
            "reachable_fraction": reachable_fraction,
            "detour_efficiency": detour_efficiency,
            "sunlit_fraction": sunlit_fraction,
            "weights": weights,
        },
        "assumptions": list(ASSUMPTIONS),
        "scene": (
            _scene(request, terrain, heights, passable, site, searches)
            if request.include_scene
            else None
        ),
    }
    identity = json.dumps(output, sort_keys=True, allow_nan=False).encode()
    output["result_id"] = hashlib.sha256(identity).hexdigest()
    return output


def _surface_distance_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance on the reference sphere (m)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    cos_angle = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return MOON_REFERENCE_RADIUS_M * math.acos(max(-1.0, min(1.0, cos_angle)))


def register_surface_routes(app, data_dir: Path, store) -> None:
    """POST /surface/routes: 409 without cached terrain, 404 unknown analysis."""

    @app.post("/surface/routes")
    def surface_routes(body: RouteRequest):
        frames = settings = None
        if body.analysis_job_id is not None:
            job = store.get(body.analysis_job_id)
            if job is None:
                raise HTTPException(404, "Unknown analysis job")
            if job["status"] not in ("ready", "insufficient_data") or not job["result"]:
                raise HTTPException(409, "The site analysis is not finished yet")
            submission = job["submission"]
            offset = _surface_distance_m(
                body.latitude_deg,
                body.longitude_deg_east,
                submission["latitude_deg"],
                submission["longitude_deg_east"],
            )
            if offset > MAX_ANALYSIS_OFFSET_M:
                raise HTTPException(
                    422,
                    f"The analysis site is {offset / 1000:.1f} km from the landing site; at most {MAX_ANALYSIS_OFFSET_M / 1000:g} km is allowed.",
                )
            frames = job["result"]["frames"]
            settings = HorizonSettings(**job["result"]["horizon"]["settings"])
        try:
            terrain = load_local_terrain(
                data_dir,
                body.latitude_deg,
                body.longitude_deg_east,
                max(t.distance_m for t in body.targets) + 3 * DEM_PIXEL_M,
            )
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        except AlternativesError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            if frames is None:
                return plan_routes(body, terrain)
            with rasterio.open(_cached_dem(data_dir)) as dataset:

                def horizon_at(latitude, longitude, altitude_m):
                    site = ObserverSite(
                        site_id="route-waypoint",
                        latitude_deg=float(latitude),
                        longitude_deg_east=float(longitude),
                        altitude_km=float(altitude_m) / 1000,
                    )
                    return compute_profile(dataset, site, settings)

                return plan_routes(body, terrain, frames, horizon_at)
        except (RouteError, HorizonError) as exc:
            raise HTTPException(422, str(exc)) from exc
