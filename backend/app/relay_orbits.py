"""H11 CR3BP propagation and independently validated relay selection.

Dynamics: https://ssd.jpl.nasa.gov/tools/periodic_orbits.html.
No date-specific MOON_ME/SPICE conversion or radio-link budget is implied.
"""

import hashlib
import json
import math
from datetime import datetime, timedelta
from itertools import product

import numpy as np
from typing import Annotated
from pydantic import Field
from shared.hybrid import Contract, Scenario
from shared.horizon_geometry import interpolate_horizon
from app.solver_arena import ArenaRequest, compare_scenario


class RelayRequest(Contract):
    relay_count: Annotated[int, Field(strict=True, ge=1, le=2)] = 2
    minimum_coverage: Annotated[float, Field(strict=True, ge=0, le=1)] = 0.8
    maximum_gap_s: Annotated[float, Field(strict=True, ge=0, le=691200)] = 86400.0
    minimum_contact_s: Annotated[float, Field(strict=True, ge=0, le=691200)] = 3600.0
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17


def cr3bp_rhs(time_tu, state, mass_ratio):
    """Six-state nondimensional synodic derivative; reject primary singularities."""
    s, mu = _state(state, mass_ratio)
    x, y, z, vx, vy, vz = s
    r1 = np.linalg.norm([x + mu, y, z])
    r2 = np.linalg.norm([x - 1 + mu, y, z])
    a = (1 - mu) / r1**3
    b = mu / r2**3
    return np.array(
        [
            vx,
            vy,
            vz,
            2 * vy + x - a * (x + mu) - b * (x - 1 + mu),
            -2 * vx + y - (a + b) * y,
            -(a + b) * z,
        ]
    )


def jacobi_constant(state, mass_ratio):
    """Dimensionless Jacobi invariant from barycentric rotating state."""
    s, mu = _state(state, mass_ratio)
    x, y, z = s[:3]
    return float(
        x * x
        + y * y
        + 2 * (1 - mu) / np.linalg.norm([x + mu, y, z])
        + 2 * mu / np.linalg.norm([x - 1 + mu, y, z])
        - np.dot(s[3:], s[3:])
    )


def propagate_orbit(orbit, system, *, samples=1001, rtol=1e-11, atol=1e-13):
    """Integrate one catalog period with optional SciPy DOP853 and audit invariants."""
    from scipy.integrate import solve_ivp

    if type(samples) is not int or not 2 <= samples <= 10001:
        raise ValueError("samples must be an integer in 2..10001")
    if not (
        math.isfinite(rtol)
        and math.isfinite(atol)
        and 1e-14 <= rtol <= 1e-7
        and 1e-16 <= atol <= 1e-9
    ):
        raise ValueError("Integrator tolerances outside audited bounds")
    mu = system["mass_ratio"]
    state, _ = _state(orbit["state_nd"], mu)
    period = orbit["period_tu"]
    if not math.isfinite(period) or not 0 < period <= 10:
        raise ValueError("Catalog period must be in (0,10] TU")
    times = np.linspace(0, period, samples)
    solution = solve_ivp(
        lambda t, y: cr3bp_rhs(t, y, mu),
        (0, period),
        state,
        method="DOP853",
        t_eval=times,
        rtol=rtol,
        atol=atol,
        max_step=period / 200,
    )
    if not solution.success or solution.y.shape != (6, samples):
        raise ValueError("Orbit integration failed")
    states = solution.y.T
    drift = max(abs(jacobi_constant(y, mu) - orbit["jacobi"]) for y in states)
    closure = float(np.linalg.norm(states[-1] - state))
    radius = float(
        np.min(np.linalg.norm(states[:, :3] - [1 - mu, 0, 0], axis=1)) * system["lunit"]
    )
    if drift > 1e-7 or closure > 2e-5 or radius <= system["radius_secondary"]:
        raise ValueError(
            "Orbit failed Jacobi, closure or sampled surface-clearance reference"
        )
    return dict(
        orbit_id=orbit["orbit_id"],
        times_tu=times.tolist(),
        states_nd=states.tolist(),
        closure_error_nd=closure,
        jacobi_drift=float(drift),
        minimum_moon_radius_km=radius,
        method="DOP853",
        rtol=rtol,
        atol=atol,
        period_tu=period,
    )


