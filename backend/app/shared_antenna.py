"""H9 illustrative three-mission scheduling over saved visibility and R2 evidence."""

import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from shared.hybrid import (
    Contract,
    Scenario,
    TimeWindow,
    Site,
    Source,
    Mission,
    Candidate,
    Objective,
    Resource,
    Demand,
    Plan,
    fingerprint,
)
from .hybrid_planning import select_independent, validate_plan, solve_exact
from .hybrid_resources import StationCalendar, contact_from_r2, integrate_resources
from .hybrid_quantum import compile_qubo, compact_model
from .solver_arena import ArenaRequest, compare_scenario, _RUN_LOCK

_ROOT = Path(__file__).resolve().parents[2] / "shared/examples/campaign"
_MISSIONS = (("crew", 3), ("robot-a", 2), ("robot-b", 1))


class CampaignRequest(Contract):
    mode: Literal["standard", "deadline", "closed", "unknown"] = "standard"
    crew_option: Annotated[int, Field(strict=True, ge=0, le=4)] = 0
    depth: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    evaluations: Annotated[int, Field(strict=True, ge=1, le=256)] = 80
    shots: Annotated[int, Field(strict=True, ge=1, le=8192)] = 256
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)] = 17


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _window(value):
    return TimeWindow(start_utc=value["start_utc"], end_utc=value["end_utc"])


def load_campaign_evidence() -> dict:
    """Read the bounded bundled evidence and verify its canonical SHA-256."""
    record = json.loads((_ROOT / "evidence.json").read_text())
    if _hash(record["payload"]) != record["sha256"]:
        raise ValueError("Campaign evidence checksum mismatch")
    return record["payload"]


def _sun_seconds(rows, analysis_window, contact):
    windows = [_window(row) for row in rows]
    if (
        not windows
        or windows[0].start_utc != analysis_window.start_utc
        or windows[-1].end_utc != analysis_window.end_utc
        or any(a.end_utc != b.start_utc for a, b in zip(windows, windows[1:]))
    ):
        raise ValueError(
            "Sun timeline must cover the analysis without gaps or overlaps"
        )
    total = 0.0
    unknown = False
    for row, window in zip(rows, windows):
        if row["state"] not in ("full", "partial", "dark", "boundary", "unknown"):
            raise ValueError("Unsupported solar state")
        seconds = (window.end_utc - window.start_utc).total_seconds()
        if (
            not math.isfinite(row["duration_s"])
            or abs(row["duration_s"] - seconds) > 1e-6
        ):
            raise ValueError("Sun duration disagrees with UTC interval")
        overlap = max(
            0,
            (
                min(window.end_utc, contact.end_utc)
                - max(window.start_utc, contact.start_utc)
            ).total_seconds(),
        )
        if overlap and row["state"] == "unknown":
            unknown = True
        if row["state"] in ("full", "partial"):
            total += overlap
    return None if unknown else total


