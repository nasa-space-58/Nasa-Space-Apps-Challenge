import { Suspense, useMemo, useRef } from "react";
import { Canvas, useFrame, useLoader } from "@react-three/fiber";
import * as THREE from "three";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

function Model({ path, size }: { path: string; size: number }) {
  const gltf = useLoader(GLTFLoader, path, (loader) => {
    loader.setDRACOLoader(new DRACOLoader().setDecoderPath("/draco/"));
  });
  const { model, scale, center } = useMemo(() => {
    const model = gltf.scene.clone(true);
    const box = new THREE.Box3().setFromObject(model);
    const dimensions = box.getSize(new THREE.Vector3());
    return {
      model,
      scale: size / Math.max(dimensions.x, dimensions.y, dimensions.z),
      center: box.getCenter(new THREE.Vector3()).multiplyScalar(-1),
    };
  }, [gltf, size]);
  return (
    <group scale={scale}>
      <primitive object={model} position={center} />
    </group>
  );
}
function World({ progress, reduced }: { progress: number; reduced: boolean }) {
  const moon = useRef<THREE.Mesh>(null!);
  const lro = useRef<THREE.Group>(null!);
  const lander = useRef<THREE.Group>(null!);
  const texture = useLoader(THREE.TextureLoader, "/nasa/moon.jpg");
  texture.colorSpace = THREE.SRGBColorSpace;
  const stars = useMemo(() => {
    const points: number[] = [];
    for (let i = 0; i < 600; i++) {
      const noise = (n: number) => (Math.sin(n * 12.9898) * 43758.5453) % 1;
      points.push(
        noise(i + 1) * 24,
        noise(i + 701) * 17,
        -6 - Math.abs(noise(i + 1401)) * 20,
      );
    }
    return new Float32Array(points);
  }, []);
  useFrame(({ clock }) => {
    const p = progress;
    const scale = 1 + 1.6 * p;
    moon.current.position.set(1.65 * (1 - p), -0.15 - 6.1 * p, -0.4);
    moon.current.scale.setScalar(scale);
    moon.current.rotation.y =
      -0.5 + (reduced ? 0 : clock.elapsedTime * 0.015) + p * 0.2;
    lro.current.position.set(0.35 + 2.2 * p, 1.6 + 1.2 * p, 1.2);
    lro.current.rotation.set(0.3, -0.6 + p, -0.3);
    lro.current.scale.setScalar(1 - p * 0.85);
    lander.current.visible = p > 0.12;
    lander.current.position.set(-0.1 + 0.2 * p, 2.2 - p * 2.75, 1.4);
    lander.current.rotation.y = -0.5 + p * 0.8;
    lander.current.scale.setScalar(Math.min(1, p * 2.5));
  });
  return (
    <>
      <ambientLight intensity={0.45} />
      <directionalLight position={[-3, 4, 5]} intensity={3.2} color="#fff3da" />
      <directionalLight
        position={[6, -1, 1]}
        intensity={0.15}
        color="#a3c4dd"
      />
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[stars, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color="#c0cedc"
          size={0.018}
          transparent
          opacity={0.55}
          sizeAttenuation
        />
      </points>
      <mesh ref={moon}>
        <sphereGeometry args={[2.2, 96, 64]} />
        <meshStandardMaterial map={texture} roughness={1} />
      </mesh>
      <group ref={lro}>
        <Model path="/nasa/lro.glb" size={0.85} />
      </group>
      <group ref={lander}>
        <Model path="/nasa/apollo.glb" size={0.85} />
        {progress > 0.2 && progress < 0.94 && (
          <mesh position={[0, -0.5, 0]} rotation={[Math.PI, 0, 0]}>
            <coneGeometry args={[0.035, 0.25, 12]} />
            <meshBasicMaterial color="#ffe3aa" transparent opacity={0.75} />
          </mesh>
        )}
      </group>
    </>
  );
}
export default function MoonScene({
  progress,
  reduced,
  visible,
}: {
  progress: number;
  reduced: boolean;
  visible: boolean;
}) {
  return (
    <Canvas
      aria-label="Illustrative NASA Moon, LRO and Apollo 3D scene"
      camera={{ position: [0, 0, 8], fov: 43 }}
      dpr={[1, 1.5]}
      frameloop={visible && !reduced ? "always" : "demand"}
      gl={{ antialias: true, alpha: true }}
    >
      <Suspense fallback={null}>
        <World progress={progress} reduced={reduced} />
      </Suspense>
    </Canvas>
  );
}
