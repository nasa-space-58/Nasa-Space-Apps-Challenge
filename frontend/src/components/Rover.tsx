import {
  Component,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
  type ReactNode,
} from "react";
import { useFrame, useLoader } from "@react-three/fiber";
import * as THREE from "three";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { clone as cloneSkeleton } from "three/addons/utils/SkeletonUtils.js";
import { terrainHeight } from "./LunarSurface";
import {
  DEPLOY_END,
  PULSE_END,
  SEARCH_END,
  type SceneMapping,
  type SurfaceScene,
} from "./surfaceScene";

/** A backend R8 route cell, metres north/east of the landing cell. */
export type RoutePoint = { north_m: number; east_m: number };

/** What the surface HUD hands to the 3-D scene after touchdown. */
export type RoverState = {
  path: RoutePoint[];
  /** Travelled share of the route, 0–1. */
  drive: number;
  /** Antenna scan and A* playback progress, 0–1 (display pacing only). */
  scan: number;
  scene: SurfaceScene | null;
  targetId: string;
  source?: "demo" | "lola";
  alternatives?: { id: string; path: RoutePoint[] }[];
};

/** Where the rover rolls off, beside the lander (scene units). */
const START_OFFSET_SCENE = new THREE.Vector3(1.9, 0, 1.4);
/** Where the rover is unloaded at the foot of the lander (scene units). */
const LANDER_EXIT_SCENE = new THREE.Vector3(0.85, 0, 0.6);
/** On the art terrain the longest route is scaled to this many scene units. */
const ROUTE_SPAN_SCENE = 22;
const WHEEL_RADIUS_SCENE = 0.12;
const RIDE_HEIGHT_SCENE = 0.02;
/**
 * Optional user-supplied rover model (Sketchfab "Lunar Rover", CC BY 4.0, see
 * public/models/manifest.json). The procedural rover is used until it exists.
 */
const ROVER_MODEL_URL = "/models/lunar-rover.glb";
/** The model's nose (antenna and TV camera end) points to -z; turn it onto +z. */
const ROVER_MODEL_YAW = Math.PI;
/** Longest model dimension in scene units (wheel radius about 0.13). */
const ROVER_MODEL_SIZE = 1.3;
/** Antenna height above the ground for the scan pulses (scene units). */
const DISH_HEIGHT_SCENE = 0.62;
const PULSE_RINGS = 3;
// Presentation telemetry cycle, paced by playback so pause/scrub also stop the dish.
const antennaAngle = (scan: number, drive: number) => scan < SEARCH_END
  ? Math.PI * 4 * THREE.MathUtils.smoothstep(scan, DEPLOY_END, SEARCH_END)
  : Math.PI * 4 + 0.45 * Math.sin(drive * Math.PI * 12);


/**
 * Lays the backend route into the scene. With the backend DEM scene the route
 * sits on the real terrain at its true proportions; without it, the route is
 * scaled onto the illustrative terrain (east → +x, north → −z).
 */
function layout(path: RoutePoint[], mapping: SceneMapping | null) {
  const ground = mapping ? mapping.ground : terrainHeight;
  let points: THREE.Vector3[];
  if (mapping) {
    // The route starts under the lander, so the rover rolls off beside it
    // and joins the backend path at its second point.
    const s = mapping.scale;
    points = [
      START_OFFSET_SCENE.clone(),
      ...path.slice(1).map((p) => new THREE.Vector3(p.east_m * s, 0, -p.north_m * s)),
    ];
  } else {
    const extent_m = Math.max(
      1,
      ...path.map((p) => Math.max(Math.abs(p.north_m), Math.abs(p.east_m))),
    );
    const scale = ROUTE_SPAN_SCENE / extent_m;
    points = path.map(
      (p) =>
        new THREE.Vector3(
          START_OFFSET_SCENE.x + p.east_m * scale,
          0,
          START_OFFSET_SCENE.z - p.north_m * scale,
        ),
    );
  }
  for (const p of points) p.y = ground(p.x, p.z);
  const along = [0];
  for (let i = 1; i < points.length; i++)
    along.push(along[i - 1] + points[i].distanceTo(points[i - 1]));
  return { points, along, total: along[along.length - 1] || 1, ground };
}

