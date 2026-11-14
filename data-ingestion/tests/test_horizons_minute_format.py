"""Regression for Horizons minute-only UTC columns seen on 2026-11-14.

The live 2026-11-02 15:55–16:00 Malapert query at 60 s spacing returned
Date__(UT)__HR:MN despite TIME_DIGITS=SECONDS. Transform the existing captured
fixture's formatting only; its source numeric reference values are unchanged.
"""

from datetime import timedelta
import json
from pathlib import Path
import re

import pytest
from app.horizons import HorizonsError, parse_response
from shared.contracts import AnalysisRequest


def inputs():
    payload = json.loads(
        (Path(__file__).parent / "fixtures/horizons_10.json").read_text()
    )
    payload["result"] = payload["result"].replace(
        "Date__(UT)__HR:MN:SS", "Date__(UT)__HR:MN"
    )
    payload["result"] = re.sub(
        r"(2026-Nov-14 \d\d:\d\d):00,", r"\1,", payload["result"]
    )
    req = AnalysisRequest.model_validate(
        dict(
            site=dict(
                site_id="reference",
                longitude_deg_east=0.0,
                latitude_deg=-89.5,
                altitude_km=0.0,
            ),
            start_utc="2026-11-14T00:00:00Z",
            end_utc="2026-11-14T02:00:00Z",
            step_s=3600,
        )
    )
    return payload, req


def test_minute_only_utc_column_preserves_grid_and_angles():
    payload, req = inputs()
    result = parse_response(payload, req, "10")
    assert result["samples"][1]["sampled_at_utc"] == "2026-11-14T01:00:00Z"
    assert result["samples"][0]["azimuth_deg"] == pytest.approx(126.627333, abs=1e-6)


def test_minute_header_cannot_silently_round_second_offset():
    payload, req = inputs()
    req = req.model_copy(
        update={
            "start_utc": req.start_utc + timedelta(seconds=20),
            "end_utc": req.end_utc + timedelta(seconds=20),
        }
    )
    with pytest.raises(HorizonsError):
        parse_response(payload, req, "10")


def test_minute_column_still_requires_utc_and_single_time_column():
    payload, req = inputs()
    payload["result"] = payload["result"].replace(
        "Date__(UT)__HR:MN", "Date__(TT)__HR:MN"
    )
    with pytest.raises(HorizonsError):
        parse_response(payload, req, "10")
