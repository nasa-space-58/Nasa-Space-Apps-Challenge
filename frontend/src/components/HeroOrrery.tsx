import { useEffect, useMemo, useRef } from "react";
import { useFrame, useLoader } from "@react-three/fiber";
import * as THREE from "three";

/**
 * Home-page Sun–Earth–Moon orrery. Not to scale and accelerated; period
 * ratios (13.37 sidereal months per year), Earth's tilt and the Moon's orbital
 * inclination and synchronous rotation follow the real system.
 */
const YEAR_S = 40;
const MONTHS_PER_YEAR = 365.256 / 27.322;
const EARTH_SPINS_PER_YEAR = 366.256 / 20;
const EARTH_TILT_RAD = (23.44 * Math.PI) / 180;
const MOON_INCLINATION_RAD = (5.14 * Math.PI) / 180;
/** Viewing tilt of the ecliptic so the orbits read as ellipses. */
const ECLIPTIC_TILT: [number, number, number] = [0.42, 0, -0.12];

const SUN_RADIUS = 0.75;
const EARTH_ORBIT_RADIUS = 3.2;
const EARTH_RADIUS = 0.3;
const MOON_ORBIT_RADIUS = 0.78;
const MOON_RADIUS = 0.11;

function ring(radius: number, color: string, opacity: number) {
  const points = Array.from({ length: 193 }, (_, i) => {
    const a = (i / 192) * Math.PI * 2;
    return new THREE.Vector3(Math.cos(a) * radius, 0, -Math.sin(a) * radius);
  });
  return new THREE.LineLoop(
    new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity }),
  );
}

function glowTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const context = canvas.getContext("2d")!;
  const gradient = context.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, "#fff6d8");
  gradient.addColorStop(0.3, "#ffcf7066");
  gradient.addColorStop(1, "#ffb54700");
  context.fillStyle = gradient;
  context.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(canvas);
}

export default function HeroOrrery({
  mix,
  time,
}: {
  /** 1 = fully shown, 0 = hidden (scaled away). */
  mix: () => number;
  /** Shared ambient time in seconds (frozen when motion is reduced). */
  time: () => number;
}) {
  const [earthMap, moonMap] = useLoader(THREE.TextureLoader, [
    "/nasa/earth.jpg",
    "/nasa/moon.jpg",
  ]);
  earthMap.colorSpace = THREE.SRGBColorSpace;
  moonMap.colorSpace = THREE.SRGBColorSpace;
  const root = useRef<THREE.Group>(null!);
  const light = useRef<THREE.PointLight>(null!);
  const earthSystem = useRef<THREE.Group>(null!);
  const earth = useRef<THREE.Mesh>(null!);
  const moon = useRef<THREE.Mesh>(null!);
  const objects = useMemo(
    () => ({
      earthRing: ring(EARTH_ORBIT_RADIUS, "#9ac9e5", 0.4),
      moonRing: ring(MOON_ORBIT_RADIUS, "#d8d2c4", 0.45),
      glow: glowTexture(),
    }),
    [],
  );
  useEffect(
    () => () => {
      for (const line of [objects.earthRing, objects.moonRing]) {
        line.geometry.dispose();
        (line.material as THREE.Material).dispose();
      }
      objects.glow.dispose();
    },
    [objects],
  );

  useFrame(() => {
    const shown = mix();
    root.current.visible = shown > 0.001;
    root.current.scale.setScalar(Math.max(0.001, shown));
    light.current.intensity = 9 * shown;
    const year = time() / YEAR_S;
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
    moon.current.rotation.y = moonAngle + Math.PI;
  });

  return (
    <group ref={root} rotation={ECLIPTIC_TILT}>
      <pointLight ref={light} position={[0, 0, 0]} decay={0} color="#fff3df" />
      <mesh>
        <sphereGeometry args={[SUN_RADIUS, 48, 32]} />
        <meshBasicMaterial color="#ffe9b0" />
      </mesh>
      <sprite scale={[SUN_RADIUS * 6, SUN_RADIUS * 6, 1]}>
        <spriteMaterial
          map={objects.glow}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </sprite>
      <primitive object={objects.earthRing} />
      <group ref={earthSystem}>
        <group rotation={[0, 0, EARTH_TILT_RAD]}>
          <mesh ref={earth}>
            <sphereGeometry args={[EARTH_RADIUS, 48, 32]} />
            <meshStandardMaterial map={earthMap} roughness={0.85} />
          </mesh>
        </group>
        <group rotation={[MOON_INCLINATION_RAD, 0, 0]}>
          <primitive object={objects.moonRing} />
          <mesh ref={moon}>
            <sphereGeometry args={[MOON_RADIUS, 32, 24]} />
            <meshStandardMaterial map={moonMap} roughness={1} />
          </mesh>
        </group>
      </group>
    </group>
  );
}
