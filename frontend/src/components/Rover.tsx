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
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { Model, terrainHeight } from "./LunarSurface";
import { PULSE_END, SEARCH_END, type SceneMapping, type SurfaceScene } from "./surfaceScene";

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
};

/** Where the rover rolls off, beside the lander (scene units). */
const START_OFFSET_SCENE = new THREE.Vector3(1.9, 0, 1.4);
/** On the art terrain the longest route is scaled to this many scene units. */
const ROUTE_SPAN_SCENE = 22;
const WHEEL_RADIUS_SCENE = 0.12;
const RIDE_HEIGHT_SCENE = 0.02;
/**
 * Optional user-supplied rover model (Sketchfab "Lunar Rover", CC BY 4.0, see
 * public/models/manifest.json). The procedural rover is used until it exists.
 */
const ROVER_MODEL_URL = "/models/lunar-rover.glb";
/** Yaw that turns the model's nose onto +z (set once the file is checked). */
const ROVER_MODEL_YAW = 0;
const ROVER_MODEL_SIZE = 1.05;
/** Antenna height above the ground for the scan pulses (scene units). */
const DISH_HEIGHT_SCENE = 0.72;
const PULSE_RINGS = 3;

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
}: {
  spin: MutableRefObject<number>;
  scan: MutableRefObject<number>;
}) {
  const dish = useRef<THREE.Group>(null!);
  useFrame(({ clock }) => {
    // The high-gain dish sweeps while the scan runs.
    if (scan.current < SEARCH_END) dish.current.rotation.y = clock.elapsedTime * 1.8;
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

/** Checks that the optional model file is really there (not the SPA page). */
function useModelAvailable(url: string) {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    let live = true;
    fetch(url, { method: "HEAD" })
      .then((response) => {
        const type = response.headers.get("content-type") ?? "";
        if (live) setAvailable(response.ok && !type.includes("text/html"));
      })
      .catch(() => {
        if (live) setAvailable(false);
      });
    return () => {
      live = false;
    };
  }, [url]);
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
  reach,
  origin,
}: {
  scan: MutableRefObject<number>;
  reach: number;
  origin: THREE.Vector3;
}) {
  const rings = useRef<THREE.Mesh[]>([]);
  const geometry = useMemo(
    () => new THREE.RingGeometry(0.97, 1, 128).rotateX(-Math.PI / 2),
    [],
  );
  useEffect(() => () => geometry.dispose(), [geometry]);
  useFrame(({ clock }) => {
    const s = scan.current;
    const active = s < SEARCH_END;
    const front = reach * Math.min(1, s / PULSE_END);
    rings.current.forEach((ring, k) => {
      ring.visible = active;
      if (!active) return;
      const phase = (clock.elapsedTime * 0.45 + k / PULSE_RINGS) % 1;
      ring.position.set(origin.x, origin.y + DISH_HEIGHT_SCENE, origin.z);
      ring.scale.setScalar(Math.max(0.05, phase * front));
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
}: {
  path: RoutePoint[];
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
  const route = useMemo(() => layout(path, mapping), [path, mapping]);
  const hasModel = useModelAvailable(ROVER_MODEL_URL);
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
    spin.current += (distance - lastDistance.current) / WHEEL_RADIUS_SCENE;
    lastDistance.current = distance;
    body.current.position.set(position.x, position.y + RIDE_HEIGHT_SCENE, position.z);
    body.current.rotation.y = heading.current ?? 0;
    focus.copy(position);
    // The planned route draws itself once the search playback has finished.
    const shown = THREE.MathUtils.clamp(
      (scan.current - SEARCH_END) / (1 - SEARCH_END),
      0,
      1,
    );
    lines.planned.visible = shown > 0;
    lines.planned.geometry.setDrawRange(0, Math.max(1, Math.round(lines.count * shown)));
    lines.driven.geometry.setDrawRange(
      0,
      Math.max(1, Math.round(lines.count * (distance / route.total))),
    );
  });

  const procedural = <RoverModel spin={spin} scan={scan} />;
  return (
    <>
      <primitive object={lines.planned} />
      <primitive object={lines.driven} />
      <group ref={body} rotation-order="YXZ">
        {hasModel ? (
          <ModelBoundary fallback={procedural}>
            <Suspense fallback={procedural}>
              <group rotation={[0, ROVER_MODEL_YAW, 0]}>
                <Model path={ROVER_MODEL_URL} size={ROVER_MODEL_SIZE} grounded />
              </group>
            </Suspense>
          </ModelBoundary>
        ) : (
          procedural
        )}
      </group>
      {reach > 0 && <Pulses scan={scan} reach={reach} origin={position} />}
    </>
  );
}
