/**
 * Illustrative Earth → Moon flight geometry for the mission-control scene.
 *
 * Everything here lives in the "illustrative scene frame": dimensionless scene
 * units, Moon centre at the origin, +Y towards the lunar north pole and +X
 * towards the 0° E meridian (the Earth-facing side). One scene unit is one
 * mean lunar radius, so Earth's and the Moon's radii, their mean separation and
 * the parking/lunar orbit altitudes are to scale. The path itself is NOT a
 * computed trajectory; it only explains the order of mission phases.
 * Scientific results come from the backend.
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

// Reference sizes for the display scale (IAU 2015 nominal radii; IAU 2012
// astronomical unit; mean Earth–Moon distance from NASA's Moon fact sheet).
export const MOON_MEAN_RADIUS_KM = 1737.4;
export const EARTH_MEAN_RADIUS_KM = 6371.0;
export const EARTH_MOON_MEAN_DISTANCE_KM = 384_400;
export const SUN_NOMINAL_RADIUS_KM = 695_700;
export const ASTRONOMICAL_UNIT_KM = 149_597_870.7;
/** Illustrative circular orbit altitudes (typical LEO and low lunar orbit). */
const PARKING_ORBIT_ALTITUDE_KM = 400;
const LUNAR_ORBIT_ALTITUDE_KM = 100;

const sceneUnits = (length_km: number) => length_km / MOON_MEAN_RADIUS_KM;

/** Default layout: true relative sizes and Earth–Moon distance. */
export const SCENE_LAYOUT: SceneLayout = {
  moon_center_scene: [0, 0, 0],
  moon_radius_scene: 1,
  earth_center_scene: [sceneUnits(EARTH_MOON_MEAN_DISTANCE_KM), 0, 0],
  earth_radius_scene: sceneUnits(EARTH_MEAN_RADIUS_KM),
  parking_orbit_radius_scene: sceneUnits(
    EARTH_MEAN_RADIUS_KM + PARKING_ORBIT_ALTITUDE_KM,
  ),
  lunar_orbit_radius_scene: sceneUnits(
    MOON_MEAN_RADIUS_KM + LUNAR_ORBIT_ALTITUDE_KM,
  ),
};

/** Northern limit of the cached south-pole terrain product. */
export const COVERAGE_MAX_LATITUDE_DEG = -80;

export type PhaseId =
  | "launch"
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

export const MISSION_PHASES: readonly MissionPhase[] = [
  {
    id: "launch",
    label: "Launch",
    description:
      "The three-stage launch vehicle lifts off, drops its spent stages and fairing, and reaches a low parking orbit.",
    start_progress: 0,
    end_progress: 0.2,
  },
  {
    id: "parking_orbit",
    label: "Earth parking orbit",
    description:
      "The spacecraft circles Earth while systems are checked before departure.",
    start_progress: 0.2,
    end_progress: 0.26,
  },
  {
    id: "translunar_coast",
    label: "Translunar coast",
    description:
      "After the departure burn the spacecraft coasts towards the Moon for several days.",
    start_progress: 0.26,
    end_progress: 0.55,
  },
  {
    id: "lunar_orbit",
    label: "Polar lunar orbit",
    description:
      "A braking burn behind the Moon captures the spacecraft into a polar orbit over the landing longitude.",
    start_progress: 0.55,
    end_progress: 0.8,
  },
  {
    id: "descent",
    label: "Powered descent",
    description:
      "The lander leaves orbit and descends towards the selected south-polar site.",
    start_progress: 0.8,
    end_progress: 0.96,
  },
  {
    id: "landed",
    label: "On the surface",
    description:
      "The lander is at the site. Run the analysis to see what Sun and Earth do from here.",
    start_progress: 0.96,
    end_progress: 1,
  },
];

/**
 * Progress at which mission control cuts from the orbital scene to the
 * art-directed landing close-up (a quarter of the way into powered descent).
 */
export const LANDING_CLOSEUP_START_PROGRESS = 0.84;

/**
 * Progress at which mission control cuts from the art-directed launch close-up
 * back to the spacecraft view, shortly before parking-orbit insertion.
 */
export const LAUNCH_CLOSEUP_END_PROGRESS = 0.18;

export type SiteSelection = {
  latitude_deg: number;
  longitude_deg_east: number;
  within_coverage: boolean;
};

type FlightSegment = Exclude<PhaseId, "landed">;