def _evidence_options(evidence):
    """Require exact job/site/time association before deriving any candidate."""
    try:
        if (
            evidence["version"] != "h9-evidence-v1"
            or evidence["station_id"] != "DSS-43"
        ):
            raise ValueError("Unsupported campaign evidence")
        snapshots = evidence["snapshots"]
        entries = {s["analysis"]["id"]: s for s in snapshots}
        options = evidence["alternatives"]
        if (
            len(entries) != len(snapshots)
            or len(options) != 4
            or sorted(o["index"] for o in options) != [1, 2, 3, 4]
        ):
            raise ValueError(
                "Require four unique indexed alternatives and unique analyses"
            )
        output = []
        for option in sorted(options, key=lambda item: item["index"]):
            entry = entries[option["analysis_id"]]
            analysis, communication = entry["analysis"], entry["communication"]
            if analysis["status"] not in (
                "ready",
                "insufficient_data",
            ) or communication["status"] not in ("ready", "insufficient_data"):
                raise ValueError("Evidence jobs must be complete")
            data, link = analysis["result"]["analysis"], communication["result"]
            submitted, calculated = analysis["submission"], data["request"]
            site = Site.model_validate(
                {
                    k: calculated["site"][k]
                    for k in (
                        "site_id",
                        "latitude_deg",
                        "longitude_deg_east",
                        "reference_frame",
                    )
                }
            )
            if any(
                submitted[k] != getattr(site, k)
                for k in ("site_id", "latitude_deg", "longitude_deg_east")
            ):
                raise ValueError("Analysis site differs from submitted site")
            span = _window(submitted)
            contact = _window(option["window"])
            if (
                span != _window(calculated)
                or span != _window(link)
                or communication["analysis_id"] != analysis["id"]
                or link["analysis_id"] != analysis["id"]
            ):
                raise ValueError(
                    "Communication is not bound to the same analysis and dates"
                )
            if (
                not span.start_utc
                <= contact.start_utc
                < contact.end_utc
                <= span.end_utc
            ):
                raise ValueError("Alternative lies outside its evidence interval")
            sunlight = _sun_seconds(data["sun"]["intervals"], span, contact)
            source = Source(
                source_id=f"h9-evidence-{option['index']}",
                kind="derived",
                version="h9-evidence-projection-v1",
                parent_source_ids=(f"h9-snapshot-{option['index']}",),
                description="Saved terrain-relative Sun/Earth analysis and R2 DSN geometry; not antenna booking or landing certification.",
                analysis_id=analysis["id"],
                sha256=_hash(entry),
                locator=f"shared/examples/campaign/evidence.json#alternative-{option['index']}",
            )
            # H3 validates coverage, state vocabulary and contiguous R2 intervals.
            checked_contact = contact_from_r2(
                link["windows"],
                requirement_id="check",
                option_id="check",
                station_id=evidence["station_id"],
                window=contact,
                source=source,
            )
            output.append(
                dict(
                    index=option["index"],
                    site=site,
                    window=contact,
                    contact_state=(
                        "unavailable"
                        if any(
                            r.state == "unavailable"
                            and r.window.start_utc < contact.end_utc
                            and r.window.end_utc > contact.start_utc
                            for r in checked_contact.geometry
                        )
                        else "unknown"
                        if any(
                            r.state == "unknown"
                            and r.window.start_utc < contact.end_utc
                            and r.window.end_utc > contact.start_utc
                            for r in checked_contact.geometry
                        )
                        else "available"
                    ),
                    sunlight_s=sunlight,
                    source=source,
                    communication=link,
                )
            )
        return output
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Malformed or missing campaign evidence") from exc


def build_campaign(request: CampaignRequest, evidence: dict) -> Scenario:
    """Build 12 bounded alternatives; preserve geometry and explicit resource assumptions."""
    request = CampaignRequest.model_validate(request.model_dump())
    options = _evidence_options(evidence)
    horizon = TimeWindow(
        start_utc=min(o["window"].start_utc for o in options),
        end_utc=max(o["window"].end_utc for o in options),
    )
    assumption = Source(
        source_id="h9-assumptions",
        kind="assumption",
        description="Illustrative Artemis-style crew and two CLPS-class robots. One channel, 30-minute example contacts; priority weights crew=3, robot-a=2, robot-b=1 times preference 4/3/2/1. No real mission specifications or DSN bookings. Dark contacts do not imply adequate energy. "
        + json.dumps(
            {"mode": request.mode, "crew_option": request.crew_option}, sort_keys=True
        ),
        version="h9-v1",
    )
    deadline = (
        min(o["window"].end_utc for o in options)
        if request.mode == "deadline"
        else horizon.end_utc
    )
    candidates, contacts = [], []
    pin = Resource(
        resource_id="h9-crew-pin",
        mode="concurrent",
        unit="channel",
        capacity=0,
        availability=(horizon,),
        source_ids=(assumption.source_id,),
    )
    for mission_id, priority in _MISSIONS:
        for option in options:
            oid = f"{mission_id}-{option['index']}"
            demands = ()
            if (
                mission_id == "crew"
                and request.crew_option
                and request.crew_option != option["index"]
            ):
                demands = (
                    Demand(
                        resource_id=pin.resource_id,
                        amount=1,
                        unit="channel",
                        window=option["window"],
                    ),
                )
            candidates.append(
                Candidate(
                    option_id=oid,
                    mission_id=mission_id,
                    site=option["site"],
                    window=option["window"],
                    demands=demands,
                    metrics={
                        "preference": priority * (5 - option["index"]),
                        "sunlight_s": option["sunlight_s"],
                    },
                    source_ids=(assumption.source_id, option["source"].source_id),
                )
            )
            contacts.append(
                contact_from_r2(
                    option["communication"]["windows"],
                    requirement_id=oid,
                    option_id=oid,
                    station_id=evidence["station_id"],
                    window=option["window"],
                    source=option["source"],
                )
            )
    scenario = Scenario(
        scenario_id="h9-shared-antenna",
        revision=0,
        seed=17,
        description="Illustrative three-mission DSS-43 scheduling over source-backed visibility; not a flight or energy feasibility certificate.",
        window=horizon,
        missions=tuple(
            Mission(
                mission_id=mid,
                vehicle_id=mid,
                deadline_utc=deadline,
                source_ids=(assumption.source_id,),
            )
            for mid, _ in _MISSIONS
        ),
        candidates=tuple(candidates),
        resources=(pin,) if request.crew_option else (),
        objectives=(
            Objective(
                metric="preference",
                unit="score",
                direction="maximize",
                weight=1,
                scale=4,
            ),
            Objective(
                metric="sunlight_s",
                unit="s",
                direction="maximize",
                weight=0.1,
                scale=1800,
            ),
        ),
        sources=(
            assumption,
            *(
                Source(
                    source_id=f"h9-snapshot-{o['index']}",
                    kind="observed",
                    description="Archived analysis and R2 result documents, not raw physical observations.",
                    sha256=o["source"].sha256,
                    locator=o["source"].locator,
                )
                for o in options
            ),
            *(o["source"] for o in options),
        ),
    )
    calendar = StationCalendar(
        station_id=evidence["station_id"],
        capacity_channels=None
        if request.mode == "unknown"
        else 0
        if request.mode == "closed"
        else 1,
        availability=(horizon,),
        source=assumption,
    )
    return integrate_resources(scenario, stations=(calendar,), contacts=tuple(contacts))


