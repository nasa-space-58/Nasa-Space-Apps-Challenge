"""R3 explained site recommendations (S8) and priority weights (S9).

Candidates reuse the existing analysis (Sun/Earth) and R2 communication jobs and
the R3 terrain safety screen. Hard constraints reject candidates with recorded
reasons; uncertain inputs are reported as insufficient data, never silently
ranked. Rescoring reuses stored criterion scores without new computation.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analysis_api import ACTIVE as ANALYSIS_ACTIVE
from app.analysis_api import Submission
from app.communication import ACTIVE as COMMUNICATION_ACTIVE
from app.communication import MAX_ACTIVE_JOBS as MAX_COMMUNICATION_JOBS
from app.communication import CommunicationRequest
from app.terrain_safety import (
    SafetySettings,
    TerrainSafetyError,
    safety_score,
    terrain_safety,
)

CRITERIA = ("energy", "darkness", "communication", "terrain_safety")
NOT_MODELED = {
    "fuel": "Enable 'include descent' to add R4/R5 reachability and fuel.",
    "science": "No validated science-value dataset is integrated yet.",
}
MAX_ACTIVE_RECOMMENDATIONS = 2
MAX_ANALYSIS_JOBS = 8
MAX_DELAY_H = 720
FINISHED = ("ready", "partial", "failed")


class QueueFull(ValueError):
    """Too many active recommendations."""


class Weights(BaseModel):
    """Relative criterion importance; normalized by their sum (must be > 0)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    energy: float = Field(default=1.0, ge=0, le=10)
    darkness: float = Field(default=1.0, ge=0, le=10)
    communication: float = Field(default=1.0, ge=0, le=10)
    terrain_safety: float = Field(default=1.0, ge=0, le=10)
    # R5: used only when a recommendation includes descent fuel.
    fuel: float = Field(default=0.0, ge=0, le=10)

    @model_validator(mode="after")
    def require_positive_total(self) -> "Weights":
        if sum(getattr(self, c) for c in CRITERIA) + self.fuel <= 0:
            raise ValueError("At least one criterion weight must be positive")
        return self


class Constraints(SafetySettings):
    """Terrain screen plus optional minimum sunlight (%) and communication (h)."""

    min_sunlight_percent: float | None = Field(default=None, ge=0, le=100)
    min_communication_h: float | None = Field(default=None, ge=0, le=744)


class Candidate(BaseModel):
    """One candidate observer: MOON_ME degrees and height above terrain (m)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: str = Field(default="custom", min_length=1, max_length=80)
    latitude_deg: float = Field(ge=-90, le=-80)
    longitude_deg_east: float = Field(ge=0, lt=360)
    observer_height_m: float = Field(default=2.0, ge=0.1, le=100)


class RecommendationRequest(BaseModel):
    """2-6 candidates over one shared UTC window, settings and weights."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    candidates: list[Candidate] = Field(min_length=2, max_length=6)
    start_utc: datetime
    end_utc: datetime
    step_s: int = 3600
    refine: bool = True
    min_elevation_deg: float = Field(default=6.0, ge=6, le=90)
    constraints: Constraints = Field(default_factory=Constraints)
    weights: Weights = Field(default_factory=Weights)
    # R5: evaluate the R4 descent per candidate (reachability + fuel criterion).
    include_descent: bool = False
    descent_profile_id: str = "apollo-lm-11"

    @model_validator(mode="after")
    def validate_candidates(self) -> "RecommendationRequest":
        """Each candidate must form a valid analysis; positions must be distinct."""
        seen = set()
        for candidate in self.candidates:
            submission(self, candidate)
            key = (
                candidate.latitude_deg,
                candidate.longitude_deg_east,
                candidate.observer_height_m,
            )
            if key in seen:
                raise ValueError("Candidates must have distinct positions/heights")
            seen.add(key)
        return self


class RescheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    delay_hours: float = Field(gt=0, le=MAX_DELAY_H)


def submission(request: RecommendationRequest, candidate: Candidate) -> Submission:
    """The analysis submission for one candidate (raises on invalid inputs)."""
    return Submission(
        site_id=candidate.site_id,
        latitude_deg=candidate.latitude_deg,
        longitude_deg_east=candidate.longitude_deg_east,
        observer_height_m=candidate.observer_height_m,
        start_utc=request.start_utc,
        end_utc=request.end_utc,
        step_s=request.step_s,
        refine=request.refine,
    )


