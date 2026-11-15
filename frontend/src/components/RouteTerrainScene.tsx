import { useEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { Model } from "./LunarSurface";
import type { AnalysisPreview, PlotPoint, RouteGrid } from "../analysisView";

/** Longest horizontal side of the route area in scene units. */
const SPAN_SCENE = 26;

/**
 * Mission-run routes over the real LOLA terrain from the backend grid, at one
 * uniform scale on all axes (true relief, no exaggeration). Scene x is the
 * crossrange (right of the approach), −z the downrange, y the height above the
 * primary site. Nothing here is computed physics: paths, heights and roles
 * come from the API; heights between grid cells are interpolated for drawing.
 */
function useGrid(grid: RouteGrid) {
  return useMemo(() => {
    const { rows, cols, spacing_m, down0_m, cross0_m } = grid;
    const scale = SPAN_SCENE / Math.max(1, (Math.max(rows, cols) - 1) * spacing_m);
    // Unknown cells are drawn at their known neighbours' mean, and marked.
    const heights = new Float32Array(rows * cols);
    const unknown = new Uint8Array(rows * cols);
    for (let i = 0; i < rows; i++)
      for (let j = 0; j < cols; j++) {
        const v = grid.heights_m[i]?.[j];
        if (typeof v === "number") {
          heights[i * cols + j] = v;
          continue;
        }
        unknown[i * cols + j] = 1;
        let sum = 0, n = 0;
        for (let di = -1; di <= 1; di++)
          for (let dj = -1; dj <= 1; dj++) {
            const w = grid.heights_m[i + di]?.[j + dj];
            if (typeof w === "number") { sum += w; n++; }
          }
        heights[i * cols + j] = n ? sum / n : 0;
      }
    const heightAt = (down: number, cross: number) => {
      const u = THREE.MathUtils.clamp((cross - cross0_m) / spacing_m, 0, cols - 1);
      const v = THREE.MathUtils.clamp((down - down0_m) / spacing_m, 0, rows - 1);
      const j0 = Math.floor(u), i0 = Math.floor(v);
      const j1 = Math.min(cols - 1, j0 + 1), i1 = Math.min(rows - 1, i0 + 1);
      const fu = u - j0, fv = v - i0;
      const h = (i: number, j: number) => heights[i * cols + j];
      return (h(i0, j0) * (1 - fu) + h(i0, j1) * fu) * (1 - fv) + (h(i1, j0) * (1 - fu) + h(i1, j1) * fu) * fv;
    };
    const center = { down: down0_m + ((rows - 1) * spacing_m) / 2, cross: cross0_m + ((cols - 1) * spacing_m) / 2 };
    const toScene = (down: number, cross: number, height: number, target = new THREE.Vector3()) =>
      target.set((cross - center.cross) * scale, height * scale, -(down - center.down) * scale);
    return { scale, heights, unknown, heightAt, toScene };
  }, [grid]);
}

function TerrainMesh({ grid, map }: { grid: RouteGrid; map: ReturnType<typeof useGrid> }) {
  const geometry = useMemo(() => {
    const { rows, cols, spacing_m, down0_m, cross0_m } = grid;
    const positions = new Float32Array(rows * cols * 3);
    const colors = new Float32Array(rows * cols * 3);
    let low = Infinity, high = -Infinity;
    for (const h of map.heights) { low = Math.min(low, h); high = Math.max(high, h); }
    const p = new THREE.Vector3();
    for (let i = 0; i < rows; i++)
      for (let j = 0; j < cols; j++) {
        const k = i * cols + j;
        map.toScene(down0_m + i * spacing_m, cross0_m + j * spacing_m, map.heights[k], p);
        positions.set([p.x, p.y, p.z], k * 3);
        const shade = 0.42 + 0.22 * ((map.heights[k] - low) / Math.max(1e-6, high - low));
        colors.set(map.unknown[k] ? [0.25, 0.12, 0.12] : [shade, shade * 0.98, shade * 0.95], k * 3);
      }
    const index: number[] = [];
    for (let i = 0; i < rows - 1; i++)
      for (let j = 0; j < cols - 1; j++) {
        const a = i * cols + j, b = a + 1, c = a + cols, d = c + 1;
        index.push(a, b, c, b, d, c);
      }
    const mesh = new THREE.BufferGeometry();
    mesh.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    mesh.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    mesh.setIndex(index);
    mesh.computeVertexNormals();
    // Keep normals pointing up whatever the winding.
    const normals = mesh.attributes.normal;
    if (normals.count && normals.getY(0) < 0) {
      for (let k = 0; k < normals.count; k++) normals.setXYZ(k, -normals.getX(k), -normals.getY(k), -normals.getZ(k));
    }
    return mesh;
  }, [grid, map]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <mesh geometry={geometry} receiveShadow>
      <meshStandardMaterial vertexColors roughness={1} metalness={0} side={THREE.DoubleSide} />
    </mesh>
  );
}

function RouteLine({ points, color, opacity, dashed }: { points: THREE.Vector3[]; color: string; opacity: number; dashed?: boolean }) {
  const line = useMemo(() => {
    const material = dashed
      ? new THREE.LineDashedMaterial({ color, transparent: true, opacity, dashSize: 0.3, gapSize: 0.2 })
      : new THREE.LineBasicMaterial({ color, transparent: true, opacity });
    const object = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), material);
    if (dashed) object.computeLineDistances();
    return object;
  }, [points, color, opacity, dashed]);
  useEffect(() => () => { line.geometry.dispose(); (line.material as THREE.Material).dispose(); }, [line]);
  return <primitive object={line} />;
}