/** Polyline resampled every `step` units and draped just above the ground. */
function drape(
  points: THREE.Vector3[],
  ground: (x: number, z: number) => number,
  step = 0.2,
) {
  const out: THREE.Vector3[] = [];
  for (let i = 0; i < points.length; i++) {
    const a = points[i];
    const b = points[i + 1];
    const n = b ? Math.max(1, Math.ceil(a.distanceTo(b) / step)) : 1;
    for (let k = 0; k < n; k++) {
      const p = b ? a.clone().lerp(b, k / n) : a.clone();
      p.y = ground(p.x, p.z) + 0.06;
      out.push(p);
    }
  }
  return out;
}

function Wheel({
  position,
  spin,
}: {
  position: [number, number, number];
  spin: MutableRefObject<number>;
}) {
  const wheel = useRef<THREE.Mesh>(null!);
  useFrame(() => {
    wheel.current.rotation.x = spin.current;
  });
  return (
    <mesh ref={wheel} position={position} rotation={[0, 0, Math.PI / 2]} castShadow>
      <cylinderGeometry args={[WHEEL_RADIUS_SCENE, WHEEL_RADIUS_SCENE, 0.08, 18]} />
      <meshStandardMaterial color="#3b3d40" roughness={0.9} />
    </mesh>
  );
}

/** Six-wheel illustrative rover, built from primitives (no external model). */
function RoverModel({
  spin,
  scan,
  drive,
}: {
  spin: MutableRefObject<number>;
  scan: MutableRefObject<number>;
  drive: MutableRefObject<number>;
}) {
  const dish = useRef<THREE.Group>(null!);
  useFrame((_, delta) => {
    dish.current.rotation.y = THREE.MathUtils.damp(dish.current.rotation.y, antennaAngle(scan.current, drive.current), 5, Math.min(delta, 0.05));
  });
  const wheels: [number, number, number][] = [];
  for (const z of [-0.33, 0, 0.33])
    for (const x of [-0.34, 0.34]) wheels.push([x, WHEEL_RADIUS_SCENE, z]);
  return (
    <group>
      <mesh position={[0, 0.34, 0]} castShadow>
        <boxGeometry args={[0.56, 0.2, 0.86]} />
        <meshStandardMaterial color="#d9d5cc" roughness={0.6} metalness={0.2} />
      </mesh>
      <mesh position={[0, 0.47, 0.05]} rotation={[-0.12, 0, 0]} castShadow>
        <boxGeometry args={[0.72, 0.025, 0.7]} />
        <meshStandardMaterial color="#26385a" roughness={0.35} metalness={0.5} />
      </mesh>
      <mesh position={[0.16, 0.66, -0.32]} castShadow>
        <cylinderGeometry args={[0.025, 0.025, 0.42, 10]} />
        <meshStandardMaterial color="#b8b4ad" />
      </mesh>
      <mesh position={[0.16, 0.9, -0.34]} castShadow>
        <boxGeometry args={[0.18, 0.09, 0.1]} />
        <meshStandardMaterial color="#e2ddd2" />
      </mesh>
      <group ref={dish} position={[-0.18, 0.62, -0.26]}>
        <mesh position={[0, 0.06, 0]} rotation={[0.5, 0, 0]} castShadow>
          <cylinderGeometry args={[0.11, 0.02, 0.05, 16]} />
          <meshStandardMaterial color="#efe9dc" metalness={0.3} roughness={0.4} />
        </mesh>
      </group>
      {wheels.map((position, i) => (
        <Wheel key={i} position={position} spin={spin} />
      ))}
    </group>
  );
}

