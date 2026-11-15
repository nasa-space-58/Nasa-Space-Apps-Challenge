"""Runtime presets cover every NASA Artemis III candidate landing region.

Region list: NASA release 24-136 (2024-10-28),
https://www.nasa.gov/news-release/nasa-provides-update-on-artemis-iii-moon-landing-regions/
reconfirmed by NTRS 20250011660 (LPSC 2026 abstract). NASA publishes no point
coordinates for these regions; each preset records how its point was chosen.
"""

import math

import pytest

from app.analysis_api import Submission, presets
from shared.horizon import HorizonSettings

NASA_REGIONS = {
    "Peak near Cabeus B",
    "Haworth",
    "Malapert Massif",
    "Mons Mouton Plateau",
    "Mons Mouton",
    "Nobile Rim 1",
    "Nobile Rim 2",
    "de Gerlache Rim 2",
    "Slater Plain",
}
DATA_BOOK = (
    "https://ntrs.nasa.gov/api/citations/20250002845/downloads/"
    "M2M-30044%20Lunar%20Surface%20Data%20Book%20Baseline_Final.pdf#page=75"
)
# (region, latitude_deg, source longitude_deg_east, basis, source URL prefix)
# Data book: Table A2.1-1, DEM-derived polygon centers. LROC: NAC controlled
# mosaic centers published to 0.1 degree. IAU: USGS gazetteer feature centers.
REFERENCE = {
    "Haworth": (-86.7639, -22.777, "nasa_region_center", DATA_BOOK),
    "Malapert Massif": (-85.9948, -0.23578, "nasa_region_center", DATA_BOOK),
    "Nobile Rim 1": (-85.4381, 37.36667, "nasa_region_center", DATA_BOOK),
    "Nobile Rim 2": (-83.954, 58.82206, "nasa_region_center", DATA_BOOK),
    "de Gerlache Rim 2": (-88.23, -64.6284, "nasa_region_center", DATA_BOOK),
    "Mons Mouton Plateau": (
        -84.3,
        30.6,
        "lroc_region_mosaic_center",
        "https://data.lroc.im-ldi.com/lroc/view_rdr/NAC_ROI_MOUTNPLTLO1",
    ),
    "Peak near Cabeus B": (
        -83.6,
        291.2,
        "lroc_region_mosaic_center",
        "https://data.lroc.im-ldi.com/lroc/view_rdr/NAC_ROI_CABEUSPKLO1",
    ),
    "Mons Mouton": (
        -84.6,
        31.0,
        "iau_feature_center",
        "https://planetarynames.wr.usgs.gov/Feature/16070",
    ),
    "Slater Plain": (
        -88.08,
        111.29,
        "iau_feature_anchor",
        "https://planetarynames.wr.usgs.gov/Feature/15325",
    ),
}
ANGLE_TOLERANCE_DEG = 1e-9  # catalog values are copied, not computed
MOON_RADIUS_M = 1737400.0  # LDEM_80S_80MPP_ADJ projection sphere


def by_region():
    return {
        site["artemis_iii_region"]: site
        for site in presets()
        if site.get("artemis_iii_region") is not None
    }


def test_every_nasa_region_appears_exactly_once():
    regions = [
        s["artemis_iii_region"] for s in presets() if s.get("artemis_iii_region")
    ]
    assert sorted(regions) == sorted(NASA_REGIONS)


def test_site_ids_are_unique_and_original_presets_keep_their_ids():
    ids = [s["site_id"] for s in presets()]
    assert len(ids) == len(set(ids))
    regions = by_region()
    assert regions["Mons Mouton"]["site_id"] == "mons-mouton-feature-center"
    assert regions["Malapert Massif"]["site_id"] == "malapert-massif-r23"


def test_malapert_remains_last_for_terrain_bootstrap():
    # execute_job bootstraps the raw terrain cache with presets()[-1].
    assert presets()[-1]["site_id"] == "malapert-massif-r23"


@pytest.mark.parametrize("region", sorted(REFERENCE))
def test_reference_coordinates_and_basis(region):
    latitude, source_longitude, basis, url = REFERENCE[region]
    site = by_region()[region]
    assert math.isclose(site["latitude_deg"], latitude, abs_tol=ANGLE_TOLERANCE_DEG)
    assert math.isclose(
        site["source_longitude_deg_east"],
        source_longitude,
        abs_tol=ANGLE_TOLERANCE_DEG,
    )
    assert site["coordinate_basis"] == basis
    assert site["coordinate_source_url"].startswith(url)


@pytest.mark.parametrize("site", presets(), ids=lambda s: s["site_id"])
def test_preset_conventions(site):
    assert site["reference_frame"] == "MOON_ME"
    assert site["frame_realization"] == "DE421 mean Earth"
    assert site["runtime_preset_enabled"] is True
    assert site["coordinate_source_url"].startswith("https://")
    assert site["coordinate_source_locator"].strip()
    assert site["coordinate_precision_note"].strip()
    assert -90 <= site["latitude_deg"] <= -80
    assert 0 <= site["longitude_deg_east"] < 360
    normalized = site["source_longitude_deg_east"] % 360
    assert math.isclose(
        site["longitude_deg_east"], normalized, abs_tol=ANGLE_TOLERANCE_DEG
    )
    assert site["terrain_product"] == "LDEM_80S_80MPP_ADJ.TIF"
    assert site["reference_sphere_radius_m"] == MOON_RADIUS_M
    assert site["proposed_observer_height_above_terrain_m"] == 2.0
    # Physical sanity: south-polar LOLA heights span roughly -5 km to +9 km.
    assert -6000 < site["terrain_surface_height_m"] < 9000
    expected_km = (
        site["terrain_surface_height_m"]
        + site["proposed_observer_height_above_terrain_m"]
    ) / 1000
    assert math.isclose(
        site["provisional_horizons_altitude_km"], expected_km, abs_tol=1e-12
    )
    # Every preset is a valid analysis submission.
    Submission(
        site_id=site["site_id"],
        latitude_deg=site["latitude_deg"],
        longitude_deg_east=site["longitude_deg_east"],
        start_utc="2026-11-14T00:00:00Z",
        end_utc="2026-11-15T00:00:00Z",
    )


@pytest.mark.parametrize("site", presets(), ids=lambda s: s["site_id"])
def test_default_horizon_search_stays_inside_dem_nominal_coverage(site):
    # South-polar stereographic radius on the 1737.4 km sphere.
    def polar_radius_m(latitude_deg):
        return 2 * MOON_RADIUS_M * math.tan(math.radians(90 + latitude_deg) / 2)

    reach_m = polar_radius_m(site["latitude_deg"]) + (
        HorizonSettings().search_distance_m
    )
    assert reach_m < polar_radius_m(-80.0)


@pytest.mark.parametrize("site", presets(), ids=lambda s: s["site_id"])
def test_terrain_window_bounds_contain_the_site(site):
    left, bottom, right, top = site["terrain_verified_window_bounds_m"]
    assert right - left == pytest.approx(40080.0)
    assert top - bottom == pytest.approx(40080.0)
    rho = 2 * MOON_RADIUS_M * math.tan(math.radians(90 + site["latitude_deg"]) / 2)
    angle = math.radians(site["longitude_deg_east"])
    x_m, y_m = rho * math.sin(angle), rho * math.cos(angle)
    assert left <= x_m <= right
    assert bottom <= y_m <= top


def test_slater_anchor_is_labeled_as_not_a_region_center():
    note = by_region()["Slater Plain"]["coordinate_precision_note"].lower()
    assert "crater" in note
    assert "not" in note and "center" in note
