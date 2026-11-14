"""Boundary checks for the documented MVP request contract."""
import pytest
from pydantic import ValidationError
from shared.contracts import AnalysisRequest, ObserverSite


def site(**changes):
    values = dict(site_id="query-reference", longitude_deg_east=0,
                  latitude_deg=-89.5, altitude_km=0)
    return ObserverSite(**(values | changes))


def request(**changes):
    values = dict(site=site(), start_utc="2026-11-14T00:00:00Z",
                  end_utc="2026-11-15T00:00:00Z")
    return AnalysisRequest(**(values | changes))


def test_round_trip():
    value = request()
    assert AnalysisRequest.model_validate_json(value.model_dump_json()) == value
    assert value.step_s == 3600
    assert value.site.reference_frame == "MOON_ME"


@pytest.mark.parametrize("changes", [
    {"latitude_deg": -90}, {"latitude_deg": 90},
    {"longitude_deg_east": 359.999}, {"altitude_km": -5},
])
def test_valid_coordinate_boundaries(changes):
    assert site(**changes).site_id == "query-reference"


@pytest.mark.parametrize("changes", [
    {"latitude_deg": -91}, {"latitude_deg": 91},
    {"longitude_deg_east": -1}, {"longitude_deg_east": 360},
    {"altitude_km": float("nan")}, {"latitude_deg": float("inf")},
    {"site_id": " "}, {"reference_frame": "J2000"},
    {"longitude_deg_east": "0"}, {"extra": 1},
])
def test_invalid_coordinates(changes):
    with pytest.raises(ValidationError):
        site(**changes)


@pytest.mark.parametrize("changes", [
    {"start_utc": "2026-11-14T00:00:00"},
    {"start_utc": "2026-11-14T00:00:00+03:00"},
    {"end_utc": "2026-11-14T00:00:00Z"},
    {"end_utc": "2026-11-13T00:00:00Z"},
    {"start_utc": "2025-12-31T00:00:00Z"},
    {"end_utc": "2028-01-02T00:00:00Z"},
    {"end_utc": "2026-12-16T00:00:00Z"},
    {"end_utc": "2026-11-25T00:00:00Z", "step_s": 60},
    {"step_s": 0}, {"step_s": 30}, {"step_s": True},
])
def test_invalid_intervals(changes):
    with pytest.raises(ValidationError):
        request(**changes)


@pytest.mark.parametrize("step", [60, 300, 3600])
def test_supported_sampling(step):
    assert request(step_s=step).step_s == step
