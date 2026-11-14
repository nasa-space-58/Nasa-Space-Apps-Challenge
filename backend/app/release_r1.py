"""Exports, portable scenario manifests, teaching presets and search ranking."""

import csv
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import io
import json
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.analysis_api import Submission, presets
from app.comparison import compare_results
from app.visibility import utc

LIMITATION = (
    "Sampled terrain-relative visibility estimates, not landing safety or a radio link budget. "
    "Short windows do not establish permanent light or shadow. Unknown coverage is not darkness."
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def rank_candidates(search: dict) -> dict:
    """Mean deficit: sunlight/100 percentage points; duration deficits/mission hours.

    This is an explicit equal-weight preference, not a safety score. Unknown,
    failed and unfinished candidates are excluded; ties use UTC start then job ID.
    """
    ranked, excluded = [], []
    for candidate in search["candidates"]:
        if candidate["state"] not in ("match", "not_met"):
            excluded.append(dict(job_id=candidate["job_id"], reason=candidate["state"]))
            continue
        hours = (
            utc(candidate["end_utc"]) - utc(candidate["start_utc"])
        ).total_seconds() / 3600
        deficits = []
        for row in candidate["conditions"]:
            shortfall = max(
                0,
                (row["threshold"] - row["actual"])
                if row["operator"] == ">="
                else (row["actual"] - row["threshold"]),
            )
            scale = 100 if row["name"] == "sunlight_percent" else hours
            deficits.append(
                dict(
                    name=row["name"],
                    shortfall=shortfall,
                    unit="percentage points"
                    if scale == 100 and row["name"] == "sunlight_percent"
                    else "hours",
                    normalized=shortfall / scale,
                )
            )
        ranked.append(
            dict(
                job_id=candidate["job_id"],
                start_utc=candidate["start_utc"],
                state=candidate["state"],
                score=sum(x["normalized"] for x in deficits) / 3,
                deficits=deficits,
            )
        )
    ranked.sort(key=lambda row: (row["score"], utc(row["start_utc"]), row["job_id"]))
    return dict(
        ranked=ranked,
        excluded=excluded,
        method="Lower is closer: mean of sunlight shortfall / 100 percentage points, excess darkness / mission hours, and Earth shortfall / mission hours. Equal weights; ties by UTC start then job ID. Not a safety ranking.",
    )


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    analysis_id: str = Field(min_length=1, max_length=200)
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_version: str = Field(min_length=1, max_length=200)
    horizon_model_version: str = Field(min_length=1, max_length=200)
    provenance: dict
    horizon_source: dict


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission: Submission
    expected: Expected


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    kind: Literal["lunar-scenario"]
    scenarios: list[Scenario] = Field(min_length=1, max_length=2)


def scenario_manifest(jobs: list[dict]) -> dict:
    """Inputs and expected result fingerprints; no executable commands or imported results."""
    scenarios = []
    for job in jobs:
        result = job["result"]
        analysis, horizon = result["analysis"], result["horizon"]
        scenarios.append(
            dict(
                submission=job["submission"],
                expected=dict(
                    analysis_id=analysis["analysis_id"],
                    result_sha256=sha256(canonical(result).encode()).hexdigest(),
                    model_version=analysis["model_version"],
                    horizon_model_version=horizon["model_version"],
                    provenance=analysis.get("provenance", {}),
                    horizon_source={
                        key: value
                        for key, value in horizon.items()
                        if key
                        in (
                            "source_url",
                            "source_sha256",
                            "source_reference",
                            "settings",
                            "model_version",
                        )
                    },
                ),
            )
        )
    return validate_manifest(
        dict(schema_version=1, kind="lunar-scenario", scenarios=scenarios)
    )


def validate_manifest(document: dict) -> dict:
    """Validate only; never queue, fetch URLs, trust imported claims or alter results."""
    if len(canonical(document).encode()) > 1_000_000:
        raise ValueError("Scenario manifest exceeds 1 MB")
    if type(document.get("schema_version")) is not int:
        raise ValueError("Scenario schema version must be integer 1")
    return Manifest.model_validate(document).model_dump(mode="json")


def safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(
        ("=", "+", "-", "@", "\t", "\r", "\n")
    ):
        return "'" + value
    return value


def export_csv(jobs: list[dict]) -> str:
    """One row per actual frame. Metadata appears in the first row of each scenario."""
    columns = [
        "scenario",
        "job_id",
        "analysis_id",
        "site_id",
        "utc",
        "reference_frame",
        "latitude_deg",
        "longitude_deg_east",
        "observer_height_above_terrain_m",
        "model_version",
    ]
    columns += [
        f"{body}_{key}"
        for body in ("sun", "earth")
        for key in ("azimuth_deg", "elevation_deg", "horizon_deg", "state")
    ]
    columns += [
        "submission_json",
        "provenance_json",
        "horizon_metadata_json",
        "summary_metrics_json",
        "intervals_json",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for label, job in zip(("A", "B"), jobs):
        result = job["result"]
        analysis, horizon = result["analysis"], result["horizon"]
        for index, frame in enumerate(result["frames"]):
            row = dict(
                scenario=label,
                job_id=job["id"],
                analysis_id=analysis["analysis_id"],
                site_id=job["submission"]["site_id"],
                utc=frame["utc"],
                reference_frame=analysis["request"]["site"]["reference_frame"],
                latitude_deg=job["submission"]["latitude_deg"],
                longitude_deg_east=job["submission"]["longitude_deg_east"],
                observer_height_above_terrain_m=horizon[
                    "observer_height_above_terrain_m"
                ],
                model_version=analysis["model_version"],
            )
            for body in ("sun", "earth"):
                row.update(
                    {
                        f"{body}_{key}": frame[body][key]
                        for key in (
                            "azimuth_deg",
                            "elevation_deg",
                            "horizon_deg",
                            "state",
                        )
                    }
                )
            if index == 0:
                row.update(
                    submission_json=canonical(job["submission"]),
                    provenance_json=canonical(analysis.get("provenance", {})),
                    horizon_metadata_json=canonical(
                        {k: v for k, v in horizon.items() if k != "directions"}
                    ),
                    summary_metrics_json=canonical(
                        {k: analysis[k]["metrics"] for k in ("sun", "earth")}
                    ),
                    intervals_json=canonical(
                        {k: analysis[k]["intervals"] for k in ("sun", "earth")}
                    ),
                )
            writer.writerow({key: safe_cell(value) for key, value in row.items()})
    return output.getvalue()


def export_report(jobs: list[dict]) -> str:
    """Standalone printable HTML with escaped text, complete metadata and no scripts."""
    sections = []
    for label, job in zip(("A", "B"), jobs):
        analysis = job["result"]["analysis"]
        rows = "".join(
            f"<tr><th>{escape(target)} · {escape(key)}</th><td>{escape(str(value))}</td></tr>"
            for target in ("sun", "earth")
            for key, value in analysis[target]["metrics"].items()
        )
        sections.append(
            f"<section><h2>Scenario {label}: {escape(job['submission']['site_id'])}</h2><p>Job {escape(job['id'])}</p><table>{rows}</table><h3>Inputs (UTC, degrees east, metres above terrain)</h3><pre>{escape(json.dumps(job['submission'], indent=2))}</pre><h3>Visibility intervals (UTC, seconds)</h3><pre>{escape(json.dumps({k: analysis[k]['intervals'] for k in ('sun', 'earth')}, indent=2))}</pre></section>"
        )
    if len(jobs) == 2:
        comparison = compare_results(jobs[0]["result"], jobs[1]["result"])
        sections.append(
            "<section><h2>Comparison · B minus A</h2><pre>"
            + escape(json.dumps(comparison, indent=2))
            + "</pre></section>"
        )
    sections.append(
        "<section><h2>Sources, model versions and result fingerprints</h2><pre>"
        + escape(json.dumps(scenario_manifest(jobs), indent=2))
        + "</pre></section>"
    )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Lunar observation report</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px;color:#17232c}h1,h2{color:#233a4a}td,th{border-bottom:1px solid #ccc;padding:8px;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px monospace}section{margin:32px 0}@media print{body{margin:0;padding:0}h2,h3{break-after:avoid}tr{break-inside:avoid}}</style><h1>Lunar observation report</h1><p>Use your browser’s Print / Save as PDF to keep this report.</p><p>'
        + escape(LIMITATION)
        + "</p>"
        + "".join(sections)
        + "</html>"
    )


def classroom_lessons() -> list[dict]:
    """Two sourced 24-hour windows; not evidence of permanent light or shadow."""
    site = next(row for row in presets() if row["site_id"] == "malapert-massif-r23")
    lessons = []
    for day, lesson_id, title in [
        (4, "changing-light", "Watch sunlight cross the local horizon"),
        (6, "later-start", "Same place, 48 hours later"),
    ]:
        submission = Submission(
            site_id=site["site_id"],
            latitude_deg=site["latitude_deg"],
            longitude_deg_east=site["longitude_deg_east"],
            start_utc=f"2026-11-{day:02d}T00:00:00Z",
            end_utc=f"2026-11-{day + 1:02d}T00:00:00Z",
            refine=True,
        )
        lessons.append(
            dict(
                id=lesson_id,
                title=title,
                submission=submission.model_dump(mode="json"),
                source_url=site["coordinate_source_url"],
                limitation=LIMITATION
                + " This is a sky/horizon lesson at one observer, not a surface-wide moving-shadow simulation.",
                cards=[
                    dict(
                        title="The horizon is terrain",
                        text="A ridge can hide a low Sun even when it is above the reference horizontal plane.",
                    ),
                    dict(
                        title="Change the date, keep the place",
                        text="Compare these two 24-hour windows. A short window cannot establish permanent illumination or permanent shadow.",
                    ),
                    dict(
                        title="Earth is not a ground station",
                        text="Seeing Earth's centre is only a geometric condition; antennas, stations and a link budget are not modeled.",
                    ),
                ],
            )
        )
    return lessons


def register_r1_routes(app, store, searches):
    def completed(job_id):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Analysis not found")
        if job["status"] not in ("ready", "insufficient_data"):
            raise HTTPException(409, "Analysis has no completed result")
        return job

    def jobs_for(job_id, b_id):
        jobs = [completed(job_id)]
        if b_id:
            jobs.append(completed(b_id))
            try:
                compare_results(jobs[0]["result"], jobs[1]["result"])
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(422, f"Incompatible comparison: {exc}") from exc
        return jobs

    @app.get("/analyses/{job_id}/export/{kind}")
    def export(
        job_id: str, kind: Literal["csv", "report", "scenario"], b_id: str | None = None
    ):
        jobs = jobs_for(job_id, b_id)
        if kind == "csv":
            data, media, suffix = export_csv(jobs), "text/csv", "csv"
        elif kind == "report":
            data, media, suffix = export_report(jobs), "text/html", "html"
        else:
            data, media, suffix = (
                json.dumps(scenario_manifest(jobs), indent=2),
                "application/json",
                "json",
            )
        return Response(
            data,
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="lunar-scenario.{suffix}"',
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def document(request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1_000_000:
                raise HTTPException(413, "Scenario document exceeds 1 MB")
        try:
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("Expected a scenario object")
            return value
        except (ValueError, RecursionError) as exc:
            raise HTTPException(422, "Invalid scenario JSON") from exc

    @app.post("/scenarios/validate")
    async def validate(request: Request):
        try:
            return validate_manifest(await document(request))
        except (ValueError, RecursionError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/scenarios/check")
    async def check(request: Request):
        body = await document(request)
        try:
            manifest = validate_manifest(body["manifest"])
            ids = body["job_ids"]
            if (
                not isinstance(ids, list)
                or len(ids) != len(manifest["scenarios"])
                or not all(isinstance(i, str) for i in ids)
            ):
                raise ValueError("Provide one completed job ID per scenario")
        except (ValueError, KeyError, RecursionError) as exc:
            raise HTTPException(422, str(exc)) from exc
        actual = scenario_manifest([completed(i) for i in ids])
        checks = [
            dict(
                scenario=label,
                matches=a["expected"]["result_sha256"]
                == b["expected"]["result_sha256"],
                expected=a["expected"],
                actual=b["expected"],
            )
            for label, a, b in zip(
                ("A", "B"), manifest["scenarios"], actual["scenarios"]
            )
        ]
        return dict(
            matches=all(row["matches"] for row in checks),
            checks=checks,
            note="Fingerprints compare exact result contents, not authenticity. Imported source/model claims are untrusted until checked.",
        )

    @app.get("/searches/{search_id}/ranking")
    def ranking(search_id: str):
        search = searches.get(search_id)
        if search is None:
            raise HTTPException(404, "Search not found")
        return rank_candidates(search)

    @app.get("/classroom")
    def lessons():
        return dict(lessons=classroom_lessons())

    @app.post("/classroom/{lesson_id}", status_code=202)
    def start_lesson(lesson_id: str):
        lesson = next(
            (row for row in classroom_lessons() if row["id"] == lesson_id), None
        )
        if lesson is None:
            raise HTTPException(404, "Lesson not found")
        submission = lesson["submission"]
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute(
                "SELECT id,submission FROM jobs WHERE status != 'failed' ORDER BY created DESC"
            ):
                if json.loads(row["submission"]) == submission:
                    return dict(id=row["id"], lesson_id=lesson_id)
            count = db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('pending','preparing','analyzing','refining')"
            ).fetchone()[0]
            if count >= 8:
                raise HTTPException(
                    429, "Analysis queue is full; wait for a running job"
                )
            job_id = str(uuid4())
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,NULL,NULL)",
                (
                    job_id,
                    datetime.now(timezone.utc).isoformat(),
                    "pending",
                    json.dumps(submission),
                ),
            )
        return dict(id=job_id, lesson_id=lesson_id)
