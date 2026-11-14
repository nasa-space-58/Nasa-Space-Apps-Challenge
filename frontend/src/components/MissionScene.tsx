import {
  Suspense,
  useEffect,
  useMemo,
  useRef,
  type MutableRefObject,
} from "react";
import {
  Canvas,
  useFrame,
  useLoader,
  useThree,
  type ThreeEvent,
} from "@react-three/fiber";
import * as THREE from "three";
import { Dust, Model, Terrain, smooth } from "./LunarSurface";
import LaunchSite from "./LaunchSite";
import Rover, { type RoutePoint } from "./Rover";
import SolarSystem from "./SolarSystem";
import type { Preset } from "../types";
import { useTranslate } from "../i18n";
import {
  ASTRONOMICAL_UNIT_KM,
  COVERAGE_MAX_LATITUDE_DEG,
  LANDING_CLOSEUP_START_PROGRESS,
  MISSION_PHASES,
  SCENE_LAYOUT,
  SUN_NOMINAL_RADIUS_KM,
  allPathPoints,
  headingAt,
  pathPointsUntil,
  phaseAt,
  positionAt,
  siteSelectionFromPoint,
  surfaceUnitVector,
  type TransferPath,
  type Vec3,
} from "../missionPath";

// Display-only Sun: it cannot sit 1 AU away in a renderable scene, so it is
// placed closer but sized to keep its true apparent diameter (~0.53°).
const SUN_DISTANCE_SCENE = 2400;
const SUN_SCENE: Vec3 = (() => {
  const direction = new THREE.Vector3(-20, 0, 8).normalize();
  return [
    direction.x * SUN_DISTANCE_SCENE,
    direction.y * SUN_DISTANCE_SCENE,
    direction.z * SUN_DISTANCE_SCENE,
  ];
})();
const SUN_RADIUS_SCENE =
  (SUN_DISTANCE_SCENE * SUN_NOMINAL_RADIUS_KM) / ASTRONOMICAL_UNIT_KM;
const STAR_SPHERE_RADIUS_SCENE = 3200;
// Spacecraft models are enlarged far beyond their true size (metres against a
// 1737 km lunar radius) so they remain visible in close-up views.
const LANDER_SIZE_SCENE = 0.03;
const ORBITER_SIZE_SCENE = 0.04;
/** Progress at which the lander touches down in the landing close-up. */
const TOUCHDOWN_PROGRESS = MISSION_PHASES.find(
  (phase) => phase.id === "landed",
)!.start_progress;
/** Radius of the ambient LRO orbit (≈ 200 km altitude). */
const SCIENCE_ORBIT_RADIUS_SCENE = 1.12;
/**
 * Earth–Moon view framing: the Moon-to-Earth distance plus Earth's diameter
 * and a small margin must span the viewport width.
 */
const SYSTEM_VIEW_SPAN_SCENE =
  (SCENE_LAYOUT.earth_center_scene[0] + SCENE_LAYOUT.earth_radius_scene * 2) *
  1.1;
const SYSTEM_VIEW_YAW = 0.12;
const SYSTEM_VIEW_PITCH = 0.42;
/** Earth's atmosphere shell (display thickness ≈ 100 km). */
const EARTH_ATMOSPHERE_RADIUS_SCENE = SCENE_LAYOUT.earth_radius_scene * 1.016;
/** Display spin rates (illustrative, not physical rotation periods). */
const EARTH_SPIN_RAD_PER_S = 0.25;
const MOON_SPIN_RAD_PER_S = 0.08;

export type CameraMode =
  | "auto"
  | "system"
  | "moon"
  | "pole"
  | "spacecraft"
  | "launch"
  | "landing"
  | "solar";
export type CameraView = Exclude<CameraMode, "auto">;
export type SiteCondition = "sunlit" | "dark" | "unknown";

const v3 = (v: Vec3) => new THREE.Vector3(v[0], v[1], v[2]);
const onSurface = (lat: number, lon: number, lift = 1.004) =>
  v3(surfaceUnitVector(lat, lon)).multiplyScalar(
    SCENE_LAYOUT.moon_radius_scene * lift,
  );

function glowTexture(inner: string, outer: string) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const context = canvas.getContext("2d")!;
  const gradient = context.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, inner);
  gradient.addColorStop(0.25, inner);
  gradient.addColorStop(1, outer);
  context.fillStyle = gradient;
  context.fillRect(0, 0, 128, 128);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** Radial falloff without a hard core, for bloom around distant bodies. */
function softGlowTexture(rgb: string) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const context = canvas.getContext("2d")!;
  const gradient = context.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, `rgba(${rgb}, 0.32)`);
  gradient.addColorStop(0.3, `rgba(${rgb}, 0.2)`);
  gradient.addColorStop(0.6, `rgba(${rgb}, 0.06)`);
  gradient.addColorStop(1, `rgba(${rgb}, 0)`);
  context.fillStyle = gradient;
  context.fillRect(0, 0, 128, 128);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/**
 * Thin fresnel rim for Earth's atmosphere, brightest on the day-side limb.
 * Supports the logarithmic depth buffer used by the mission canvas.
 */