export type TransferPath = {
  layout: SceneLayout;
  site_scene: Vec3;
  /** Sampled vertices per phase for drawing; consecutive segments share ends. */
  segments: Record<FlightSegment, Vec3[]>;
  /** Exact position along each segment for a segment fraction in [0, 1]. */
  evaluate: Record<FlightSegment, (fraction: number) => Vec3>;
};

const RAD_PER_DEG = Math.PI / 180;
const DESCENT_SWEEP_RAD = 0.45;
const LUNAR_ORBIT_SWEEP_RAD = 1.5 * Math.PI;
const PARKING_SWEEP_RAD = 1.5 * Math.PI;
/** Downrange angle flown around Earth during the illustrative ascent. */
const LAUNCH_SWEEP_RAD = 0.5;

const requireFinite = (value: number, name: string) => {
  if (!Number.isFinite(value))
    throw new RangeError(`${name} must be a finite number, got ${value}`);
};
const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const scale = (a: Vec3, k: number): Vec3 => [a[0] * k, a[1] * k, a[2] * k];
const norm = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);
const unit = (a: Vec3): Vec3 => {
  const n = norm(a);
  if (!(n > 0)) throw new RangeError("Cannot normalize a zero vector");
  return scale(a, 1 / n);
};
const round5 = (value: number) => Math.round(value * 1e5) / 1e5;
const smoothstep = (t: number) => t * t * (3 - 2 * t);

export function surfaceUnitVector(
  latitude_deg: number,
  longitude_deg_east: number,
): Vec3 {
  requireFinite(latitude_deg, "latitude_deg");
  requireFinite(longitude_deg_east, "longitude_deg_east");
  if (Math.abs(latitude_deg) > 90)
    throw new RangeError(`latitude_deg must be within ±90, got ${latitude_deg}`);
  const latitude_rad = latitude_deg * RAD_PER_DEG;
  const longitude_rad = longitude_deg_east * RAD_PER_DEG;
  return [
    Math.cos(longitude_rad) * Math.cos(latitude_rad),
    Math.sin(latitude_rad),
    -Math.sin(longitude_rad) * Math.cos(latitude_rad),
  ];
}

export function surfaceCoordinates(direction_scene: Vec3): {
  latitude_deg: number;
  longitude_deg_east: number;
} {
  direction_scene.forEach((c, i) => requireFinite(c, `direction_scene[${i}]`));
  const [x, y, z] = unit(direction_scene);
  const latitude_deg = Math.asin(Math.max(-1, Math.min(1, y))) / RAD_PER_DEG;
  const longitude_deg_east =
    (((Math.atan2(-z, x) / RAD_PER_DEG) % 360) + 360) % 360;
  return { latitude_deg, longitude_deg_east };
}

export function siteSelectionFromPoint(point_scene: Vec3): SiteSelection {
  const { latitude_deg, longitude_deg_east } = surfaceCoordinates(point_scene);
  const latitude = round5(latitude_deg);
  let longitude = round5(longitude_deg_east);
  if (longitude >= 360) longitude = 0;
  return {
    latitude_deg: latitude,
    longitude_deg_east: longitude,
    within_coverage: latitude <= COVERAGE_MAX_LATITUDE_DEG,
  };
}

export function phaseAt(progress: number): {
  phase: MissionPhase;
  index: number;
  phase_progress: number;
} {
  requireFinite(progress, "progress");
  const p = Math.max(0, Math.min(1, progress));
  let index = MISSION_PHASES.findIndex((phase) => p < phase.end_progress);
  if (index < 0) index = MISSION_PHASES.length - 1;
  const phase = MISSION_PHASES[index];
  const phase_progress = Math.max(
    0,
    Math.min(
      1,
      (p - phase.start_progress) / (phase.end_progress - phase.start_progress),
    ),
  );
  return { phase, index, phase_progress };
}

