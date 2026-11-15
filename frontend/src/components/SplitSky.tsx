import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import type { Frame, Result } from "../types";
import { SKY_ELEVATION_DISPLAY_FACTOR as ELEVATION_DISPLAY_FACTOR } from "../comparisonPlayback";

/**
 * Local-sky illustration of one analysed scenario: the backend terrain
 * horizon as a ring wall and the backend Sun/Earth azimuth and elevation of
 * the shown sample. Scene units are arbitrary and every elevation angle,
 * terrain and bodies alike, is multiplied by the same display factor so the
 * low polar angles stay readable; nothing here is computed physics.
 */
const MAX_DISPLAY_ELEVATION_RAD = THREE.MathUtils.degToRad(80);
const MIN_DISPLAY_ELEVATION_RAD = THREE.MathUtils.degToRad(-20);
const HORIZON_RADIUS = 10;
const SKY_RADIUS = 9.2;
const VISIBLE = new Set(["full", "partial", "visible"]);

const displayElevation = (elevation_deg: number) =>
  THREE.MathUtils.clamp(
    THREE.MathUtils.degToRad(elevation_deg * ELEVATION_DISPLAY_FACTOR),
    MIN_DISPLAY_ELEVATION_RAD,
    MAX_DISPLAY_ELEVATION_RAD,
  );

/** Scene direction for a local azimuth (from north, toward east) and elevation; north is -z, east is +x. */
function skyPoint(azimuth_deg: number, elevation_deg: number, radius: number) {
  const az = THREE.MathUtils.degToRad(azimuth_deg);
  const el = displayElevation(elevation_deg);
  return new THREE.Vector3(
    Math.sin(az) * Math.cos(el) * radius,
    Math.sin(el) * radius,
    -Math.cos(az) * Math.cos(el) * radius,
  );
}

