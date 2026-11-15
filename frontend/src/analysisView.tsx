import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslate } from "./i18n";

export type AnalysisKind = "descent" | "risk" | "backups" | "twin" | "recovery" | "surface" | "routes";
export type PlotPoint = { x: number; y: number; z: number; time?: number; id?: string; ok?: boolean; radius?: number; color?: string };
export type PlotLine = { id: string; color: string; points: PlotPoint[]; dashed?: boolean };
/** Mission-run route colours: primary green, reachable backup yellow, rejected red, flown run white. */
export const ROUTE_COLORS = { primary: "#4fd18b", backup: "#f2c94c", rejected: "#ff6b5e", flown: "#f4f7fb" } as const;
export type AnalysisPreview = { kind: AnalysisKind; lines: PlotLine[]; points: PlotPoint[]; obstacles: PlotPoint[]; events: number[]; selected?: string; stale?: boolean };
const Context = createContext<{
  candidateId: string | null;
  setCandidateId: React.Dispatch<React.SetStateAction<string | null>>;
  preview: AnalysisPreview | null;
  setPreview: React.Dispatch<React.SetStateAction<AnalysisPreview | null>>;
}>({ candidateId:null, setCandidateId:()=>{}, preview: null, setPreview: () => {} });
export function AnalysisViewProvider({ children }: { children: ReactNode }) {
  const [candidateId,setCandidateId] = useState<string | null>(null);
  const [preview, setPreview] = useState<AnalysisPreview | null>(null);
  const value = useMemo(() => ({preview, setPreview,candidateId,setCandidateId}), [preview,candidateId]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export const useAnalysisView = () => useContext(Context);

type Row = Record<string, unknown>;
const obj = (v: unknown): Row => v && typeof v === "object" ? v as Row : {};
const rows = (v: unknown): Row[] => Array.isArray(v) ? v.map(obj) : [];
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const gold = "#e8c58b", blue = "#74b9df", green = "#65ddbd", red = "#ff8a78";

/** Rendering adapter only: preserve API metres and timestamps, never solve a trajectory here. */
export function previewFor(kind: AnalysisKind, value: unknown): AnalysisPreview {
  const d = obj(value);
  const result: AnalysisPreview = {kind, lines: [], points: [], obstacles: [], events: []};
  const trajectory = (data: unknown, stream?: string) => rows(data).flatMap(r => {
    const p = stream ? obj(r[stream]) : r;
    if (!finite(p.downrange_m) || !finite(p.altitude_m)) return [];
    return [{x:p.downrange_m + (finite(r.target_down_m) ? r.target_down_m : 0), y:p.altitude_m, z:finite(r.target_cross_m) ? r.target_cross_m : 0, time:finite(r.t_s) ? r.t_s : undefined}];
  });
  if (kind === "descent") result.lines.push({id:"planned",color:gold,points:trajectory(d.samples)});
  if (kind === "twin") {
    const run = obj(d.run);
    result.lines.push({id:"truth",color:gold,points:trajectory(run.timeline,"truth")}, {id:"estimate",color:blue,points:trajectory(run.timeline,"estimate")});
    result.events = rows(run.log).filter(r => r.kind === "event_applied" && finite(r.t_s)).map(r=>r.t_s as number);
  }
  if (kind === "recovery") {
    result.lines.push({id:"original",color:blue,points:trajectory(d.original_timeline,"truth")}, {id:"replanned",color:gold,points:trajectory(d.timeline,"truth")});
    result.points = rows(d.candidates).flatMap(p => finite(p.down_m) && finite(p.cross_m) ? [{x:p.down_m,y:0,z:p.cross_m,id:String(p.id)}] : []);
    result.obstacles = rows(d.obstacles).flatMap(p=>finite(p.down_m)&&finite(p.cross_m)&&finite(p.radius_m)?[{x:p.down_m,y:0,z:p.cross_m,radius:p.radius_m}]:[]);
    result.events = rows(d.decisions).filter(r=>finite(r.t_s)).map(r=>r.t_s as number);
  }
  if (kind === "risk") result.points = rows(obj(d.landing).points).flatMap(p=>finite(p.down_m)&&finite(p.cross_m)?[{x:p.down_m,y:0,z:p.cross_m,ok:p.success===true}]:[]);
  if (kind === "backups") result.points = [obj(d.primary),...rows(d.candidates)].flatMap(p=>finite(p.down_m)&&finite(p.cross_m)?[{x:p.down_m,y:0,z:p.cross_m,id:String(p.id),ok:p.reachable===true}]:[]);
  if (kind === "routes") {
    // Backend paths in the primary target frame; terrain-only rejections have no path.
    const routes = rows(d.routes);
    const colorOf = (r: Row) => r.role === "primary" && r.reachable !== false ? ROUTE_COLORS.primary : r.role === "backup" ? ROUTE_COLORS.backup : ROUTE_COLORS.rejected;
    result.lines = routes.filter(r => rows(r.trajectory).length > 1).map(r => ({
      id: String(r.id), color: colorOf(r), dashed: r.role === "rejected",
      points: rows(r.trajectory).flatMap(p => finite(p.down_m) && finite(p.altitude_m) && finite(p.cross_m) ? [{x:p.down_m,y:p.altitude_m,z:p.cross_m,time:finite(p.t_s)?p.t_s:undefined}] : []),
    }));
    const start = finite(obj(d.divert_state).t_s) ? obj(d.divert_state).t_s as number : -Infinity;
    const flown = rows(d.flown).flatMap(p => finite(p.t_s) && p.t_s >= start && finite(p.downrange_m) && finite(p.altitude_m) ? [{x:p.downrange_m,y:p.altitude_m,z:0,time:p.t_s}] : []);
    if (flown.length > 1) result.lines.push({id:"flown",color:ROUTE_COLORS.flown,points:flown});
    result.points = routes.flatMap(r => finite(r.down_m) && finite(r.cross_m) ? [{x:r.down_m,y:0,z:r.cross_m,id:String(r.id),color:colorOf(r)}] : []);
  }
  if (kind === "surface") result.lines = rows(d.routes).map((r,i)=>({id:String(r.target_id),color:r.reachable===false?red:[gold,blue,green][i%3],points:rows(r.path).flatMap(p=>finite(p.north_m)&&finite(p.east_m)?[{x:p.east_m,y:0,z:-p.north_m}]:[])}));
  return result;
}

/** Ignore late responses and mark results stale whenever their exact input snapshot changes. */
export function useScenarioResult<T>(kind: AnalysisKind, inputs: unknown) {
  const {preview,setPreview} = useAnalysisView();
  const [result,setResult] = useState<T | null>(null);
  const key = JSON.stringify(inputs);
  const current = useRef(key);
  current.current = key;
  const serial = useRef(0);
  const [resultKey,setResultKey] = useState<string | null>(null);
  useEffect(() => {
    setPreview(p => p?.kind === kind ? {...p,stale:true} : p);
    return () => { serial.current++; setPreview(p => p?.kind === kind ? {...p,stale:true} : p); };
  }, [key,kind,setPreview]);
  const begin = () => ({key:current.current,serial:++serial.current});
  const isCurrent = (ticket: ReturnType<typeof begin>) => ticket.key === current.current && ticket.serial === serial.current;
  const commit = (ticket: ReturnType<typeof begin>, value:T) => {
    if (!isCurrent(ticket)) return;
    setResult(value);setResultKey(ticket.key);setPreview(previewFor(kind,value));
  };
  const show = useCallback(() => {
    if (result && resultKey === current.current) setPreview(previewFor(kind,result));
  },[result,resultKey,kind,setPreview]);
  const select = (id:string) => {
    if(result && resultKey===current.current) setPreview({...previewFor(kind,result),selected:id});
  };
  return {result,begin,isCurrent,commit,show,select,selected:preview?.kind===kind?preview.selected:undefined,stale:result!==null&&resultKey!==key};
}
export function ResultSceneLink({stale,show}: {stale:boolean;show:()=>void}) {
  const {t} = useTranslate();
  return <div className={`scene-result-link ${stale ? "stale" : ""}`} role="status">
    <span>{t(stale ? "workspace.stale" : "workspace.shown")}</span>
    <button type="button" disabled={stale} onClick={show}>{t("workspace.show3d")}</button>
  </div>;
}
