import { Suspense, useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useLoader, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const smooth = (a: number, b: number, value: number) =>
  THREE.MathUtils.smoothstep(value, a, b);
const noise = (n: number) =>
  THREE.MathUtils.euclideanModulo(Math.sin(n * 127.1 + 311.7) * 43758.5453, 1);

function Model({
  path,
  size,
  grounded = false,
}: {
  path: string;
  size: number;
  grounded?: boolean;
}) {
  const gltf = useLoader(GLTFLoader, path, (loader) => {
    loader.setDRACOLoader(new DRACOLoader().setDecoderPath("/draco/"));
  });
  const { model, scale, center } = useMemo(() => {
    const model = gltf.scene.clone(true);
    model.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        object.castShadow = true;
        object.receiveShadow = true;
      }
    });
    const box = new THREE.Box3().setFromObject(model);
    const dimensions = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3()).negate();
    if (grounded) center.y = -box.min.y;
    return {
      model,
      scale: size / Math.max(dimensions.x, dimensions.y, dimensions.z),
      center,
    };
  }, [gltf, size, grounded]);
  return (
    <group scale={scale}>
      <primitive object={model} position={center} />
    </group>
  );
}

// Art-directed scene coordinates, not meters or a scientific terrain product.
function Terrain() {
  const grain = useMemo(() => {
    const pixels = new Uint8Array(256 * 256 * 4);
    for (let i = 0; i < 256 * 256; i++) {
      const value = 75 + noise(i + 900) * 95;
      pixels.set([value, value, value, 255], i * 4);
    }
    const texture = new THREE.DataTexture(pixels, 256, 256);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.repeat.set(45, 45);
    texture.magFilter = THREE.LinearFilter;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.generateMipmaps = true;
    texture.needsUpdate = true;
    return texture;
  }, []);
  useEffect(() => () => grain.dispose(), [grain]);
  const geometry = useMemo(() => {
    const mesh = new THREE.PlaneGeometry(70, 70, 256, 256);
    mesh.rotateX(-Math.PI / 2);
    const positions = mesh.attributes.position;
    const colors = new Float32Array(positions.count * 3);
    const craters = Array.from({ length: 52 }, (_, i) => ({
      x: (noise(i + 20) - 0.5) * 58,
      z: (noise(i + 100) - 0.5) * 58,
      radius: 0.6 + noise(i + 300) * 2.8,
    })).filter(({ x, z }) => Math.hypot(x, z) > 4);
    for (let i = 0; i < positions.count; i++) {
      const x = positions.getX(i),
        z = positions.getZ(i);
      const landing = smooth(1.5, 6, Math.hypot(x, z));
      let y =
        (Math.sin(x * 0.32) * Math.cos(z * 0.27) * 0.3 +
          Math.sin(x * 1.6 + z * 0.8) * 0.035) *
        landing;
      y +=
        Math.exp(-((z + 16) ** 2) / 30) *
        (1.4 + Math.sin(x * 0.4) * 0.8 + Math.cos(x * 0.9) * 0.35);
      for (const crater of craters) {
        const d = Math.hypot(x - crater.x, z - crater.z) / crater.radius;
        if (d < 1.6)
          y +=
            (-Math.exp(-d * d * 3.5) * 0.48 +
              Math.exp(-((d - 0.96) ** 2) * 35) * 0.2) *
            crater.radius *
            landing;
      }
      y +=
        (Math.sin(x * 4.3 + z * 1.2) * Math.sin(z * 3.8 - x) * 0.045 +
          Math.sin(x * 9.1 + z * 6.2) * 0.015) *
        landing;
      positions.setY(i, y);
      const shade = 0.48 + noise(i + 6) * 0.1 + Math.min(y, 1) * 0.025;
      colors.set([shade, shade * 0.99, shade * 0.97], i * 3);
    }
    mesh.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    mesh.computeVertexNormals();
    return mesh;
  }, []);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <mesh geometry={geometry} receiveShadow>
      <meshStandardMaterial
        vertexColors
        color="#767978"
        roughness={1}
        bumpMap={grain}
        bumpScale={0.07}
      />
    </mesh>
  );
}

function Dust({ progress }: { progress: number }) {
  const ref = useRef<THREE.Points>(null!);
  const particles = useMemo(() => new Float32Array(180 * 3), []);
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        uniforms: { opacity: { value: 0 } },
        vertexShader: `void main() { vec4 p = modelViewMatrix * vec4(position, 1.0); gl_Position = projectionMatrix * p; gl_PointSize = min(18.0, 65.0 / max(1.0, -p.z)); }`,
        fragmentShader: `uniform float opacity; void main() { float d = length(gl_PointCoord - 0.5); float a = (1.0 - smoothstep(0.05, 0.5, d)) * opacity; gl_FragColor = vec4(0.65, 0.62, 0.55, a); }`,
      }),
    [],
  );
  useEffect(() => () => material.dispose(), [material]);
  useFrame(({ clock }) => {
    const amount =
      smooth(0.68, 0.86, progress) * (1 - smooth(0.92, 1, progress));
    material.uniforms.opacity.value = amount * 0.36;
    ref.current.visible = amount > 0;
    const array = ref.current.geometry.attributes.position;
    for (let i = 0; i < 180; i++) {
      const angle = noise(i) * Math.PI * 2;
      const phase = (noise(i + 200) + clock.elapsedTime * 0.18) % 1;
      const radius = 0.6 + phase * 5;
      array.setXYZ(
        i,
        Math.cos(angle) * radius,
        0.04 + Math.sin(phase * Math.PI) * noise(i + 500) * 0.32,
        Math.sin(angle) * radius,
      );
    }
    array.needsUpdate = true;
  });
  return (
    <points ref={ref} material={material}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[particles, 3]} />
      </bufferGeometry>
    </points>
  );
}

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
        {!reduced && <Dust progress={progress} />}
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
  return (
    <Canvas
      aria-label="Illustrative NASA Moon, LRO and Apollo 3D scene"
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
