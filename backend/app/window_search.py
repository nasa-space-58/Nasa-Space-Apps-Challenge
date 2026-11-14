"""Bounded durable searches over candidate observation start times."""

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4, uuid5, UUID
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app.analysis_api import Submission


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    template: Submission
    last_start_utc: datetime
    start_step_hours: int = Field(default=24, ge=1, le=168, strict=True)
    min_sunlight_percent: float = Field(default=70, ge=0, le=100)
    max_darkness_hours: float = Field(default=6, ge=0, le=744)
    min_earth_hours: float = Field(default=4, ge=0, le=744)


def candidate_submissions(request: SearchRequest) -> list[Submission]:
    """Inclusive bounded grid; final mission end must meet the analysis contract."""
    first = request.template.start_utc
    last = request.last_start_utc
    if last.utcoffset() != timedelta(0):
        raise ValueError("Last candidate start must use explicit UTC")
    span = (last - first).total_seconds()
    if span < 0 or span > 31 * 86400:
        raise ValueError("Candidate start span must be between 0 and 31 days")
    count = int(span // (request.start_step_hours * 3600)) + 1
    if count > 16:
        raise ValueError("Search exceeds 16 candidates; increase the start spacing")
    duration = request.template.end_utc - first
    candidates = []
    for index in range(count):
        start = first + timedelta(hours=index * request.start_step_hours)
        candidates.append(
            Submission.model_validate(
                request.template.model_dump()
                | dict(start_utc=start, end_utc=start + duration)
            )
        )
    return candidates


def assess_result(result: dict, request: SearchRequest) -> dict:
    """Threshold arithmetic only; scientific estimates originate in analysis."""
    analysis = result["analysis"]
    sun, earth = (analysis[target]["metrics"] for target in ("sun", "earth"))
    definitions = [
        (
            "sunlight_percent",
            sun["visible_percent_of_interval"],
            request.min_sunlight_percent,
            ">=",
            sun["unknown_duration_s"],
        ),
        (
            "longest_darkness_hours",
            sun["longest_blocked_s"] / 3600,
            request.max_darkness_hours,
            "<=",
            sun["unknown_duration_s"],
        ),
        (
            "earth_visible_hours",
            earth["visible_duration_s"] / 3600,
            request.min_earth_hours,
            ">=",
            earth["unknown_duration_s"],
        ),
    ]
    conditions = []
    for name, actual, threshold, operator, unknown in definitions:
        passed = (
            None
            if unknown > 0
            else actual >= threshold
            if operator == ">="
            else actual <= threshold
        )
        conditions.append(
            dict(
                name=name,
                actual=actual,
                threshold=threshold,
                operator=operator,
                passed=passed,
            )
        )
    state = (
        "unknown"
        if any(row["passed"] is None for row in conditions)
        else "match"
        if all(row["passed"] for row in conditions)
        else "not_met"
    )
    return dict(
        state=state,
        conditions=conditions,
        analysis_id=analysis["analysis_id"],
        unknown_seconds=dict(
            sun=sun["unknown_duration_s"], earth=earth["unknown_duration_s"]
        ),
    )


class SearchStore:
    """Search progress shares the analysis SQLite transaction boundary."""

    def __init__(self, jobs):
        self.jobs = jobs
        with jobs.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS window_searches (id TEXT PRIMARY KEY, status TEXT, body TEXT)"
            )

    def create(self, request: SearchRequest):
        plans = candidate_submissions(request)
        search_id = str(uuid4())
        view = dict(
            id=search_id,
            status="pending",
            request=request.model_dump(mode="json"),
            total=len(plans),
            completed=0,
            matches=0,
            created_utc=datetime.now(timezone.utc).isoformat(),
            limitation="Only sampled candidate starts are evaluated; shorter windows between starts may be missed.",
            candidates=[
                dict(
                    job_id=str(uuid5(UUID(search_id), str(index))),
                    start_utc=plan.start_utc.isoformat(),
                    end_utc=plan.end_utc.isoformat(),
                    submission=plan.model_dump(mode="json"),
                    state="waiting",
                )
                for index, plan in enumerate(plans)
            ],
        )
        with self.jobs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute(
                    "SELECT count(*) FROM window_searches WHERE status IN ('pending','running')"
                ).fetchone()[0]
                >= 2
            ):
                raise ValueError("Search queue is full; wait for an active search")
            db.execute(
                "INSERT INTO window_searches VALUES (?,?,?)",
                (search_id, "pending", json.dumps(view, allow_nan=False)),
            )
        return view

    def get(self, search_id: str):
        with self.jobs.connect() as db:
            row = db.execute(
                "SELECT body FROM window_searches WHERE id=?", (search_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def tick(self):
        # One transaction makes candidate-job linkage crash safe. No NASA access here.
        with self.jobs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT body FROM window_searches WHERE status IN ('pending','running') ORDER BY rowid"
            ).fetchall()
            for row in rows:
                view = json.loads(row[0])
                request = SearchRequest.model_validate(view["request"])
                for candidate in view["candidates"]:
                    if candidate["state"] != "processing":
                        continue
                    job = db.execute(
                        "SELECT status,result,error FROM jobs WHERE id=?",
                        (candidate["job_id"],),
                    ).fetchone()
                    if job["status"] == "failed":
                        candidate.update(state="failed", error=job["error"])
                    elif job["status"] in ("ready", "insufficient_data"):
                        try:
                            candidate.update(
                                assess_result(json.loads(job["result"]), request)
                            )
                        except (ValueError, KeyError, TypeError) as exc:
                            candidate.update(
                                state="failed",
                                error=f"Cannot assess saved result: {exc}",
                            )
                active = any(c["state"] == "processing" for c in view["candidates"])
                waiting = next(
                    (c for c in view["candidates"] if c["state"] == "waiting"), None
                )
                capacity = db.execute(
                    "SELECT count(*) FROM jobs WHERE status IN ('pending','preparing','analyzing','refining')"
                ).fetchone()[0]
                if not active and waiting and capacity < 8:
                    db.execute(
                        "INSERT INTO jobs VALUES (?,?,?,?,NULL,NULL)",
                        (
                            waiting["job_id"],
                            datetime.now(timezone.utc).isoformat(),
                            "pending",
                            json.dumps(waiting["submission"]),
                        ),
                    )
                    waiting["state"] = "processing"
                view["completed"] = sum(
                    c["state"] not in ("waiting", "processing")
                    for c in view["candidates"]
                )
                view["matches"] = sum(c["state"] == "match" for c in view["candidates"])
                view["status"] = "running"
                if view["completed"] == view["total"]:
                    view["status"] = (
                        "failed"
                        if all(c["state"] == "failed" for c in view["candidates"])
                        else "partial"
                        if any(
                            c["state"] in ("unknown", "failed")
                            for c in view["candidates"]
                        )
                        else "ready"
                    )
                db.execute(
                    "UPDATE window_searches SET status=?,body=? WHERE id=?",
                    (view["status"], json.dumps(view, allow_nan=False), view["id"]),
                )


def register_search_routes(app, searches):
    @app.post("/searches", status_code=202)
    def create_search(body: SearchRequest):
        try:
            candidate_submissions(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return searches.create(body)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.get("/searches/{search_id}")
    def get_search(search_id: str):
        result = searches.get(search_id)
        if result is None:
            raise HTTPException(404, "Search not found")
        return result
