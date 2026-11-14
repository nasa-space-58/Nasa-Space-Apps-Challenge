import { useEffect, useMemo, useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { noise, smooth } from "./LunarSurface";
import { LAUNCH_CLOSEUP_END_PROGRESS } from "../missionPath";

// Art-directed launch close-up for mission control. Scene units here are not
// metres, the vehicle is a generic procedural rocket and nothing in this file
// is a computed ascent; it only illustrates the start of the mission.

export type OrbitInput = MutableRefObject<{
  yaw: number;
  pitch: number;
  zoom: number;
}>;

/** Close-up fraction at which the hold-down release lets the vehicle rise. */
const LIFTOFF_AT = 0.2;
/** Height reached (scene units) when the close-up hands over to orbit view. */
const CLIMB_HEIGHT = 46;
/** Final pitch-over angle of the illustrative gravity turn. */
const PITCH_OVER_RAD = 0.42;
const TRAIL_PARTICLES = 320;
const CLOUD_PARTICLES = 260;
const DAY_SKY = new THREE.Color("#7fa9d3");
const SPACE_SKY = new THREE.Color("#03060a");
const tmpColor = new THREE.Color();

/** Close-up timeline in [0, 1] for a mission progress value. */
const closeupAt = (p: number) =>
  THREE.MathUtils.clamp(p / LAUNCH_CLOSEUP_END_PROGRESS, 0, 1);
/** Climb fraction in [0, 1] after lift-off. */
const climbAt = (u: number) =>
  THREE.MathUtils.clamp((u - LIFTOFF_AT) / (1 - LIFTOFF_AT), 0, 1);

function vehicleState(u: number, target: THREE.Vector3) {
  const c = climbAt(u);
  // Constant-acceleration feel: height grows with the square of time.
  const height = CLIMB_HEIGHT * c * c;
  const pitch = PITCH_OVER_RAD * smooth(0.15, 1, c);
  const downrange = height * Math.sin(pitch) * 0.55;
  target.set(downrange, height, 0);
  return { climb: c, height, pitch };
}

function Rocket() {
  const white = (
    <meshStandardMaterial color="#e9e7e1" roughness={0.55} metalness={0.1} />
  );
  const dark = (
    <meshStandardMaterial color="#25292d" roughness={0.6} metalness={0.4} />
  );
  return (
    <group>
      {/* Core stage, interstage, upper stage and payload fairing. */}
      <mesh position={[0, 1.9, 0]} castShadow>
        <cylinderGeometry args={[0.22, 0.22, 3.1, 32]} />
        {white}
      </mesh>
      <mesh position={[0, 3.5, 0]} castShadow>
        <cylinderGeometry args={[0.215, 0.22, 0.14, 32]} />
        {dark}
      </mesh>
      <mesh position={[0, 4.05, 0]} castShadow>
        <cylinderGeometry args={[0.2, 0.215, 0.95, 32]} />
        {white}
      </mesh>
      <mesh position={[0, 4.83, 0]} castShadow>
        <coneGeometry args={[0.2, 0.62, 32]} />
        {white}
      </mesh>
      {/* Main engine bell. */}
      <mesh position={[0, 0.2, 0]} castShadow>
        <cylinderGeometry args={[0.1, 0.17, 0.36, 24, 1, true]} />
        {dark}
      </mesh>
      {/* Strap-on boosters with their own nozzles. */}
      {[-1, 1].map((side) => (
        <group key={side} position={[side * 0.33, 0, 0]}>
          <mesh position={[0, 1.38, 0]} castShadow>
            <cylinderGeometry args={[0.11, 0.11, 2.2, 24]} />
            {white}
          </mesh>
          <mesh position={[0, 2.66, 0]} castShadow>
            <coneGeometry args={[0.11, 0.36, 24]} />
            {white}
          </mesh>
          <mesh position={[0, 0.17, 0]} castShadow>
            <cylinderGeometry args={[0.06, 0.1, 0.22, 16, 1, true]} />
            {dark}
          </mesh>
        </group>
      ))}
      {[0, 1, 2, 3].map((i) => (
        <mesh
          key={i}
          rotation={[0, (i * Math.PI) / 2 + Math.PI / 4, 0]}
          position={[0, 0.62, 0]}
          castShadow
        >
          <boxGeometry args={[0.02, 0.4, 0.62]} />
          {dark}
        </mesh>
      ))}
    </group>
  );
}

function Pad() {
  const lattice = useMemo(() => {
    const beams: { position: [number, number, number]; size: [number, number, number] }[] = [];
    const towerX = -1.15;
    const half = 0.3;
    const height = 5.4;
    for (const dx of [-half, half])
      for (const dz of [-half, half])
        beams.push({
          position: [towerX + dx, height / 2, dz],
          size: [0.05, height, 0.05],
        });
    for (let y = 0.45; y < height; y += 0.45) {
      beams.push({ position: [towerX, y, -half], size: [half * 2, 0.04, 0.04] });
      beams.push({ position: [towerX, y, half], size: [half * 2, 0.04, 0.04] });
      beams.push({ position: [towerX - half, y, 0], size: [0.04, 0.04, half * 2] });
      beams.push({ position: [towerX + half, y, 0], size: [0.04, 0.04, half * 2] });
    }
    // Crew-access and umbilical arms reaching to the vehicle.
    for (const y of [2.6, 4.3])
      beams.push({ position: [towerX + 0.62, y, 0], size: [0.65, 0.07, 0.12] });
    return beams;
  }, []);
  return (
    <group>
      <mesh position={[0, -0.1, 0]} receiveShadow>
        <cylinderGeometry args={[4.2, 4.8, 0.2, 48]} />
        <meshStandardMaterial color="#8b8a84" roughness={0.95} />
      </mesh>
      {/* Flame trench under the vehicle. */}
      <mesh position={[1.6, 0.005, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[4.2, 1.1]} />
        <meshStandardMaterial color="#2a2a28" roughness={1} />
      </mesh>
      {lattice.map((beam, i) => (
        <mesh key={i} position={beam.position} castShadow>
          <boxGeometry args={beam.size} />
          <meshStandardMaterial color="#b24a2c" roughness={0.7} metalness={0.3} />
        </mesh>
      ))}
      {/* Lightning masts around the pad. */}
      {[
        [-3.4, -3.1],
        [3.6, -2.8],
        [-2.6, 3.7],
      ].map(([x, z], i) => (
        <mesh key={i} position={[x, 3.2, z]} castShadow>
          <cylinderGeometry args={[0.03, 0.05, 6.4, 8]} />
          <meshStandardMaterial color="#9a9c9e" roughness={0.6} />
        </mesh>
      ))}
    </group>
  );
}

/** Soft billboard particles with per-particle size and opacity. */
function useSmokeMaterial(color: string) {
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        uniforms: { tint: { value: new THREE.Color(color) } },
        vertexShader: `attribute float size; attribute float alpha; varying float vAlpha;
          void main() { vec4 p = modelViewMatrix * vec4(position, 1.0); gl_Position = projectionMatrix * p;
          gl_PointSize = min(260.0, size * 320.0 / max(0.5, -p.z)); vAlpha = alpha; }`,
        fragmentShader: `uniform vec3 tint; varying float vAlpha;
          void main() { float d = length(gl_PointCoord - 0.5); float a = (1.0 - smoothstep(0.08, 0.5, d)) * vAlpha;
          if (a < 0.003) discard; gl_FragColor = vec4(tint, a); }`,
      }),
    [color],
  );
  useEffect(() => () => material.dispose(), [material]);
  return material;
}