export function buildTransferPath(
  latitude_deg: number,
  longitude_deg_east: number,
  layout: SceneLayout = SCENE_LAYOUT,
  samples_per_segment = 96,
): TransferPath {
  if (!Number.isInteger(samples_per_segment) || samples_per_segment < 2)
    throw new RangeError("samples_per_segment must be an integer of at least 2");
  const site_unit = surfaceUnitVector(latitude_deg, longitude_deg_east);
  const moon = layout.moon_center_scene;
  const earth = layout.earth_center_scene;
  const moon_r = layout.moon_radius_scene;
  const orbit_r = layout.lunar_orbit_radius_scene;
  const parking_r = layout.parking_orbit_radius_scene;
  const site_scene = add(moon, scale(site_unit, moon_r));

  // Polar orbit plane: spanned by the site's horizontal direction and +Y.
  const horizontal_length = Math.hypot(site_unit[0], site_unit[2]);
  const horizontal: Vec3 =
    horizontal_length < 1e-9
      ? [1, 0, 0]
      : [site_unit[0] / horizontal_length, 0, site_unit[2] / horizontal_length];
  const site_angle_rad = Math.atan2(site_unit[1], horizontal_length);
  const orbitPoint = (angle_rad: number, radius: number): Vec3 =>
    add(
      moon,
      add(
        scale(horizontal, Math.cos(angle_rad) * radius),
        [0, Math.sin(angle_rad) * radius, 0],
      ),
    );
  const descent_start_rad = site_angle_rad + DESCENT_SWEEP_RAD;
  const capture_rad = descent_start_rad + LUNAR_ORBIT_SWEEP_RAD;

  const parking = (f: number): Vec3 => {
    const angle_rad = (1 - f) * PARKING_SWEEP_RAD;
    return add(earth, [
      Math.sin(angle_rad) * parking_r,
      0,
      Math.cos(angle_rad) * parking_r,
    ]);
  };
  // Gravity turn: vertical at lift-off (radius grows first), horizontal and
  // tangent to the parking orbit at insertion (the angle changes last).
  const earth_r = layout.earth_radius_scene;
  const launch = (f: number): Vec3 => {
    const angle_rad = PARKING_SWEEP_RAD + LAUNCH_SWEEP_RAD * (1 - f * f);
    const radius = earth_r + (parking_r - earth_r) * (1 - (1 - f) * (1 - f));
    return add(earth, [
      Math.sin(angle_rad) * radius,
      0,
      Math.cos(angle_rad) * radius,
    ]);
  };
  const departure = parking(1);
  const departure_heading = unit(sub(parking(1), parking(1 - 1e-6)));
  const capture = orbitPoint(capture_rad, orbit_r);
  const capture_heading: Vec3 = unit(
    add(scale(horizontal, Math.sin(capture_rad)), [0, -Math.cos(capture_rad), 0]),
  );
  const separation = norm(sub(earth, moon));
  const c1 = add(departure, scale(departure_heading, separation * 0.4));
  const c2 = sub(capture, scale(capture_heading, separation * 0.25));
  const pushOutside = (point: Vec3, center: Vec3, radius: number): Vec3 => {
    const offset = sub(point, center);
    const d = norm(offset);
    return d < radius ? add(center, scale(unit(offset), radius)) : point;
  };
  const coast = (f: number): Vec3 => {
    const g = 1 - f;
    const raw = add(
      add(scale(departure, g * g * g), scale(c1, 3 * g * g * f)),
      add(scale(c2, 3 * g * f * f), scale(capture, f * f * f)),
    );
    return pushOutside(
      pushOutside(raw, moon, orbit_r),
      earth,
      parking_r,
    );
  };
  const lunar_orbit = (f: number): Vec3 =>
    orbitPoint(capture_rad - f * LUNAR_ORBIT_SWEEP_RAD, orbit_r);
  const descent = (f: number): Vec3 =>
    f >= 1
      ? site_scene
      : orbitPoint(
          descent_start_rad - f * DESCENT_SWEEP_RAD,
          orbit_r + (moon_r - orbit_r) * smoothstep(f),
        );

  const evaluate: Record<FlightSegment, (fraction: number) => Vec3> = {
    launch,
    parking_orbit: parking,
    translunar_coast: coast,
    lunar_orbit,
    descent,
  };
  const sample = (curve: (f: number) => Vec3) =>
    Array.from({ length: samples_per_segment }, (_, i) =>
      curve(i / (samples_per_segment - 1)),
    );
  return {
    layout,
    site_scene,
    segments: {
      launch: sample(launch),
      parking_orbit: sample(parking),
      translunar_coast: sample(coast),
      lunar_orbit: sample(lunar_orbit),
      descent: sample(descent),
    },
    evaluate,
  };
}

export function positionAt(path: TransferPath, progress: number): Vec3 {
  const { phase, phase_progress } = phaseAt(progress);
  if (phase.id === "landed") return path.site_scene;
  return path.evaluate[phase.id](phase_progress);
}