def segment_clear(start, end, center, radius):
    """Strict sphere clearance for a finite segment, all coordinates in one unit."""
    a, b, c = (np.asarray(v, dtype=float) for v in (start, end, center))
    if (
        any(v.shape != (3,) or not np.all(np.isfinite(v)) for v in (a, b, c))
        or not math.isfinite(radius)
        or radius <= 0
    ):
        raise ValueError("Finite 3D segment and positive radius required")
    d = b - a
    length2 = float(np.dot(d, d))
    if length2 == 0:
        raise ValueError("Zero-length segment")
    u = float(np.clip(np.dot(c - a, d) / length2, 0, 1))
    return bool(np.linalg.norm(a + u * d - c) > radius)


def contact_geometry(position_nd, site, system, horizon):
    """Ideal synchronous Moon: explicit pi rotation, terrain and Moon occultation."""
    pos = np.asarray(position_nd, dtype=float)
    if pos.shape != (3,) or not np.all(np.isfinite(pos)):
        raise ValueError("Finite synodic position required")
    mu = system["mass_ratio"]
    length = system["lunit"]
    radius = system["radius_secondary"]
    if not 0 < mu < 0.5 or not math.isfinite(length) or length <= 0 or radius <= 0:
        raise ValueError("Invalid CR3BP system")
    lat, lon = site["latitude_deg"], site["longitude_deg_east"]
    if not math.isfinite(lat) or not -90 <= lat <= 90 or not math.isfinite(lon):
        raise ValueError("Invalid site angles")
    lat, lon = math.radians(lat), math.radians(lon)
    # Declared ideal synchronous orientation: local prime meridian points to Earth.
    rotation = np.diag([-1.0, -1.0, 1.0])
    up = rotation @ np.array(
        [math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)]
    )
    north = rotation @ np.array(
        [-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon), math.cos(lat)]
    )
    east = rotation @ np.array([-math.sin(lon), math.cos(lon), 0])
    moon = np.array([1 - mu, 0.0, 0.0])
    observer = moon + up * (radius + site["altitude_km"]) / length
    delta = pos - observer
    if np.linalg.norm(delta) == 0:
        raise ValueError("Relay coincides with observer")
    n, e, u = float(delta @ north), float(delta @ east), float(delta @ up)
    elevation = math.degrees(math.atan2(u, math.hypot(n, e)))
    azimuth = math.degrees(math.atan2(e, n)) % 360
    terrain = interpolate_horizon(horizon, azimuth)
    clear = segment_clear(pos, [-mu, 0, 0], moon, radius / length)
    # Strict boundary rule matches R2: equality is not an available link.
    state = (
        "unknown"
        if terrain is None
        else ("available" if elevation > terrain and clear else "unavailable")
    )
    return dict(
        state=state,
        elevation_deg=elevation,
        azimuth_deg=azimuth,
        horizon_deg=terrain,
        earth_center_clear=clear,
        range_km=float(np.linalg.norm(delta) * length),
    )


