import { useEffect, useMemo, useRef, type MutableRefObject } from "react";
import { useFrame, useLoader } from "@react-three/fiber";
import * as THREE from "three";

/**
 * Illustrative Sun–Earth–Moon orrery. Sizes and distances are NOT to scale
 * (at true scale Earth and the Moon would be invisible next to the Sun) and
 * time is accelerated. Period ratios and tilts follow the real system so the
 * order of motions is right: the Moon circles Earth about 13.37 times per
 * Earth orbit, keeps one face towards Earth, and its orbit is inclined ~5.1°.
 */

/** Wall-clock seconds for one illustrative year. */
const YEAR_S = 60;
/** Sidereal months per sidereal year (365.256 d / 27.322 d). */
const MONTHS_PER_YEAR = 365.256 / 27.322;
/** Sidereal days per sidereal year; slowed 20× so Earth's spin stays legible. */
const EARTH_SPINS_PER_YEAR = 366.256 / 20;
const EARTH_TILT_RAD = (23.44 * Math.PI) / 180;
const MOON_INCLINATION_RAD = (5.14 * Math.PI) / 180;

const SUN_RADIUS = 6;
const EARTH_ORBIT_RADIUS = 42;
const EARTH_RADIUS = 1.6;
const MOON_ORBIT_RADIUS = 5.2;
const MOON_RADIUS = 0.45;

function ring(radius: number, color: string, opacity: number) {
  const points = Array.from({ length: 257 }, (_, i) => {
    const a = (i / 256) * Math.PI * 2;
    return new THREE.Vector3(Math.cos(a) * radius, 0, -Math.sin(a) * radius);
  });
  return new THREE.LineLoop(
    new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity }),
  );
}

function glow() {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const context = canvas.getContext("2d")!;
  const gradient = context.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, "#fff4cf");
  gradient.addColorStop(0.25, "#ffd27a88");
  gradient.addColorStop(1, "#ffb54700");
  context.fillStyle = gradient;
  context.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(canvas);
}

export default function SolarSystem({
  reduced,
  compact,
  user,
}: {
  reduced: boolean;
  compact: boolean;
  user: MutableRefObject<{ yaw: number; pitch: number; zoom: number }>;
}) {
  const [earthMap, moonMap] = useLoader(THREE.TextureLoader, [
    "/nasa/earth.jpg",
    "/nasa/moon.jpg",
  ]);
  earthMap.colorSpace = THREE.SRGBColorSpace;
  moonMap.colorSpace = THREE.SRGBColorSpace;
  const earthSystem = useRef<THREE.Group>(null!);
  const earth = useRef<THREE.Mesh>(null!);
  const moonOrbit = useRef<THREE.Group>(null!);
  const moon = useRef<THREE.Mesh>(null!);
  const time = useRef(0);
  const objects = useMemo(
    () => ({
      earthRing: ring(EARTH_ORBIT_RADIUS, "#9ac9e5", 0.35),
      moonRing: ring(MOON_ORBIT_RADIUS, "#d8d2c4", 0.35),
      sunGlow: glow(),
    }),
    [],
  );
  useEffect(
    () => () => {
      for (const line of [objects.earthRing, objects.moonRing]) {
        line.geometry.dispose();
        (line.material as THREE.Material).dispose();
      }
      objects.sunGlow.dispose();
    },
    [objects],
  );
  const stars = useMemo(() => {
    const values = new Float32Array(2000 * 3);
    for (let i = 0; i < 2000; i++) {
      const a = Math.sin(i * 12.9898) * 43758.5453;
      const b = Math.sin(i * 78.233) * 12345.678;
      const u = a - Math.floor(a),
        w = b - Math.floor(b);
      const theta = u * Math.PI * 2,
        z = w * 2 - 1,
        s = Math.sqrt(1 - z * z);
      values.set([Math.cos(theta) * s * 400, z * 400, Math.sin(theta) * s * 400], i * 3);
    }
    return values;
  }, []);
  const spherical = useMemo(() => new THREE.Spherical(), []);

  useFrame(({ camera }, delta) => {
    if (!reduced) time.current += Math.min(delta, 0.05);
    const year = time.current / YEAR_S;
    // Counter-clockwise seen from ecliptic north, like the real system.
    const earthAngle = year * Math.PI * 2;
    earthSystem.current.position.set(
      Math.cos(earthAngle) * EARTH_ORBIT_RADIUS,
      0,
      -Math.sin(earthAngle) * EARTH_ORBIT_RADIUS,
    );
    earth.current.rotation.y = year * EARTH_SPINS_PER_YEAR * Math.PI * 2;
    const moonAngle = year * MONTHS_PER_YEAR * Math.PI * 2;
    moon.current.position.set(
      Math.cos(moonAngle) * MOON_ORBIT_RADIUS,
      0,
      -Math.sin(moonAngle) * MOON_ORBIT_RADIUS,
    );
    // Synchronous rotation: one face always towards Earth.
    moon.current.rotation.y = moonAngle + Math.PI;
    spherical.set(
      (compact ? 120 : 95) * THREE.MathUtils.clamp(user.current.zoom, 0.15, 2.4),
      THREE.MathUtils.clamp(1.05 - user.current.pitch, 0.15, 1.55),
      0.6 + user.current.yaw,
    );
    camera.position.setFromSpherical(spherical);
    camera.lookAt(0, 0, 0);
  });

  return (
    <>
      <color attach="background" args={["#03060a"]} />
      <ambientLight intensity={0.08} />
      <pointLight position={[0, 0, 0]} intensity={2.6} decay={0} color="#fff3df" />
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[stars, 3]} />
        </bufferGeometry>
        <pointsMaterial color="#b9c7d4" size={1.4} sizeAttenuation={false} transparent opacity={0.7} />
      </points>
      <mesh>
        <sphereGeometry args={[SUN_RADIUS, 48, 32]} />
        <meshBasicMaterial color="#fff1c9" />
      </mesh>
      <sprite scale={[SUN_RADIUS * 6, SUN_RADIUS * 6, 1]}>
        <spriteMaterial
          map={objects.sunGlow}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </sprite>
      <primitive object={objects.earthRing} />
      <group ref={earthSystem}>
        <group rotation={[0, 0, EARTH_TILT_RAD]}>
          <mesh ref={earth}>
            <sphereGeometry args={[EARTH_RADIUS, 64, 48]} />
            <meshStandardMaterial map={earthMap} roughness={0.85} />
          </mesh>
        </group>
        <group ref={moonOrbit} rotation={[MOON_INCLINATION_RAD, 0, 0]}>
          <primitive object={objects.moonRing} />
          <mesh ref={moon}>
            <sphereGeometry args={[MOON_RADIUS, 48, 32]} />
            <meshStandardMaterial map={moonMap} roughness={1} />
          </mesh>
        </group>
      </group>
    </>
  );
}