function Atmosphere({
  radius,
  sunDirection,
}: {
  radius: number;
  sunDirection: THREE.Vector3;
}) {
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        uniforms: {
          sunDirection: { value: sunDirection.clone().normalize() },
          dayColor: { value: new THREE.Color("#7fb8ff") },
          twilightColor: { value: new THREE.Color("#ffb37a") },
        },
        vertexShader: /* glsl */ `
          #include <common>
          #include <logdepthbuf_pars_vertex>
          varying vec3 vNormal;
          varying vec3 vView;
          varying vec3 vWorldNormal;
          void main() {
            vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
            vNormal = normalize(normalMatrix * normal);
            vView = normalize(-mvPosition.xyz);
            vWorldNormal = normalize(mat3(modelMatrix) * normal);
            gl_Position = projectionMatrix * mvPosition;
            #include <logdepthbuf_vertex>
          }
        `,
        fragmentShader: /* glsl */ `
          #include <common>
          #include <logdepthbuf_pars_fragment>
          uniform vec3 sunDirection;
          uniform vec3 dayColor;
          uniform vec3 twilightColor;
          varying vec3 vNormal;
          varying vec3 vView;
          varying vec3 vWorldNormal;
          void main() {
            #include <logdepthbuf_fragment>
            float rim = pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), 2.6);
            float sun = dot(normalize(vWorldNormal), sunDirection);
            float day = smoothstep(-0.25, 0.35, sun);
            float twilight = (1.0 - abs(sun) * 2.2);
            vec3 color = mix(dayColor, twilightColor, clamp(twilight, 0.0, 1.0) * 0.45);
            gl_FragColor = vec4(color, rim * day * 0.95);
          }
        `,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    [sunDirection],
  );
  useEffect(() => () => material.dispose(), [material]);
  return (
    <mesh material={material}>
      <sphereGeometry args={[radius, 96, 64]} />
    </mesh>
  );
}

/**
 * Soft light bloom that keeps a distant body readable, like an overexposed
 * point in a wide-field photo. It never changes the body's true size and
 * fades out once the body itself covers enough of the view.
 */
function DistantGlow({
  radius,
  texture,
  strength,
}: {
  radius: number;
  texture: THREE.Texture;
  strength: number;
}) {
  const sprite = useRef<THREE.Sprite>(null!);
  const world = useMemo(() => new THREE.Vector3(), []);
  useFrame(({ camera }) => {
    sprite.current.getWorldPosition(world);
    const distance = camera.position.distanceTo(world);
    const apparent = radius / distance;
    const material = sprite.current.material as THREE.SpriteMaterial;
    material.opacity =
      strength * (1 - THREE.MathUtils.smoothstep(apparent, 0.012, 0.05));
    sprite.current.visible = material.opacity > 0.01;
    sprite.current.scale.setScalar(Math.max(radius * 3, distance * 0.12));
  });
  return (
    <sprite ref={sprite}>
      <spriteMaterial
        map={texture}
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </sprite>
  );
}

function Line({
  points,
  color,
  opacity,
  dashed = false,
  dashSize = 0.18,
}: {
  points: THREE.Vector3[];
  color: string;
  opacity: number;
  dashed?: boolean;
  dashSize?: number;
}) {
  const line = useMemo(() => {
    const geometry = new THREE.BufferGeometry().setFromPoints(points);
    const material = dashed
      ? new THREE.LineDashedMaterial({
          color,
          transparent: true,
          opacity,
          dashSize,
          gapSize: dashSize * 0.78,
        })
      : new THREE.LineBasicMaterial({ color, transparent: true, opacity });
    const object = new THREE.Line(geometry, material);
    if (dashed) object.computeLineDistances();
    return object;
  }, [points, color, opacity, dashed, dashSize]);
  useEffect(
    () => () => {
      line.geometry.dispose();
      (line.material as THREE.Material).dispose();
    },
    [line],
  );
  return <primitive object={line} />;
}

function Trail({
  path,
  progress,
}: {
  path: TransferPath;
  progress: MutableRefObject<number>;
}) {
  const capacity = allPathPoints(path).length + 2;
  const line = useMemo(() => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.BufferAttribute(new Float32Array(capacity * 3), 3),
    );
    return new THREE.Line(
      geometry,
      new THREE.LineBasicMaterial({
        color: "#edbb66",
        transparent: true,
        opacity: 0.95,
      }),
    );
  }, [capacity]);
  useEffect(
    () => () => {
      line.geometry.dispose();
      (line.material as THREE.Material).dispose();
    },
    [line],
  );
  useFrame(() => {
    const points = pathPointsUntil(path, progress.current);
    const attribute = line.geometry.getAttribute(
      "position",
    ) as THREE.BufferAttribute;
    const count = Math.min(points.length, capacity);
    for (let i = 0; i < count; i++) attribute.setXYZ(i, ...points[i]);
    attribute.needsUpdate = true;
    line.geometry.setDrawRange(0, count);
    line.geometry.computeBoundingSphere();
  });
  return <primitive object={line} />;
}