function particleGeometry(count: number) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(new Float32Array(count * 3), 3),
  );
  geometry.setAttribute("size", new THREE.BufferAttribute(new Float32Array(count), 1));
  geometry.setAttribute("alpha", new THREE.BufferAttribute(new Float32Array(count), 1));
  return geometry;
}

/**
 * Exhaust trail behind the vehicle plus the billowing cloud at the pad. Both
 * are functions of the close-up timeline, so scrubbing backwards stays exact.
 */
function Exhaust({
  progress,
  reduced,
}: {
  progress: MutableRefObject<number>;
  reduced: boolean;
}) {
  const trailMaterial = useSmokeMaterial("#e4e1da");
  const cloudMaterial = useSmokeMaterial("#d2cec6");
  const trail = useMemo(() => particleGeometry(TRAIL_PARTICLES), []);
  const cloud = useMemo(() => particleGeometry(CLOUD_PARTICLES), []);
  const point = useMemo(() => new THREE.Vector3(), []);
  useEffect(
    () => () => {
      trail.dispose();
      cloud.dispose();
    },
    [trail, cloud],
  );
  useFrame(({ clock }) => {
    const u = closeupAt(progress.current);
    const now = climbAt(u);
    const churn = reduced ? 0 : clock.elapsedTime;
    const position = trail.getAttribute("position") as THREE.BufferAttribute;
    const size = trail.getAttribute("size") as THREE.BufferAttribute;
    const alpha = trail.getAttribute("alpha") as THREE.BufferAttribute;
    for (let i = 0; i < TRAIL_PARTICLES; i++) {
      // Each particle was emitted when the climb fraction was `emitted`.
      const emitted = (i / TRAIL_PARTICLES) * now;
      const age = now - emitted;
      vehicleState(LIFTOFF_AT + emitted * (1 - LIFTOFF_AT), point);
      const spread = 0.15 + age * 3.2;
      const angle = noise(i + 40) * Math.PI * 2 + churn * 0.3;
      position.setXYZ(
        i,
        point.x + Math.cos(angle) * spread * noise(i + 70) - 0.15,
        Math.max(0.1, point.y - 0.4 + age * 0.6),
        point.z + Math.sin(angle) * spread * noise(i + 90),
      );
      size.setX(i, 0.5 + age * 9 + noise(i + 11) * 0.4);
      // Thin out with altitude and fade with age.
      alpha.setX(
        i,
        now > 0
          ? (0.55 - Math.min(0.4, age * 0.9)) *
              (1 - smooth(18, CLIMB_HEIGHT, point.y) * 0.7)
          : 0,
      );
    }
    position.needsUpdate = size.needsUpdate = alpha.needsUpdate = true;

    const ignition = smooth(0.02, LIFTOFF_AT, u);
    const billow = smooth(0.02, 0.85, u);
    const cp = cloud.getAttribute("position") as THREE.BufferAttribute;
    const cs = cloud.getAttribute("size") as THREE.BufferAttribute;
    const ca = cloud.getAttribute("alpha") as THREE.BufferAttribute;
    for (let i = 0; i < CLOUD_PARTICLES; i++) {
      const cycle = (noise(i + 200) + churn * 0.05) % 1;
      const angle = noise(i) * Math.PI * 2;
      // Most smoke is pushed out of the flame trench to the downrange side.
      const side = noise(i + 600) < 0.6 ? 1 : 0;
      const radius = (0.8 + billow * 9 * (0.35 + cycle * 0.65)) * noise(i + 300);
      cp.setXYZ(
        i,
        side ? 1.6 + radius * 1.4 : Math.cos(angle) * radius,
        0.3 + billow * (0.4 + noise(i + 500) * 2.6) * (0.4 + cycle),
        side ? (noise(i + 700) - 0.5) * (1 + radius) : Math.sin(angle) * radius,
      );
      cs.setX(i, (1.2 + billow * 4.5) * (0.5 + noise(i + 800)));
      ca.setX(i, ignition * 0.5 * (1 - cycle * 0.6));
    }
    cp.needsUpdate = cs.needsUpdate = ca.needsUpdate = true;
  });
  return (
    <>
      <points geometry={cloud} material={cloudMaterial} frustumCulled={false} />
      <points geometry={trail} material={trailMaterial} frustumCulled={false} />
    </>
  );
}