def candidate_ids(request: RecommendationRequest) -> list[str]:
    """Stable IDs c1..cN in request order."""
    return [f"c{index + 1}" for index in range(len(request.candidates))]


def _utc(value) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _window_s(analysis: dict) -> float:
    request = analysis["request"]
    return (_utc(request["end_utc"]) - _utc(request["start_utc"])).total_seconds()


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def criterion_scores(
    analysis: dict, communication: dict, safety: dict, constraints: Constraints
) -> dict:
    """Score each criterion 0..1 with its raw value, unit and rule."""
    sun = analysis["sun"]["metrics"]
    combined = communication["windows"]["combined"]["metrics"]
    window_s = _window_s(analysis)
    slope = safety.get("max_landing_slope_deg")
    return {
        "energy": {
            "score": _clamp(sun["visible_percent_of_interval"] / 100),
            "value": sun["visible_percent_of_interval"],
            "unit": "% of window with full or partial sunlight",
            "rule": "sunlight percentage / 100",
        },
        "darkness": {
            "score": _clamp(1 - sun["longest_blocked_s"] / window_s),
            "value": sun["longest_blocked_s"],
            "unit": "s longest continuous terrain-blocked sunlight",
            "rule": "1 - longest darkness / observation window",
        },
        "communication": {
            "score": _clamp(combined["available_percent_of_interval"] / 100),
            "value": combined["available_percent_of_interval"],
            "unit": "% of window with a geometric DSN direct-to-Earth path",
            "rule": "combined communication percentage / 100",
        },
        "terrain_safety": {
            "score": None if slope is None else safety_score(safety, constraints),
            "value": slope,
            "unit": "deg maximum slope in the landing area (80 m DEM)",
            "rule": "0.5 * (1 - min(slope / limit, 1)) + 0.5 * min(steep distance / search radius, 1)",
        },
    }


def descent_summary(plan: dict) -> dict:
    """Reachability and fuel metrics of an R4 plan for the recommendation.

    fuel_score = propellant margin / propellant available at PDI, clamped 0..1.
    """
    available = plan["profile"]["usable_propellant_kg"]
    used = plan["totals"]["propellant_used_kg"]
    margin = available - used
    return {
        "feasible": bool(plan["feasible"]),
        "limiting_factors": plan.get("limiting_factors", []),
        "detail": "; ".join(f["detail"] for f in plan.get("limiting_factors", [])),
        "propellant_used_kg": used,
        "usable_propellant_kg": available,
        "propellant_margin_kg": margin,
        "powered_delta_v_mps": plan["totals"]["powered_delta_v_mps"],
        "fuel_score": _clamp(margin / available) if available > 0 else 0.0,
    }


def descent_plan(data_dir, latitude_deg, longitude_deg_east, profile_id) -> dict:
    """R4 plan for one candidate using the cached terrain height (read-only)."""
    from app.descent import DescentRequest, plan_descent, terrain_height_m

    height = terrain_height_m(data_dir, latitude_deg, longitude_deg_east)
    return plan_descent(
        DescentRequest(
            latitude_deg=latitude_deg,
            longitude_deg_east=longitude_deg_east,
            profile_id=profile_id,
        ),
        height,
    )


def _failure(constraint, actual, limit, unit) -> dict:
    return {"constraint": constraint, "actual": actual, "limit": limit, "unit": unit}


