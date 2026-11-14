import { Suspense, useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useLoader, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { Dust, Model, Terrain, smooth, noise } from "./LunarSurface";
import { useTranslate } from "../i18n";

function World({
  progress,
  reduced,
  compact,
}: {
  progress: number;
  reduced: boolean;
  compact: boolean;
}) {
  const space = useRef<THREE.Group>(null!);
  const surface = useRef<THREE.Group>(null!);
  const moon = useRef<THREE.Mesh>(null!);
  const lro = useRef<THREE.Group>(null!);
  const lander = useRef<THREE.Group>(null!);
  const plume = useRef<THREE.Mesh>(null!);
  const [texture, relief] = useLoader(THREE.TextureLoader, [
    "/nasa/moon.jpg",
    "/nasa/moon-relief.jpg",
  ]);
  texture.colorSpace = THREE.SRGBColorSpace;
  const look = useMemo(() => new THREE.Vector3(), []);
  const ambientTime = useRef(0);
  const stars = useMemo(
    () =>
      new Float32Array(
        Array.from({ length: 1500 }, (_, i) => (noise(i + 80) - 0.5) * 100),
      ),
    [],
  );
  const orbit = useMemo(() => {
    const curve = new THREE.EllipseCurve(
      0,
      0,
      3.3,
      3.3,
      0,
      Math.PI * 2,
      false,
      0,
    );
    return new THREE.BufferGeometry().setFromPoints(curve.getPoints(160));
  }, []);
  const orbitLine = useMemo(
    () =>
      new THREE.LineLoop(
        orbit,
        new THREE.LineBasicMaterial({
          color: "#9c947c",
          transparent: true,
          opacity: 0.32,
        }),
      ),
    [orbit],
  );
  useEffect(
    () => () => {
      orbit.dispose();
      orbitLine.material.dispose();
    },
    [orbit, orbitLine],
  );
  useFrame(({ camera }, delta) => {
    if (!reduced) ambientTime.current += Math.min(delta, 0.05);
    const approach = smooth(0, 0.43, progress);
    const descent = smooth(0.46, 0.93, progress);
    const orbitTime = ambientTime.current * 0.025;
    space.current.visible = progress < 0.49;
    surface.current.visible = progress >= 0.49;
    moon.current.rotation.y = -0.55 + approach * 0.45;
    space.current.position.set(compact ? 0 : 2.25, compact ? 2.9 : 0, 0);
    space.current.scale.setScalar((compact ? 0.73 : 1) + approach * 0.5);
    lro.current.position.set(
      Math.cos(orbitTime + 2.1 + approach * 1.5) * 3.35,
      Math.sin(orbitTime + 2.1 + approach * 1.5) * 2.6,
      1.8,
    );
    lro.current.rotation.set(0.35, -0.6 + approach, -0.25);
    const height = (1 - descent) * 5;
    if (progress < 0.49) {
      camera.position.set(0, 0, compact ? 12.5 : 10);
      look.set(0, 0, 0);
    } else {
      camera.position.set(
        3.8 - descent * 1.8,
        6.5 - descent * 3.7 + height * 0.45,
        compact ? 15 : 11 - descent * 2,
      );
      look.set(compact ? 0 : -2.7, (compact ? -1.0 : 1.1) + height * 0.7, 0);
    }
    camera.lookAt(look);
    lander.current.position.set((1 - descent) * -1.6, height, 0);
    lander.current.rotation.set(0, -0.4 + descent * 0.35, (1 - descent) * 0.12);
    plume.current.visible = progress >= 0.49 && progress < 0.935;
    plume.current.scale.set(
      1,
      0.8 + (reduced ? 0 : Math.sin(ambientTime.current * 24) * 0.08),
      1,
    );
    plume.current.position.set(lander.current.position.x, height + 0.2, 0);
  });
  return (
    <>
      <color attach="background" args={["#05080b"]} />
      <fog attach="fog" args={["#05080b", 28, 65]} />
      <ambientLight intensity={0.2} />
      <directionalLight
        position={[-10, 7, 6]}
        intensity={3.4}
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
      <directionalLight
        position={[5, 3, -4]}
        intensity={0.35}
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
      <group ref={space}>
        <mesh ref={moon}>
          <sphereGeometry args={[2.45, 96, 64]} />
          <meshStandardMaterial
            map={texture}
            bumpMap={relief}
            bumpScale={0.065}
            roughness={1}
          />
        </mesh>
        <group rotation={[0.9, 0.25, -0.4]}>
          <primitive object={orbitLine} />
        </group>
        <group ref={lro}>
          <Model path="/nasa/lro.glb" size={1.05} />
        </group>
      </group>
      <group ref={surface}>
        <Terrain />
        <group ref={lander}>
          <Model path="/nasa/apollo.glb" size={2.3} grounded />
        </group>
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
            amount={() =>
              smooth(0.68, 0.86, progress) * (1 - smooth(0.92, 1, progress))
            }
          />
        )}
      </group>
    </>
  );
}

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

export default function MoonScene({
  progress,
  reduced,
  visible,
  compact = false,
  onFailure,
}: {
  progress: number;
  reduced: boolean;
  visible: boolean;
  compact?: boolean;
  onFailure: () => void;
}) {
  const { t } = useTranslate();
  return (
    <Canvas
      aria-label={t("moonScene.label")}
      camera={{ position: [0, 0, 10], fov: 43 }}
      dpr={compact ? 1 : [1, 1.5]}
      shadows={{ type: THREE.PCFShadowMap }}
      frameloop={visible && !reduced ? "always" : "demand"}
      gl={{ antialias: !compact, alpha: false }}
    >
      <ContextGuard onFailure={onFailure} />
      <Suspense fallback={null}>
        <World progress={progress} reduced={reduced} compact={compact} />
      </Suspense>
    </Canvas>
  );
}
