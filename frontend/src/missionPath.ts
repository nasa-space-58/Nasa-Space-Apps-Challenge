/**
 * Illustrative Earth → Moon flight geometry for the mission-control scene.
 *
 * Everything here lives in the "illustrative scene frame": dimensionless scene
 * units, Moon centre at the origin, +Y towards the lunar north pole and +X
 * towards the 0° E meridian (the Earth-facing side). Sizes and distances are
 * NOT to scale and the path is NOT a computed trajectory; it only explains the
 * order of mission phases. Scientific results come from the backend.
 *
 * Angles cross this module's boundary in degrees (suffix `_deg`) and are
 * handled in radians internally.
 */

export type Vec3 = readonly [number, number, number];

export type SceneLayout = {
  moon_center_scene: Vec3;
  moon_radius_scene: number;
  earth_center_scene: Vec3;
  earth_radius_scene: number;
  parking_orbit_radius_scene: number;
  lunar_orbit_radius_scene: number;
};

/** Default illustrative layout (not to scale). */
export const SCENE_LAYOUT: SceneLayout = {
  moon_center_scene: [0, 0, 0],
  moon_radius_scene: 1,
  earth_center_scene: [14, 0, 0],
  earth_radius_scene: 2.2,
  parking_orbit_radius_scene: 3,
  lunar_orbit_radius_scene: 1.6,
};

/** Northern limit of the cached south-pole terrain product. */
export const COVERAGE_MAX_LATITUDE_DEG = -80;

export type PhaseId =
  | "parking_orbit"
  | "translunar_coast"
  | "lunar_orbit"
  | "descent"
  | "landed";

export type MissionPhase = {
  id: PhaseId;
  label: string;
  description: string;
  /** Inclusive start of the phase as a fraction of total mission progress. */
  start_progress: number;
  /** Exclusive end (inclusive for the final phase). */
  end_progress: number;
};

export const MISSION_PHASES: readonly MissionPhase[] = [];

export type SiteSelection = {
  latitude_deg: number;
  longitude_deg_east: number;
  within_coverage: boolean;
};

export type TransferPath = {
  layout: SceneLayout;
  site_scene: Vec3;
  segments: Record<Exclude<PhaseId, "landed">, Vec3[]>;
};

/** Unit vector (scene frame) for a selenographic latitude / east longitude. */
export function surfaceUnitVector(
  latitude_deg: number,
  longitude_deg_east: number,
): Vec3 {
  void latitude_deg;
  void longitude_deg_east;
  throw new Error("not implemented");
}

/** Latitude / east longitude (degrees) of a scene-frame direction. */
export function surfaceCoordinates(direction_scene: Vec3): {
  latitude_deg: number;
  longitude_deg_east: number;
} {
  void direction_scene;
  throw new Error("not implemented");
}

/**
 * Converts a clicked point (relative to the Moon centre, scene frame) to a
 * site selection rounded to 5 decimals, flagging whether the cached
 * south-pole terrain covers it.
 */
export function siteSelectionFromPoint(point_scene: Vec3): SiteSelection {
  void point_scene;
  throw new Error("not implemented");
}

/** Phase active at a mission progress fraction in [0, 1]. */
export function phaseAt(progress: number): {
  phase: MissionPhase;
  index: number;
  phase_progress: number;
} {
  void progress;
  throw new Error("not implemented");
}

/** Builds the illustrative path from Earth parking orbit to the site. */
export function buildTransferPath(
  latitude_deg: number,
  longitude_deg_east: number,
  layout: SceneLayout = SCENE_LAYOUT,
  samples_per_segment = 96,
): TransferPath {
  void latitude_deg;
  void longitude_deg_east;
  void layout;
  void samples_per_segment;
  throw new Error("not implemented");
}

/** Spacecraft position at a mission progress fraction. */
export function positionAt(path: TransferPath, progress: number): Vec3 {
  void path;
  void progress;
  throw new Error("not implemented");
}

/** Unit direction of travel at a progress fraction (zero vector once landed). */
export function headingAt(path: TransferPath, progress: number): Vec3 {
  void path;
  void progress;
  throw new Error("not implemented");
}

/** Path vertices flown so far, ending exactly at the current position. */
export function pathPointsUntil(path: TransferPath, progress: number): Vec3[] {
  void path;
  void progress;
  throw new Error("not implemented");
}

/** Every vertex of the full path in flight order. */
export function allPathPoints(path: TransferPath): Vec3[] {
  void path;
  throw new Error("not implemented");
}