def validate_selection(evidence, orbit_ids, request):
    """Check distinct k relays, sampled union coverage, gaps and each site demand."""
    request = RelayRequest.model_validate(request.model_dump())
    _check_evidence(evidence)
    known = {o["orbit_id"] for o in evidence["orbits"]}
    if any(o not in known for o in orbit_ids):
        raise ValueError("Unknown relay orbit")
    issues = []
    if len(orbit_ids) != request.relay_count or len(set(orbit_ids)) != len(orbit_ids):
        issues.append("Select exactly k distinct relay orbits")
    dt = np.diff(evidence["times_s"])
    duration = float(sum(dt))
    sites = []
    incomplete = False
    for site in evidence["sites"]:
        sid = site["site_id"]
        rows = evidence["contacts"][sid]
        union = []
        for i in range(len(dt)):
            values = [rows[o][i] for o in orbit_ids]
            union.append(
                True
                if any(v is True for v in values)
                else None
                if any(v is None for v in values)
                else False
            )
        covered = sum(float(d) for d, v in zip(dt, union) if v is True)
        unknown = sum(float(d) for d, v in zip(dt, union) if v is None)
        gap = maximum = 0.0
        for d, v in zip(dt, union):
            gap = 0.0 if v is True else gap + float(d)
            maximum = max(maximum, gap)
        fraction = covered / duration
        if unknown:
            incomplete = True
            issues.append(sid + ": unknown contact intervals")
        if fraction + 1e-12 < request.minimum_coverage:
            issues.append(sid + ": coverage below requirement")
        if maximum > request.maximum_gap_s + 1e-9:
            issues.append(sid + ": communication gap exceeds requirement")
        if covered + 1e-9 < request.minimum_contact_s:
            issues.append(sid + ": contact duration below mission demand")
        sites.append(
            dict(
                site_id=sid,
                covered_s=covered,
                coverage_fraction=fraction,
                maximum_gap_s=maximum,
                unknown_s=unknown,
            )
        )
    return dict(
        status="incomplete" if incomplete else "infeasible" if issues else "feasible",
        orbit_ids=list(orbit_ids),
        sites=sites,
        issues=issues,
    )