export default function LaunchSite({
  progress,
  reduced,
  compact,
  user,
}: {
  progress: MutableRefObject<number>;
  reduced: boolean;
  compact: boolean;
  user: OrbitInput;
}) {
  const vehicle = useRef<THREE.Group>(null!);
  const flame = useRef<THREE.Group>(null!);
  const glow = useRef<THREE.PointLight>(null!);
  const sky = useRef<THREE.Color>(null!);
  const fog = useRef<THREE.Fog>(null!);
  const ground = useRef<THREE.MeshStandardMaterial>(null!);
  const position = useMemo(() => new THREE.Vector3(), []);
  const look = useMemo(() => new THREE.Vector3(), []);
  const groundCamera = useMemo(() => new THREE.Vector3(), []);
  const chaseCamera = useMemo(() => new THREE.Vector3(), []);
  const offset = useMemo(() => new THREE.Vector3(), []);
  const spherical = useMemo(() => new THREE.Spherical(), []);
  const plume = useMemo(() => {
    // Unit-length cone with its base at the nozzle and its tip at -Y.
    const geometry = new THREE.ConeGeometry(1, 1, 28, 1, true);
    geometry.rotateX(Math.PI);
    geometry.translate(0, -0.5, 0);
    return geometry;
  }, []);
  useEffect(() => () => plume.dispose(), [plume]);
  useFrame(({ camera, clock }) => {
    const u = closeupAt(progress.current);
    const { climb, height, pitch } = vehicleState(u, position);
    vehicle.current.position.copy(position);
    vehicle.current.rotation.set(0, 0, -pitch);

    // Engines ignite before release and throttle up; the plume lengthens and
    // widens as the air thins.
    const thrust = smooth(0.03, LIFTOFF_AT * 0.8, u);
    const flicker = reduced ? 1 : 0.9 + Math.sin(clock.elapsedTime * 31) * 0.06 + noise(Math.floor(clock.elapsedTime * 40)) * 0.08;
    const thin = smooth(4, CLIMB_HEIGHT, height);
    flame.current.visible = thrust > 0.01;
    flame.current.scale.set(
      (1 + thin * 1.6) * thrust,
      (1.1 + thin * 2.4) * thrust * flicker,
      (1 + thin * 1.6) * thrust,
    );
    glow.current.intensity = thrust * flicker * 60 * (1 - thin * 0.7);

    // Daylight blue fades to black as the vehicle leaves the atmosphere.
    const space = smooth(6, CLIMB_HEIGHT * 0.9, height);
    tmpColor.copy(DAY_SKY).lerp(SPACE_SKY, space);
    sky.current.copy(tmpColor);
    fog.current.color.copy(tmpColor);
    fog.current.far = 260 + space * 600;
    ground.current.emissiveIntensity = thrust * (1 - smooth(0, 6, height)) * 0.35;

    // Camera: a fixed pad-side view through lift-off, then a chase view that
    // climbs with the vehicle.
    groundCamera.set(9.5, 1.6 + height * 0.18, 12.5);
    chaseCamera.set(position.x + 5, position.y - 3.2, position.z + 8.5);
    const chase = smooth(0.18, 0.75, climb);
    const cameraBase = groundCamera.lerp(chaseCamera, chase);
    look.set(position.x, position.y + 2.4 - chase * 1.2, position.z);
    offset.copy(cameraBase).sub(look);
    spherical.setFromVector3(offset);
    spherical.theta += user.current.yaw;
    spherical.phi = THREE.MathUtils.clamp(
      spherical.phi - user.current.pitch,
      0.15,
      2.6,
    );
    spherical.radius *=
      (compact ? 1.25 : 1) * THREE.MathUtils.clamp(user.current.zoom, 0.35, 2.4);
    camera.position.setFromSpherical(spherical).add(look);
    // Ground shake from ignition through the first seconds of the climb.
    const shake = reduced ? 0 : thrust * (1 - smooth(0.05, 0.4, climb)) * 0.05;
    if (shake > 0) {
      const t = clock.elapsedTime * 37;
      camera.position.x += Math.sin(t) * shake;
      camera.position.y += Math.sin(t * 1.3 + 1) * shake;
    }
    camera.lookAt(look);
  });
  return (
    <>
      <color ref={sky} attach="background" args={[DAY_SKY]} />
      <fog ref={fog} attach="fog" args={[DAY_SKY, 70, 260]} />
      <hemisphereLight args={["#bcd6f0", "#4a5236", 0.75]} />
      <directionalLight
        position={[18, 26, 12]}
        intensity={2.6}
        color="#fff4e2"
        castShadow
        shadow-mapSize={[compact ? 1024 : 2048, compact ? 1024 : 2048]}
        shadow-camera-left={-14}
        shadow-camera-right={14}
        shadow-camera-top={14}
        shadow-camera-bottom={-14}
        shadow-normalBias={0.04}
      />
      {/* Coastal launch site: scrub land with the ocean behind the pad. */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.2, 0]} receiveShadow>
        <planeGeometry args={[700, 700]} />
        <meshStandardMaterial
          ref={ground}
          color="#56603f"
          roughness={1}
          emissive="#ff9a4a"
          emissiveIntensity={0}
        />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.18, -230]}>
        <planeGeometry args={[900, 400]} />
        <meshStandardMaterial color="#2b5573" roughness={0.25} metalness={0.2} />
      </mesh>
      <Pad />
      <group ref={vehicle}>
        <Rocket />
        <group ref={flame}>
          <mesh geometry={plume} scale={[0.2, 2.6, 0.2]}>
            <meshBasicMaterial
              color="#ff9b45"
              transparent
              opacity={0.55}
              depthWrite={false}
              blending={THREE.AdditiveBlending}
            />
          </mesh>
          <mesh geometry={plume} scale={[0.11, 1.5, 0.11]}>
            <meshBasicMaterial
              color="#fff3d1"
              transparent
              opacity={0.9}
              depthWrite={false}
              blending={THREE.AdditiveBlending}
            />
          </mesh>
          {[-1, 1].map((side) => (
            <mesh
              key={side}
              geometry={plume}
              position={[side * 0.33, 0, 0]}
              scale={[0.12, 1.8, 0.12]}
            >
              <meshBasicMaterial
                color="#ffc27a"
                transparent
                opacity={0.7}
                depthWrite={false}
                blending={THREE.AdditiveBlending}
              />
            </mesh>
          ))}
        </group>
        <pointLight
          ref={glow}
          position={[0, -0.6, 0]}
          color="#ffb36b"
          intensity={0}
          distance={30}
          decay={1.6}
        />
      </group>
      <Exhaust progress={progress} reduced={reduced} />
    </>
  );
}