/** Ground marker: a pole with a ring at the landing point; terrain-only rejections get a cross. */
function Marker({ position, color, selected, cross, onClick }: { position: THREE.Vector3; color: string; selected: boolean; cross: boolean; onClick: () => void }) {
  const size = selected ? 1.6 : 1;
  return (
    <group position={position} scale={size} onClick={(e) => { e.stopPropagation(); if (e.delta < 6) onClick(); }}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
        <ringGeometry args={[0.16, 0.24, 32]} />
        <meshBasicMaterial color={color} side={THREE.DoubleSide} />
      </mesh>
      {cross ? (
        <>
          <mesh rotation={[-Math.PI / 2, 0, Math.PI / 4]} position={[0, 0.03, 0]}><planeGeometry args={[0.42, 0.06]} /><meshBasicMaterial color={color} side={THREE.DoubleSide} /></mesh>
          <mesh rotation={[-Math.PI / 2, 0, -Math.PI / 4]} position={[0, 0.03, 0]}><planeGeometry args={[0.42, 0.06]} /><meshBasicMaterial color={color} side={THREE.DoubleSide} /></mesh>
        </>
      ) : (
        <>
          <mesh position={[0, 0.3, 0]}><cylinderGeometry args={[0.015, 0.015, 0.6, 8]} /><meshBasicMaterial color={color} /></mesh>
          <mesh position={[0, 0.62, 0]}><sphereGeometry args={[0.07, 12, 10]} /><meshBasicMaterial color={color} /></mesh>
        </>
      )}
      {/* Larger invisible hit area for clicking. */}
      <mesh position={[0, 0.3, 0]} visible={false}><cylinderGeometry args={[0.3, 0.3, 0.8, 8]} /></mesh>
    </group>
  );
}

