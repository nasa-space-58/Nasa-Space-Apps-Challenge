import * as THREE from "three";

/**
 * Opt-in R8 scene payload from POST /surface/routes (include_scene). All
 * values come from the backend: the DEM window around the routes (heights
 * relative to the landing cell), the slope-limit traversability, the affine
 * from window cell coordinates to local east/north metres and the A* search
 * order per target. This module only maps them into scene units.
 */
export type SurfaceScene = {
  pixel_m: number;
  slope_limit_deg: number;
  rows: number;
  cols: number;
  window: { row0: number; col0: number; row1: number; col1: number };
  site_cell: [number, number];
  heights_m: (number | null)[][];
  passable: boolean[][];
  local_from_cell: {
    east: [number, number, number];
    north: [number, number, number];
  };
  explored: Record<string, [number, number][]>;
  explored_truncated: Record<string, boolean>;
  path_cells: Record<string, [number, number][]>;
};

/** Scan timeline shares of the 0–1 scan clock (display pacing only). */
export const PULSE_END = 0.4;
export const SEARCH_END = 0.85;

/** The longest route spans this many scene units (same as the art terrain). */
const ROUTE_SPAN_SCENE = 22;

/**
 * Maps the DEM window into scene units with one uniform scale on all three
 * axes (east → +x, north → −z, height → +y), so the relief keeps its true
 * proportions. The rover model itself is not to scale.
 */
export function sceneMapping(scene: SurfaceScene) {
  const { rows, cols } = scene;
  const [ea, eb, ec] = scene.local_from_cell.east;
  const [na, nb, nc] = scene.local_from_cell.north;
  const det = ea * nb - eb * na;

  // Unknown (null) heights take the mean of their known neighbours, else 0;
  // they stay marked impassable by the backend.
  const heights = new Float32Array(rows * cols);
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++) {
      const value = scene.heights_m[r][c];
      if (value !== null) {
        heights[r * cols + c] = value;
        continue;
      }
      let sum = 0;
      let count = 0;
      for (let dr = -1; dr <= 1; dr++)
        for (let dc = -1; dc <= 1; dc++) {
          const n = scene.heights_m[r + dr]?.[c + dc];
          if (typeof n === "number") {
            sum += n;
            count++;
          }
        }
      heights[r * cols + c] = count ? sum / count : 0;
    }

  /** Window grid coordinates (cell centre = index + 0.5) to local metres. */
  const local = (gx: number, gy: number) => ({
    east: ea * gx + eb * gy + ec,
    north: na * gx + nb * gy + nc,
  });
  const grid = (east: number, north: number) => ({
    gx: (nb * (east - ec) - eb * (north - nc)) / det,
    gy: (-na * (east - ec) + ea * (north - nc)) / det,
  });
  /** Bilinear height between cell centres, clamped at the window edge. */
  const heightAtGrid = (gx: number, gy: number) => {
    const u = THREE.MathUtils.clamp(gx - 0.5, 0, cols - 1);
    const v = THREE.MathUtils.clamp(gy - 0.5, 0, rows - 1);
    const c0 = Math.floor(u);
    const r0 = Math.floor(v);
    const c1 = Math.min(cols - 1, c0 + 1);
    const r1 = Math.min(rows - 1, r0 + 1);
    const fu = u - c0;
    const fv = v - r0;
    const h = (r: number, c: number) => heights[r * cols + c];
    return (
      (h(r0, c0) * (1 - fu) + h(r0, c1) * fu) * (1 - fv) +
      (h(r1, c0) * (1 - fu) + h(r1, c1) * fu) * fv
    );
  };

  let extent_m = 1;
  for (const path of Object.values(scene.path_cells))
    for (const [r, c] of path) {
      const p = local(c + 0.5, r + 0.5);
      extent_m = Math.max(extent_m, Math.abs(p.east), Math.abs(p.north));
    }
  if (extent_m <= 1) extent_m = (Math.max(rows, cols) * scene.pixel_m) / 2;
  const scale = ROUTE_SPAN_SCENE / extent_m;

  /** Scene-space point of window grid coordinates. */
  const gridToScene = (gx: number, gy: number, target = new THREE.Vector3()) => {
    const p = local(gx, gy);
    return target.set(p.east * scale, heightAtGrid(gx, gy) * scale, -p.north * scale);
  };
  /** Ground height (scene units) under scene (x, z). */
  const ground = (x: number, z: number) => {
    const g = grid(x / scale, -z / scale);
    return heightAtGrid(g.gx, g.gy) * scale;
  };
  return { scale, gridToScene, ground, heightAtGrid };
}

export type SceneMapping = ReturnType<typeof sceneMapping>;

/**
 * Grid mesh over window cell centres from `from` to `to` cells (may extend
 * past the window, where heights clamp to the edge). Winding is fixed so
 * normals point up, whatever the handedness of the DEM affine.
 */
export function gridGeometry(
  scene: SurfaceScene,
  mapping: SceneMapping,
  from: [number, number],
  to: [number, number],
  lift = 0,
) {
  const [i0, j0] = from;
  const [i1, j1] = to;
  const nx = i1 - i0 + 1;
  const ny = j1 - j0 + 1;
  const positions = new Float32Array(nx * ny * 3);
  const uvs = new Float32Array(nx * ny * 2);
  const point = new THREE.Vector3();
  for (let j = 0; j < ny; j++)
    for (let i = 0; i < nx; i++) {
      const gx = i0 + i + 0.5;
      const gy = j0 + j + 0.5;
      mapping.gridToScene(gx, gy, point);
      const k = j * nx + i;
      positions.set([point.x, point.y + lift, point.z], k * 3);
      uvs.set([gx / scene.cols, gy / scene.rows], k * 2);
    }
  const index: number[] = [];
  for (let j = 0; j < ny - 1; j++)
    for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i;
      const b = a + 1;
      const c = a + nx;
      const d = c + 1;
      index.push(a, c, b, b, c, d);
    }
  // Flip the winding when the first triangle faces down.
  const p = (k: number) =>
    new THREE.Vector3(positions[k * 3], positions[k * 3 + 1], positions[k * 3 + 2]);
  const normal = new THREE.Vector3()
    .subVectors(p(index[1]), p(index[0]))
    .cross(new THREE.Vector3().subVectors(p(index[2]), p(index[0])));
  if (normal.y < 0)
    for (let t = 0; t < index.length; t += 3)
      [index[t + 1], index[t + 2]] = [index[t + 2], index[t + 1]];
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
  geometry.setIndex(index);
  geometry.computeVertexNormals();
  return geometry;
}