/** Material look per model part, chosen by the nearest named ancestor. */
const PART_LOOKS: [RegExp, { color: string; metalness: number; roughness: number }][] = [
  [/^(llant|tira|ring|arito)/, { color: "#9a958a", metalness: 0.75, roughness: 0.5 }],
  [/^guard/, { color: "#55606b", metalness: 0.2, roughness: 0.6 }],
  [/^celdas_sol/, { color: "#1d3352", metalness: 0.5, roughness: 0.25 }],
  [/^(tela_anten|estructura)/, { color: "#ece8dd", metalness: 0.15, roughness: 0.75 }],
  [/^(cable|cinta|correas|Spline)/, { color: "#1c1c1c", metalness: 0, roughness: 0.8 }],
  [/^(bolsa|tela_tapa)/, { color: "#cdbf9b", metalness: 0, roughness: 0.95 }],
  [/^lente/, { color: "#101215", metalness: 0.4, roughness: 0.15 }],
];
const DEFAULT_LOOK = { color: "#c3c5c6", metalness: 0.55, roughness: 0.42 };
/** Parts of each wheel that roll (fenders and supports stay fixed). */
const WHEEL_NODE = /^neuma/;
const FIXED_WHEEL_PART = /^(guard|sopor)/;
/** High-gain dish parts that sweep around the mast while scanning. */
const DISH_PART = /^(tela_anten|estructura|base_$|punta_rece|base_recep|contrapeso|rosca$)/;

/**
 * Re-parents `parts` (siblings) under a pivot at `centre` (scene-root space)
 * so they can turn about `axis` (scene-root space). Returns the pivot and the
 * axis in the pivot's parent frame.
 */
function pivotFor(parts: THREE.Object3D[], centre: THREE.Vector3, axis: THREE.Vector3) {
  const parent = parts[0].parent!;
  const local = parent.worldToLocal(centre.clone());
  const origin = parent.worldToLocal(new THREE.Vector3());
  const localAxis = parent.worldToLocal(axis.clone()).sub(origin).normalize();
  const pivot = new THREE.Group();
  pivot.position.copy(local);
  parent.add(pivot);
  for (const part of parts) {
    part.position.sub(local);
    pivot.add(part);
  }
  return { pivot, axis: localAxis };
}

/** The Sketchfab LRV model with rolling wheels and a sweeping dish. */
function RoverGlb({
  spin,
  scan,
  drive,
}: {
  spin: MutableRefObject<number>;
  scan: MutableRefObject<number>;
  drive: MutableRefObject<number>;
}) {
  const gltf = useLoader(GLTFLoader, ROVER_MODEL_URL, withDraco);
  const rig = useMemo(() => {
    const model = cloneSkeleton(gltf.scene);
    model.updateMatrixWorld(true);
    const materials = new Map<string, THREE.MeshStandardMaterial>();
    const look = (object: THREE.Object3D) => {
      for (let node: THREE.Object3D | null = object; node; node = node.parent) {
        const match = PART_LOOKS.find(([pattern]) => pattern.test(node!.name));
        if (match) return match;
      }
      return null;
    };
    model.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const match = look(object);
      const key = match ? match[0].source : "default";
      if (!materials.has(key))
        materials.set(
          key,
          new THREE.MeshStandardMaterial({ ...(match ? match[1] : DEFAULT_LOOK), side: THREE.DoubleSide }),
        );
      object.material = materials.get(key)!;
      object.castShadow = true;
      object.receiveShadow = true;
    });
    const box = new THREE.Box3().setFromObject(model);
    const size = box.getSize(new THREE.Vector3());
    const wheels: { pivot: THREE.Group; axis: THREE.Vector3 }[] = [];
    const wheelNodes: THREE.Object3D[] = [];
    model.traverse((object) => {
      if (WHEEL_NODE.test(object.name) && object.children.length) wheelNodes.push(object);
    });
    for (const wheel of wheelNodes) {
      const parts = wheel.children.filter((c) => !FIXED_WHEEL_PART.test(c.name));
      if (!parts.length) continue;
      const centre = new THREE.Box3();
      for (const part of parts) centre.expandByObject(part);
      wheels.push(pivotFor(parts, centre.getCenter(new THREE.Vector3()), new THREE.Vector3(1, 0, 0)));
    }
    const dishParts: THREE.Object3D[] = [];
    model.traverse((object) => {
      if (DISH_PART.test(object.name)) dishParts.push(object);
    });
    let dish: { pivot: THREE.Group; axis: THREE.Vector3 } | null = null;
    const sameParent = dishParts.filter((p) => p.parent === dishParts[0]?.parent);
    if (sameParent.length) {
      const bounds = new THREE.Box3();
      for (const part of sameParent) bounds.expandByObject(part);
      const centre = bounds.getCenter(new THREE.Vector3());
      // Turn about the mast, not the dish's own centre.
      const mast = model.getObjectByName("Tubo1");
      if (mast) {
        const axle = new THREE.Box3().setFromObject(mast).getCenter(new THREE.Vector3());
        centre.set(axle.x, centre.y, axle.z);
      }
      dish = pivotFor(sameParent, centre, new THREE.Vector3(0, 1, 0));
    }
    return {
      model,
      wheels,
      dish,
      materials: [...materials.values()],
      scale: ROVER_MODEL_SIZE / Math.max(size.x, size.y, size.z),
      offset: new THREE.Vector3(
        -(box.min.x + box.max.x) / 2,
        -box.min.y,
        -(box.min.z + box.max.z) / 2,
      ),
    };
  }, [gltf]);
  useEffect(() => () => rig.materials.forEach((m) => m.dispose()), [rig]);
  const dishAngle = useRef(0);
  useFrame((_, delta) => {
    // The model's nose points to −z, so forward rolling is a negative turn.
    for (const wheel of rig.wheels) wheel.pivot.quaternion.setFromAxisAngle(wheel.axis, -spin.current);
    dishAngle.current = THREE.MathUtils.damp(dishAngle.current, antennaAngle(scan.current, drive.current), 5, Math.min(delta, 0.05));
    if (rig.dish) rig.dish.pivot.quaternion.setFromAxisAngle(rig.dish.axis, dishAngle.current);
  });
  return (
    <group rotation={[0, ROVER_MODEL_YAW, 0]} scale={rig.scale}>
      <primitive object={rig.model} position={rig.offset} />
    </group>
  );
}