export function headingAt(path: TransferPath, progress: number): Vec3 {
  const { phase, phase_progress } = phaseAt(progress);
  if (phase.id === "landed") return [0, 0, 0];
  const curve = path.evaluate[phase.id];
  const step = 1e-4;
  const ahead = curve(Math.min(1, phase_progress + step));
  const behind = curve(Math.max(0, phase_progress - step));
  return unit(sub(ahead, behind));
}

const flightOrder: FlightSegment[] = [
  "launch",
  "parking_orbit",
  "translunar_coast",
  "lunar_orbit",
  "descent",
];

export function pathPointsUntil(path: TransferPath, progress: number): Vec3[] {
  const { phase, index, phase_progress } = phaseAt(progress);
  if (phase.id === "landed") return allPathPoints(path);
  const points: Vec3[] = [];
  for (let i = 0; i < index; i++) {
    const segment = path.segments[flightOrder[i]];
    points.push(...(i === 0 ? segment : segment.slice(1)));
  }
  const current = path.segments[phase.id];
  const completed = Math.floor(phase_progress * (current.length - 1));
  points.push(...current.slice(index === 0 ? 0 : 1, completed + 1));
  points.push(positionAt(path, progress));
  return points;
}

export function allPathPoints(path: TransferPath): Vec3[] {
  return flightOrder.flatMap((id, i) =>
    i === 0 ? path.segments[id] : path.segments[id].slice(1),
  );
}

// ---------------------------------------------------------------------------
// Flight views: where Earth, the Moon, the Sun and the spacecraft are drawn
// at a given mission progress. The static view is the original fixed layout
// (Moon at the origin, Earth on +X). The live view places bodies from the
// backend's JPL Horizons geocentric vectors (Earth at the origin) and moves
// the illustrative path with them. No ephemeris is computed here: samples
// are only interpolated and mapped to scene axes for display.
// ---------------------------------------------------------------------------

/** Backend /flight-ephemeris result (subset used by the scene). */
export type FlightEphemeris = {
  window: {
    launch_utc: string;
    landing_utc: string;
    phases: { id: FlightSegment; start_utc: string; end_utc: string }[];
  };
  samples: {
    utc: string;
    moon_geocentric_km: Vec3;
    sun_geocentric_km: Vec3;
    earth_rotation_deg: number;
  }[];
};

/** Moon frame in world coordinates: origin and unit axes (x towards Earth). */
export type MoonPose = { position: Vec3; x: Vec3; y: Vec3; z: Vec3 };

export type FlightView = {
  live: boolean;
  earthCenter: (progress: number) => Vec3;
  moonPose: (progress: number) => MoonPose;
  sunDirection: (progress: number) => Vec3;
  /** Earth's rotation about its axis in radians, or null for the static spin. */
  earthRotationRad: (progress: number) => number | null;
  /** Earth's equator tilt as an X-axis (live) or Z-axis (static) rotation. */
  earthTilt: [number, number, number];
  positionAt: (progress: number) => Vec3;
  headingAt: (progress: number) => Vec3;
  pointsUntil: (progress: number) => Vec3[];
  allPoints: Vec3[];
  parkingRing: Vec3[];
  /** UTC milliseconds at a progress, or null without dated ephemeris. */
  utcAt: (progress: number) => number | null;
};

/** Obliquity of the ecliptic at J2000.0 (IAU 2006: 84381.406″). */
const OBLIQUITY_J2000_RAD = (84381.406 / 3600) * RAD_PER_DEG;
const STATIC_EARTH_TILT_RAD = 23.4 * RAD_PER_DEG;
const IDENTITY_POSE = (center: Vec3): MoonPose => ({
  position: center,
  x: [1, 0, 0],
  y: [0, 1, 0],
  z: [0, 0, 1],
});
const cross = (a: Vec3, b: Vec3): Vec3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const lerp3 = (a: Vec3, b: Vec3, t: number): Vec3 => [
  a[0] + (b[0] - a[0]) * t,
  a[1] + (b[1] - a[1]) * t,
  a[2] + (b[2] - a[2]) * t,
];
/** Moon-frame point to world through a pose. */
export const applyPose = (pose: MoonPose, v: Vec3): Vec3 =>
  add(
    pose.position,
    add(add(scale(pose.x, v[0]), scale(pose.y, v[1])), scale(pose.z, v[2])),
  );
const rotateByPose = (pose: MoonPose, v: Vec3): Vec3 =>
  add(add(scale(pose.x, v[0]), scale(pose.y, v[1])), scale(pose.z, v[2]));