function Spacecraft({
  path,
  progress,
  spacecraft,
  reduced,
}: {
  path: TransferPath;
  progress: MutableRefObject<number>;
  spacecraft: MutableRefObject<THREE.Vector3>;
  reduced: boolean;
}) {
  const group = useRef<THREE.Group>(null!);
  const plume = useRef<THREE.Mesh>(null!);
  const halo = useRef<THREE.Sprite>(null!);
  const glow = useMemo(() => glowTexture("#fff3d6", "#edbb6600"), []);
  const look = useMemo(() => new THREE.Vector3(), []);
  useEffect(() => () => glow.dispose(), [glow]);
  useFrame(({ camera, clock }) => {
    const p = progress.current;
    const position = positionAt(path, p);
    group.current.position.set(...position);
    spacecraft.current.set(...position);
    const phase = phaseAt(p).phase.id;
    const nearMoon = phase === "descent" || phase === "landed";
    plume.current.visible = phase === "descent";
    plume.current.scale.set(
      1,
      0.8 + (reduced ? 0 : Math.sin(clock.elapsedTime * 24) * 0.08),
      1,
    );
    // Keep the beacon a similar size on screen at every camera distance; hide
    // it in close-ups where the model itself is visible.
    const cameraDistance = camera.position.distanceTo(group.current.position);
    halo.current.visible = phase !== "landed" && cameraDistance > 1.2;
    halo.current.scale.setScalar(cameraDistance * 0.012);
    if (nearMoon) {
      // Keep the lander upright relative to the local surface normal.
      group.current.quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 1, 0),
        v3(position).normalize(),
      );
    } else {
      const heading = headingAt(path, p);
      look.set(
        position[0] + heading[0],
        position[1] + heading[1],
        position[2] + heading[2],
      );
      group.current.lookAt(look);
    }
  });
  return (
    <group ref={group}>
      <group position={[0, LANDER_SIZE_SCENE / 2, 0]}>
        <Model path="/nasa/apollo.glb" size={LANDER_SIZE_SCENE} />
      </group>
      <mesh
        ref={plume}
        rotation={[Math.PI, 0, 0]}
        position={[0, -LANDER_SIZE_SCENE * 0.25, 0]}
      >
        <coneGeometry
          args={[LANDER_SIZE_SCENE * 0.06, LANDER_SIZE_SCENE * 0.3, 24]}
        />
        <meshBasicMaterial
          color="#ead5aa"
          transparent
          opacity={0.36}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
      <sprite ref={halo}>
        <spriteMaterial
          map={glow}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          opacity={0.75}
        />
      </sprite>
    </group>
  );
}

/** LRO circling the Moon on a near-polar orbit, as on the introduction page. */
function ScienceOrbiter({ reduced }: { reduced: boolean }) {
  const orbiter = useRef<THREE.Group>(null!);
  const elapsed = useRef(0);
  const ring = useMemo(
    () =>
      Array.from({ length: 161 }, (_, i) => {
        const a = (i / 160) * Math.PI * 2;
        return new THREE.Vector3(
          Math.cos(a) * SCIENCE_ORBIT_RADIUS_SCENE,
          Math.sin(a) * SCIENCE_ORBIT_RADIUS_SCENE,
          0,
        );
      }),
    [],
  );
  useFrame((_, delta) => {
    if (!reduced) elapsed.current += Math.min(delta, 0.05);
    const a = 2.1 + elapsed.current * 0.22;
    orbiter.current.position.set(
      Math.cos(a) * SCIENCE_ORBIT_RADIUS_SCENE,
      Math.sin(a) * SCIENCE_ORBIT_RADIUS_SCENE,
      0,
    );
    // Nadir-pointing: face the direction of motion with the instrument deck down.
    orbiter.current.rotation.set(0, 0, a + Math.PI / 2);
  });
  return (
    <group rotation={[0, 0.6, 0.12]}>
      <Line points={ring} color="#9c947c" opacity={0.32} />
      <group ref={orbiter}>
        <Model path="/nasa/lro.glb" size={ORBITER_SIZE_SCENE} />
      </group>
    </group>
  );
}

