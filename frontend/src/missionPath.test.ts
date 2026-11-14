import { describe, expect, test } from "vitest";
import {
  COVERAGE_MAX_LATITUDE_DEG,
  MISSION_PHASES,
  SCENE_LAYOUT,
  allPathPoints,
  buildTransferPath,
  headingAt,
  pathPointsUntil,
  phaseAt,
  positionAt,
  siteSelectionFromPoint,
  surfaceCoordinates,
  surfaceUnitVector,
  type Vec3,
} from "./missionPath";

const length = (v: Vec3) => Math.hypot(v[0], v[1], v[2]);
const distance = (a: Vec3, b: Vec3) =>
  Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const close = (a: Vec3, b: Vec3, tolerance = 1e-9) =>
  expect(distance(a, b)).toBeLessThan(tolerance);
const sites: [number, number][] = [
  [-85.9948, 359.76422],
  [-89.9, 0],
  [-90, 0],
  [-84.5, 90],
  [-86.2, 180],
  [-81, 270],
  [-87.3, 31.4],
];

describe("surface conversions", () => {
  test("uses +X for 0° E, -Z for 90° E and -Y for the south pole", () => {
    close(surfaceUnitVector(0, 0), [1, 0, 0]);
    close(surfaceUnitVector(0, 90), [0, 0, -1]);
    close(surfaceUnitVector(0, 180), [-1, 0, 0]);
    close(surfaceUnitVector(90, 0), [0, 1, 0]);
    close(surfaceUnitVector(-90, 123), [0, -1, 0]);
  });

  test("returns unit vectors and round-trips coordinates", () => {
    for (const [lat, lon] of [...sites, [12.5, 45.25] as [number, number]]) {
      const v = surfaceUnitVector(lat, lon);
      expect(length(v)).toBeCloseTo(1, 12);
      const back = surfaceCoordinates(v);
      expect(back.latitude_deg).toBeCloseTo(lat, 9);
      if (Math.abs(lat) < 89.999)
        expect(back.longitude_deg_east).toBeCloseTo(lon % 360, 9);
    }
  });

  test("normalizes longitude to [0, 360) and accepts non-unit directions", () => {
    const back = surfaceCoordinates([0, 0, 5]);
    expect(back.latitude_deg).toBeCloseTo(0, 12);
    expect(back.longitude_deg_east).toBeCloseTo(270, 12);
    const west = surfaceCoordinates(surfaceUnitVector(-85, -10));
    expect(west.longitude_deg_east).toBeCloseTo(350, 9);
    expect(west.longitude_deg_east).toBeGreaterThanOrEqual(0);
    expect(west.longitude_deg_east).toBeLessThan(360);
  });

  test("rejects invalid inputs loudly", () => {
    expect(() => surfaceUnitVector(91, 0)).toThrow();
    expect(() => surfaceUnitVector(Number.NaN, 0)).toThrow();
    expect(() => surfaceUnitVector(-85, Number.POSITIVE_INFINITY)).toThrow();
    expect(() => surfaceCoordinates([0, 0, 0])).toThrow();
    expect(() => surfaceCoordinates([Number.NaN, 0, 1])).toThrow();
  });
});

describe("site selection from a clicked point", () => {
  test("rounds to 5 decimals and flags the south-pole coverage", () => {
    const point = surfaceUnitVector(-85.123456789, 12.987654321).map(
      (c) => c * 1.0003,
    ) as unknown as Vec3;
    const selected = siteSelectionFromPoint(point);
    expect(selected.latitude_deg).toBe(-85.12346);
    expect(selected.longitude_deg_east).toBe(12.98765);
    expect(selected.within_coverage).toBe(true);
  });

  test("treats the coverage limit as inclusive and rejects the north", () => {
    expect(COVERAGE_MAX_LATITUDE_DEG).toBe(-80);
    expect(
      siteSelectionFromPoint(surfaceUnitVector(-80, 40)).within_coverage,
    ).toBe(true);
    expect(
      siteSelectionFromPoint(surfaceUnitVector(-79.9, 40)).within_coverage,
    ).toBe(false);
    expect(
      siteSelectionFromPoint(surfaceUnitVector(30, 40)).within_coverage,
    ).toBe(false);
  });

  test("never returns 360° after rounding", () => {
    const selected = siteSelectionFromPoint(
      surfaceUnitVector(-85, 359.999999),
    );
    expect(selected.longitude_deg_east).toBeGreaterThanOrEqual(0);
    expect(selected.longitude_deg_east).toBeLessThan(360);
  });
});

describe("mission phases", () => {
  test("cover [0, 1] contiguously in flight order", () => {
    expect(MISSION_PHASES.map((p) => p.id)).toEqual([
      "parking_orbit",
      "translunar_coast",
      "lunar_orbit",
      "descent",
      "landed",
    ]);
    expect(MISSION_PHASES[0].start_progress).toBe(0);
    expect(MISSION_PHASES[MISSION_PHASES.length - 1].end_progress).toBe(1);
    MISSION_PHASES.forEach((phase, i) => {
      expect(phase.end_progress).toBeGreaterThan(phase.start_progress);
      expect(phase.label.length).toBeGreaterThan(0);
      expect(phase.description.length).toBeGreaterThan(0);
      if (i > 0)
        expect(phase.start_progress).toBe(MISSION_PHASES[i - 1].end_progress);
    });
  });

  test("looks up the active phase and the progress inside it", () => {
    const first = phaseAt(0);
    expect(first.phase.id).toBe("parking_orbit");
    expect(first.index).toBe(0);
    expect(first.phase_progress).toBe(0);
    const coast = MISSION_PHASES[1];
    const middle = phaseAt((coast.start_progress + coast.end_progress) / 2);
    expect(middle.phase.id).toBe("translunar_coast");
    expect(middle.phase_progress).toBeCloseTo(0.5, 9);
    const boundary = phaseAt(coast.end_progress);
    expect(boundary.phase.id).toBe("lunar_orbit");
    expect(boundary.phase_progress).toBe(0);
    const end = phaseAt(1);
    expect(end.phase.id).toBe("landed");
    expect(end.index).toBe(MISSION_PHASES.length - 1);
    expect(end.phase_progress).toBe(1);
  });

  test("clamps out-of-range progress and rejects NaN", () => {
    expect(phaseAt(-0.5).phase.id).toBe("parking_orbit");
    expect(phaseAt(1.7).phase.id).toBe("landed");
    expect(() => phaseAt(Number.NaN)).toThrow();
  });
});

