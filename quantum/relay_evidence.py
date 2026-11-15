"""Offline H11 evidence generation; optional scientific environment, no provider SDK."""

import hashlib
import json
from copy import deepcopy
from itertools import combinations

import numpy as np

from app.relay_orbits import (
    RelayRequest,
    propagate_orbit,
    contact_geometry,
    validate_selection,
    compare_relays,
)
from shared.constants import MOON_REFERENCE_RADIUS_KM


def build_geometry(inputs, *, duration_s=691200, step_s=600):
    """Propagate catalog candidates and sample two-leg contacts, no downloads.

    Both endpoints and midpoint must be clear for an interval to count. This is
    a sampled conservative rule, not a continuous-time visibility certificate.
    The catalog period is replayed modulo elapsed dynamical seconds, with a UTC
    label for H0 interoperability only (no date-specific ephemeris conversion).
    """
    if (
        type(duration_s) is not int
        or type(step_s) is not int
        or not 300 <= step_s <= 3600
        or not 0 < duration_s <= 691200
        or duration_s % step_s
        or duration_s // step_s > 2304
    ):
        raise ValueError("Use a divisible 300..3600 s grid over at most eight days")
    if inputs["signature"]["version"] != "1.0":
        raise ValueError("Unrecognized JPL catalog version")
    inputs = deepcopy(inputs)
    source_digest = hashlib.sha256(
        json.dumps(
            inputs, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    system = inputs["system"]
    mu = system["mass_ratio"]
    times = np.arange(0, duration_s + step_s, step_s, dtype=float)
    half_times = np.arange(0, duration_s + step_s / 2, step_s / 2, dtype=float)
    sites = []
    contacts = {}
    direct = {}
    audits = []
    orbits = []
    for row in inputs["sites"]:
        site = row["site"]
        # Terrain heights refer to 1737.4 km, catalog sphere to 1737.1 km.
        site["altitude_km"] += MOON_REFERENCE_RADIUS_KM - system["radius_secondary"]
        sid = site["site_id"]
        sites.append(
            {k: site[k] for k in ("site_id", "latitude_deg", "longitude_deg_east")}
        )
        contacts[sid] = {}
        direct[sid] = contact_geometry([-mu, 0, 0], site, system, row)
    for index, orbit in enumerate(inputs["orbits"]):
        trajectory = propagate_orbit(
            orbit, system, samples=10001, rtol=1e-12, atol=1e-14
        )
        states = np.asarray(trajectory["states_nd"])
        ts = np.asarray(trajectory["times_tu"])
        query = (half_times / system["tunit"]) % orbit["period_tu"]
        positions = np.column_stack(
            [np.interp(query, ts, states[:, i]) for i in range(3)]
        )
        oid = orbit["orbit_id"]
        orbits.append(dict(orbit_id=oid, cost_score=float(index + 1)))
        # Retain a decimated trajectory for plotting, not propagation/revalidation.
        audits.append(
            {k: v for k, v in trajectory.items() if k not in ("times_tu", "states_nd")}
            | dict(
                period_days=orbit["period_tu"] * system["tunit"] / 86400,
                projection_xz_nd=states[::100][:, [0, 2]].tolist(),
            )
        )
        for row in inputs["sites"]:
            site = row["site"]
            sid = site["site_id"]
            samples = [
                contact_geometry(p, site, system, row)["state"] for p in positions
            ]
            result = []
            for i in range(len(times) - 1):
                values = samples[2 * i : 2 * i + 3]
                result.append(
                    None
                    if "unknown" in values
                    else all(v == "available" for v in values)
                )
            contacts[sid][oid] = result
    return dict(
        version="h11-geometry-v1",
        source_digest=source_digest,
        epoch_label_utc=inputs["epoch_label_utc"],
        times_s=times.tolist(),
        orbits=orbits,
        sites=sites,
        contacts=contacts,
        orbit_audits=audits,
        direct_earth_geometry=direct,
        step_s=step_s,
        orientation_assumption=inputs["orientation_assumption"],
    )


def build_report(inputs):
    """Record coarse/fine geometry, audits and local classical/QAOA evidence."""
    coarse = build_geometry(inputs, step_s=600)
    fine = build_geometry(inputs, step_s=300)
    changed = total = 0
    coverage_deltas = []
    for sid, rows in coarse["contacts"].items():
        for oid, values in rows.items():
            refined = fine["contacts"][sid][oid]
            coverage_deltas.append(
                dict(
                    site_id=sid,
                    orbit_id=oid,
                    fine_minus_coarse_covered_s=300 * sum(v is True for v in refined)
                    - 600 * sum(v is True for v in values),
                )
            )
            for i, value in enumerate(values):
                sub = refined[2 * i : 2 * i + 2]
                reduced = None if any(x is None for x in sub) else all(sub)
                changed += value != reduced
                total += 1
    all_xy = np.array(
        [xy for a in fine["orbit_audits"] for xy in a["projection_xz_nd"]]
    )
    center = (all_xy.min(axis=0) + all_xy.max(axis=0)) / 2
    span = max(np.ptp(all_xy, axis=0))
    audits = []
    for audit in fine["orbit_audits"]:
        a = dict(audit)
        xy = (np.array(a.pop("projection_xz_nd")) - center) / span * 80 + 50
        a["svg_points"] = " ".join(f"{x:.4f},{100 - y:.4f}" for x, y in xy)
        audits.append(a)
    request = RelayRequest()
    validation = [
        validate_selection(fine, (o["orbit_id"],), RelayRequest(relay_count=1))
        for o in fine["orbits"]
    ]
    pairs = [
        validate_selection(fine, ids, request)
        for ids in combinations([o["orbit_id"] for o in fine["orbits"]], 2)
    ]
    return dict(
        version="h11-report-v1",
        hardware_jobs=0,
        credits_spent=0,
        source_url=inputs["catalog_url"],
        catalog_sha256=inputs["catalog_sha256"],
        orientation_assumption=inputs["orientation_assumption"],
        interval_rule="Available only if endpoints and midpoint are clear; 300 s interval grid and periodic replay. Not continuous visibility certification.",
        geometry=fine,
        orbit_audits=audits,
        single_relay=validation,
        pair_validation=pairs,
        convergence=dict(
            changed_intervals=int(changed),
            interval_count=total,
            coarse_step_s=600,
            fine_step_s=300,
            coverage_deltas=coverage_deltas,
        ),
        rejected_orbits=inputs.get("rejected_orbits", []),
        reference_comparison=compare_relays(fine, request),
    )


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Rebuild local H11 evidence without network/provider calls"
    )
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(json.loads(args.inputs.read_text()))
    digest = hashlib.sha256(
        json.dumps(
            report, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    args.output.write_text(
        json.dumps(
            {"sha256": digest, "payload": report},
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    print("Wrote offline relay evidence; hardware_jobs=0, credits_spent=0")