def evaluate(
    candidate_id: str,
    analysis: dict,
    communication: dict,
    safety: dict,
    constraints: Constraints,
    descent: dict | None = None,
) -> dict:
    """Classify as ranked, rejected (with failures) or insufficient_data.

    A hard constraint rejects only when it fails for certain; when unknown
    durations could still satisfy it the candidate is insufficient_data.
    """
    failures, unknowns = [], []

    def unknown(name):
        if name not in unknowns:
            unknowns.append(name)

    if not safety.get("covered", False):
        failures.append(_failure("terrain_coverage", None, None, None))
    slope = safety.get("max_landing_slope_deg")
    if slope is not None and slope > constraints.slope_limit_deg:
        failures.append(_failure("slope", slope, constraints.slope_limit_deg, "deg"))
    if (
        safety.get("covered", False)
        and (safety.get("unknown_landing_fraction") or 0) > 0
    ):
        unknown("terrain")

    window_s = _window_s(analysis)
    sun = analysis["sun"]["metrics"]
    sun_pct = sun["visible_percent_of_interval"]
    sun_unknown_pct = 100 * sun["unknown_duration_s"] / window_s
    if sun["unknown_duration_s"] > 0:
        unknown("sunlight")
    if constraints.min_sunlight_percent is not None:
        limit = constraints.min_sunlight_percent
        if sun_pct + sun_unknown_pct < limit:
            failures.append(_failure("sunlight", sun_pct, limit, "%"))
        elif sun_pct < limit:
            unknown("sunlight")

    combined = communication["windows"]["combined"]["metrics"]
    available_h = combined["available_duration_s"] / 3600
    unknown_h = combined["unknown_duration_s"] / 3600
    if analysis["earth"]["metrics"]["unknown_duration_s"] > 0 or unknown_h > 0:
        unknown("communication")
    if constraints.min_communication_h is not None:
        limit = constraints.min_communication_h
        if available_h + unknown_h < limit:
            failures.append(_failure("communication", available_h, limit, "h"))
        elif available_h < limit:
            unknown("communication")

    if descent is not None and not descent["feasible"]:
        failures.append(
            {**_failure("descent", None, None, None), "detail": descent["detail"]}
        )

    status = "rejected" if failures else "insufficient_data" if unknowns else "ranked"
    criteria = (
        criterion_scores(analysis, communication, safety, constraints)
        if safety.get("covered", False)
        else {}
    )
    if criteria and descent is not None:
        criteria["fuel"] = {
            "score": descent["fuel_score"],
            "value": descent["propellant_margin_kg"],
            "unit": "kg propellant margin at touchdown (R4 descent)",
            "rule": "propellant margin / propellant available at PDI",
        }
    return {
        "candidate_id": candidate_id,
        "status": status,
        "failures": failures,
        "unknowns": unknowns,
        "criteria": criteria,
    }


def _active_criteria(evaluations: list[dict]) -> tuple[str, ...]:
    """R3 criteria, plus fuel when any evaluation carries an R5 descent score."""
    with_fuel = any("fuel" in e.get("criteria", {}) for e in evaluations)
    return CRITERIA + (("fuel",) if with_fuel else ())


def _describe_failure(failure: dict) -> str:
    if failure["constraint"] == "terrain_coverage":
        return "no verified terrain coverage for the landing and search area"
    if failure["constraint"] == "descent":
        return (
            f"descent is not feasible ({failure.get('detail') or 'see descent plan'})"
        )
    comparison = "exceeds" if failure["constraint"] == "slope" else "is below"
    return (
        f"{failure['constraint']} {failure['actual']:.1f} {failure['unit']} "
        f"{comparison} the limit {failure['limit']:.1f} {failure['unit']}"
    )


def rank(evaluations: list[dict], weights: Weights) -> dict:
    """Weighted ranking with contributions and explanations for each candidate.

    total = sum(w_c * s_c) / sum(w_c); ties break by candidate ID.
    """
    active = _active_criteria(evaluations)
    raw = {c: getattr(weights, c) for c in active}
    total_weight = sum(raw.values())
    if total_weight <= 0:
        raise ValueError(
            "At least one weight of the evaluated criteria must be positive"
        )
    norm = {c: raw[c] / total_weight for c in active}
    rows = []
    for evaluation in evaluations:
        if evaluation["status"] != "ranked":
            continue
        scores = evaluation["criteria"]
        contributions = {c: norm[c] * scores[c]["score"] for c in active}
        weighted = [c for c in active if norm[c] > 0]
        strongest = max(weighted, key=lambda c: (contributions[c], -active.index(c)))
        weakest = min(weighted, key=lambda c: (scores[c]["score"], active.index(c)))
        rows.append(
            {
                "candidate_id": evaluation["candidate_id"],
                "total_score": sum(contributions.values()),
                "contributions": contributions,
                "criteria": scores,
                "strongest": strongest,
                "weakest": weakest,
            }
        )
    rows.sort(key=lambda row: (-row["total_score"], row["candidate_id"]))
    for index, row in enumerate(rows):
        row["rank"] = index + 1
        strong, weak = row["strongest"], row["weakest"]
        row["explanation"] = (
            f"Rank {index + 1} with total score {row['total_score']:.2f}. "
            f"Strongest criterion: {strong} (score {row['criteria'][strong]['score']:.2f}); "
            f"weakest: {weak} (score {row['criteria'][weak]['score']:.2f})."
        )
    rejected = [
        {
            "candidate_id": e["candidate_id"],
            "failures": e["failures"],
            "explanation": "Rejected: "
            + "; ".join(_describe_failure(f) for f in e["failures"])
            + ".",
        }
        for e in evaluations
        if e["status"] == "rejected"
    ]
    insufficient = [
        {
            "candidate_id": e["candidate_id"],
            "unknowns": e["unknowns"],
            "explanation": "Not ranked: unknown coverage affects "
            + ", ".join(e["unknowns"])
            + ".",
        }
        for e in evaluations
        if e["status"] == "insufficient_data"
    ]
    return {
        "weights": norm,
        "ranked": rows,
        "rejected": rejected,
        "insufficient_data": insufficient,
        "not_modeled": {k: v for k, v in NOT_MODELED.items() if k not in active},
    }


