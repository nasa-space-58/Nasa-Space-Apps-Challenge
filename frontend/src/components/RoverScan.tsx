import { useEffect, useMemo, useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { noise } from "./LunarSurface";
import {
  DEPLOY_END,
  PULSE_END,
  SEARCH_END,
  gridGeometry,
  type SceneMapping,
  type SurfaceScene,
} from "./surfaceScene";

/** Extra cells of clamped terrain around the DEM window (hidden by fog). */
const SKIRT_CELLS = 40;

/**
 * The analysed LOLA DEM window as the landing ground. Heights and footprint
 * are the backend's; colour and micro-grain are art only.
 */
export function DemTerrain({
  scene,
  mapping,
}: {
  scene: SurfaceScene;
  mapping: SceneMapping;
}) {
  const grain = useMemo(() => {
    const pixels = new Uint8Array(256 * 256 * 4);
    for (let i = 0; i < 256 * 256; i++) {
      const value = 75 + noise(i + 900) * 95;
      pixels.set([value, value, value, 255], i * 4);
    }
    const texture = new THREE.DataTexture(pixels, 256, 256);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.repeat.set(30, 30);
    texture.magFilter = THREE.LinearFilter;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.generateMipmaps = true;
    texture.needsUpdate = true;
    return texture;
  }, []);
  const geometry = useMemo(() => {
    const mesh = gridGeometry(
      scene,
      mapping,
      [-SKIRT_CELLS, -SKIRT_CELLS],
      [scene.cols - 1 + SKIRT_CELLS, scene.rows - 1 + SKIRT_CELLS],
    );
    // Regolith shading; the skirt fades darker so the analysed window reads.
    const positions = mesh.attributes.position;
    const uv = mesh.attributes.uv;
    const colors = new Float32Array(positions.count * 3);
    for (let i = 0; i < positions.count; i++) {
      const u = uv.getX(i);
      const v = uv.getY(i);
      const outside = Math.max(0, -u, u - 1, -v, v - 1) * 4;
      const shade = (0.5 + noise(i + 6) * 0.08) * (1 - Math.min(outside, 0.35));
      colors.set([shade, shade * 0.99, shade * 0.97], i * 3);
    }
    mesh.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    // Rescale the grain UVs so the bump pattern is uniform in scene space.
    const uvs = new Float32Array(positions.count * 2);
    for (let i = 0; i < positions.count; i++)
      uvs.set([positions.getX(i) / 70 + 0.5, positions.getZ(i) / 70 + 0.5], i * 2);
    mesh.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
    return mesh;
  }, [scene, mapping]);
  useEffect(
    () => () => {
      grain.dispose();
      geometry.dispose();
    },
    [grain, geometry],
  );
  return (
    <mesh geometry={geometry} receiveShadow>
      <meshStandardMaterial
        vertexColors
        color="#7a7c7a"
        roughness={1}
        bumpMap={grain}
        bumpScale={0.06}
      />
    </mesh>
  );
}

type Rgba = [number, number, number, number];
const UNSCANNED: Rgba = [0, 0, 0, 0];
const OPEN: Rgba = [70, 190, 160, 22];
const STEEP: Rgba = [235, 58, 48, 180];
const UNKNOWN: Rgba = [30, 30, 34, 110];
const SEARCHED: Rgba = [57, 135, 229, 90];
const FRONTIER: Rgba = [170, 215, 255, 190];
const FRONTIER_CELLS = 60;
/** Keep hazards legible while the rover follows the planned route. */
const SETTLED_OPACITY = 0.7;

/**
 * Scan reveal draped on the DEM: the antenna pulse front marks traversable
 * and too-steep cells, then the backend's A* expansion order plays back and
 * the chosen path lights up. The pacing is display-only; the cells and their
 * order are the backend's.
 */
export function ScanOverlay({
  scene,
  mapping,
  target,
  scan,
}: {
  scene: SurfaceScene;
  mapping: SceneMapping;
  target: string;
  scan: MutableRefObject<number>;
}) {
  const { rows, cols } = scene;
  const geometry = useMemo(
    () => gridGeometry(scene, mapping, [0, 0], [cols - 1, rows - 1], 0.015),
    [scene, mapping, rows, cols],
  );
  const pixels = useMemo(() => new Uint8Array(rows * cols * 4), [rows, cols]);
  const texture = useMemo(() => {
    const value = new THREE.DataTexture(pixels, cols, rows);
    // Smooth between cell centres so the 80 m grid does not read as blocks.
    value.magFilter = THREE.LinearFilter;
    value.minFilter = THREE.LinearFilter;
    value.needsUpdate = true;
    return value;
  }, [pixels, rows, cols]);
  useEffect(
    () => () => {
      geometry.dispose();
      texture.dispose();
    },
    [geometry, texture],
  );
  const distance = useMemo(() => {
    const [sr, sc] = scene.site_cell;
    const values = new Float32Array(rows * cols);
    let max = 1;
    for (let r = 0; r < rows; r++)
      for (let c = 0; c < cols; c++) {
        const d = Math.hypot(r - sr, c - sc);
        values[r * cols + c] = d;
        max = Math.max(max, d);
      }
    return { values, max };
  }, [scene, rows, cols]);
  const explored = scene.explored[target] ?? [];
  const material = useRef<THREE.MeshBasicMaterial>(null!);
  const drawn = useMemo(() => ({ key: "" }), [scene, target]);

  useFrame((_, delta) => {
    const s = THREE.MathUtils.clamp(scan.current, 0, 1);
    const goal = s >= 1 ? SETTLED_OPACITY : 1;
    material.current.opacity +=
      (goal - material.current.opacity) * Math.min(1, Math.min(delta, 0.05) * 3);
    const radius =
      distance.max *
      THREE.MathUtils.clamp((s - DEPLOY_END) / (PULSE_END - DEPLOY_END), 0, 1);
    const searched = Math.floor(
      explored.length *
        THREE.MathUtils.clamp((s - PULSE_END) / (SEARCH_END - PULSE_END), 0, 1),
    );
    const routed = s >= SEARCH_END;
    const key = `${radius.toFixed(1)}|${searched}|${routed}`;
    if (key === drawn.key) return;
    drawn.key = key;
    const put = (r: number, c: number, color: Rgba) =>
      pixels.set(color, (r * cols + c) * 4);
    for (let r = 0; r < rows; r++)
      for (let c = 0; c < cols; c++) {
        if (distance.values[r * cols + c] > radius) put(r, c, UNSCANNED);
        else if (scene.heights_m[r][c] === null) put(r, c, UNKNOWN);
        else put(r, c, scene.passable[r][c] ? OPEN : STEEP);
      }
    for (let k = 0; k < searched; k++) {
      const [r, c] = explored[k];
      put(r, c, k >= searched - FRONTIER_CELLS && !routed ? FRONTIER : SEARCHED);
    }
    texture.needsUpdate = true;
  });

  return (
    <mesh geometry={geometry} renderOrder={1}>
      <meshBasicMaterial
        ref={material}
        map={texture}
        transparent
        depthWrite={false}
        polygonOffset
        polygonOffsetFactor={-2}
        toneMapped={false}
      />
    </mesh>
  );
}
