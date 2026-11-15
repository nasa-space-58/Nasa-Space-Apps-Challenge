import { useAnalysisView } from "../analysisView";
import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { candidateColor, type MissionCandidate } from "../missionCandidates";
import "./mission-candidates.css";
import PolarMap from "./PolarMap";
import SurfaceOps from "./SurfaceOps";
import "./flight-readout.css";
import type { RoverState } from "./Rover";
import type { Preset } from "../types";
import {
  COVERAGE_MAX_LATITUDE_DEG,
  LANDING_CLOSEUP_START_PROGRESS,
  LAUNCH_CLOSEUP_END_PROGRESS,
  MISSION_PHASES,
  MOON_MEAN_RADIUS_KM,
  buildTransferPath,
  liveFlightView,
  phaseAt,
  type FlightEphemeris,
  type SiteSelection,
} from "../missionPath";
import type { CameraMode, CameraView, SiteCondition } from "./MissionScene";
import { useTranslate, type Translate } from "../i18n";
import { touchdownOutcome, useMissionRun } from "../missionRunStore";
import "./mission-run.css";

const MissionScene = lazy(() => import("./MissionScene"));
/** Wall-clock length of one illustrative flight playback. */
const FLIGHT_PLAYBACK_S = 45;
/** Wait for the date to settle before requesting dated flight positions. */
const FLIGHT_REQUEST_DELAY_MS = 600;
const FLIGHT_POLL_MS = 1500;
const LANDING_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

type FlightJob = { id: string; status: string; result: FlightEphemeris | null; error: string | null };

/**
 * Dated Moon/Sun/Earth-rotation samples for a flight landing at landingUtc,
 * from the backend's JPL Horizons job. Null while loading or on failure.
 */