const withDraco = (loader: GLTFLoader) => {
  loader.setDRACOLoader(new DRACOLoader().setDecoderPath("/draco/"));
};
let modelCheck: Promise<boolean> | null = null;

/**
 * Checks once that the optional model file is really there (not the SPA
 * page) and starts downloading it, so it is ready by touchdown.
 */
export function preloadRoverModel(): Promise<boolean> {
  modelCheck ??= fetch(ROVER_MODEL_URL, { method: "HEAD" })
    .then((response) => {
      const type = response.headers.get("content-type") ?? "";
      const available = response.ok && !type.includes("text/html");
      if (available) useLoader.preload(GLTFLoader, ROVER_MODEL_URL, withDraco);
      return available;
    })
    .catch(() => false);
  return modelCheck;
}

function useModelAvailable() {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    let live = true;
    preloadRoverModel().then((value) => {
      if (live) setAvailable(value);
    });
    return () => {
      live = false;
    };
  }, []);
  return available;
}

/** Falls back to the procedural rover if the model file fails to load. */
class ModelBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/** Antenna pulse rings expanding from the rover over the scanned terrain. */
function Pulses({
  scan,
  drive,
  reach,
  origin,
}: {
  scan: MutableRefObject<number>;
  reach: number;
  drive: MutableRefObject<number>;
  origin: THREE.Vector3;
}) {
  const rings = useRef<THREE.Mesh[]>([]);
  const geometry = useMemo(
    () => new THREE.RingGeometry(0.97, 1, 128).rotateX(-Math.PI / 2),
    [],
  );
  useEffect(() => () => geometry.dispose(), [geometry]);
  useFrame(() => {
    const s = scan.current;
    const searching = s > DEPLOY_END && s < SEARCH_END;
    const transmitting = s >= 1 && drive.current > 0 && (drive.current * 8) % 1 < 0.24;
    const active = searching || transmitting;
    const front =
      reach * THREE.MathUtils.clamp((s - DEPLOY_END) / (PULSE_END - DEPLOY_END), 0, 1);
    rings.current.forEach((ring, k) => {
      ring.visible = active;
      if (!active) return;
      const phase = ((searching ? s * 6 : drive.current * 32) + k / PULSE_RINGS) % 1;
      ring.position.set(origin.x, origin.y + DISH_HEIGHT_SCENE + (transmitting ? phase * 3 : 0), origin.z);
      ring.rotation.z = transmitting ? -0.35 : 0;
      ring.scale.setScalar(Math.max(0.05, phase * (transmitting ? 2.6 : front)));
      (ring.material as THREE.MeshBasicMaterial).opacity = (1 - phase) * 0.55;
    });
  });
  return (
    <>
      {Array.from({ length: PULSE_RINGS }, (_, k) => (
        <mesh
          key={k}
          ref={(mesh) => {
            if (mesh) rings.current[k] = mesh;
          }}
          geometry={geometry}
          renderOrder={2}
        >
          <meshBasicMaterial
            color="#9fd8ff"
            transparent
            opacity={0}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
            side={THREE.DoubleSide}
            toneMapped={false}
          />
        </mesh>
      ))}
    </>
  );
}

