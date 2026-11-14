// Real API excerpt, 2026-11-14. Two playback samples and eight horizon directions.
// Source request and analysis ID retained; not a replacement for scientific tests.
export default {
  "analysis": {
    "analysis_id": "f93f9a84ad684fe74f72d838f2930aced188ff8f8c64cebd3be3169d24c0d438",
    "request": {
      "site": {
        "site_id": "malapert-massif-r23",
        "longitude_deg_east": 359.76422,
        "latitude_deg": -85.9948,
        "altitude_km": 4.78662451171875,
        "reference_frame": "MOON_ME"
      },
      "start_utc": "2026-11-04T00:00:00Z",
      "end_utc": "2026-11-05T00:00:00Z",
      "step_s": 3600
    },
    "sun": {
      "intervals": [
        {
          "start_utc": "2026-11-04T00:00:00Z",
          "end_utc": "2026-11-04T04:55:38.157360Z",
          "state": "partial",
          "duration_s": 17738.15736
        },
        {
          "start_utc": "2026-11-04T04:55:38.157360Z",
          "end_utc": "2026-11-05T00:00:00Z",
          "state": "dark",
          "duration_s": 68661.84264
        }
      ],
      "metrics": {
        "visible_duration_s": 17738.15736,
        "full_duration_s": 0,
        "partial_duration_s": 17738.15736,
        "blocked_duration_s": 68661.84264,
        "unknown_duration_s": 0,
        "visible_percent_of_interval": 20.530274722222224,
        "longest_visible_s": 17738.15736,
        "longest_blocked_s": 68661.84264
      },
      "next_setting": {
        "kind": "sunset",
        "estimated_at_utc": "2026-11-04T04:55:38.157360Z",
        "bracket_start_utc": "2026-11-04T04:55:00Z",
        "bracket_end_utc": "2026-11-04T04:56:00Z",
        "bracket_width_s": 60.0,
        "status": "found",
        "time_until_estimate_s": 17738.15736,
        "time_until_bracket_s": [
          17700.0,
          17760.0
        ]
      }
    },
    "earth": {
      "intervals": [
        {
          "start_utc": "2026-11-04T00:00:00Z",
          "end_utc": "2026-11-05T00:00:00Z",
          "state": "blocked",
          "duration_s": 86400.0
        }
      ],
      "metrics": {
        "visible_duration_s": 0,
        "full_duration_s": 0,
        "partial_duration_s": 0,
        "blocked_duration_s": 86400.0,
        "unknown_duration_s": 0,
        "visible_percent_of_interval": 0.0,
        "longest_visible_s": 0.0,
        "longest_blocked_s": 86400.0
      },
      "next_setting": {
        "status": "no_event_in_range"
      }
    },
    "provenance": {
      "terrain_reference": "terrain/processed/2d16e4a91f72903dc821c84342efe7350bb308b479b41e2b7a6ae62669ecbd29",
      "ephemerides": [
        {
          "reference": "horizons/a2a7a9bda67f6af633dcd1cb5e86b1be430f1bc52764ee7de3cbb6c9d61e067e",
          "sha256": {
            "raw/10.json": "51510ffadd4e13490e388fab37dc5ac9cfc8436630ca3926b8d7e1fa77f8ff00",
            "raw/399.json": "c46c67acd1150e252f72026d8f20319d745c1537f9b88f17a67064917d2863f7",
            "samples.json": "2ab4ea4535bef66a0ea5fb0830f73057e94500a781a3db14aab0ecea3464daf8"
          }
        },
        {
          "reference": "horizons/f63559670a969e8b6d0d52cdaf749b9dcd94817e1b3a9fea678ec404944bc51b",
          "sha256": {
            "raw/10.json": "8b7347f2d250341940a6a6858a6c182189a9a63276a1c42a2b3b4c4b204b8f07",
            "raw/399.json": "580b87305dbc9d89ab36b1038dc6d817448b1e97cd26cc550a51792535c90ab2",
            "samples.json": "508f58d85da0cc13ccdc82e6207f9b83a53845c724e221204a72816c78ebcb06"
          }
        },
        {
          "reference": "horizons/450b6452bee0b25f53cca829d96bad53770351c23c70677e97bc71aae5dee586",
          "sha256": {
            "raw/10.json": "b559c6f2674ce485d495fb9ecc230693558df415d9751f068fc2a7fe181d4ffc",
            "raw/399.json": "180c0df0183deed07c0171448f70f620026990634deeb8fd788f1f2cdd67c14c",
            "samples.json": "9c0ff974caba27bfa6a7b95a5106164d005a92773658e3c9d076d246a1f40604"
          }
        }
      ]
    },
    "refinement": {
      "status": "completed",
      "passes": [
        {
          "step_s": 300,
          "windows": 1
        },
        {
          "step_s": 60,
          "windows": 1
        }
      ],
      "scope": "detected transition brackets only; not a full fine-grid search"
    },
    "spatial_sensitivity": {
      "offset_deg": 0.75,
      "is_error_bound": false,
      "horizon_raised": {
        "sun_visible_duration_s": 0,
        "earth_visible_duration_s": 0
      },
      "horizon_lowered": {
        "sun_visible_duration_s": 86400.0,
        "earth_visible_duration_s": 0
      }
    },
    "events": [
      {
        "kind": "sunset",
        "estimated_at_utc": "2026-11-04T04:55:38.157360Z",
        "bracket_start_utc": "2026-11-04T04:55:00Z",
        "bracket_end_utc": "2026-11-04T04:56:00Z",
        "bracket_width_s": 60.0
      }
    ]
  },
  "horizon": {
    "model_version": "spherical-ray-nearest-v1",
    "site": {
      "site_id": "malapert-massif-r23",
      "longitude_deg_east": 359.76422,
      "latitude_deg": -85.9948,
      "altitude_km": 4.78662451171875,
      "reference_frame": "MOON_ME"
    },
    "settings": {
      "azimuth_step_deg": 1.0,
      "search_distance_m": 100000,
      "radial_step_m": 40,
      "source_extent": "raw"
    },
    "reference_frame": "MOON_ME",
    "frame_realization": "DE421 mean Earth",
    "reference_sphere_radius_m": 1737400.0,
    "surface_height_m": 4784.62451171875,
    "observer_height_above_terrain_m": 2.0,
    "terrain_resolution_m": [
      80.0,
      80.0
    ],
    "azimuth_convention": "clockwise from local north; at pole, supplied longitude defines north",
    "sampling": "containing native pixel; 1 m steps through 1 km, radial steps and endpoint; no interpolation",
    "near_field_distance_m": 1000,
    "near_field_step_m": 1,
    "beyond_search_radius": "not_evaluated",
    "quality": {
      "terrain_model": "piecewise_constant_80m_cells",
      "subpixel_terrain_resolved": false,
      "azimuth_interpolation_is_approximate": true,
      "global_horizon_verified": false
    },
    "directions": [
      {
        "azimuth_deg": 0.0,
        "elevation_deg": 4.711139612883288
      },
      {
        "azimuth_deg": 45.0,
        "elevation_deg": 5.694973885754616
      },
      {
        "azimuth_deg": 90.0,
        "elevation_deg": 4.671429840648696
      },
      {
        "azimuth_deg": 135.0,
        "elevation_deg": -1.846113200376451
      },
      {
        "azimuth_deg": 180.0,
        "elevation_deg": -1.93713021752059
      },
      {
        "azimuth_deg": 225.0,
        "elevation_deg": -1.3779324100737436
      },
      {
        "azimuth_deg": 270.0,
        "elevation_deg": 2.3476998237074
      },
      {
        "azimuth_deg": 315.0,
        "elevation_deg": 4.925293619523117
      }
    ],
    "terrain_reference": "terrain/processed/2d16e4a91f72903dc821c84342efe7350bb308b479b41e2b7a6ae62669ecbd29",
    "source_sha256": "82030dcbdd7c4bb0c7d858e9b4b293b9f2cda5a543ba31ec0f42784bd6beee5a",
    "sampled_file_sha256": "82030dcbdd7c4bb0c7d858e9b4b293b9f2cda5a543ba31ec0f42784bd6beee5a",
    "source_url": "https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_80MPP_ADJ.TIF",
    "product_id": "LDEM_80S_80MPP_ADJ",
    "software": {
      "numpy": "2.5.4",
      "rasterio": "1.5.2",
      "gdal": "3.12.2"
    },
    "profile_id": "0db8b71d945ebd7601746a3b4501ef843c17df2ba4cccdd4f7776f52e23ae5c2"
  },
  "frames": [
    {
      "utc": "2026-11-04T00:00:00Z",
      "sun": {
        "azimuth_deg": 248.783468909,
        "elevation_deg": 0.038851459,
        "horizon_deg": 0.15087331467755266,
        "state": "partial",
        "next_setting": {
          "kind": "sunset",
          "estimated_at_utc": "2026-11-04T04:55:38.157360Z",
          "bracket_start_utc": "2026-11-04T04:55:00Z",
          "bracket_end_utc": "2026-11-04T04:56:00Z",
          "bracket_width_s": 60.0,
          "status": "found",
          "time_until_estimate_s": 17738.15736,
          "time_until_bracket_s": [
            17700.0,
            17760.0
          ]
        }
      },
      "earth": {
        "azimuth_deg": 5.814383908,
        "elevation_deg": 2.324147869,
        "horizon_deg": 4.388437666375163,
        "state": "blocked",
        "next_setting": {
          "status": "no_event_in_range"
        }
      }
    },
    {
      "utc": "2026-11-04T23:00:00Z",
      "sun": {
        "azimuth_deg": 237.126957453,
        "elevation_deg": -0.676192592,
        "horizon_deg": 0.13623288718889942,
        "state": "dark",
        "next_setting": {
          "status": "no_event_in_range"
        }
      },
      "earth": {
        "azimuth_deg": 5.883222974,
        "elevation_deg": 0.936199667,
        "horizon_deg": 4.386774624096286,
        "state": "blocked",
        "next_setting": {
          "status": "no_event_in_range"
        }
      }
    }
  ]
};
