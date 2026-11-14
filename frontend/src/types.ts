export type Preset = {
  site_id: string;
  display_name: string;
  latitude_deg: number;
  longitude_deg_east: number;
  coordinate_source_url: string;
  proposed_observer_height_above_terrain_m: number;
};
export type Job = { id: string; status: string; error: string | null };
export type Setting = {
  status: string;
  estimated_at_utc?: string;
  bracket_start_utc?: string;
  bracket_end_utc?: string;
  time_until_estimate_s?: number;
};
export type BodyFrame = {
  azimuth_deg: number | null;
  elevation_deg: number | null;
  horizon_deg: number | null;
  state: string;
  next_setting: Setting;
};
export type Frame = { utc: string; sun: BodyFrame; earth: BodyFrame };
export type Interval = {
  start_utc: string;
  end_utc: string;
  state: string;
  duration_s: number;
};
export type Metrics = {
  visible_duration_s: number;
  visible_percent_of_interval: number;
  longest_blocked_s: number;
  longest_visible_s: number;
  unknown_duration_s: number;
  full_duration_s: number;
  partial_duration_s: number;
};
export type Result = {
  analysis: {
    analysis_id: string;
    request: {
      site: {
        site_id: string;
        latitude_deg: number;
        longitude_deg_east: number;
        altitude_km: number;
      };
      start_utc: string;
      end_utc: string;
    };
    sun: { metrics: Metrics; intervals: Interval[] };
    earth: { metrics: Metrics; intervals: Interval[] };
    provenance: { ephemerides: { reference: string }[] };
    refinement: { status: string };
    spatial_sensitivity: { offset_deg: number };
    events: { kind: string; bracket_width_s: number }[];
  };
  horizon: {
    directions: { azimuth_deg: number; elevation_deg: number | null }[];
    settings: { search_distance_m: number; azimuth_step_deg: number };
    source_url: string;
    observer_height_above_terrain_m: number;
  };
  frames: Frame[];
};