describe("transfer path", () => {
  test.each(sites)("starts in Earth orbit and lands on %f, %f", (lat, lon) => {
    const path = buildTransferPath(lat, lon);
    const site = surfaceUnitVector(lat, lon).map(
      (c) => c * SCENE_LAYOUT.moon_radius_scene,
    ) as unknown as Vec3;
    close(path.site_scene, site);
    close(positionAt(path, 1), site);
    close(positionAt(path, 5), site);
    expect(distance(positionAt(path, 0), SCENE_LAYOUT.earth_center_scene)).toBeCloseTo(
      SCENE_LAYOUT.parking_orbit_radius_scene,
      9,
    );
    close(positionAt(path, -1), positionAt(path, 0));
  });

  test.each(sites)("is continuous and clears both bodies for %f, %f", (lat, lon) => {
    const path = buildTransferPath(lat, lon);
    const descentStart = MISSION_PHASES.find((p) => p.id === "descent")!
      .start_progress;
    let previous = positionAt(path, 0);
    for (let i = 1; i <= 4000; i++) {
      const progress = i / 4000;
      const point = positionAt(path, progress);
      expect(distance(point, previous)).toBeLessThan(0.3);
      expect(distance(point, SCENE_LAYOUT.earth_center_scene)).toBeGreaterThan(
        SCENE_LAYOUT.earth_radius_scene * 1.05,
      );
      const fromMoon = distance(point, SCENE_LAYOUT.moon_center_scene);
      if (progress < descentStart)
        expect(fromMoon).toBeGreaterThan(SCENE_LAYOUT.moon_radius_scene * 1.2);
      else
        expect(fromMoon).toBeGreaterThanOrEqual(
          SCENE_LAYOUT.moon_radius_scene - 1e-9,
        );
      previous = point;
    }
  });

  test("keeps the lunar orbit at the orbit radius and descends monotonically", () => {
    const path = buildTransferPath(-85.9948, 359.76422);
    const orbit = MISSION_PHASES.find((p) => p.id === "lunar_orbit")!;
    const descent = MISSION_PHASES.find((p) => p.id === "descent")!;
    for (let i = 0; i <= 50; i++) {
      const t =
        orbit.start_progress +
        ((orbit.end_progress - orbit.start_progress) * i) / 50;
      expect(
        distance(positionAt(path, t), SCENE_LAYOUT.moon_center_scene),
      ).toBeCloseTo(SCENE_LAYOUT.lunar_orbit_radius_scene, 6);
    }
    let previous = Number.POSITIVE_INFINITY;
    for (let i = 0; i <= 200; i++) {
      const t =
        descent.start_progress +
        ((descent.end_progress - descent.start_progress) * i) / 200;
      const r = distance(positionAt(path, t), SCENE_LAYOUT.moon_center_scene);
      expect(r).toBeLessThanOrEqual(previous + 1e-9);
      previous = r;
    }
    expect(previous).toBeCloseTo(SCENE_LAYOUT.moon_radius_scene, 9);
  });

  test("describes the flown trail and the direction of travel", () => {
    const path = buildTransferPath(-86.2, 180);
    const all = allPathPoints(path);
    expect(all.length).toBeGreaterThan(100);
    close(all[0], positionAt(path, 0));
    close(all[all.length - 1], path.site_scene);
    expect(pathPointsUntil(path, 0).length).toBeGreaterThanOrEqual(1);
    const halfway = pathPointsUntil(path, 0.5);
    close(halfway[halfway.length - 1], positionAt(path, 0.5));
    expect(halfway.length).toBeLessThan(all.length);
    expect(pathPointsUntil(path, 1).length).toBeGreaterThanOrEqual(
      all.length - 1,
    );
    const heading = headingAt(path, 0.3);
    expect(length(heading)).toBeCloseTo(1, 9);
    const ahead = positionAt(path, 0.3005);
    const here = positionAt(path, 0.3);
    const step = [ahead[0] - here[0], ahead[1] - here[1], ahead[2] - here[2]];
    const dot =
      (step[0] * heading[0] + step[1] * heading[1] + step[2] * heading[2]) /
      Math.hypot(...step);
    expect(dot).toBeGreaterThan(0.9);
    expect(length(headingAt(path, 1))).toBe(0);
  });

  test("is deterministic and rejects invalid sites", () => {
    expect(allPathPoints(buildTransferPath(-87.3, 31.4))).toEqual(
      allPathPoints(buildTransferPath(-87.3, 31.4)),
    );
    expect(() => buildTransferPath(Number.NaN, 0)).toThrow();
    expect(() => buildTransferPath(-95, 0)).toThrow();
    expect(() => buildTransferPath(-85, 0, SCENE_LAYOUT, 1)).toThrow();
    expect(() => positionAt(buildTransferPath(-85, 0), Number.NaN)).toThrow();
  });
});
