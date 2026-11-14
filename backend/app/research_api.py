"""Read-only H10/H11 evidence and bounded local relay comparison; no cloud jobs."""

from pathlib import Path
import hashlib
import json
import math
from app.relay_orbits import RelayRequest, compare_relays
from app.solver_arena import _RUN_LOCK

REPORT_ROOT = Path(__file__).resolve().parents[2] / "shared/examples"


def read_report(name, root=None):
    """Read a fixed checksummed report; never accept arbitrary paths from HTTP."""
    if name not in ("scaling", "relays"):
        raise ValueError("Unknown research report")
    try:
        envelope = json.loads(
            ((root or REPORT_ROOT) / name / "report.json").read_text()
        )
        payload = envelope["payload"]
        digest = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        if digest != envelope["sha256"]:
            raise ValueError("Research report checksum mismatch")
        return payload
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Research report missing or malformed") from exc


def scaling_view(report):
    """Prepare log-scaled chart coordinates in Python; preserve raw measurements."""
    definitions = (
        (
            "runtime_s",
            [
                (
                    "exact",
                    [
                        (
                            p["missions"],
                            p["exact_median_s"],
                            "measured"
                            if all(r["complete"] for r in p["exact_runs"])
                            else "budget_limited",
                            p["options"],
                        )
                        for p in report["points"]
                    ],
                ),
                (
                    "milp",
                    [
                        (p["missions"], p["milp_median_s"], "measured", p["options"])
                        for p in report["points"]
                    ],
                ),
                (
                    "projection",
                    [
                        (
                            p["missions"],
                            p["estimated_exact_s"],
                            "extrapolated",
                            p["options"],
                        )
                        for p in report["projections"]
                    ],
                ),
            ],
        ),
        (
            "combinations",
            [
                (
                    "count",
                    [
                        (
                            p["missions"],
                            int(p["combinations"]),
                            "measured",
                            p["options"],
                        )
                        for p in report["points"]
                    ],
                )
            ],
        ),
        (
            "logical_qubits",
            [
                (
                    "qubits",
                    [
                        (p["missions"], p["logical_qubits"], "measured", p["options"])
                        for p in report["points"]
                    ],
                )
            ],
        ),
    )
    plots = []
    for label, series in definitions:
        all_points = [p for _, ps in series for p in ps]
        if not all_points:
            continue
        xmax = max(p[0] for p in all_points)
        logs = [math.log10(max(p[1], 1e-9)) for p in all_points]
        lo, hi = min(logs), max(logs)
        out = []
        for name, ps in series:
            # Separate option counts so graph lines never join different problems.
            for options in sorted({p[3] for p in ps}):
                points = [
                    dict(
                        x=100 * m / max(xmax, 1),
                        y=100
                        * (1 - (math.log10(max(v, 1e-9)) - lo) / max(hi - lo, 1e-9)),
                        missions=m,
                        value=v,
                        kind=kind,
                        options=o,
                    )
                    for m, v, kind, o in ps
                    if o == options
                ]
                out.append(dict(name=name, options=options, points=points))
        plots.append(
            dict(
                metric=label,
                axis="log10",
                minimum_log10=lo,
                maximum_log10=hi,
                series=out,
            )
        )
    return {**report, "plots": plots}


def register_research_routes(app):
    """Attach fixed read-only evidence and bounded H8-backed local comparison."""
    from fastapi import HTTPException

    @app.get("/research/scaling", tags=["research"])
    def scaling_report():
        try:
            return scaling_view(read_report("scaling"))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/research/relays", tags=["research"])
    def relay_report():
        try:
            return read_report("relays")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/research/relays/compare", tags=["research"])
    def relay_compare(request: RelayRequest):
        if not _RUN_LOCK.acquire(blocking=False):
            raise HTTPException(429, "Solver is busy; retry after the current run")
        try:
            return compare_relays(read_report("relays")["geometry"], request)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            _RUN_LOCK.release()