def rescore(evaluations: list[dict], previous: Weights, current: Weights) -> dict:
    """Previous and new rankings side by side with rank changes and reasons.

    The reason is the criterion whose weighted contribution changed most.
    """
    before, after = rank(evaluations, previous), rank(evaluations, current)
    old = {row["candidate_id"]: row for row in before["ranked"]}
    new = {row["candidate_id"]: row for row in after["ranked"]}
    changes = []
    for cid in sorted(set(old) | set(new)):
        a, b = old.get(cid), new.get(cid)
        reason = None
        if a and b and a["rank"] != b["rank"]:
            reason = max(
                list(b["contributions"]),
                key=lambda c: abs(b["contributions"][c] - a["contributions"][c]),
            )
        changes.append(
            {
                "candidate_id": cid,
                "previous_rank": a["rank"] if a else None,
                "new_rank": b["rank"] if b else None,
                "previous_score": a["total_score"] if a else None,
                "new_score": b["total_score"] if b else None,
                "reason": reason,
            }
        )
    return {"previous": before, "current": after, "changes": changes}


def _status_of(recommendation: dict, cid: str) -> tuple[str, int | None]:
    ranking = recommendation["ranking"]
    for row in ranking["ranked"]:
        if row["candidate_id"] == cid:
            return "ranked", row["rank"]
    for key in ("rejected", "insufficient_data", "failed"):
        if any(row["candidate_id"] == cid for row in ranking.get(key, [])):
            return key, None
    return "unknown", None


def _best(recommendation: dict) -> str | None:
    ranked = recommendation["ranking"]["ranked"]
    return ranked[0]["candidate_id"] if ranked else None


def compare_schedules(previous: dict, current: dict) -> dict:
    """Compare two finished recommendations for the same sites at shifted dates."""
    sites = {c["candidate_id"]: c["site"]["site_id"] for c in current["candidates"]}
    ids = sorted({c["candidate_id"] for c in previous["candidates"]} | set(sites))
    changes = []
    for cid in ids:
        old_status, old_rank = _status_of(previous, cid)
        new_status, new_rank = _status_of(current, cid)
        changes.append(
            {
                "candidate_id": cid,
                "site_id": sites.get(cid),
                "previous_rank": old_rank,
                "new_rank": new_rank,
                "previous_status": old_status,
                "new_status": new_status,
            }
        )
    best_before, best_after = _best(previous), _best(current)
    if best_after is None:
        suggestion = (
            "No candidate qualifies after the schedule change; relax constraints, "
            "add candidates or try other dates."
        )
    elif best_after == best_before:
        suggestion = f"{sites[best_after]} ({best_after}) remains the best option after the change."
    else:
        suggestion = f"Switch to {sites[best_after]} ({best_after}): it ranks first at the new date."
    return {
        "best_before": best_before,
        "best_after": best_after,
        "changes": changes,
        "suggestion": suggestion,
    }


