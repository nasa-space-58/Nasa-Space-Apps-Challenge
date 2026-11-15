import { useEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { Model } from "./LunarSurface";
import type { AnalysisPreview, PlotPoint } from "../analysisView";

function Path({points,color,opacity,dashed}: {points:THREE.Vector3[];color:string;opacity:number;dashed?:boolean}) {
  const line = useMemo(()=>{
    const material = dashed ? new THREE.LineDashedMaterial({color,transparent:true,opacity,dashSize:0.25,gapSize:0.18}) : new THREE.LineBasicMaterial({color,transparent:true,opacity});
    const object = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), material);
    if (dashed) object.computeLineDistances();
    return object;
  },[points,color,opacity,dashed]);
  useEffect(()=>()=>{line.geometry.dispose();line.material.dispose();},[line]);
  return <primitive object={line} />;
}
/** A labelled local metric plot, separate from the illustrative landing terrain. */
export default function AnalysisScene({preview,progress,onSelect}: {preview:AnalysisPreview;progress:number;onSelect:(id:string)=>void}) {
  const {camera,gl,invalidate} = useThree();
  const body = useRef<THREE.Group>(null!);
  const all = useMemo(()=>[...preview.lines.flatMap(l=>l.points),...preview.points,...preview.obstacles], [preview]);
  const bounds = useMemo(()=> {
    const box = new THREE.Box3();
    all.forEach(p=>box.expandByPoint(new THREE.Vector3(p.x,p.y,p.z)));
    box.expandByPoint(new THREE.Vector3());
    const center=box.getCenter(new THREE.Vector3()), span=Math.max(1,box.getSize(new THREE.Vector3()).length());
    return {center,scale:14/span};
  },[all]);
  const point = (p:PlotPoint)=>new THREE.Vector3(p.x,p.y,p.z).sub(bounds.center).multiplyScalar(bounds.scale);
  const paths = useMemo(()=>preview.lines.map(l=>({...l,vertices:l.points.map(p=>new THREE.Vector3(p.x,p.y,p.z).sub(bounds.center).multiplyScalar(bounds.scale))})),[preview,bounds]);
  const angle = useRef({yaw:0.65,pitch:0.6,zoom:1});
  useEffect(()=>{
    angle.current={yaw:0.65,pitch:0.6,zoom:1};
    const el=gl.domElement;let last:{x:number;y:number}|null=null;
    const down=(e:PointerEvent)=>{last={x:e.clientX,y:e.clientY};el.setPointerCapture(e.pointerId);};
    const move=(e:PointerEvent)=>{if(!last)return;angle.current.yaw-=(e.clientX-last.x)*0.006;angle.current.pitch=THREE.MathUtils.clamp(angle.current.pitch+(e.clientY-last.y)*0.006,0.08,1.45);last={x:e.clientX,y:e.clientY};invalidate();};
    const up=()=>{last=null;};
    const wheel=(e:WheelEvent)=>{e.preventDefault();angle.current.zoom=THREE.MathUtils.clamp(angle.current.zoom*Math.exp(e.deltaY*0.001),0.4,3);invalidate();};
    el.addEventListener("pointerdown",down);el.addEventListener("pointermove",move);el.addEventListener("pointerup",up);el.addEventListener("pointercancel",up);el.addEventListener("wheel",wheel,{passive:false});
    return ()=>{el.removeEventListener("pointerdown",down);el.removeEventListener("pointermove",move);el.removeEventListener("pointerup",up);el.removeEventListener("pointercancel",up);el.removeEventListener("wheel",wheel);};
  },[gl,preview.kind,invalidate]);
  useFrame(()=>{
    const a=angle.current;
    camera.position.set(Math.sin(a.yaw)*Math.cos(a.pitch),Math.sin(a.pitch),Math.cos(a.yaw)*Math.cos(a.pitch)).multiplyScalar(22*a.zoom);camera.lookAt(0,0,0);
    const route=preview.lines.find(l=>l.id===preview.selected)??preview.lines.at(-1);
    if(body.current && route?.points.length){
      const pts=route.points;const start=pts[0].time??0,end=pts.at(-1)?.time??1;
      let i=1;const time=start+(end-start)*progress;
      if(pts.every(p=>p.time!==undefined)){while(i<pts.length-1&&(pts[i].time??0)<time)i++;}
      else i=Math.max(1,Math.min(pts.length-1,Math.floor(progress*(pts.length-1))+1));
      const p=pts[Math.max(0,i-1)],q=pts[Math.min(i,pts.length-1)];
      const fraction=p.time!==undefined&&q.time!==undefined?(time-p.time)/Math.max(1e-9,q.time-p.time):progress*(pts.length-1)-(i-1);
      body.current.position.copy(point(p)).lerp(point(q),THREE.MathUtils.clamp(fraction,0,1));
    }
  });
  const origin=point({x:0,y:0,z:0});
  return <>
    <color attach="background" args={["#080f18"]} /><ambientLight intensity={1.8}/><directionalLight position={[5,12,8]} intensity={3}/>
    <gridHelper args={[22,22,"#365061","#172b39"]} position={[0,origin.y-0.03,0]}/>
    <mesh position={origin} rotation={[-Math.PI/2,0,0]}><ringGeometry args={[0.12,0.2,32]}/><meshBasicMaterial color="#ffffff" side={THREE.DoubleSide}/></mesh>
    {paths.map(l=><Path key={l.id} points={l.vertices} color={l.color} dashed={l.dashed} opacity={preview.selected&&preview.selected!==l.id?0.25:1}/>)}
    {preview.points.map((p,i)=><mesh key={i} position={point(p)} onClick={e=>{e.stopPropagation();if(e.delta<6&&p.id)onSelect(p.id);}} scale={preview.selected===p.id?1.5:1}>
      <sphereGeometry args={[0.075,10,8]}/><meshBasicMaterial color={p.color??(p.ok===false?"#ff8a78":p.ok===true?"#65ddbd":"#e8c58b")}/>
    </mesh>)}
    {preview.obstacles.map((p,i)=><mesh key={i} position={point(p)} rotation={[-Math.PI/2,0,0]}><circleGeometry args={[Math.max(0.06,(p.radius??0)*bounds.scale),40]}/><meshBasicMaterial color="#ff6655" transparent opacity={0.4} side={THREE.DoubleSide}/></mesh>)}
    {preview.lines.some(l=>l.points.length>1)&&preview.kind!=="surface"&&<group ref={body}><Model path="/nasa/apollo.glb" size={0.65} grounded/></group>}
  </>;
}