function HorizonWall({ result }: { result: Result }) {
  const geometry = useMemo(() => {
    const rows = [...result.horizon.directions].sort((p, q) => p.azimuth_deg - q.azimuth_deg);
    const ring = rows.length ? [...rows, { ...rows[0], azimuth_deg: rows[0].azimuth_deg + 360 }] : [];
    const positions: number[] = [];
    const colors: number[] = [];
    const known = new THREE.Color("#8d8a84");
    const unknown = new THREE.Color("#7a3b34");
    for (const point of ring) {
      const az = THREE.MathUtils.degToRad(point.azimuth_deg);
      const height =
        point.elevation_deg === null
          ? 0.15
          : Math.max(0.02, Math.tan(Math.max(0, displayElevation(point.elevation_deg))) * HORIZON_RADIUS);
      const x = Math.sin(az) * HORIZON_RADIUS,
        z = -Math.cos(az) * HORIZON_RADIUS;
      positions.push(x, -0.05, z, x, height, z);
      const color = point.elevation_deg === null ? unknown : known;
      colors.push(color.r * 0.55, color.g * 0.55, color.b * 0.55, color.r, color.g, color.b);
    }
    const indices: number[] = [];
    for (let i = 0; i + 1 < ring.length; i++) {
      const a = i * 2;
      indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
    const built = new THREE.BufferGeometry();
    built.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    built.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
    built.setIndex(indices);
    built.computeVertexNormals();
    return built;
  }, [result]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <mesh geometry={geometry} receiveShadow>
      <meshStandardMaterial vertexColors side={THREE.DoubleSide} roughness={1} />
    </mesh>
  );
}

function Lander() {
  const legs = [0, 1, 2, 3].map((i) => (i * Math.PI) / 2 + Math.PI / 4);
  return (
    <group>
      <mesh position={[0, 0.75, 0]} castShadow>
        <cylinderGeometry args={[0.45, 0.55, 0.7, 8]} />
        <meshStandardMaterial color="#d8c48a" metalness={0.5} roughness={0.45} />
      </mesh>
      <mesh position={[0, 1.3, 0]} castShadow>
        <coneGeometry args={[0.35, 0.4, 8]} />
        <meshStandardMaterial color="#c9ccd1" metalness={0.3} roughness={0.5} />
      </mesh>
      {legs.map((angle) => (
        <mesh
          key={angle}
          position={[Math.cos(angle) * 0.6, 0.3, Math.sin(angle) * 0.6]}
          rotation={[Math.sin(angle) * 0.5, 0, -Math.cos(angle) * 0.5]}
          castShadow
        >
          <cylinderGeometry args={[0.04, 0.04, 0.75, 5]} />
          <meshStandardMaterial color="#9aa0a6" />
        </mesh>
      ))}
    </group>
  );
}

function Body({
  azimuth_deg,
  elevation_deg,
  color,
  size,
  glow,
}: {
  azimuth_deg: number;
  elevation_deg: number;
  color: string;
  size: number;
  glow: boolean;
}) {
  const position = skyPoint(azimuth_deg, elevation_deg, SKY_RADIUS);
  return (
    <mesh position={position}>
      <sphereGeometry args={[size, 24, 16]} />
      <meshBasicMaterial color={color} transparent opacity={glow ? 1 : 0.45} />
    </mesh>
  );
}

function SunLight({ frame }: { frame?: Frame }) {
  const sun = frame?.sun;
  const lit = !!sun && VISIBLE.has(sun.state);
  const position =
    sun && sun.azimuth_deg !== null && sun.elevation_deg !== null
      ? skyPoint(sun.azimuth_deg, Math.max(sun.elevation_deg, 0.5), 30)
      : new THREE.Vector3(0, 30, 0);
  return (
    <directionalLight
      position={position}
      intensity={lit ? (sun!.state === "partial" ? 1.4 : 2.6) : 0}
      color="#fff4e0"
      castShadow
      shadow-mapSize={[1024, 1024]}
      shadow-camera-left={-12}
      shadow-camera-right={12}
      shadow-camera-top={12}
      shadow-camera-bottom={-12}
    />
  );
}

/** Camera from the south-east, looking at the lander, with a slow drift that the reduced-motion setting stops. */
function CameraRig({ animate }: { animate: boolean }) {
  const camera = useThree((state) => state.camera);
  const angle = useRef(0);
  useFrame((_, delta) => {
    if (animate) angle.current += delta * 0.03;
    const a = Math.PI * 0.8 + Math.sin(angle.current) * 0.35;
    camera.position.set(Math.sin(a) * 15, 7.5, -Math.cos(a) * 15);
    camera.lookAt(0, 1.2, 0);
  });
  return null;
}

function Compass() {
  const labels: [string, number][] = [
    ["N", 0],
    ["E", 90],
    ["S", 180],
    ["W", 270],
  ];
  return (
    <>
      {labels.map(([label, az]) => {
        const p = skyPoint(az, 0, HORIZON_RADIUS + 0.6);
        return (
          <mesh key={label} position={[p.x, 0.02, p.z]} rotation={[-Math.PI / 2, 0, 0]}>
            <circleGeometry args={[label === "N" ? 0.32 : 0.2, 16]} />
            <meshBasicMaterial color={label === "N" ? "#e8c58b" : "#64748b"} />
          </mesh>
        );
      })}
    </>
  );
}

export default function SplitSky({
  result,
  frame,
  label,
  visible,
  animate,
  compact,
}: {
  result: Result;
  frame?: Frame;
  label: string;
  visible: boolean;
  animate: boolean;
  compact: boolean;
}) {
  const sun = frame?.sun;
  const earth = frame?.earth;
  const sunLit = !!sun && VISIBLE.has(sun.state);
  const background = sunLit ? "#0d1522" : "#04060a";
  return (
    <Canvas
      aria-label={label}
      camera={{ position: [8, 7.5, 12], fov: 46, near: 0.1, far: 200 }}
      dpr={compact ? 1 : [1, 1.5]}
      shadows
      frameloop={visible && animate ? "always" : "demand"}
    >
      <color attach="background" args={[background]} />
      <CameraRig animate={animate} />
      <ambientLight intensity={sunLit ? 0.25 : 0.12} />
      <hemisphereLight args={["#5a6b85", "#1a1a1a", sunLit ? 0.25 : 0.1]} />
      <SunLight frame={frame} />
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <circleGeometry args={[HORIZON_RADIUS, 64]} />
        <meshStandardMaterial color="#6c6a66" roughness={1} />
      </mesh>
      <HorizonWall result={result} />
      <Compass />
      <Lander />
      {sun && sun.azimuth_deg !== null && sun.elevation_deg !== null && (
        <Body
          azimuth_deg={sun.azimuth_deg}
          elevation_deg={sun.elevation_deg}
          color="#ffd27a"
          size={0.55}
          glow={sunLit}
        />
      )}
      {earth && earth.azimuth_deg !== null && earth.elevation_deg !== null && (
        <Body
          azimuth_deg={earth.azimuth_deg}
          elevation_deg={earth.elevation_deg}
          color="#74b9df"
          size={0.4}
          glow={VISIBLE.has(earth.state)}
        />
      )}
    </Canvas>
  );
}