/**
 * Drives the rover along the backend route. `drive` is the travelled share of
 * the route (0–1); `scan` paces the antenna scan before the route appears;
 * `focus` receives the rover position for the camera.
 */
export default function Rover({
  path,
  drive,
  focus,
  scan,
  mapping = null,
  reach = 0,
  alternatives = [],
  showRoutes = true,
}: {
  path: RoutePoint[];
  showRoutes?: boolean;
  alternatives?: { id: string; path: RoutePoint[] }[];
  drive: MutableRefObject<number>;
  focus: THREE.Vector3;
  scan: MutableRefObject<number>;
  /** Real DEM mapping when the backend scene is loaded, else the art terrain. */
  mapping?: SceneMapping | null;
  /** Radius of the scanned window in scene units (0: no pulses). */
  reach?: number;
}) {
  const body = useRef<THREE.Group>(null!);
  const spin = useRef(0);
  const lastDistance = useRef(0);
  const heading = useRef<number | null>(null);
  const route = useMemo(() => layout(path.length ? path : [{north_m:0,east_m:0}], mapping), [path, mapping]);
  useEffect(() => { lastDistance.current = 0; spin.current = 0; heading.current = null; }, [route]);
  const hasModel = useModelAvailable();
  const comparisonLines = useMemo(() => alternatives.filter(a => a.path.length > 1 && a.path !== path).map(a => {
    const alternate = layout(a.path, mapping);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(drape(alternate.points, alternate.ground)), new THREE.LineDashedMaterial({color: "#78bdbb",transparent:true,opacity:0.42,dashSize:0.12,gapSize:0.18,depthWrite:false}));
    line.computeLineDistances(); return line;
  }), [alternatives, path, mapping]);
  useEffect(() => () => comparisonLines.forEach(line => {line.geometry.dispose();line.material.dispose();}), [comparisonLines]);
  const lines = useMemo(() => {
    const lifted = drape(route.points, route.ground);
    const planned = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(lifted),
      new THREE.LineDashedMaterial({
        color: "#edbb66",
        transparent: true,
        opacity: 0.75,
        dashSize: 0.25,
        gapSize: 0.18,
      }),
    );
    planned.computeLineDistances();
    const driven = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(lifted),
      new THREE.LineBasicMaterial({ color: "#f4d18f", transparent: true, opacity: 0.95 }),
    );
    driven.geometry.setDrawRange(0, 1);
    return { planned, driven, count: lifted.length };
  }, [route]);
  useEffect(
    () => () => {
      for (const line of [lines.planned, lines.driven]) {
        line.geometry.dispose();
        (line.material as THREE.Material).dispose();
      }
    },
    [lines],
  );
  const position = useMemo(() => new THREE.Vector3(), []);
  const ahead = useMemo(() => new THREE.Vector3(), []);

  const pointAt = (distance: number, target: THREE.Vector3) => {
    const { points, along } = route;
    if (points.length === 1) return target.copy(points[0]);
    let i = 1;
    while (i < along.length - 1 && along[i] < distance) i++;
    const span = along[i] - along[i - 1] || 1;
    const t = THREE.MathUtils.clamp((distance - along[i - 1]) / span, 0, 1);
    target.lerpVectors(points[i - 1], points[i], t);
    target.y = route.ground(target.x, target.z);
    return target;
  };

  useFrame((_, delta) => {
    const distance = THREE.MathUtils.clamp(drive.current, 0, 1) * route.total;
    pointAt(distance, position);
    pointAt(Math.min(route.total, distance + 0.3), ahead);
    // Before the scan the rover rolls off the lander to the route start.
    if (scan.current < DEPLOY_END) {
      const k = THREE.MathUtils.smoothstep(scan.current / DEPLOY_END, 0, 1);
      position.lerpVectors(LANDER_EXIT_SCENE, route.points[0], k);
      position.y = route.ground(position.x, position.z);
      ahead.copy(route.points[0]);
      if (k > 0.98) pointAt(0.3, ahead);
    }
    const run = Math.hypot(ahead.x - position.x, ahead.z - position.z);
    if (run > 1e-3) {
      const yaw = Math.atan2(ahead.x - position.x, ahead.z - position.z);
      if (heading.current === null) heading.current = yaw;
      let turn = yaw - heading.current;
      turn = Math.atan2(Math.sin(turn), Math.cos(turn));
      heading.current += turn * Math.min(1, delta * 6);
      // Pitch the body onto the local slope along the heading.
      const pitch = Math.atan2(ahead.y - position.y, run);
      body.current.rotation.x = THREE.MathUtils.lerp(
        body.current.rotation.x,
        -pitch,
        Math.min(1, delta * 6),
      );
    }
    const travelled =
      scan.current < DEPLOY_END
        ? -position.distanceTo(route.points[0])
        : distance;
    spin.current += (travelled - lastDistance.current) / WHEEL_RADIUS_SCENE;
    lastDistance.current = travelled;
    body.current.position.set(position.x, position.y + RIDE_HEIGHT_SCENE, position.z);
    body.current.rotation.y = heading.current ?? 0;
    // Ground-contact roll across the wheel track, independently of the heading pitch.
    const yaw = heading.current ?? 0;
    const dx = Math.cos(yaw) * 0.34, dz = -Math.sin(yaw) * 0.34;
    const crossSlope = Math.atan2(route.ground(position.x + dx, position.z + dz) - route.ground(position.x - dx, position.z - dz), 0.68);
    body.current.rotation.z = THREE.MathUtils.damp(body.current.rotation.z, crossSlope, 6, Math.min(delta, 0.05));
    focus.copy(position);
    // The planned route draws itself once the search playback has finished.
    const shown = THREE.MathUtils.clamp(
      (scan.current - SEARCH_END) / (1 - SEARCH_END),
      0,
      1,
    );
    comparisonLines.forEach(line => { line.visible = shown > 0; });
    lines.planned.visible = shown > 0;
    lines.planned.geometry.setDrawRange(0, Math.max(1, Math.round(lines.count * shown)));
    lines.driven.geometry.setDrawRange(
      0,
      Math.max(1, Math.round(lines.count * (distance / route.total))),
    );
  });

  const procedural = <RoverModel spin={spin} scan={scan} drive={drive} />;
  return (
    <>
      <group visible={showRoutes}>
      {comparisonLines.map((line, i) => <primitive key={i} object={line} />)}
      <primitive object={lines.planned} />
      <primitive object={lines.driven} />
      </group>
      <group ref={body} rotation-order="YXZ">
        {hasModel ? (
          <ModelBoundary fallback={procedural}>
            <Suspense fallback={procedural}>
              <RoverGlb spin={spin} scan={scan} drive={drive} />
            </Suspense>
          </ModelBoundary>
        ) : (
          procedural
        )}
      </group>
      {reach > 0 && <Pulses scan={scan} drive={drive} reach={reach} origin={position} />}
    </>
  );
}