function SiteMarker({
  lat,
  lon,
  condition,
  reduced,
}: {
  lat: number;
  lon: number;
  condition: SiteCondition;
  reduced: boolean;
}) {
  const ring = useRef<THREE.Mesh>(null!);
  const position = useMemo(() => onSurface(lat, lon, 1.006), [lat, lon]);
  const quaternion = useMemo(
    () =>
      new THREE.Quaternion().setFromUnitVectors(
        new THREE.Vector3(0, 0, 1),
        position.clone().normalize(),
      ),
    [position],
  );
  const color =
    condition === "sunlit"
      ? "#edbb66"
      : condition === "dark"
        ? "#7f93a3"
        : "#edbb66";
  useFrame(({ clock }) => {
    const pulse = reduced ? 1 : 1 + 0.35 * Math.sin(clock.elapsedTime * 3);
    ring.current.scale.setScalar(pulse);
  });
  return (
    <group position={position} quaternion={quaternion}>
      <mesh>
        <circleGeometry args={[0.022, 24]} />
        <meshBasicMaterial color={color} />
      </mesh>
      <mesh ref={ring}>
        <ringGeometry args={[0.05, 0.062, 40]} />
        <meshBasicMaterial color={color} transparent opacity={0.85} />
      </mesh>
      <Line
        points={[new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, 0.32)]}
        color={color}
        opacity={0.9}
      />
    </group>
  );
}

/** Drag to orbit and wheel to zoom; offsets reset whenever `resetKey` changes. */
function useOrbitInput(resetKey: unknown) {
  const { gl, invalidate } = useThree();
  const user = useRef({ yaw: 0, pitch: 0, zoom: 1 });
  useEffect(() => {
    user.current = { yaw: 0, pitch: 0, zoom: 1 };
    invalidate();
  }, [resetKey, invalidate]);
  useEffect(() => {
    const canvas = gl.domElement;
    let dragging: { x: number; y: number } | null = null;
    const down = (e: PointerEvent) => {
      dragging = { x: e.clientX, y: e.clientY };
    };
    const move = (e: PointerEvent) => {
      if (!dragging) return;
      user.current.yaw -= (e.clientX - dragging.x) * 0.006;
      user.current.pitch = THREE.MathUtils.clamp(
        user.current.pitch + (e.clientY - dragging.y) * 0.005,
        -1.2,
        1.2,
      );
      dragging = { x: e.clientX, y: e.clientY };
      invalidate();
    };
    const up = () => {
      dragging = null;
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      user.current.zoom = THREE.MathUtils.clamp(
        user.current.zoom * Math.exp(e.deltaY * 0.001),
        0.05,
        2.4,
      );
      invalidate();
    };
    canvas.addEventListener("pointerdown", down);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    canvas.addEventListener("wheel", wheel, { passive: false });
    return () => {
      canvas.removeEventListener("pointerdown", down);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      canvas.removeEventListener("wheel", wheel);
    };
  }, [gl, invalidate]);
  return user;
}

function CameraRig({
  mode,
  lat,
  lon,
  spacecraft,
  progress,
  reduced,
}: {
  mode: Exclude<CameraView, "landing" | "launch">;
  lat: number;
  lon: number;
  spacecraft: MutableRefObject<THREE.Vector3>;
  progress: MutableRefObject<number>;
  reduced: boolean;
}) {
  const { camera, size } = useThree();
  const user = useOrbitInput(mode);
  const target = useMemo(() => new THREE.Vector3(7, 0, 0), []);
  const desiredTarget = useMemo(() => new THREE.Vector3(), []);
  const desiredPosition = useMemo(() => new THREE.Vector3(), []);
  const first = useRef(true);
  useFrame((_, delta) => {
    const phase = phaseAt(progress.current).phase.id;
    // Camera direction from yaw (around +Y) and pitch (positive = above).
    let yaw = 0.2,
      pitch = 0.78,
      distance = 270;
    const siteYaw = () => {
      const site = surfaceUnitVector(
        Number.isFinite(lat) ? lat : -89,
        Number.isFinite(lon) ? lon : 0,
      );
      return Math.atan2(site[0], site[2]) || 0.35;
    };
    if (mode === "system") {
      desiredTarget.set(SCENE_LAYOUT.earth_center_scene[0] / 2, 0, 0);
      // Fit the true-scale Earth–Moon line to the viewport width instead of a
      // fixed distance, so both bodies appear as large as the frame allows
      // without changing their sizes or separation.
      yaw = SYSTEM_VIEW_YAW;
      pitch = SYSTEM_VIEW_PITCH;
      const perspective = camera as THREE.PerspectiveCamera;
      const halfHeight = Math.tan(
        THREE.MathUtils.degToRad(perspective.fov ?? 50) / 2,
      );
      const halfWidth = halfHeight * Math.max(0.3, size.width / size.height);
      distance =
        (SYSTEM_VIEW_SPAN_SCENE / 2 / halfWidth) * Math.cos(SYSTEM_VIEW_YAW);
    } else if (mode === "pole") {
      desiredTarget.set(0, -SCENE_LAYOUT.moon_radius_scene, 0);
      yaw = siteYaw();
      pitch = -1.38;
      distance = 1.25;
    } else if (mode === "moon") {
      desiredTarget.set(0, -0.35, 0);
      yaw = siteYaw();
      pitch = -1.0;
      distance = 4.6;
    } else {
      // Look over the spacecraft from outside the body it is closest to.
      desiredTarget.copy(spacecraft.current);
      const earth = v3(SCENE_LAYOUT.earth_center_scene);
      const center =
        spacecraft.current.distanceTo(earth) /
          SCENE_LAYOUT.earth_radius_scene <
        spacecraft.current.length() / SCENE_LAYOUT.moon_radius_scene
          ? earth
          : new THREE.Vector3();
      const view = spacecraft.current
        .clone()
        .sub(center)
        .normalize()
        .add(new THREE.Vector3(0.4, 0.3, 0.5))
        .normalize();
      yaw = Math.atan2(view.x, view.z);
      pitch = Math.asin(view.y);
      distance = phase === "landed" ? 0.22 : 0.3;
    }
    yaw += user.current.yaw;
    pitch = THREE.MathUtils.clamp(pitch + user.current.pitch, -1.45, 1.45);
    distance *= user.current.zoom;
    desiredPosition
      .set(
        Math.sin(yaw) * Math.cos(pitch),
        Math.sin(pitch),
        Math.cos(yaw) * Math.cos(pitch),
      )
      .multiplyScalar(distance)
      .add(desiredTarget);
    const k = reduced || first.current ? 1 : 1 - Math.exp(-delta * 3.2);
    first.current = false;
    target.lerp(desiredTarget, k);
    camera.position.lerp(desiredPosition, k);
    camera.lookAt(target);
  });
  return null;
}

