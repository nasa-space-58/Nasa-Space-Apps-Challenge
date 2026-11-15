import { useEffect, useMemo, useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { terrainHeight } from "./LunarSurface";

/** A backend R8 route cell, metres north/east of the landing cell. */
export type RoutePoint = { north_m: number; east_m: number };

/** Where the rover rolls off, beside the lander (scene units). */
const START_OFFSET_SCENE = new THREE.Vector3(1.9, 0, 1.4);
/** The longest route is scaled to this many scene units from the start. */
const ROUTE_SPAN_SCENE = 22;
const WHEEL_RADIUS_SCENE = 0.12;
const RIDE_HEIGHT_SCENE = 0.02;

/**
 * Lays the backend route onto the illustrative terrain with one uniform scale
 * (east → +x, north → −z). The geometry is the real route; distances in the
 * scene are not to scale with the terrain art.
 */
function layout(path: RoutePoint[]) {
  const extent_m = Math.max(
    1,
    ...path.map((p) => Math.max(Math.abs(p.north_m), Math.abs(p.east_m))),
  );
  const scale = ROUTE_SPAN_SCENE / extent_m;
  const points = path.map((p) => {
    const x = START_OFFSET_SCENE.x + p.east_m * scale;
    const z = START_OFFSET_SCENE.z - p.north_m * scale;
    return new THREE.Vector3(x, terrainHeight(x, z), z);
  });
  const along = [0];
  for (let i = 1; i < points.length; i++)
    along.push(along[i - 1] + points[i].distanceTo(points[i - 1]));
  return { points, along, total: along[along.length - 1] || 1 };
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
function RoverModel({ spin }: { spin: MutableRefObject<number> }) {
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
      <mesh position={[-0.18, 0.68, -0.26]} rotation={[0.5, 0, 0]}>
        <cylinderGeometry args={[0.11, 0.02, 0.05, 16]} />
        <meshStandardMaterial color="#efe9dc" />
      </mesh>
      {wheels.map((position, i) => (
        <Wheel key={i} position={position} spin={spin} />
      ))}
    </group>
  );
}

/**
 * Drives the rover along the backend route. `drive` is the travelled share of
 * the route (0–1); `focus` receives the rover position for the camera.
 */
export default function Rover({
  path,
  drive,
  focus,
}: {
  path: RoutePoint[];
  drive: MutableRefObject<number>;
  focus: THREE.Vector3;
}) {
  const body = useRef<THREE.Group>(null!);
  const spin = useRef(0);
  const lastDistance = useRef(0);
  const heading = useRef<number | null>(null);
  const route = useMemo(() => layout(path), [path]);
  const lines = useMemo(() => {
    const lifted = route.points.map((p) => p.clone().setY(p.y + 0.05));
    const planned = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(lifted),
      new THREE.LineDashedMaterial({
        color: "#edbb66",
        transparent: true,
        opacity: 0.55,
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
    return { planned, driven };
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
    if (points.length === 1) return { target: target.copy(points[0]), index: 0 };
    let i = 1;
    while (i < along.length - 1 && along[i] < distance) i++;
    const span = along[i] - along[i - 1] || 1;
    const t = THREE.MathUtils.clamp((distance - along[i - 1]) / span, 0, 1);
    target.lerpVectors(points[i - 1], points[i], t);
    target.y = terrainHeight(target.x, target.z);
    return { target, index: i };
  };

  useFrame((_, delta) => {
    const distance = THREE.MathUtils.clamp(drive.current, 0, 1) * route.total;
    const { index } = pointAt(distance, position);
    pointAt(Math.min(route.total, distance + 0.3), ahead);
    if (ahead.distanceToSquared(position) > 1e-6) {
      const yaw = Math.atan2(ahead.x - position.x, ahead.z - position.z);
      if (heading.current === null) heading.current = yaw;
      let turn = yaw - heading.current;
      turn = Math.atan2(Math.sin(turn), Math.cos(turn));
      heading.current += turn * Math.min(1, delta * 6);
    }
    spin.current += (distance - lastDistance.current) / WHEEL_RADIUS_SCENE;
    lastDistance.current = distance;
    body.current.position.set(position.x, position.y + RIDE_HEIGHT_SCENE, position.z);
    body.current.rotation.y = heading.current ?? 0;
    focus.copy(position);
    lines.driven.geometry.setDrawRange(0, Math.max(1, index));
  });

  return (
    <>
      <primitive object={lines.planned} />
      <primitive object={lines.driven} />
      <group ref={body}>
        <RoverModel spin={spin} />
      </group>
    </>
  );
}