function ringAround(center: Vec3, radius: number, rotate: (v: Vec3) => Vec3) {
  return Array.from({ length: 129 }, (_, i) => {
    const a = (i / 128) * Math.PI * 2;
    return add(center, rotate([Math.sin(a) * radius, 0, Math.cos(a) * radius]));
  });
}

function pointsUntilFrom(
  segments: Record<FlightSegment, Vec3[]>,
  position: (progress: number) => Vec3,
  progress: number,
): Vec3[] {
  const { phase, index, phase_progress } = phaseAt(progress);
  const all = () =>
    flightOrder.flatMap((id, i) =>
      i === 0 ? segments[id] : segments[id].slice(1),
    );
  if (phase.id === "landed") return all();
  const points: Vec3[] = [];
  for (let i = 0; i < index; i++) {
    const segment = segments[flightOrder[i]];
    points.push(...(i === 0 ? segment : segment.slice(1)));
  }
  const current = segments[phase.id];
  const completed = Math.floor(phase_progress * (current.length - 1));
  points.push(...current.slice(index === 0 ? 0 : 1, completed + 1));
  points.push(position(progress));
  return points;
}

/** The original fixed layout: nothing moves except the spacecraft. */
export function staticFlightView(path: TransferPath, sun_direction: Vec3): FlightView {
  const earth = path.layout.earth_center_scene;
  const sun = unit(sun_direction);
  return {
    live: false,
    earthCenter: () => earth,
    moonPose: () => IDENTITY_POSE(path.layout.moon_center_scene),
    sunDirection: () => sun,
    earthRotationRad: () => null,
    earthTilt: [0, 0, STATIC_EARTH_TILT_RAD],
    positionAt: (p) => positionAt(path, p),
    headingAt: (p) => headingAt(path, p),
    pointsUntil: (p) => pathPointsUntil(path, p),
    allPoints: allPathPoints(path),
    parkingRing: ringAround(earth, path.layout.parking_orbit_radius_scene, (v) => v),
    utcAt: () => null,
  };
}

/** Ecliptic J2000 km (x equinox, z north) to scene units (x, y up, z). */
const eclipticToScene = (km: Vec3): Vec3 =>
  scale([km[0], km[2], -km[1]], 1 / MOON_MEAN_RADIUS_KM);

/**
 * Bodies from dated Horizons samples, with the illustrative path re-anchored:
 * Earth segments ride with Earth (fixed in the translunar-injection
 * orientation), the coast aims at where the Moon will be at lunar orbit
 * insertion, and lunar segments ride with the moving Moon.
 */