function ProgressEaser({
  target,
  progress,
  snap,
}: {
  target: number;
  progress: MutableRefObject<number>;
  snap: boolean;
}) {
  useFrame((_, delta) => {
    const gap = target - progress.current;
    progress.current =
      snap || Math.abs(gap) > 0.08 || Math.abs(gap) < 1e-5
        ? target
        : progress.current + gap * (1 - Math.exp(-delta * 14));
  });
  return null;
}

function System({
  path,
  lat,
  lon,
  sites,
  progress,
  cameraMode,
  condition,
  reduced,
  selectable,
  onPick,
  onPreset,
}: SceneProps & {
  path: TransferPath | null;
  progress: MutableRefObject<number>;
}) {
  const [moonMap, moonRelief, earthMap] = useLoader(THREE.TextureLoader, [
    "/nasa/moon.jpg",
    "/nasa/moon-relief.jpg",
    "/nasa/earth.jpg",
  ]);
  moonMap.colorSpace = THREE.SRGBColorSpace;
  earthMap.colorSpace = THREE.SRGBColorSpace;
  const earth = useRef<THREE.Mesh>(null!);
  const spacecraft = useRef(new THREE.Vector3());
  const sunGlow = useMemo(() => glowTexture("#fff4cf", "#ffb54700"), []);
  const earthGlow = useMemo(() => softGlowTexture("159, 203, 255"), []);
  const moonGlow = useMemo(() => softGlowTexture("230, 226, 216"), []);
  const sunDirection = useMemo(() => v3(SUN_SCENE).normalize(), []);
  useEffect(
    () => () => {
      sunGlow.dispose();
      earthGlow.dispose();
      moonGlow.dispose();
    },
    [sunGlow, earthGlow, moonGlow],
  );
  const stars = useMemo(() => {
    const values = new Float32Array(2400 * 3);
    for (let i = 0; i < 2400; i++) {
      const a = Math.sin(i * 12.9898) * 43758.5453;
      const b = Math.sin(i * 78.233) * 12345.678;
      const u = a - Math.floor(a),
        w = b - Math.floor(b);
      const theta = u * Math.PI * 2,
        z = w * 2 - 1,
        r = STAR_SPHERE_RADIUS_SCENE;
      const s = Math.sqrt(1 - z * z);
      values.set([Math.cos(theta) * s * r, z * r, Math.sin(theta) * s * r], i * 3);
    }
    return values;
  }, []);
  const coverage = useMemo(
    () =>
      Array.from({ length: 129 }, (_, i) =>
        onSurface(COVERAGE_MAX_LATITUDE_DEG, (i / 128) * 360, 1.003),
      ),
    [],
  );
  const fullPath = useMemo(
    () => (path ? allPathPoints(path).map(v3) : []),
    [path],
  );
  const parkingRing = useMemo(
    () =>
      Array.from({ length: 129 }, (_, i) => {
        const a = (i / 128) * Math.PI * 2;
        const r = SCENE_LAYOUT.parking_orbit_radius_scene;
        return v3(SCENE_LAYOUT.earth_center_scene).add(
          new THREE.Vector3(Math.sin(a) * r, 0, Math.cos(a) * r),
        );
      }),
    [],
  );
  // Illustrative spin (not a physical rotation rate). The Moon, with its site
  // markers, only spins in the idle overview; during the flight and while
  // picking it eases back to its Earth-facing pose so the path and the
  // selected site stay aligned with the transfer geometry.
  const moonFrame = useRef<THREE.Group>(null!);
  const moonSpin = useRef(0);
  useFrame((_, delta) => {
    const step = Math.min(delta, 0.05);
    if (!reduced) earth.current.rotation.y += step * EARTH_SPIN_RAD_PER_S;
    const free = !reduced && cameraMode === "system" && progress.current <= 0;
    if (free) moonSpin.current += step * MOON_SPIN_RAD_PER_S;
    else {
      const rest =
        Math.round(moonSpin.current / (Math.PI * 2)) * Math.PI * 2;
      moonSpin.current += (rest - moonSpin.current) * Math.min(1, step * 3);
    }
    moonFrame.current.rotation.y = moonSpin.current;
  });
  const pick = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    if (event.delta > 6) return;
    const point = moonFrame.current.worldToLocal(event.point.clone());
    onPick(siteSelectionFromPoint([point.x, point.y, point.z]));
  };
  return (
    <>
      <color attach="background" args={["#03060a"]} />
      <ambientLight intensity={0.22} />
      <directionalLight
        position={SUN_SCENE as unknown as [number, number, number]}
        intensity={3.1}
        color="#fff3df"
      />
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[stars, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color="#b9c7d4"
          size={1.6}
          sizeAttenuation={false}
          transparent
          opacity={0.75}
        />
      </points>
      <group position={SUN_SCENE as unknown as [number, number, number]}>
        <mesh>
          <sphereGeometry args={[SUN_RADIUS_SCENE, 48, 32]} />
          <meshBasicMaterial color="#fff1c9" />
        </mesh>
        <sprite
          scale={[SUN_RADIUS_SCENE * 9, SUN_RADIUS_SCENE * 9, 1]}
        >
          <spriteMaterial
            map={sunGlow}
            transparent
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </sprite>
      </group>
      <group
        position={
          SCENE_LAYOUT.earth_center_scene as unknown as [number, number, number]
        }
      >
        <group rotation={[0, 0, (23.4 * Math.PI) / 180]}>
          <mesh ref={earth}>
            <sphereGeometry args={[SCENE_LAYOUT.earth_radius_scene, 96, 64]} />
            <meshStandardMaterial map={earthMap} roughness={0.85} />
          </mesh>
        </group>
        <Atmosphere
          radius={EARTH_ATMOSPHERE_RADIUS_SCENE}
          sunDirection={sunDirection}
        />
        <DistantGlow
          radius={SCENE_LAYOUT.earth_radius_scene}
          texture={earthGlow}
          strength={0.55}
        />
      </group>
      <DistantGlow
        radius={SCENE_LAYOUT.moon_radius_scene}
        texture={moonGlow}
        strength={0.4}
      />
      <Line points={parkingRing} color="#9ac9e5" opacity={0.18} />
      <group ref={moonFrame}>
      <mesh
        onClick={pick}
        onPointerOver={() => {
          document.body.style.cursor = selectable ? "crosshair" : "zoom-in";
        }}
        onPointerOut={() => {
          document.body.style.cursor = "";
        }}
      >
        <sphereGeometry args={[SCENE_LAYOUT.moon_radius_scene, 128, 96]} />
        <meshStandardMaterial
          map={moonMap}
          bumpMap={moonRelief}
          bumpScale={0.03}
          roughness={1}
        />
      </mesh>
      <Line
        points={coverage}
        color="#9ac9e5"
        opacity={0.75}
        dashed
        dashSize={0.04}
      />
      {sites.map((site) => (
        <mesh
          key={site.site_id}
          position={onSurface(site.latitude_deg, site.longitude_deg_east, 1.008)}
          onClick={(event) => {
            event.stopPropagation();
            if (event.delta <= 6) onPreset(site);
          }}
        >
          <sphereGeometry args={[0.012, 16, 12]} />
          <meshBasicMaterial color="#dfe8ee" />
        </mesh>
      ))}
      {Number.isFinite(lat) && Number.isFinite(lon) && (
        <SiteMarker lat={lat} lon={lon} condition={condition} reduced={reduced} />
      )}
      </group>
      <ScienceOrbiter reduced={reduced} />
      {path && (
        <>
          <Line
            points={fullPath}
            color="#c6d3dc"
            opacity={0.28}
            dashed
            dashSize={0.3}
          />
          <Trail path={path} progress={progress} />
          <Spacecraft
            path={path}
            progress={progress}
            spacecraft={spacecraft}
            reduced={reduced}
          />
        </>
      )}
      <CameraRig
        mode={
          cameraMode === "landing"
            ? "pole"
            : cameraMode === "launch"
              ? "spacecraft"
              : cameraMode
        }
        lat={lat}
        lon={lon}
        spacecraft={spacecraft}
        progress={progress}
        reduced={reduced}
      />
    </>
  );
}