def campaign_catalog() -> dict:
    """Expose bounded choices, measured solar durations and provenance without solving."""
    evidence = load_campaign_evidence()
    options = _evidence_options(evidence)
    return dict(
        illustrative=True,
        station_id=evidence["station_id"],
        missions=[dict(id=mid, priority=priority) for mid, priority in _MISSIONS],
        options=[
            dict(
                index=o["index"],
                site=o["site"].model_dump(mode="json"),
                window=o["window"].model_dump(mode="json"),
                sunlight_s=o["sunlight_s"],
                contact_state=o["contact_state"],
                source=o["source"].model_dump(mode="json"),
            )
            for o in options
        ],
    )


def campaign_timeline(scenario: Scenario, plan) -> dict:
    """Return independently validated occupancy using exact half-open UTC intervals."""
    if plan is None:
        return dict(rows=[], occupancy=[], validation=None)
    validation = validate_plan(plan, scenario)
    station = next(
        r for r in scenario.resources if r.resource_id.startswith("station-")
    )
    selected = [
        c for c in scenario.candidates if c.option_id in plan.selected_option_ids
    ]
    duration = (scenario.window.end_utc - scenario.window.start_utc).total_seconds()

    def position(window):
        return dict(
            left_percent=100
            * (window.start_utc - scenario.window.start_utc).total_seconds()
            / duration,
            width_percent=100
            * (window.end_utc - window.start_utc).total_seconds()
            / duration,
        )

    rows = [
        dict(
            mission_id=c.mission_id,
            option_id=c.option_id,
            site=c.site.model_dump(mode="json"),
            window=c.window.model_dump(mode="json"),
            **position(c.window),
        )
        for c in selected
    ]
    demands = [
        (c.option_id, d)
        for c in selected
        for d in c.demands
        if d.resource_id == station.resource_id
    ]
    cuts = sorted(
        {scenario.window.start_utc, scenario.window.end_utc}
        | {t for _, d in demands for t in (d.window.start_utc, d.window.end_utc)}
    )
    occupancy = []
    for start, end in zip(cuts, cuts[1:]):
        active = [
            (oid, d)
            for oid, d in demands
            if d.window.start_utc < end and d.window.end_utc > start
        ]
        needed = sum(d.amount for _, d in active)
        interval = TimeWindow(start_utc=start, end_utc=end)
        occupancy.append(
            dict(
                window=interval.model_dump(mode="json"),
                option_ids=[oid for oid, _ in active],
                required_channels=needed,
                capacity_channels=station.capacity,
                conflict=station.capacity is not None and needed > station.capacity,
                unknown=station.capacity is None and needed > 0,
                **position(interval),
            )
        )
    return dict(
        rows=rows, occupancy=occupancy, validation=validation.model_dump(mode="json")
    )