class RecommendationStore:
    """Persistent recommendations orchestrating analysis and communication jobs."""

    def __init__(self, jobs, communications):
        self.jobs = jobs
        self.communications = communications
        with jobs.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS recommendations (id TEXT PRIMARY KEY, status TEXT, body TEXT)"
            )

    def create(self, request: RecommendationRequest, previous_id: str | None = None):
        rec_id = str(uuid4())
        body = {
            "id": rec_id,
            "status": "pending",
            "created_utc": _iso(datetime.now(timezone.utc)),
            "previous_id": previous_id,
            "request": json.loads(request.model_dump_json()),
            "candidates": [
                {
                    "candidate_id": cid,
                    "site": candidate.model_dump(),
                    "analysis_job_id": str(uuid5(UUID(rec_id), cid)),
                    "communication_job_id": None,
                    "state": "waiting",
                    "error": None,
                    "safety": None,
                    "evaluation": None,
                    "descent": None,
                }
                for cid, candidate in zip(candidate_ids(request), request.candidates)
            ],
            "ranking": None,
            "not_modeled": NOT_MODELED,
        }
        with self.jobs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT count(*) FROM recommendations WHERE status IN ('pending','running')"
            ).fetchone()[0]
            if active >= MAX_ACTIVE_RECOMMENDATIONS:
                raise QueueFull("Recommendation queue is full; wait for a running one")
            db.execute(
                "INSERT INTO recommendations VALUES (?,?,?)",
                (rec_id, "pending", json.dumps(body, allow_nan=False)),
            )
        return self.get(rec_id)

    def _load(self, rec_id: str) -> dict | None:
        with self.jobs.connect() as db:
            row = db.execute(
                "SELECT body FROM recommendations WHERE id=?", (rec_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, recommendation_id: str) -> dict | None:
        body = self._load(recommendation_id)
        if body is None:
            return None
        body["schedule_change"] = None
        previous = self._load(body["previous_id"]) if body["previous_id"] else None
        if (
            previous
            and body["status"] in ("ready", "partial")
            and previous["status"] in ("ready", "partial")
        ):
            body["schedule_change"] = compare_schedules(previous, body)
        return body

    def _advance(self, db, body: dict, data_dir) -> None:
        request = RecommendationRequest.model_validate(body["request"])
        now = datetime.now(timezone.utc).isoformat()
        for cand in body["candidates"]:
            if cand["state"] == "waiting":
                busy = db.execute(
                    f"SELECT count(*) FROM jobs WHERE status IN ({','.join('?' * len(ANALYSIS_ACTIVE))})",
                    ANALYSIS_ACTIVE,
                ).fetchone()[0]
                if busy >= MAX_ANALYSIS_JOBS:
                    continue
                plan = submission(request, Candidate.model_validate(cand["site"]))
                db.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,NULL,NULL)",
                    (cand["analysis_job_id"], now, "pending", plan.model_dump_json()),
                )
                cand["state"] = "analyzing"
            elif cand["state"] == "analyzing":
                job = db.execute(
                    "SELECT status,error FROM jobs WHERE id=?",
                    (cand["analysis_job_id"],),
                ).fetchone()
                if job["status"] == "failed":
                    cand.update(
                        state="failed", error=f"Analysis failed: {job['error']}"
                    )
                elif job["status"] in ("ready", "insufficient_data"):
                    busy = db.execute(
                        f"SELECT count(*) FROM communications WHERE status IN ({','.join('?' * len(COMMUNICATION_ACTIVE))})",
                        COMMUNICATION_ACTIVE,
                    ).fetchone()[0]
                    if busy >= MAX_COMMUNICATION_JOBS:
                        continue
                    comm_id = str(
                        uuid5(UUID(body["id"]), cand["candidate_id"] + ":dte")
                    )
                    db.execute(
                        "INSERT INTO communications VALUES (?,?,?,?,?,NULL,NULL)",
                        (
                            comm_id,
                            now,
                            cand["analysis_job_id"],
                            CommunicationRequest(
                                min_elevation_deg=request.min_elevation_deg
                            ).model_dump_json(),
                            "pending",
                        ),
                    )
                    cand.update(state="communicating", communication_job_id=comm_id)
            elif cand["state"] == "communicating":
                comm = db.execute(
                    "SELECT status,result,error FROM communications WHERE id=?",
                    (cand["communication_job_id"],),
                ).fetchone()
                if comm["status"] == "failed":
                    cand.update(
                        state="failed", error=f"Communication failed: {comm['error']}"
                    )
                elif comm["status"] in ("ready", "insufficient_data"):
                    self._evaluate(
                        db, cand, json.loads(comm["result"]), request, data_dir
                    )
        done = all(c["state"] in ("evaluated", "failed") for c in body["candidates"])
        if not done:
            body["status"] = "running"
            return
        evaluations = [c["evaluation"] for c in body["candidates"] if c["evaluation"]]
        ranking = rank(evaluations, request.weights)
        ranking["failed"] = [
            {"candidate_id": c["candidate_id"], "error": c["error"]}
            for c in body["candidates"]
            if c["state"] == "failed"
        ]
        body["ranking"] = ranking
        if not evaluations:
            body["status"] = "failed"
        elif ranking["failed"] or ranking["insufficient_data"]:
            body["status"] = "partial"
        else:
            body["status"] = "ready"

    def _evaluate(self, db, cand, comm_result, request, data_dir) -> None:
        job = db.execute(
            "SELECT result FROM jobs WHERE id=?", (cand["analysis_job_id"],)
        ).fetchone()
        analysis = json.loads(job["result"])["analysis"]
        site = cand["site"]
        settings = SafetySettings(
            slope_limit_deg=request.constraints.slope_limit_deg,
            landing_radius_m=request.constraints.landing_radius_m,
            hazard_search_m=request.constraints.hazard_search_m,
        )
        try:
            safety = terrain_safety(
                data_dir, site["latitude_deg"], site["longitude_deg_east"], settings
            )
        except TerrainSafetyError as exc:
            safety = {"covered": False, "error": str(exc)}
        except FileNotFoundError as exc:
            cand.update(state="failed", error=f"Terrain unavailable: {exc}")
            return
        cand["safety"] = safety
        descent = None
        if request.include_descent:
            from app.descent import DescentError

            try:
                plan = descent_plan(
                    data_dir,
                    site["latitude_deg"],
                    site["longitude_deg_east"],
                    request.descent_profile_id,
                )
                descent = descent_summary(plan)
            except DescentError as exc:
                descent = {
                    "feasible": False,
                    "limiting_factors": [],
                    "detail": str(exc),
                    "fuel_score": 0.0,
                    "propellant_margin_kg": None,
                }
            except FileNotFoundError as exc:
                cand.update(state="failed", error=f"Terrain unavailable: {exc}")
                return
            cand["descent"] = descent
        cand["evaluation"] = evaluate(
            cand["candidate_id"],
            analysis,
            comm_result,
            safety,
            request.constraints,
            descent=descent,
        )
        cand["state"] = "evaluated"

    def tick(self, data_dir) -> None:
        """Advance active recommendations in one transaction; no NASA access."""
        with self.jobs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT body FROM recommendations WHERE status IN ('pending','running') ORDER BY rowid"
            ).fetchall()
            for row in rows:
                body = json.loads(row[0])
                try:
                    self._advance(db, body, data_dir)
                except (ValueError, KeyError, TypeError) as exc:
                    # A malformed saved input fails this recommendation visibly.
                    logging.exception("Recommendation %s failed", body["id"])
                    body.update(status="failed", error=str(exc)[:600])
                db.execute(
                    "UPDATE recommendations SET status=?,body=? WHERE id=?",
                    (body["status"], json.dumps(body, allow_nan=False), body["id"]),
                )

    def reschedule(self, recommendation_id: str, delay_hours: float) -> dict:
        """Create the same recommendation shifted later by delay_hours."""
        base = self._load(recommendation_id)
        if base is None:
            raise KeyError(recommendation_id)
        if base["status"] not in ("ready", "partial"):
            raise ValueError("Recommendation is not finished")
        if not 0 < delay_hours <= MAX_DELAY_H:
            raise ValueError(f"delay_hours must be in (0, {MAX_DELAY_H}]")
        body = dict(base["request"])
        shift = timedelta(hours=delay_hours)
        body["start_utc"] = _iso(_utc(body["start_utc"]) + shift)
        body["end_utc"] = _iso(_utc(body["end_utc"]) + shift)
        return self.create(
            RecommendationRequest.model_validate(body), previous_id=recommendation_id
        )