/**
 * Close-up of the final descent and touchdown, staged like the introduction
 * page. Art-directed local terrain in its own scene units (not metres, not the
 * analysed DEM). The touchdown is always staged on the sunlit side; the
 * analysed Sun condition is reported by the HUD, not by this illustration.
 */
function LandingSite({
  progress,
  reduced,
  compact,
  roverPath,
  roverDrive,
}: {
  progress: MutableRefObject<number>;
  reduced: boolean;
  compact: boolean;
  roverPath: RoutePoint[] | null;
  roverDrive: MutableRefObject<number>;
}) {
  const lander = useRef<THREE.Group>(null!);
  // After touchdown the camera eases from the lander to the driving rover.
  const roverFocus = useMemo(() => new THREE.Vector3(), []);
  const roverLook = useMemo(() => new THREE.Vector3(), []);
  const cameraLook = useRef<THREE.Vector3 | null>(null);
  const follow = useRef(0);
  const plume = useRef<THREE.Mesh>(null!);
  const user = useOrbitInput("landing");
  const look = useMemo(() => new THREE.Vector3(), []);
  const offset = useMemo(() => new THREE.Vector3(), []);
  const spherical = useMemo(() => new THREE.Spherical(), []);
  const settledTime = useRef(0);
  const stars = useMemo(() => {
    const values = new Float32Array(1500 * 3);
    for (let i = 0; i < values.length; i++) {
      const a = Math.sin((i + 80) * 127.1 + 311.7) * 43758.5453;
      values[i] = (a - Math.floor(a) - 0.5) * 100;
    }
    return values;
  }, []);
  const descentAt = (p: number) =>
    smooth(LANDING_CLOSEUP_START_PROGRESS, TOUCHDOWN_PROGRESS, p);
  useFrame(({ camera, clock }, delta) => {
    const p = progress.current;
    const descent = descentAt(p);
    const height = (1 - descent) * 5;
    const landerX = (1 - descent) * -1.6;
    lander.current.position.set(landerX, height, 0);
    lander.current.rotation.set(0, -0.4 + descent * 0.35, (1 - descent) * 0.12);
    plume.current.visible = descent < 1;
    plume.current.position.set(landerX, height + 0.2, 0);
    plume.current.scale.set(
      1,
      0.8 + (reduced ? 0 : Math.sin(clock.elapsedTime * 24) * 0.08),
      1,
    );
    // After touchdown the camera drifts slowly around the lander.
    if (descent >= 1 && !reduced) settledTime.current += Math.min(delta, 0.05);
    else if (descent < 1) settledTime.current = 0;
    look.set(landerX * 0.5, 0.9 + height * 0.75, 0);
    const driving = descent >= 1 && roverPath !== null;
    follow.current = THREE.MathUtils.clamp(
      follow.current + (driving ? 1 : -1) * Math.min(delta, 0.05) * 0.8,
      0,
      1,
    );
    if (follow.current > 0) {
      const weight = smooth(0, 1, follow.current);
      roverLook.copy(roverFocus).setY(roverFocus.y + 0.45);
      look.lerp(roverLook, weight);
    }
    if (!cameraLook.current) cameraLook.current = look.clone();
    else cameraLook.current.lerp(look, 1 - Math.exp(-Math.min(delta, 0.05) * 6));
    offset.set(
      4.2 - descent * 1.6,
      5.8 - descent * 3.4 + height * 0.5,
      10.5 - descent * 2.8,
    );
    spherical.setFromVector3(offset);
    spherical.theta += user.current.yaw + settledTime.current * 0.06;
    spherical.phi = THREE.MathUtils.clamp(
      spherical.phi - user.current.pitch,
      0.2,
      1.5,
    );
    spherical.radius *=
      (compact ? 1.25 : 1) *
      THREE.MathUtils.clamp(user.current.zoom, 0.35, 2.4) *
      (1 - 0.25 * smooth(0, 1, follow.current));
    camera.position.setFromSpherical(spherical).add(cameraLook.current);
    camera.lookAt(cameraLook.current);
  });
  return (
    <>
      <color attach="background" args={["#05080b"]} />
      <fog attach="fog" args={["#05080b", 28, 65]} />
      <ambientLight intensity={0.32} />
      {/* Regolith-bounce sky/ground fill so shadowed slopes keep their relief. */}
      <hemisphereLight args={["#9fb4c8", "#4a4640", 0.45]} />
      {/* Low polar Sun: long shadows across the art-directed terrain. */}
      <directionalLight
        position={[-12, 5.4, 7]}
        intensity={3.6}
        color="#fff1dc"
        castShadow
        shadow-mapSize={[compact ? 1024 : 2048, compact ? 1024 : 2048]}
        shadow-camera-left={-18}
        shadow-camera-right={18}
        shadow-camera-top={18}
        shadow-camera-bottom={-18}
        shadow-normalBias={0.04}
        shadow-bias={-0.0001}
      />
      {/* Earthshine fill from the opposite side of the Sun. */}
      <directionalLight
        position={[5, 4, -4]}
        intensity={0.6}
        color="#a9bfd1"
      />
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[stars, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color="#a5b5c4"
          size={0.028}
          transparent
          opacity={0.65}
          sizeAttenuation
        />
      </points>
      <Terrain />
      <group ref={lander}>
        <Model path="/nasa/apollo.glb" size={2.3} grounded />
      </group>
      {roverPath && (
        <Rover path={roverPath} drive={roverDrive} focus={roverFocus} />
      )}
      <mesh ref={plume} rotation={[Math.PI, 0, 0]}>
        <coneGeometry args={[0.13, 0.65, 24]} />
        <meshBasicMaterial
          color="#ead5aa"
          transparent
          opacity={0.36}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
      {!reduced && (
        <Dust
          amount={() => {
            const p = progress.current;
            return (
              smooth(0.6, 0.95, descentAt(p)) *
              (1 - smooth(TOUCHDOWN_PROGRESS, 1, p))
            );
          }}
        />
      )}
    </>
  );
}

/** Launch close-up with drag-to-orbit and wheel-to-zoom camera input. */
function LaunchCloseup({
  progress,
  reduced,
  compact,
}: {
  progress: MutableRefObject<number>;
  reduced: boolean;
  compact: boolean;
}) {
  const user = useOrbitInput("launch");
  return (
    <LaunchSite
      progress={progress}
      reduced={reduced}
      compact={compact}
      user={user}
    />
  );
}

/** Illustrative Sun–Earth–Moon orrery with drag-to-orbit camera input. */
function SolarCloseup({ reduced, compact }: { reduced: boolean; compact: boolean }) {
  const user = useOrbitInput("solar");
  return <SolarSystem reduced={reduced} compact={compact} user={user} />;
}

type SceneProps = {
  lat: number;
  lon: number;
  sites: Preset[];
  cameraMode: CameraView;
  condition: SiteCondition;
  reduced: boolean;
  selectable: boolean;
  onPick: (selection: ReturnType<typeof siteSelectionFromPoint>) => void;
  onPreset: (site: Preset) => void;
};

function ContextGuard({ onFailure }: { onFailure: () => void }) {
  const gl = useThree((state) => state.gl);
  useEffect(() => {
    const canvas = gl.domElement;
    const lost = (event: Event) => {
      event.preventDefault();
      onFailure();
    };
    canvas.addEventListener("webglcontextlost", lost);
    return () => canvas.removeEventListener("webglcontextlost", lost);
  }, [gl, onFailure]);
  return null;
}

function Invalidator({ deps }: { deps: unknown[] }) {
  const invalidate = useThree((state) => state.invalidate);
  useEffect(() => {
    invalidate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return null;
}

export default function MissionScene(
  props: SceneProps & {
    path: TransferPath | null;
    visible: boolean;
    animate: boolean;
    compact: boolean;
    progressValue: number;
    onFailure: () => void;
    /** Backend surface route driven after touchdown, if any. */
    roverPath?: RoutePoint[] | null;
    /** Travelled share of the rover route, 0–1. */
    roverDrive?: number;
  },
) {
  const {
    visible,
    animate,
    compact,
    onFailure,
    progressValue,
    roverPath = null,
    roverDrive = 0,
    ...scene
  } = props;
  const progress = useRef(progressValue);
  const drive = useRef(roverDrive);
  drive.current = roverDrive;
  const { t } = useTranslate();
  return (
    <Canvas
      aria-label={t("missionScene.label")}
      camera={{ position: [150, 190, 175], fov: 50, near: 0.001, far: 8000 }}
      dpr={compact ? 1 : [1, 1.75]}
      shadows={{ type: THREE.PCFShadowMap }}
      frameloop={visible && animate ? "always" : "demand"}
      gl={{ antialias: true, alpha: false, logarithmicDepthBuffer: true }}
    >
      <ContextGuard onFailure={onFailure} />
      <Invalidator
        deps={[
          progressValue,
          scene.lat,
          scene.lon,
          scene.cameraMode,
          scene.condition,
          visible,
          roverPath,
          roverDrive,
        ]}
      />
      <ProgressEaser
        target={progressValue}
        progress={progress}
        snap={!animate}
      />
      <Suspense fallback={null}>
        {scene.cameraMode === "solar" ? (
          <SolarCloseup reduced={scene.reduced} compact={compact} />
        ) : scene.cameraMode === "launch" ? (
          <LaunchCloseup
            progress={progress}
            reduced={scene.reduced}
            compact={compact}
          />
        ) : scene.cameraMode === "landing" ? (
          <LandingSite
            progress={progress}
            reduced={scene.reduced}
            compact={compact}
            roverPath={roverPath}
            roverDrive={drive}
          />
        ) : (
          <System {...scene} progress={progress} />
        )}
      </Suspense>
    </Canvas>
  );
}