export default function RouteTerrainScene({ preview, progress, onSelect }: { preview: AnalysisPreview; progress: number; onSelect: (id: string) => void }) {
  const grid = preview.terrain!;
  const map = useGrid(grid);
  const { camera, gl, invalidate } = useThree();
  const body = useRef<THREE.Group>(null!);
  const lift = 0.04; // keeps lines visible just above the drawn ground
  const paths = useMemo(
    () => preview.lines.map((l) => ({ ...l, vertices: l.points.map((p) => map.toScene(p.x, p.z, p.y).setY(p.y * map.scale + lift)) })),
    [preview.lines, map],
  );
  const markers = useMemo(
    () => preview.points.map((p: PlotPoint) => ({ ...p, position: map.toScene(p.x, p.z, map.heightAt(p.x, p.z)) })),
    [preview.points, map],
  );
  const angle = useRef({ yaw: 0.35, pitch: 0.42, zoom: 1 });
  useEffect(() => {
    angle.current = { yaw: 0.35, pitch: 0.42, zoom: 1 };
    const el = gl.domElement;
    let last: { x: number; y: number } | null = null;
    const down = (e: PointerEvent) => { last = { x: e.clientX, y: e.clientY }; el.setPointerCapture(e.pointerId); };
    const move = (e: PointerEvent) => {
      if (!last) return;
      angle.current.yaw -= (e.clientX - last.x) * 0.006;
      angle.current.pitch = THREE.MathUtils.clamp(angle.current.pitch + (e.clientY - last.y) * 0.006, 0.08, 1.45);
      last = { x: e.clientX, y: e.clientY };
      invalidate();
    };
    const up = () => { last = null; };
    const wheel = (e: WheelEvent) => { e.preventDefault(); angle.current.zoom = THREE.MathUtils.clamp(angle.current.zoom * Math.exp(e.deltaY * 0.001), 0.25, 3); invalidate(); };
    el.addEventListener("pointerdown", down); el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
    el.addEventListener("wheel", wheel, { passive: false });
    return () => {
      el.removeEventListener("pointerdown", down); el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up);
      el.removeEventListener("wheel", wheel);
    };
  }, [gl, invalidate]);
  const focus = useMemo(() => map.toScene(0, 0, 0), [map]); // the primary target
  useFrame(() => {
    const a = angle.current;
    camera.position
      .set(Math.sin(a.yaw) * Math.cos(a.pitch), Math.sin(a.pitch), Math.cos(a.yaw) * Math.cos(a.pitch))
      .multiplyScalar(24 * a.zoom)
      .add(focus);
    camera.lookAt(focus);
    // The lander follows the selected route, else the flown run, else the primary.
    const route = paths.find((l) => l.id === preview.selected) ?? paths.find((l) => l.id === "flown") ?? paths.find((l) => l.id === "PRIMARY");
    if (!body.current) return;
    body.current.visible = !!route && route.vertices.length > 1;
    if (!route || route.vertices.length < 2) return;
    const pts = route.points, verts = route.vertices;
    const start = pts[0].time ?? 0, end = pts.at(-1)?.time ?? 1;
    const time = start + (end - start) * progress;
    let i = 1;
    while (i < pts.length - 1 && (pts[i].time ?? 0) < time) i++;
    const t0 = pts[i - 1].time ?? 0, t1 = pts[i].time ?? 1;
    const f = THREE.MathUtils.clamp((time - t0) / Math.max(1e-9, t1 - t0), 0, 1);
    body.current.position.copy(verts[i - 1]).lerp(verts[i], f);
  });
  const terrainOnly = new Set(preview.terrainOnly ?? []);
  return (
    <>
      <color attach="background" args={["#05080b"]} />
      <fog attach="fog" args={["#05080b", 40, 90]} />
      <ambientLight intensity={0.35} />
      <hemisphereLight args={["#9fb4c8", "#4a4640", 0.4]} />
      {/* Low polar Sun so the relief reads; illustrative lighting, not the analysed Sun. */}
      <directionalLight position={[-14, 6, 8]} intensity={3.2} color="#fff1dc" />
      <TerrainMesh grid={grid} map={map} />
      {paths.map((l) => (
        <RouteLine
          key={l.id}
          points={l.vertices}
          color={l.color}
          dashed={l.dashed}
          opacity={preview.selected && preview.selected !== l.id ? 0.22 : 1}
        />
      ))}
      {markers.map((m) => (
        <Marker
          key={m.id}
          position={m.position}
          color={m.color ?? "#e8c58b"}
          selected={preview.selected === m.id}
          cross={terrainOnly.has(m.id ?? "")}
          onClick={() => m.id && onSelect(m.id)}
        />
      ))}
      <group ref={body}>
        <Model path="/nasa/apollo.glb" size={0.55} grounded />
      </group>
    </>
  );
}
