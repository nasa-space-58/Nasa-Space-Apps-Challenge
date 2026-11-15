import { useEffect, useMemo, useRef, type MutableRefObject } from "react";
import { useFrame, useLoader, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { noise, smooth } from "./LunarSurface";
import { LAUNCH_CLOSEUP_END_PROGRESS } from "../missionPath";

// Art-directed launch close-up for mission control: NASA's Mobile Launcher
// with a generic three-stage vehicle (see public/nasa and public/models
// manifests). Scene units are not metres, the vehicle is enlarged to suit the
// tower, and the ascent and staging timeline is illustrative, not computed.

export type OrbitInput = MutableRefObject<{
  yaw: number;
  pitch: number;
  zoom: number;
}>;

const LAUNCHER_PATH = "/nasa/mobile-launcher.glb";
const ROCKET_PATH = "/models/rocket-in-stages.glb";
/** Mobile Launcher file units → scene units (file is ~246 units tall). */
const LAUNCHER_SCALE = 5;
/** Launcher geometry (file units × 0.01): vehicle axis and deck height. */
const VEHICLE_AXIS_X = -0.07 * LAUNCHER_SCALE;
const DECK_TOP_Y = 0.3 * LAUNCHER_SCALE;
/** Rocket file units → scene units; ~47 file units tall → ~9.5 scene units. */
const ROCKET_SCALE = 0.2;

/** Close-up fraction at which the hold-down release lets the vehicle rise. */
const LIFTOFF_AT = 0.12;
/** Scene-unit height reached when the close-up hands over to orbit view. */
const CLIMB_HEIGHT = 520;
const PITCH_OVER_RAD = 1.05;
// Staging events as fractions of the climb (0 = lift-off, 1 = hand-over).
const STAGE1_SEP = 0.42;
const STAGE2_IGNITION = 0.47;
const FAIRING_SEP = 0.6;
const STAGE2_SEP = 0.8;
const STAGE3_IGNITION = 0.84;
// Nozzle exit heights in rocket file units (from the model's node layout).
const NOZZLE_Y = { stage1: 0.41, stage2: 24.86, stage3: 36.05 };

const TRAIL_PARTICLES = 520;
const CLOUD_PARTICLES = 320;
const DAY_SKY = new THREE.Color("#7fa9d3");
const SPACE_SKY = new THREE.Color("#03060a");
const tmpColor = new THREE.Color();

/** Close-up timeline in [0, 1] for a mission progress value. */
const closeupAt = (p: number) =>
  THREE.MathUtils.clamp(p / LAUNCH_CLOSEUP_END_PROGRESS, 0, 1);
/** Climb fraction in [0, 1] after lift-off. */
const climbAt = (u: number) =>
  THREE.MathUtils.clamp((u - LIFTOFF_AT) / (1 - LIFTOFF_AT), 0, 1);
const since = (c: number, event: number) => Math.max(0, c - event);

/** Vehicle base position (scene units) and pitch for a climb fraction. */
function vehicleState(c: number, target: THREE.Vector3) {
  const height = CLIMB_HEIGHT * c * c;
  const pitch = PITCH_OVER_RAD * smooth(0.06, 1, c);
  // Pitch away from the tower (towards −X) as the gravity turn develops.
  const downrange = height * Math.sin(pitch) * 0.6;
  target.set(VEHICLE_AXIS_X - downrange, DECK_TOP_Y + height, 0);
  return { height, pitch };
}

const dracoLoader = new DRACOLoader().setDecoderPath("/draco/");
const withDraco = (loader: GLTFLoader) => {
  loader.setDRACOLoader(dracoLoader);
};

function castShadows(object: THREE.Object3D) {
  object.traverse((child) => {
    if (child instanceof THREE.Mesh) {
      child.castShadow = true;
      child.receiveShadow = true;
    }
  });
}

/** Rocket model regrouped into separable stage 1, stage 2 and stage 3. */
function useStagedRocket() {
  const gltf = useLoader(GLTFLoader, ROCKET_PATH, withDraco);
  return useMemo(() => {
    const model = gltf.scene.clone(true);
    castShadows(model);
    const find = (name: string) => {
      const node = model.getObjectByName(name);
      if (!node) throw new Error(`Rocket model is missing node "${name}"`);
      return node;
    };
    const root = find("rocket-launch-site-rocket-in-stages");
    const stage1 = new THREE.Group();
    const stage1Parts = [
      "stage_1",
      "fins",
      ...[1, 2, 3, 4, 5].map((i) => `engine_${i}`),
      ...[1, 2, 3, 4].map((i) => `grid_fin_${i}`),
    ].map(find);
    root.add(stage1);
    stage1Parts.forEach((part) => stage1.attach(part));
    const stage2 = find("stage_2");
    const stage3 = find("stage_3");
    const fairingLeft = find("fairing_left");
    const fairingRight = find("fairing_right");
    const rest = (o: THREE.Object3D) => ({
      position: o.position.clone(),
      quaternion: o.quaternion.clone(),
    });
    return {
      model,
      stage1,
      stage2,
      stage3,
      fairingLeft,
      fairingRight,
      rest: {
        stage2: rest(stage2),
        fairingLeft: rest(fairingLeft),
        fairingRight: rest(fairingRight),
      },
    };
  }, [gltf]);
}

function Launcher() {
  const gltf = useLoader(GLTFLoader, LAUNCHER_PATH, withDraco);
  const model = useMemo(() => {
    const clone = gltf.scene.clone(true);
    castShadows(clone);
    return clone;
  }, [gltf]);
  return <primitive object={model} scale={LAUNCHER_SCALE} />;
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
          gl_PointSize = min(300.0, size * 320.0 / max(0.5, -p.z)); vAlpha = alpha; }`,
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
 * First-stage exhaust trail plus the billowing cloud around the launcher.
 * Both are functions of the close-up timeline, so scrubbing stays exact.
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
    // Only the first stage burns low enough to leave a visible trail.
    const emittedUntil = Math.min(now, STAGE1_SEP);
    const churn = reduced ? 0 : clock.elapsedTime;
    const position = trail.getAttribute("position") as THREE.BufferAttribute;
    const size = trail.getAttribute("size") as THREE.BufferAttribute;
    const alpha = trail.getAttribute("alpha") as THREE.BufferAttribute;
    for (let i = 0; i < TRAIL_PARTICLES; i++) {
      const emitted = (i / TRAIL_PARTICLES) * emittedUntil;
      const age = now - emitted;
      vehicleState(emitted, point);
      const spread = 0.6 + age * 22;
      const angle = noise(i + 40) * Math.PI * 2 + churn * 0.2;
      position.setXYZ(
        i,
        point.x + Math.cos(angle) * spread * noise(i + 70),
        Math.max(0.5, point.y - 1 + age * 4),
        point.z + Math.sin(angle) * spread * noise(i + 90),
      );
      size.setX(i, 2.2 + age * 60 + noise(i + 11) * 1.5);
      alpha.setX(
        i,
        now > 0
          ? Math.max(0, 0.55 - age * 0.9) *
              (1 - smooth(40, CLIMB_HEIGHT * STAGE1_SEP * STAGE1_SEP, point.y) * 0.6)
          : 0,
      );
    }
    position.needsUpdate = size.needsUpdate = alpha.needsUpdate = true;

    const ignition = smooth(0.01, LIFTOFF_AT, u);
    const billow = smooth(0.01, 0.7, u);
    const fade = 1 - smooth(0.6, 1, u) * 0.6;
    const cp = cloud.getAttribute("position") as THREE.BufferAttribute;
    const cs = cloud.getAttribute("size") as THREE.BufferAttribute;
    const ca = cloud.getAttribute("alpha") as THREE.BufferAttribute;
    for (let i = 0; i < CLOUD_PARTICLES; i++) {
      const cycle = (noise(i + 200) + churn * 0.04) % 1;
      const angle = noise(i) * Math.PI * 2;
      // Exhaust leaves the flame trench along ±X; the rest rolls out around
      // the launcher base.
      const trench = noise(i + 600) < 0.65;
      const reach = (2 + billow * 34 * (0.35 + cycle * 0.65)) * noise(i + 300);
      const direction = noise(i + 650) < 0.5 ? -1 : 1;
      cp.setXYZ(
        i,
        VEHICLE_AXIS_X +
          (trench ? direction * (3 + reach * 1.3) : Math.cos(angle) * reach),
        0.6 + billow * (1 + noise(i + 500) * 9) * (0.4 + cycle),
        trench ? (noise(i + 700) - 0.5) * (2 + reach * 0.5) : Math.sin(angle) * reach,
      );
      cs.setX(i, (4 + billow * 16) * (0.5 + noise(i + 800)));
      ca.setX(i, ignition * fade * 0.5 * (1 - cycle * 0.6));
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

/** Additive flame: outer glow plus a bright core, in rocket file units. */
function Flame({
  flameRef,
  radius,
}: {
  flameRef: MutableRefObject<THREE.Group>;
  radius: number;
}) {
  const geometry = useMemo(() => {
    // Unit-length cone with its base at the nozzle and its tip at −Y.
    const cone = new THREE.ConeGeometry(1, 1, 28, 1, true);
    cone.rotateX(Math.PI);
    cone.translate(0, -0.5, 0);
    return cone;
  }, []);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <group ref={flameRef} visible={false}>
      <mesh geometry={geometry} scale={[radius, radius * 9, radius]}>
        <meshBasicMaterial
          color="#ff9b45"
          transparent
          opacity={0.5}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
      <mesh geometry={geometry} scale={[radius * 0.55, radius * 5, radius * 0.55]}>
        <meshBasicMaterial
          color="#fff3d1"
          transparent
          opacity={0.9}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
    </group>
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
  const rocket = useStagedRocket();
  const sceneCamera = useThree(state => state.camera);
  useEffect(() => {
    if (!(sceneCamera instanceof THREE.PerspectiveCamera)) return;
    const fov = sceneCamera.fov;
    return () => { sceneCamera.fov = fov; sceneCamera.updateProjectionMatrix(); };
  }, [sceneCamera]);
  const vehicle = useRef<THREE.Group>(null!);
  const flame1 = useRef<THREE.Group>(null!);
  const flame2 = useRef<THREE.Group>(null!);
  const flame3 = useRef<THREE.Group>(null!);
  const glow = useRef<THREE.PointLight>(null!);
  const sky = useRef<THREE.Color>(null!);
  const fog = useRef<THREE.Fog>(null!);
  const ground = useRef<THREE.MeshStandardMaterial>(null!);
  const starMaterial = useRef<THREE.PointsMaterial>(null!);
  const scratch = useMemo(
    () => ({
      position: new THREE.Vector3(),
      look: new THREE.Vector3(),
      ground: new THREE.Vector3(),
      chase: new THREE.Vector3(),
      offset: new THREE.Vector3(),
      spherical: new THREE.Spherical(),
      euler: new THREE.Euler(),
      quaternion: new THREE.Quaternion(),
    }),
    [],
  );
  const stars = useMemo(() => {
    const values = new Float32Array(1800 * 3);
    for (let i = 0; i < 1800; i++) {
      const theta = noise(i + 3) * Math.PI * 2;
      const z = noise(i + 9000) * 1.6 - 0.6;
      const s = Math.sqrt(1 - z * z);
      values.set(
        [Math.cos(theta) * s * 3000, z * 3000, Math.sin(theta) * s * 3000],
        i * 3,
      );
    }
    return values;
  }, []);

  useFrame(({ camera, clock }, delta) => {
    const s = scratch;
    const u = closeupAt(progress.current);
    const c = climbAt(u);
    // A restrained lens pull opens the frame for staging, then settles for cruise.
    if (camera instanceof THREE.PerspectiveCamera) {
      const targetFov = 45 + 9 * smooth(0.08, 0.42, c) - 6 * smooth(0.8, 1, c);
      camera.fov = reduced ? targetFov : THREE.MathUtils.damp(camera.fov, targetFov, 3, Math.min(delta, 0.05));
      camera.updateProjectionMatrix();
    }
    const { height, pitch } = vehicleState(c, s.position);
    vehicle.current.position.copy(s.position);
    vehicle.current.rotation.set(0, 0, pitch);

    // Staging: each spent part drifts back and tumbles relative to the
    // accelerating vehicle (rocket file units, vehicle frame).
    const d1 = since(c, STAGE1_SEP);
    rocket.stage1.position.set(d1 * 30, -(d1 * 90 + d1 * d1 * 700), 0);
    rocket.stage1.rotation.set(d1 * 1.4, 0, -d1 * 2.2);
    const d2 = since(c, STAGE2_SEP);
    rocket.stage2.position
      .copy(rocket.rest.stage2.position)
      .add(s.offset.set(-d2 * 25, -(d2 * 80 + d2 * d2 * 900), 0));
    rocket.stage2.quaternion
      .copy(rocket.rest.stage2.quaternion)
      .multiply(s.quaternion.setFromEuler(s.euler.set(-d2 * 1.6, 0, d2 * 2.5)));
    const df = since(c, FAIRING_SEP);
    const hinge = Math.min(1.5, df * 9) + df * 2;
    for (const [half, side] of [
      [rocket.fairingRight, 1],
      [rocket.fairingLeft, -1],
    ] as const) {
      const rest = side > 0 ? rocket.rest.fairingRight : rocket.rest.fairingLeft;
      half.position
        .copy(rest.position)
        .add(s.offset.set(side * df * 120, -df * df * 600, df * 10 * side));
      half.quaternion
        .copy(rest.quaternion)
        .multiply(s.quaternion.setFromEuler(s.euler.set(0, 0, -side * hinge)));
    }
    rocket.stage1.visible = d1 < 0.3;
    rocket.stage2.visible = d2 < 0.2;
    rocket.fairingLeft.visible = rocket.fairingRight.visible = df < 0.3;

    // Engines: stage 1 ignites before release, stage 2 after separation, and
    // stage 3 after the second separation; vacuum plumes widen with altitude.
    const thrust1 = smooth(0.02, LIFTOFF_AT * 0.8, u) * (c < STAGE1_SEP ? 1 : 0);
    const thrust2 =
      smooth(STAGE2_IGNITION, STAGE2_IGNITION + 0.02, c) * (c < STAGE2_SEP ? 1 : 0);
    const thrust3 = smooth(STAGE3_IGNITION, STAGE3_IGNITION + 0.02, c);
    const flicker = reduced
      ? 1
      : 0.9 +
        Math.sin(clock.elapsedTime * 31) * 0.06 +
        noise(Math.floor(clock.elapsedTime * 40)) * 0.08;
    const thin = smooth(10, CLIMB_HEIGHT * 0.5, height);
    const setFlame = (
      flame: THREE.Group,
      thrust: number,
      y: number,
      attached: THREE.Object3D | null,
    ) => {
      flame.visible = thrust > 0.01;
      if (attached) flame.position.copy(attached.position);
      else flame.position.set(0, 0, 0);
      flame.position.y += y;
      flame.scale.set(
        (1 + thin * 1.8) * thrust,
        (1 + thin * 1.2) * thrust * flicker,
        (1 + thin * 1.8) * thrust,
      );
    };
    setFlame(flame1.current, thrust1, NOZZLE_Y.stage1, rocket.stage1);
    setFlame(flame2.current, thrust2, NOZZLE_Y.stage2, null);
    setFlame(flame3.current, thrust3, NOZZLE_Y.stage3, null);
    glow.current.intensity =
      Math.max(thrust1, thrust2, thrust3) * flicker * 300 * (1 - thin * 0.8);
    glow.current.position.set(0, (thrust1 > 0 ? NOZZLE_Y.stage1 : NOZZLE_Y.stage3) - 4, 0);

    // Daylight blue fades to black as the vehicle leaves the atmosphere.
    const space = smooth(20, CLIMB_HEIGHT * 0.6, height);
    tmpColor.copy(DAY_SKY).lerp(SPACE_SKY, space);
    sky.current.copy(tmpColor);
    fog.current.color.copy(tmpColor);
    fog.current.far = 900 + space * 2500;
    starMaterial.current.opacity = smooth(0.5, 1, space) * 0.8;
    ground.current.emissiveIntensity =
      thrust1 * (1 - smooth(0, 15, height)) * 0.35;

    // Camera: a fixed pad-side view through lift-off, then a chase view
    // beside the vehicle that keeps the separating stages in frame.
    vehicle.current.updateMatrixWorld();
    const lookLocalY =
      (22 + 11 * smooth(STAGE1_SEP, STAGE1_SEP + 0.08, c) +
      8 * smooth(STAGE2_SEP, STAGE2_SEP + 0.06, c)) * ROCKET_SCALE;
    s.look.set(0, lookLocalY, 0);
    vehicle.current.localToWorld(s.look);
    s.chase.set(22 * ROCKET_SCALE, lookLocalY - 18 * ROCKET_SCALE, 75 * ROCKET_SCALE);
    vehicle.current.localToWorld(s.chase);
    s.ground.set(VEHICLE_AXIS_X + 11, 4 + height * 0.08, 20);
    const chase = smooth(0.04, 0.3, c);
    s.offset.copy(s.ground).lerp(s.chase, chase).sub(s.look);
    s.spherical.setFromVector3(s.offset);
    s.spherical.theta += user.current.yaw;
    s.spherical.phi = THREE.MathUtils.clamp(
      s.spherical.phi - user.current.pitch,
      0.15,
      2.7,
    );
    s.spherical.radius *=
      (compact ? 1.25 : 1) * THREE.MathUtils.clamp(user.current.zoom, 0.35, 2.4);
    camera.position.setFromSpherical(s.spherical).add(s.look);
    // Ground shake from ignition through the first seconds of the climb.
    const shake = reduced ? 0 : thrust1 * (1 - smooth(0.02, 0.2, c)) * 0.12;
    if (shake > 0) {
      const t = clock.elapsedTime * 37;
      camera.position.x += Math.sin(t) * shake;
      camera.position.y += Math.sin(t * 1.3 + 1) * shake;
    }
    camera.lookAt(s.look);
  });

  return (
    <>
      <color ref={sky} attach="background" args={[DAY_SKY]} />
      <fog ref={fog} attach="fog" args={[DAY_SKY, 120, 900]} />
      <hemisphereLight args={["#bcd6f0", "#4a5236", 0.8]} />
      <directionalLight
        position={[40, 60, 30]}
        intensity={2.6}
        color="#fff4e2"
        castShadow
        shadow-mapSize={[compact ? 1024 : 2048, compact ? 1024 : 2048]}
        shadow-camera-left={-30}
        shadow-camera-right={30}
        shadow-camera-top={30}
        shadow-camera-bottom={-30}
        shadow-camera-far={200}
        shadow-normalBias={0.04}
      />
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[stars, 3]} />
        </bufferGeometry>
        <pointsMaterial
          ref={starMaterial}
          color="#c4d0dc"
          size={1.6}
          sizeAttenuation={false}
          transparent
          opacity={0}
          fog={false}
        />
      </points>
      {/* Coastal launch complex: scrub land with the ocean behind the pad. */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.02, 0]} receiveShadow>
        <planeGeometry args={[4000, 4000]} />
        <meshStandardMaterial
          ref={ground}
          color="#56603f"
          roughness={1}
          emissive="#ff9a4a"
          emissiveIntensity={0}
        />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, -1300]}>
        <planeGeometry args={[5000, 2200]} />
        <meshStandardMaterial color="#2b5573" roughness={0.25} metalness={0.2} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[VEHICLE_AXIS_X, 0, 0]} receiveShadow>
        <circleGeometry args={[18, 48]} />
        <meshStandardMaterial color="#8b8a84" roughness={0.95} />
      </mesh>
      <Launcher />
      <group ref={vehicle}>
        <group scale={ROCKET_SCALE}>
          <primitive object={rocket.model} />
          <Flame flameRef={flame1} radius={1.7} />
          <Flame flameRef={flame2} radius={1.15} />
          <Flame flameRef={flame3} radius={0.75} />
          <pointLight
            ref={glow}
            color="#ffb36b"
            intensity={0}
            distance={80}
            decay={1.6}
          />
        </group>
      </group>
      <Exhaust progress={progress} reduced={reduced} />
    </>
  );
}