function useFlightEphemeris(landingUtc: string | undefined) {
  const [flight, setFlight] = useState<FlightEphemeris | null>(null);
  const [state, setState] = useState<"off" | "loading" | "ready" | "failed">("off");
  useEffect(() => {
    setFlight(null);
    if (!landingUtc || !LANDING_UTC.test(landingUtc)) {
      setState("off");
      return;
    }
    setState("loading");
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const settle = (job: FlightJob) => {
      if (!live) return;
      if (job.status === "ready" && job.result) {
        setFlight(job.result);
        setState("ready");
      } else if (job.status === "failed") setState("failed");
      else timer = setTimeout(() => poll(job.id), FLIGHT_POLL_MS);
    };
    const fail = () => live && setState("failed");
    const poll = (id: string) =>
      fetch(`/api/flight-ephemeris/${encodeURIComponent(id)}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then(settle, fail);
    timer = setTimeout(() => {
      fetch("/api/flight-ephemeris", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ landing_utc: landingUtc }),
      })
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then(settle, fail);
    }, FLIGHT_REQUEST_DELAY_MS);
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [landingUtc]);
  return { flight, state };
}
const TICK_MS = 50;
const cameraModes: CameraMode[] = [
  "auto",
  "system",
  "moon",
  "pole",
  "spacecraft",
  "launch",
  "landing",
];

/** Width limits of the resizable mission-control side panel, in CSS pixels. */
const PANEL_MIN_WIDTH_PX = 300;
const PANEL_DEFAULT_WIDTH_PX = 420;
const PANEL_MAX_SHARE = 0.75;
const PANEL_KEY_STEP_PX = 32;
const PANEL_WIDTH_STORAGE_KEY = "lunar-panel-width-px";

/**
 * A mission-control module. `group` places it in one of the four numbered steps
 * ("results", "compare", "descent"), under "Learn" or in the "Advanced" menu.
 */
export type StageModule = {
  id: string;
  label: string;
  content: ReactNode;
  group: "results" | "compare" | "descent" | "learn" | "advanced";
};
/** The numbered steps of mission control, in order. */
const STEP_GROUPS = ["plan", "results", "compare", "descent"] as const;

const clampPanelWidth = (width_px: number, container_px: number) =>
  Math.round(
    Math.max(
      PANEL_MIN_WIDTH_PX,
      Math.min(width_px, Math.max(PANEL_MIN_WIDTH_PX, container_px * PANEL_MAX_SHARE)),
    ),
  );

const coordinateLabel = (t: Translate, lat: number, lon: number) =>
  Number.isFinite(lat) && Number.isFinite(lon)
    ? `${Math.abs(lat).toFixed(5)}° ${lat < 0 ? t("missionStage.south") : t("missionStage.north")} · ${lon.toFixed(5)}° ${t("missionStage.east")}`
    : t("missionStage.invalidCoordinates");

class SceneBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

export default function MissionStage({
  lat,
  lon,
  sites,
  candidates = [],
  busy,
  webgl,
  reduced,
  visible,
  compact,
  launchKey,
  landingUtc,
  analysisId,
  condition,
  siteStatus,
  onSelect,
  onPreset,
  onSceneFailure,
  onArrived,
  timeline,
  status,
  modules,
  activeModule,
  onModuleChange,
  children,
}: {
  candidates?: MissionCandidate[];
  lat: number;
  lon: number;
  sites: Preset[];
  busy: boolean;
  webgl: boolean;
  reduced: boolean;
  visible: boolean;
  compact: boolean;
  launchKey: number;
  /** Landing time of the flight (observation start), "YYYY-MM-DDTHH:MM:SSZ". */
  landingUtc?: string;
  /** Completed analysis used for post-landing sky checks and space weather. */
  analysisId?: string;
  condition: SiteCondition;
  siteStatus?: ReactNode;
  onSelect: (latitude_deg: number, longitude_deg_east: number) => void;
  onPreset: (site: Preset) => void;
  onSceneFailure: () => void;
  /** Called once each time the flight reaches a successful touchdown. */
  onArrived?: () => void;
  /** Rendered directly below the 3D viewport, e.g. the start-date strip. */
  timeline?: ReactNode;
  /** Short connection/job state shown in the panel header. */
  status?: ReactNode;
  /** Extra panel tabs after "Plan", e.g. results and analysis modules. */
  modules: StageModule[];
  activeModule: string;
  onModuleChange: (id: string) => void;
  /** Content of the "Plan" tab, above the flight simulation controls. */
  children: ReactNode;
}) {
  const { t, locale } = useTranslate();
  const [inspector, setInspector] = useState<"flight" | "surface" | "candidates" | null>(null);
  const [cleanView, setCleanView] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const navigate = (id: string) => { setInspector(null); onModuleChange(id); };
  const inspect = (view: "flight" | "surface" | "candidates") => { setInspector(view); setPanelOpen(true); };
  useEffect(() => {
    const sync = () => setFullscreen(document.fullscreenElement === stageRef.current);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);
  const [showHazards, setShowHazards] = useState(true);
  const [showSearch, setShowSearch] = useState(true);
  const [showRoutes, setShowRoutes] = useState(true);
  const [layersOpen, setLayersOpen] = useState(false);
  const [showCandidates, setShowCandidates] = useState(true);
  const {preview,setPreview,candidateId,setCandidateId} = useAnalysisView();
  const [previewProgress,setPreviewProgress] = useState(0);
  const [previewPlaying,setPreviewPlaying] = useState(false);
  useEffect(() => { setPreviewProgress(0); setPreviewPlaying(false); if(preview) setFlying(false); }, [preview?.kind, preview?.lines]);
  useEffect(() => {
    if(!previewPlaying || !visible || preview?.stale) return;
    const timer=setInterval(()=>setPreviewProgress(p=>{if(p>=1){setPreviewPlaying(false);return 1;}return Math.min(1,p+0.005);}),100);
    return ()=>clearInterval(timer);
  },[previewPlaying,visible,preview?.stale]);
  useEffect(() => {if(candidateId){setCameraMode("pole");setShowCandidates(true);}},[candidateId]);
  const inspected = candidates.find((c) => c.id === candidateId);
  const tr = locale.startsWith("tr");
  const lockedNotice = t("missionStage.locked");
  const phaseLabel = (id: string) => t(`missionPhases.${id}.label`);
  const stageRef = useRef<HTMLDivElement>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelWidth, setPanelWidth] = useState(() => {
    const stored = Number(localStorage.getItem(PANEL_WIDTH_STORAGE_KEY));
    return Number.isFinite(stored) && stored >= PANEL_MIN_WIDTH_PX
      ? stored
      : PANEL_DEFAULT_WIDTH_PX;
  });
  const resizePanel = (width_px: number) => {
    const container = stageRef.current?.clientWidth ?? window.innerWidth;
    const next = clampPanelWidth(width_px, container);
    setPanelWidth(next);
    localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(next));
  };
  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    const move = (e: PointerEvent) => {
      const right = stageRef.current?.getBoundingClientRect().right ?? window.innerWidth;
      resizePanel(right - e.clientX);
    };
    const stop = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", stop);
      handle.removeEventListener("pointercancel", stop);
      document.body.classList.remove("panel-resizing");
    };
    document.body.classList.add("panel-resizing");
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
  };
  const groupOf = (id: string) =>
    id === "plan" ? "plan" : (modules.find((m) => m.id === id)?.group ?? "plan");
  const activeGroup = inspector === "surface" || inspector === "candidates" ? "descent" : inspector === "flight" ? "plan" : groupOf(activeModule);
  const lastInGroup = useRef<Record<string, string>>({});
  if (!inspector) lastInGroup.current[activeGroup] = activeModule;
  const openGroup = (group: string) => {
    if (group === "plan") return navigate("plan");
    const remembered = lastInGroup.current[group];
    const target =
      modules.find((m) => m.id === remembered && m.group === group) ??
      modules.find((m) => m.group === group);
    if (target) navigate(target.id);
  };
  const steps = STEP_GROUPS.filter(
    (g) => g === "plan" || modules.some((m) => m.group === g),
  );
  const learn = modules.find((m) => m.group === "learn");
  const advanced = modules.filter((m) => m.group === "advanced");
  const siblings =
    activeGroup === "plan" ? [] : modules.filter((m) => m.group === activeGroup);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [progress, setProgress] = useState(0);
  const [flying, setFlying] = useState(false);
  const [cameraMode, setCameraMode] = useState<CameraMode>("auto");
  const [notice, setNotice] = useState("");
  const [focusMoon, setFocusMoon] = useState(false);
  const [rover, setRover] = useState<RoverState | null>(null);
  const path = useMemo(() => {
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90)
      return null;
    return buildTransferPath(lat, lon);
  }, [lat, lon]);
  const active = phaseAt(progress);
  const { flight, state: flightState } = useFlightEphemeris(landingUtc);
  const flightView = useMemo(() => {
    if (!path || !flight) return null;
    try {
      return liveFlightView(path, flight);
    } catch {
      return null;
    }
  }, [path, flight]);
  // Display-only readouts: backend positions converted back to km, and the
  // illustrative spacecraft's distances (labelled as such).
  const readout = (() => {
    if (!flightView) return null;
    const utc = flightView.utcAt(progress);
    if (utc === null) return null;
    const km = (a: readonly number[], b: readonly number[]) =>
      Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) * MOON_MEAN_RADIUS_KM;
    const earth = flightView.earthCenter(progress);
    const moon = flightView.moonPose(progress).position;
    const craft = flightView.positionAt(progress);
    const elapsed_s = Math.max(0, (utc - Date.parse(flight!.window.launch_utc)) / 1000);
    const days = Math.floor(elapsed_s / 86400);
    const clock = new Date((elapsed_s % 86400) * 1000).toISOString().slice(11, 19);
    return {
      utc: new Date(utc).toLocaleString(locale, {
        timeZone: "UTC",
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }),
      met: `T+ ${days} ${t("missionStage.flightData.days")} ${clock}`,
      earthMoon: Math.round(km(earth, moon)).toLocaleString(locale),
      craftEarth: Math.round(km(craft, earth)).toLocaleString(locale),
      craftMoon: Math.round(km(craft, moon)).toLocaleString(locale),
    };
  })();
  // Auto camera: with true-scale distances the wide Earth–Moon view only
  // suits the coast, so the spacecraft close-up covers Earth orbit and early
  // descent; lift-off opens on the launch close-up and the final descent and
  // touchdown cut to the landing close-up.
  const cameraView: CameraView =
    cameraMode !== "auto"
      ? cameraMode
      : progress === 0 && !flying
        ? focusMoon
          ? "pole"
          : "system"
        : progress < LAUNCH_CLOSEUP_END_PROGRESS
          ? "launch"
          : active.phase.id === "translunar_coast"
          ? "system"
          : active.phase.id === "lunar_orbit"
            ? "moon"
            : progress >= LANDING_CLOSEUP_START_PROGRESS
              ? "landing"
              : "spacecraft";
  const picking = cameraView === "pole" || cameraView === "moon";
  // Launch, space and landing are separate 3-D scenes: a short fade hides
  // the cut between them.
  const sceneFamily =
    cameraView === "launch" || cameraView === "landing" ? cameraView : "space";
  const [fadeKey, setFadeKey] = useState(0);
  const lastFamily = useRef(sceneFamily);
  useEffect(() => {
    if (lastFamily.current === sceneFamily) return;
    lastFamily.current = sceneFamily;
    if (!reduced) setFadeKey((key) => key + 1);
  }, [sceneFamily, reduced]);
  // Post-landing operations start once touchdown is complete and stay alive
  // across camera changes; the rover is drawn only in the landing close-up.
  // A mission run of this analysis decides how the touchdown ends; a crash or
  // tip-over leaves no surface operations.
  const missionRun = useMissionRun(analysisId);
  const touchdown = touchdownOutcome(missionRun);
  const arrived = active.phase.id === "landed" && progress >= 1;
  const landed = webgl && arrived && touchdown === "landed";
  const wasArrived = useRef(arrived);
  useEffect(() => {
    if (arrived && !wasArrived.current && touchdown === "landed") onArrived?.();
    wasArrived.current = arrived;
  }, [arrived]);
  const stageLabel = (id: string) =>
    id === "landed" && touchdown !== "landed"
      ? t(`missionRun.scene.${touchdown}`)
      : phaseLabel(id);

  // A new target restarts the illustrative flight from Earth orbit.
  useEffect(() => {
    setProgress(0);
    setFlying(false);
  }, [lat, lon]);
  useEffect(() => {
    if (!launchKey) return;
    setProgress(reduced ? 1 : 0);
    setFocusMoon(false);
    setFlying(!reduced);
  }, [launchKey]);
  useEffect(() => {
    if (!visible || reduced) setFlying(false);
  }, [visible, reduced]);
  useEffect(() => {
    if (!flying) return;
    const step = TICK_MS / 1000 / FLIGHT_PLAYBACK_S;
    const timer = setInterval(() => {
      setProgress((value) => {
        const next = Math.min(1, value + step);
        if (next >= 1) setFlying(false);
        return next;
      });
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [flying]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 6000);
    return () => clearTimeout(timer);
  }, [notice]);

  const pick = (selection: SiteSelection) => {
    if (!picking) {
      setFocusMoon(true);
      if (cameraMode !== "auto") setCameraMode("auto");
      if (!flying) setProgress(0);
      setNotice("");
      return;
    }
    if (busy) {
      setNotice(lockedNotice);
      return;
    }
    if (!selection.within_coverage) {
      setNotice(
        t("missionStage.outsideCoverage", {
          lat: selection.latitude_deg.toFixed(2),
          max: Math.abs(COVERAGE_MAX_LATITUDE_DEG),
        }),
      );
      return;
    }
    setNotice("");
    onSelect(selection.latitude_deg, selection.longitude_deg_east);
  };
  const toggleFlight = () => {
    if (reduced) {
      setProgress(1);
      return;
    }
    if (progress >= 1) setProgress(0);
    setFocusMoon(false);
    setFlying(!flying);
  };

  const fallback = (
    <div className="stage-fallback">
      <PolarMap
        lat={lat}
        lon={lon}
        sites={sites}
        onPreset={(site) => !busy && onPreset(site)}
        onSelect={(latitude, longitude) => !busy && onSelect(latitude, longitude)}
      />
      <p>{t("missionStage.needsWebgl")}</p>
    </div>
  );

  return (
    <div
      className="mission-stage"
      ref={stageRef}
      data-panel={panelOpen ? "open" : "closed"}
      style={{ "--panel-width": `${panelWidth}px` } as CSSProperties}
    >
      <div className="stage-main">
      <div
        className="stage-viewport"
        role="region"
        aria-label={t("missionStage.viewLabel")}
      >
        {fadeKey > 0 && <div key={fadeKey} className="stage-fade" aria-hidden="true" />}
        {webgl ? (
          <SceneBoundary fallback={fallback}>
            <Suspense fallback={<div className="stage-loading">{t("missionStage.loading")}</div>}>
              <MissionScene
                analysisPreview={preview}
                analysisProgress={previewProgress}
                onAnalysisSelect={id=>setPreview(p=>p?{...p,selected:id}:p)}
                showHazards={showHazards} showSearch={showSearch} showRoutes={showRoutes}
                candidates={showCandidates ? candidates : []}
                selectedCandidate={candidateId}
                onCandidate={(id) => { setCandidateId(id); inspect("candidates"); }}
                path={path}
                lat={lat}
                lon={lon}
                sites={sites}
                progressValue={progress}
                cameraMode={cameraView}
                condition={condition}
                reduced={reduced}
                selectable={!busy && picking}
                visible={visible}
                animate={visible && !reduced}
                compact={compact}
                onPick={pick}
                onPreset={(site) => {
                  if (!picking) setFocusMoon(true);
                  if (busy) setNotice(lockedNotice);
                  else onPreset(site);
                }}
                onFailure={onSceneFailure}
                flight={flight}
                roverPath={landed ? (rover?.path ?? null) : null}
                roverDrive={rover?.drive ?? 0}
                roverScan={rover?.scan ?? 1}
                roverScene={landed ? (rover?.scene ?? null) : null}
                roverTarget={rover?.targetId ?? ""}
                roverAlternatives={rover?.alternatives}
                touchdown={touchdown}
              />
            </Suspense>
          </SceneBoundary>
        ) : (
          fallback
        )}
        {!preview && !cleanView && <button aria-live="polite" className="stage-hud stage-phase phase-chip" onClick={() => inspect("flight")}>
          <span className="eyebrow">{t("missionStage.phase", { n: active.index + 1, total: MISSION_PHASES.length })}</span>
          <strong>{progress === 0 && !flying ? t("missionStage.preLaunch") : stageLabel(active.phase.id)}</strong>
          <span aria-hidden="true">↗</span>
        </button>}
        <div className="stage-hud scene-toolbar">
          {preview && <button onClick={()=>{setPreview(null);setPreviewPlaying(false);}}>{t("workspace.backScene")}</button>}
          {webgl && !cleanView && !preview && <label className="camera-picker">
            <span className="sr-only">{t("missionStage.cameraView")}</span>
            <select aria-label={t("missionStage.cameraView")} value={cameraMode} onChange={e => setCameraMode(e.target.value as CameraMode)}>
              {cameraModes.map(mode => <option key={mode} value={mode}>{t(`missionStage.camera.${mode}`)}</option>)}
            </select>
          </label>}
          {!preview && !cleanView && <div className="layer-picker">
            <button aria-expanded={layersOpen} onClick={() => setLayersOpen(!layersOpen)}>{tr ? "Katmanlar" : "Layers"}</button>
            {layersOpen && <div className="layer-options" onKeyDown={e => { if (e.key === "Escape") setLayersOpen(false); }}>
              <label><input type="checkbox" checked={showHazards} onChange={e => setShowHazards(e.target.checked)} />{tr ? "Tehlike bölgeleri" : "Hazards"}</label>
              <label><input type="checkbox" checked={showSearch} onChange={e => setShowSearch(e.target.checked)} />{tr ? "Tarama alanı" : "Scan coverage"}</label>
              <label><input type="checkbox" checked={showRoutes} onChange={e => setShowRoutes(e.target.checked)} />{tr ? "Rover rotaları" : "Rover routes"}</label>
              <label><input type="checkbox" checked={showCandidates} onChange={e => setShowCandidates(e.target.checked)} />{tr ? "İniş adayları" : "Landing candidates"}</label>
              <button onClick={() => setLayersOpen(false)}>{tr ? "Kapat" : "Close"}</button>
            </div>}
          </div>}
          <button aria-pressed={cleanView} onClick={() => setCleanView(!cleanView)}>{tr ? (cleanView ? "Kontroller" : "Sade görünüm") : (cleanView ? "Controls" : "Clean view")}</button>
          <button aria-label={tr ? "Tam ekranı değiştir" : "Toggle fullscreen"} aria-pressed={fullscreen} onClick={async () => {
            try {
              if (document.fullscreenElement) await document.exitFullscreen();
              else await stageRef.current?.requestFullscreen();
            } catch { setNotice(tr ? "Bu tarayıcıda tam ekran açılamadı." : "Fullscreen is unavailable in this browser."); }
          }}>⛶</button>
        </div>
        {preview && !cleanView && <div className="stage-hud analysis-caption">
          <strong>{t(`workspace.kind.${preview.kind}`)}</strong>
          <p>{t(preview.stale ? "workspace.stale" : "workspace.localFrame")}</p>
          {preview.lines.map(line=><button key={line.id} aria-pressed={preview.selected===line.id} onClick={()=>setPreview({...preview,selected:line.id})}><i style={{background:line.color}}/>{t(`workspace.line.${line.id}`) === `workspace.line.${line.id}` ? line.id : t(`workspace.line.${line.id}`)}</button>)}
          {preview.selected && <small>{t("workspace.selected")}: {preview.selected}</small>}
          {!preview.lines.length && <p>{t("workspace.dots")}</p>}
          {!!preview.events.length && <details><summary>{t("workspace.events")}</summary>{preview.events.map((time,i)=><button key={i} disabled={preview.stale} onClick={()=>{const pts=preview.lines.at(-1)?.points??[];const start=pts[0]?.time??0;const end=pts.at(-1)?.time??1;setPreviewPlaying(false);setPreviewProgress(Math.max(0,Math.min(1,(time-start)/Math.max(1e-9,end-start))));}}>{time.toFixed(1)} s</button>)}</details>}
        </div>}
        {preview && !cleanView && preview.lines.some(l=>l.points.some(p=>p.time!==undefined)) && <div className="stage-hud scene-dock">
          <button disabled={preview.stale} onClick={()=>{if(previewProgress>=1)setPreviewProgress(0);setPreviewPlaying(!previewPlaying);}} aria-label={t("workspace.playResult")}>{previewPlaying?"Ⅱ":"▷"}</button>
          <input type="range" aria-label={t("workspace.resultTime")} min="0" max="1000" value={Math.round(previewProgress*1000)} disabled={preview.stale} onChange={e=>{setPreviewPlaying(false);setPreviewProgress(Number(e.target.value)/1000);}}/>
          <span>{Math.round(previewProgress*100)}%</span>
        </div>}
        {!preview && !cleanView && missionRun && arrived && (
          <button
            className={`stage-hud mission-outcome run-${missionRun.outcome}`}
            onClick={() => onModuleChange("mission-run")}
          >
            <strong>{t(`missionRun.outcome.${missionRun.outcome}`)}</strong>
            {missionRun.failure && (
              <span>
                {t(`missionRun.gates.${missionRun.failure.gate}`)} ·{" "}
                {t(`missionRun.events.${missionRun.failure.code}`)}
              </span>
            )}
          </button>
        )}
        {!preview && !cleanView && <div className="scene-source">{landed && rover?.source ? (rover.source === "demo" ? "DEMO · A*" : "LOLA DEM · A*") : t("missionStage.illustrative")}</div>}
        {!preview && !cleanView && <div className="stage-hud scene-dock">
          <button disabled={!path} onClick={toggleFlight} aria-label={flying ? t("missionStage.pauseFlight") : t("missionStage.simulate")}>{flying ? "Ⅱ" : "▷"}</button>
          <input type="range" min="0" max="1000" value={Math.round(progress * 1000)} disabled={!path}
            aria-label={t("missionStage.progress")} aria-valuetext={`${phaseLabel(active.phase.id)}, ${Math.round(progress * 100)}%`}
            onChange={e => { setFlying(false); setProgress(Number(e.target.value) / 1000); }} />
          <span>{Math.round(progress * 100)}%</span>
          {landed && <button onClick={() => inspect("surface")}>{tr ? "Keşif aracı" : "Rover"} ↗</button>}
        </div>}
        {notice && (
          <div className="stage-notice" role="status">
            {notice}
          </div>
        )}
      </div>
      {timeline}
      </div>
      {!panelOpen && (
        <button
          type="button"
          className="panel-reopen"
          onClick={() => setPanelOpen(true)}
          aria-label={t("missionStage.showPanel")}
        >
          ‹ {t("missionStage.panel")}
        </button>
      )}
      <aside
        className="stage-controls"
        hidden={!panelOpen}
        aria-label={t("missionStage.panelLabel")}
      >
        <div
          className="panel-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label={t("missionStage.resize")}
          aria-valuemin={PANEL_MIN_WIDTH_PX}
          aria-valuenow={panelWidth}
          tabIndex={0}
          onPointerDown={startResize}
          onDoubleClick={() => resizePanel(PANEL_DEFAULT_WIDTH_PX)}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") resizePanel(panelWidth + PANEL_KEY_STEP_PX);
            else if (e.key === "ArrowRight") resizePanel(panelWidth - PANEL_KEY_STEP_PX);
            else return;
            e.preventDefault();
          }}
        />
        <nav className="panel-bar menu-bar" aria-label={t("missionStage.modules")}>
          <div className="menu-top">
            <span className="menu-title">{t("missionStage.menuTitle")}</span>
            <div className="panel-bar-end">
              {status}
              <button
                type="button"
                className="panel-collapse"
                onClick={() => setPanelOpen(false)}
                aria-label={t("missionStage.hidePanel")}
              >
                ›
              </button>
            </div>
          </div>
          <div className="step-tabs" role="tablist" aria-label={t("missionStage.modules")}>
            {steps.map((group, index) => (
              <button
                key={group}
                type="button"
                role="tab"
                id={`step-${group}`}
                aria-selected={activeGroup === group}
                onClick={() => {
                  setAdvancedOpen(false);
                  openGroup(group);
                }}
              >
                <b>{index + 1}</b>
                <span>{t(`missionStage.steps.${group}`)}</span>
              </button>
            ))}
          </div>
          {(learn || advanced.length > 0) && (
            <div className="menu-extra">
              {learn && (
                <button
                  type="button"
                  id="step-learn"
                  aria-pressed={activeGroup === "learn"}
                  onClick={() => {
                    setAdvancedOpen(false);
                    navigate(learn.id);
                  }}
                >
                  <span aria-hidden="true">✦</span> {t("missionStage.learn")}
                </button>
              )}
              {advanced.length > 0 && (
                <div className="advanced-menu">
                  <button
                    type="button"
                    aria-expanded={advancedOpen}
                    aria-haspopup="menu"
                    data-active={activeGroup === "advanced" || undefined}
                    onClick={() => setAdvancedOpen((open) => !open)}
                  >
                    <span aria-hidden="true">⚙</span> {t("missionStage.advanced")}
                    {activeGroup === "advanced"
                      ? `: ${advanced.find((m) => m.id === activeModule)?.label ?? ""}`
                      : ""}{" "}
                    <span aria-hidden="true">{advancedOpen ? "▴" : "▾"}</span>
                  </button>
                  {advancedOpen && (
                    <div className="advanced-list" role="menu">
                      <p>{t("missionStage.advancedHint")}</p>
                      {advanced.map((m) => (
                        <button
                          key={m.id}
                          type="button"
                          role="menuitem"
                          aria-current={activeModule === m.id || undefined}
                          onClick={() => {
                            setAdvancedOpen(false);
                            navigate(m.id);
                          }}
                        >
                          {m.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </nav>
        <details className="scene-inspector-tools"><summary>{tr ? "Sahne ayrıntıları" : "Scene details"}</summary><div className="scene-inspector-nav" role="group" aria-label={tr ? "Sahne ayrıntıları" : "Scene details"}>
          <button aria-pressed={inspector === "flight"} onClick={() => inspect("flight")}>{tr ? "Uçuş bilgisi" : "Flight details"}</button>
          {landed && <button aria-pressed={inspector === "surface"} onClick={() => inspect("surface")}>{tr ? "Yüzey operasyonları" : "Surface operations"}</button>}
          <button aria-pressed={inspector === "candidates"} onClick={() => inspect("candidates")}>{tr ? "Adaylar" : "Candidates"} · {candidates.length}</button>
          {inspector && <button onClick={() => setInspector(null)}>{tr ? "Göreve dön" : "Back to task"} ↩</button>}
        </div>
        </details>
        <div className="panel-body scene-inspector" hidden={inspector !== "flight"}>
        <div className="scene-information" aria-live="polite">
          {progress === 0 && !flying ? (
            <>
              <span className="eyebrow">{t("missionStage.preLaunch")}</span>
              <strong>{t("missionStage.chooseSite")}</strong>
              <p>{t("missionStage.chooseSiteHelp")}</p>
            </>
          ) : (
            <>
              <span className="eyebrow">
                {t("missionStage.phase", {
                  n: active.index + 1,
                  total: MISSION_PHASES.length,
                })}
              </span>
              <strong>{phaseLabel(active.phase.id)}</strong>
              <p>{t(`missionPhases.${active.phase.id}.description`)}</p>
            </>
          )}
          {readout ? (
            <dl className="flight-readout">
              <dt>{t("missionStage.flightData.utc")}</dt>
              <dd>{readout.utc} UTC</dd>
              <dt>{t("missionStage.flightData.met")}</dt>
              <dd>{readout.met}</dd>
              <dt>{t("missionStage.flightData.earthMoon")}</dt>
              <dd>{readout.earthMoon} km</dd>
              <dt>{t("missionStage.flightData.craftEarth")}</dt>
              <dd>{readout.craftEarth} km</dd>
              <dt>{t("missionStage.flightData.craftMoon")}</dt>
              <dd>{readout.craftMoon} km</dd>
            </dl>
          ) : (
            flightState !== "off" &&
            flightState !== "ready" && (
              <small className="flight-note">
                {t(`missionStage.flightData.${flightState}`)}
              </small>
            )
          )}
        </div>
        <div className="scene-legend">
          <span>
            <i className="legend-sun" /> {t("common.sun")}
          </span>
          <span>
            <i className="legend-earth" /> {t("common.earth")}
          </span>
          <span>
            <i className="legend-trail" /> {t("missionStage.flownPath")}
          </span>
          <span>
            <i className="legend-ring" /> {t("missionStage.coverage")}
          </span>
          <small>{t("missionStage.scaleNote")}</small>
        </div>
        <div className="scene-target">
          <span className="eyebrow">{t("missionStage.target")}</span>
          <strong>{coordinateLabel(t, lat, lon)}</strong>
          {siteStatus && <div className="stage-status">{siteStatus}</div>}
          {webgl && !busy && (
            <small>
              {picking
                ? t("missionStage.hint.picking")
                : cameraView === "landing"
                  ? t("missionStage.hint.landing")
                  : cameraView === "launch"
                    ? t("missionStage.hint.launch")
                  : t("missionStage.hint.default")}
            </small>
          )}
        </div>
        </div>
        <div className="panel-body scene-inspector" hidden={inspector !== "candidates"}>
        {candidates.length > 0 && <details className="mission-candidates" open>
          <summary>{tr ? "Rota ve iniş adayları" : "Route & landing candidates"} · {candidates.length}</summary>
          <label><input type="checkbox" checked={showCandidates} onChange={e => setShowCandidates(e.target.checked)} /> {tr ? "3D üzerinde göster" : "Show in 3D"}</label>
          <p>{tr ? "Çizgiler temsili yaklaşım koridorlarıdır. Skor ve gerekçeler API sonuçlarıdır; hesaplanmamış skor — gösterilir." : "Lines are illustrative approach corridors. Scores and reasons come from the API; missing scores appear as —."}</p>
          <div className="candidate-list">{candidates.map(c => <button key={c.id} aria-pressed={candidateId === c.id} onClick={() => { setCandidateId(c.id); setShowCandidates(true); setCameraMode("pole"); }} style={{ borderLeftColor: candidateColor(c) }}>
            <span>{c.label}</span><b>{c.score?.toFixed(3) ?? "—"}</b><small>{tr ? ({accepted:"Uygun",rejected:"Elendi",failed:"Hata",unknown:"Veri yetersiz"})[c.status] : c.status}</small>
          </button>)}</div>
          {inspected && <article><strong>{inspected.label}</strong><p>{inspected.source} · {inspected.lat.toFixed(4)}°, {inspected.lon.toFixed(4)}°</p><p>{inspected.explanation}</p><dl>{inspected.metrics.map((m, i) => <div key={i}><dt>{tr ? ({Orbit:"Yörünge",Propellant:"Yakıt", "Reserve margin":"Rezerv marjı",Assessment:"Değerlendirme"} as Record<string,string>)[m.label] ?? m.label : m.label}</dt><dd>{m.value}</dd></div>)}</dl></article>}
        </details>}
          {!candidates.length && <p>{tr ? "Rota ve iniş adayları için Karşılaştır ve öner veya İniş ve yüzey adımında bir hesaplama çalıştırın." : "Run recommendations or descent planning to see route and landing candidates."}</p>}
        </div>
        <div className="panel-body scene-inspector" hidden={inspector !== "surface"}>
        {landed && (
          <SceneBoundary fallback={null}>
            <SurfaceOps
              lat={lat}
              lon={lon}
              analysisId={analysisId}
              reduced={reduced}
              onRover={setRover}
            />
          </SceneBoundary>
        )}
          {!landed && <p>{arrived && touchdown !== "landed" ? (tr ? "Araç inişte kaybedildi; yüzey operasyonları yapılamaz." : "The vehicle was lost at touchdown; no surface operations are possible.") : (tr ? "Yüzey operasyonları inişten sonra açılır." : "Surface operations become available after landing.")}</p>}
        </div>
        <div
          className="panel-body"
          role="tabpanel"
          id="tabpanel-plan"
          aria-labelledby="step-plan"
          hidden={inspector !== null || activeModule !== "plan"}
        >
        {children}
        <section className="panel flight-panel" aria-labelledby="flight-title">
          <div className="panel-heading">
            <span id="flight-title">{t("missionStage.flight")}</span>
            <small>{t("missionStage.illustrative")}</small>
          </div>
          <div className="flight-body">
            <div className="flight-actions">
              <button
                type="button"
                className="primary"
                onClick={toggleFlight}
                disabled={!path}
              >
                {reduced
                  ? t("missionStage.jump")
                  : flying
                    ? t("missionStage.pauseFlight")
                    : progress >= 1
                      ? t("missionStage.flyAgain")
                      : progress > 0
                        ? t("missionStage.resume")
                        : t("missionStage.simulate")}{" "}
                <span>{flying ? "Ⅱ" : "▷"}</span>
              </button>
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  setFlying(false);
                  setProgress(0);
                }}
                disabled={progress === 0}
              >
                {t("missionStage.restart")}
              </button>
            </div>
            <input
              type="range"
              aria-label={t("missionStage.progress")}
              aria-valuetext={`${phaseLabel(active.phase.id)}, ${Math.round(progress * 100)}%`}
              min="0"
              max="1000"
              value={Math.round(progress * 1000)}
              disabled={!path}
              onChange={(e) => {
                setFlying(false);
                setProgress(Number(e.target.value) / 1000);
              }}
            />
            <ol className="phase-list">
              {MISSION_PHASES.map((phase, i) => (
                <li
                  key={phase.id}
                  className={
                    i < active.index ? "done" : i === active.index ? "active" : ""
                  }
                  aria-current={i === active.index ? "step" : undefined}
                >
                  <button
                    type="button"
                    onClick={() => {
                      setFlying(false);
                      setProgress(phase.id === "landed" ? 1 : phase.start_progress);
                    }}
                    disabled={!path}
                  >
                    <b>{String(i + 1).padStart(2, "0")}</b> {phaseLabel(phase.id)}
                  </button>
                </li>
              ))}
            </ol>
            <p className="field-help">{t("missionStage.phasesNote")}</p>
          </div>
        </section>
        </div>
        {!inspector && siblings.length > 1 && (
          <div
            className="sub-tabs"
            role="tablist"
            aria-label={
              activeGroup === "advanced"
                ? t("missionStage.advanced")
                : t(`missionStage.steps.${activeGroup}`)
            }
          >
            {siblings.map((m) => (
              <button
                key={m.id}
                type="button"
                role="tab"
                id={`tab-${m.id}`}
                aria-selected={activeModule === m.id}
                aria-controls={`tabpanel-${m.id}`}
                onClick={() => navigate(m.id)}
              >
                {m.label}
              </button>
            ))}
          </div>
        )}
        {modules.map((module) => (
          <div
            key={module.id}
            className="panel-body"
            role="tabpanel"
            id={`tabpanel-${module.id}`}
            aria-label={module.label}
            hidden={inspector !== null || activeModule !== module.id}
          >
            {module.content}
          </div>
        ))}
      </aside>
    </div>
  );
}
