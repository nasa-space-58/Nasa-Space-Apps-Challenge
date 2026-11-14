import { useEffect, useMemo, useRef } from "react";
import { useFrame, useLoader } from "@react-three/fiber";
import * as THREE from "three";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

// Shared, art-directed lunar surface pieces used by the introduction page and
// the mission-control landing close-up. Scene units here are not metres and the
// terrain is not a scientific product.

export const smooth = (a: number, b: number, value: number) =>
  THREE.MathUtils.smoothstep(value, a, b);
export const noise = (n: number) =>
  THREE.MathUtils.euclideanModulo(Math.sin(n * 127.1 + 311.7) * 43758.5453, 1);

export function Model({
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

export function Terrain() {
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

/** Engine-blown regolith; `amount` returns the current opacity weight in [0, 1]. */
export function Dust({ amount: getAmount }: { amount: () => number }) {
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
    const amount = getAmount();
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