def compare_campaign(request: CampaignRequest) -> dict:
    """Compare independent choices and joint solvers; never call a provider."""
    scenario = build_campaign(request, load_campaign_evidence())
    independent = select_independent(scenario).model_dump(mode="json")
    plan = Plan.model_validate(independent["plan"]) if independent["plan"] else None
    if plan and request.crew_option:
        raw = plan.model_dump(mode="python")
        raw["selected_option_ids"] = tuple(
            oid for oid in plan.selected_option_ids if not oid.startswith("crew-")
        ) + (f"crew-{request.crew_option}",)
        plan = Plan.model_validate(raw)
        independent.update(
            plan=plan.model_dump(mode="json"),
            validation=validate_plan(plan, scenario).model_dump(mode="json"),
        )
    comparison = compare_scenario(
        scenario,
        ArenaRequest(
            **{
                k: getattr(request, k)
                for k in ("depth", "evaluations", "shots", "seed")
            }
        ),
    )
    comparison["request"]["preset_id"] = "h9"
    classical = comparison["classical"]["result"]["plan"]
    quantum = comparison["quantum"]["result"]
    quantum_plan = quantum["best_sampled_plan"] if quantum else None
    return dict(
        campaign_request=request.model_dump(mode="json"),
        comparison=comparison,
        independent=independent,
        window=scenario.window.model_dump(mode="json"),
        timelines={
            "independent": campaign_timeline(scenario, plan),
            "classical": campaign_timeline(
                scenario, Plan.model_validate(classical) if classical else None
            ),
            "quantum": campaign_timeline(
                scenario, Plan.model_validate(quantum_plan) if quantum_plan else None
            ),
        },
    )


def campaign_hardware() -> dict:
    """Read optional sanitized H9 counts, bind identity, and revalidate every outcome."""
    path = _ROOT / "hardware.json"
    if not path.exists():
        return dict(status="not_run", new_jobs_submitted=0)
    record = json.loads(path.read_text())
    scenario = build_campaign(CampaignRequest(), load_campaign_evidence())
    if (
        record["input_fingerprint"] != fingerprint(scenario)
        or record["execution_mode"] != "hardware"
    ):
        raise ValueError("Hardware evidence does not match the default H9 scenario")
    model = compact_model(compile_qubo(scenario), max_qubits=6)
    counts, shots = record["counts"], record["shots"]
    keys = {
        format(i, f"0{model.logical_qubits}b") for i in range(len(model.one_hot_masks))
    }
    if (
        type(shots) is not int
        or shots < 1
        or not isinstance(counts, dict)
        or not set(counts) <= keys
        or any(type(n) is not int or n < 0 for n in counts.values())
        or sum(counts.values()) != shots
    ):
        raise ValueError("Invalid H9 hardware counts")
    exact = solve_exact(scenario)
    if exact.status != "optimal":
        raise ValueError("Hardware evidence lacks an exact reference")
    best = best_score = None
    feasible = optimal = 0
    for i, mask in enumerate(model.one_hot_masks):
        count = counts.get(format(i, f"0{model.logical_qubits}b"), 0)
        plan = Plan(
            plan_id=f"h9-hardware-{i}",
            scenario_id=scenario.scenario_id,
            scenario_revision=scenario.revision,
            input_fingerprint=fingerprint(scenario),
            state_revisions={},
            selected_option_ids=tuple(
                oid
                for bit, oid in enumerate(model.qubo.option_ids)
                if mask & (1 << bit)
            ),
            execution_mode="hardware",
            solver_name="h9-recorded-qaoa",
        )
        check = validate_plan(plan, scenario)
        if check.status == "feasible":
            score = Fraction(check.exact_score)
            feasible += count
            optimal += count if score == Fraction(exact.validation.exact_score) else 0
            if count and (best_score is None or score > best_score):
                best, best_score = plan, score
    return dict(
        **record,
        status="completed",
        new_jobs_submitted=0,
        sampled_feasible_rate=feasible / shots,
        sampled_optimal_rate=optimal / shots,
        best_plan=best.model_dump(mode="json") if best else None,
        optimum_gap=float(Fraction(exact.validation.exact_score) - best_score)
        if best
        else None,
        timeline=campaign_timeline(scenario, best),
    )


def register_campaign_routes(app):
    """Attach local routes sharing H8 admission control; no live execution route."""
    from fastapi import HTTPException

    @app.get("/campaign/catalog", tags=["campaign"])
    def catalog():
        try:
            return campaign_catalog()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/campaign/compare", tags=["campaign"])
    def compare(request: CampaignRequest):
        if not _RUN_LOCK.acquire(blocking=False):
            raise HTTPException(429, "Solver is busy; retry after the current run")
        try:
            return compare_campaign(request)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            _RUN_LOCK.release()

    @app.get("/campaign/hardware", tags=["campaign"])
    def hardware():
        if not _RUN_LOCK.acquire(blocking=False):
            raise HTTPException(429, "Solver is busy; retry after the current run")
        try:
            return campaign_hardware()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            _RUN_LOCK.release()