def register_recommendation_routes(app, recommendations) -> None:
    """Recommendation create/get, rescore and reschedule routes."""

    def finished(rec_id):
        rec = recommendations.get(rec_id)
        if rec is None:
            raise HTTPException(404, "Recommendation not found")
        if rec["status"] not in ("ready", "partial"):
            raise HTTPException(409, "Recommendation is not finished")
        return rec

    @app.post("/recommendations", status_code=202)
    def create_recommendation(body: RecommendationRequest):
        try:
            return recommendations.create(body)
        except QueueFull as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.get("/recommendations/{rec_id}")
    def get_recommendation(rec_id: str):
        rec = recommendations.get(rec_id)
        if rec is None:
            raise HTTPException(404, "Recommendation not found")
        return rec

    @app.post("/recommendations/{rec_id}/rescore")
    def rescore_recommendation(rec_id: str, body: Weights):
        rec = finished(rec_id)
        evaluations = [c["evaluation"] for c in rec["candidates"] if c["evaluation"]]
        previous = Weights.model_validate(rec["request"]["weights"])
        return rescore(evaluations, previous, body)

    @app.post("/recommendations/{rec_id}/reschedule", status_code=202)
    def reschedule_recommendation(rec_id: str, body: RescheduleRequest):
        finished(rec_id)
        try:
            return recommendations.reschedule(rec_id, body.delay_hours)
        except QueueFull as exc:
            raise HTTPException(429, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