def build_relay_scenario(evidence, request):
    """Encode k=1/2 distinct choices with independently determined pair exclusions."""
    request = RelayRequest.model_validate(request.model_dump())
    _check_evidence(evidence)
    digest = hashlib.sha256(
        json.dumps(
            evidence, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    start = datetime.fromisoformat(evidence["epoch_label_utc"].replace("Z", "+00:00"))
    window = dict(
        start_utc=start, end_utc=start + timedelta(seconds=evidence["times_s"][-1])
    )
    source_ids = ["relay-evidence", "relay-assumptions"]
    candidates = []
    missions = []
    exclusions = []
    resources = []
    unknown = any(
        v is None
        for rows in evidence["contacts"].values()
        for row in rows.values()
        for v in row
    )
    resources.append(
        dict(
            resource_id="known-geometry",
            mode="concurrent",
            unit="channel",
            capacity=None if unknown else float(request.relay_count),
            availability=[window],
            source_ids=source_ids,
        )
    )
    for slot in range(request.relay_count):
        mid = f"relay-{slot}"
        missions.append(
            dict(
                mission_id=mid,
                vehicle_id=mid,
                deadline_utc=window["end_utc"],
                source_ids=source_ids,
            )
        )
        for orbit in evidence["orbits"]:
            oid = orbit["orbit_id"]
            demands = [
                dict(
                    resource_id="known-geometry",
                    amount=1.0,
                    unit="channel",
                    window=window,
                )
            ]
            if (
                request.relay_count == 1
                and validate_selection(evidence, (oid,), request)["status"]
                != "feasible"
            ):
                rid = "blocked-" + oid
                resources.append(
                    dict(
                        resource_id=rid,
                        mode="concurrent",
                        unit="channel",
                        capacity=0.0,
                        availability=[window],
                        source_ids=source_ids,
                    )
                )
                demands.append(
                    dict(resource_id=rid, amount=1.0, unit="channel", window=window)
                )
            candidates.append(
                dict(
                    option_id=mid + ":" + oid,
                    mission_id=mid,
                    site={
                        k: evidence["sites"][0][k]
                        for k in ("site_id", "latitude_deg", "longitude_deg_east")
                    },
                    window=window,
                    demands=demands,
                    metrics={"cost": float(orbit["cost_score"])},
                    source_ids=source_ids,
                )
            )
    if request.relay_count == 2:
        for a, b in product(evidence["orbits"], repeat=2):
            ids = (a["orbit_id"], b["orbit_id"])
            if validate_selection(evidence, ids, request)["status"] != "feasible":
                exclusions.append(
                    dict(
                        constraint_id=f"pair-{len(exclusions)}",
                        option_ids=["relay-0:" + ids[0], "relay-1:" + ids[1]],
                    )
                )
    return Scenario.model_validate(
        dict(
            scenario_id="h11-relay-selection",
            revision=1,
            seed=request.seed,
            description="Illustrative k=1/2 relay selection; ideal synchronous Moon and sampled contact geometry.",
            window=window,
            missions=missions,
            candidates=candidates,
            resources=resources,
            incompatibilities=exclusions,
            objectives=[
                dict(
                    metric="cost",
                    unit="score",
                    direction="minimize",
                    weight=1.0,
                    scale=1.0,
                )
            ],
            sources=[
                dict(
                    source_id="relay-evidence",
                    kind="observed",
                    description="Archived JPL propagation and terrain geometry snapshot",
                    sha256=digest,
                    version="h11-geometry-v1",
                    locator="shared/examples/relays/report.json",
                ),
                dict(
                    source_id="relay-assumptions",
                    kind="assumption",
                    description=json.dumps(request.model_dump(), sort_keys=True)
                    + "; cost_score is an illustrative preference, not fuel or money",
                ),
            ],
        )
    )


def compare_relays(evidence, request):
    """Use H8 exact/QAOA pair and recheck selected sets against original coverage."""
    scenario = build_relay_scenario(evidence, request)
    comparison = compare_scenario(
        scenario, ArenaRequest(depth=1, evaluations=80, shots=256, seed=request.seed)
    )
    reports = {}
    for name, plan in [
        ("classical", comparison["classical"]["result"]["plan"]),
        (
            "quantum",
            comparison["quantum"]["result"]["best_sampled_plan"]
            if comparison["quantum"]["result"]
            else None,
        ),
    ]:
        report = (
            validate_selection(
                evidence,
                tuple(oid.split(":", 1)[1] for oid in plan["selected_option_ids"]),
                request,
            )
            if plan
            else None
        )
        if report and report["status"] != "feasible":
            raise ValueError("Solver output failed original relay coverage constraints")
        reports[name + "_validation"] = report
    return dict(
        request=request.model_dump(mode="json"), comparison=comparison, **reports
    )


def _state(state, mu):
    s = np.asarray(state, dtype=float)
    if (
        s.shape != (6,)
        or not np.all(np.isfinite(s))
        or not math.isfinite(mu)
        or not 0 < mu < 0.5
    ):
        raise ValueError("Finite six-state vector and mass ratio in (0,0.5) required")
    if (
        min(np.linalg.norm(s[:3] - [-mu, 0, 0]), np.linalg.norm(s[:3] - [1 - mu, 0, 0]))
        < 1e-12
    ):
        raise ValueError("CR3BP primary singularity")
    return s, mu


def _check_evidence(evidence):
    times = np.asarray(evidence["times_s"], dtype=float)
    if (
        times.ndim != 1
        or not 2 <= len(times) <= 2305
        or not np.all(np.isfinite(times))
        or times[0] != 0
        or times[-1] > 691200
        or np.any(np.diff(times) <= 0)
    ):
        raise ValueError(
            "Strictly increasing elapsed seconds from zero, bounded to eight days, required"
        )
    orbits = evidence["orbits"]
    ids = [o["orbit_id"] for o in orbits]
    if not 2 <= len(ids) <= 4 or len(set(ids)) != len(ids):
        raise ValueError("Two to four distinct orbit candidates required")
    for orbit in orbits:
        if not math.isfinite(orbit["cost_score"]) or orbit["cost_score"] <= 0:
            raise ValueError("Positive finite declared orbit preference required")
    sites = evidence["sites"]
    site_ids = [s["site_id"] for s in sites]
    if not 1 <= len(sites) <= 3 or len(set(site_ids)) != len(sites):
        raise ValueError("One to three distinct sites required")
    for sid in site_ids:
        rows = evidence["contacts"][sid]
        if set(rows) != set(ids) or any(
            len(v) != len(times) - 1
            or any(x is not None and type(x) is not bool for x in v)
            for v in rows.values()
        ):
            raise ValueError("Complete boolean-or-unknown contact grid required")