export function liveFlightView(path: TransferPath, flight: FlightEphemeris): FlightView {
  const times = flight.samples.map((s) => Date.parse(s.utc));
  if (times.length < 2 || times.some((t, i) => i > 0 && !(t > times[i - 1])))
    throw new RangeError("Flight ephemeris needs at least two increasing samples");
  const moonKm = flight.samples.map((s) => eclipticToScene(s.moon_geocentric_km));
  const sunKm = flight.samples.map((s) => eclipticToScene(s.sun_geocentric_km));
  // Unwrap the rotation angle so interpolation never runs backwards.
  const era: number[] = [];
  flight.samples.forEach((s, i) => {
    let value = s.earth_rotation_deg * RAD_PER_DEG;
    if (i > 0) while (value < era[i - 1]) value += 2 * Math.PI;
    era.push(value);
  });
  const sampleAt = (ms: number) => {
    let i = 1;
    while (i < times.length - 1 && times[i] < ms) i++;
    const t = Math.max(0, Math.min(1, (ms - times[i - 1]) / (times[i] - times[i - 1])));
    return { i, t };
  };
  const phases = new Map(
    flight.window.phases.map((p) => [p.id, [Date.parse(p.start_utc), Date.parse(p.end_utc)]]),
  );
  const landing = Date.parse(flight.window.landing_utc);
  const launch = Date.parse(flight.window.launch_utc);
  const timeOf = (segment: FlightSegment, fraction: number) => {
    const [start, end] = phases.get(segment) ?? [launch, landing];
    return start + (end - start) * fraction;
  };
  const utcAt = (progress: number) => {
    const { phase, phase_progress } = phaseAt(progress);
    return phase.id === "landed" ? landing : timeOf(phase.id, phase_progress);
  };
  const moonAt = (ms: number): MoonPose => {
    const { i, t } = sampleAt(ms);
    const position = lerp3(moonKm[i - 1], moonKm[i], t);
    const x = unit(scale(position, -1));
    const up: Vec3 = [0, 1, 0];
    const y = unit(sub(up, scale(x, dot(up, x))));
    return { position, x, y, z: cross(x, y) };
  };
  const earthCenter: Vec3 = [0, 0, 0];
  const earthOffset = path.layout.earth_center_scene;
  const tliPose = moonAt(timeOf("translunar_coast", 0));
  const fromEarthFrame = (v: Vec3) => rotateByPose(tliPose, sub(v, earthOffset));
  const loiPose = moonAt(timeOf("lunar_orbit", 0));
  const { evaluate } = path;
  const step = 1e-4;
  const departure = fromEarthFrame(evaluate.parking_orbit(1));
  const departureHeading = unit(
    rotateByPose(tliPose, sub(evaluate.parking_orbit(1), evaluate.parking_orbit(1 - step))),
  );
  const capture = applyPose(loiPose, evaluate.lunar_orbit(0));
  const captureHeading = unit(
    rotateByPose(loiPose, sub(evaluate.lunar_orbit(step), evaluate.lunar_orbit(0))),
  );
  const separation = norm(sub(capture, departure));
  const c1 = add(departure, scale(departureHeading, separation * 0.4));
  const c2 = sub(capture, scale(captureHeading, separation * 0.25));
  const orbitRadius = path.layout.lunar_orbit_radius_scene;
  const parkingRadius = path.layout.parking_orbit_radius_scene;
  const outside = (point: Vec3, center: Vec3, radius: number): Vec3 => {
    const offset = sub(point, center);
    const d = norm(offset);
    return d < radius ? add(center, scale(unit(offset), radius)) : point;
  };
  const world: Record<FlightSegment, (f: number) => Vec3> = {
    launch: (f) => fromEarthFrame(evaluate.launch(f)),
    parking_orbit: (f) => fromEarthFrame(evaluate.parking_orbit(f)),
    translunar_coast: (f) => {
      const g = 1 - f;
      const raw = add(
        add(scale(departure, g * g * g), scale(c1, 3 * g * g * f)),
        add(scale(c2, 3 * g * f * f), scale(capture, f * f * f)),
      );
      const moon = moonAt(timeOf("translunar_coast", f)).position;
      return outside(outside(raw, moon, orbitRadius), earthCenter, parkingRadius);
    },
    lunar_orbit: (f) =>
      applyPose(moonAt(timeOf("lunar_orbit", f)), evaluate.lunar_orbit(f)),
    descent: (f) => applyPose(moonAt(timeOf("descent", f)), evaluate.descent(f)),
  };
  const samples = path.segments.launch.length;
  const segments = Object.fromEntries(
    flightOrder.map((id) => [
      id,
      Array.from({ length: samples }, (_, i) => world[id](i / (samples - 1))),
    ]),
  ) as Record<FlightSegment, Vec3[]>;
  const position = (progress: number): Vec3 => {
    const { phase, phase_progress } = phaseAt(progress);
    if (phase.id === "landed")
      return applyPose(moonAt(landing), path.site_scene);
    return world[phase.id](phase_progress);
  };
  return {
    live: true,
    earthCenter: () => earthCenter,
    moonPose: (p) => moonAt(utcAt(p)),
    sunDirection: (p) => {
      const { i, t } = sampleAt(utcAt(p));
      return unit(lerp3(sunKm[i - 1], sunKm[i], t));
    },
    earthRotationRad: (p) => {
      const { i, t } = sampleAt(utcAt(p));
      return era[i - 1] + (era[i] - era[i - 1]) * t;
    },
    // The north celestial pole lies at ecliptic longitude 90°, i.e. scene
    // +Y tilted towards −Z: a negative rotation about the equinox (X) axis.
    earthTilt: [-OBLIQUITY_J2000_RAD, 0, 0],
    positionAt: position,
    headingAt: (p) => {
      const ahead = position(Math.min(1, p + 1e-4));
      const behind = position(Math.max(0, p - 1e-4));
      const delta = sub(ahead, behind);
      return norm(delta) > 0 ? unit(delta) : [0, 0, 0];
    },
    pointsUntil: (p) => pointsUntilFrom(segments, position, p),
    allPoints: flightOrder.flatMap((id, i) =>
      i === 0 ? segments[id] : segments[id].slice(1),
    ),
    parkingRing: ringAround(earthCenter, parkingRadius, (v) => rotateByPose(tliPose, v)),
    utcAt,
  };
}
