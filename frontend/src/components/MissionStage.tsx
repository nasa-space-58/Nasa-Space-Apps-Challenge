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
import PolarMap from "./PolarMap";
import SurfaceOps from "./SurfaceOps";
import type { RoutePoint } from "./Rover";
import type { Preset } from "../types";
import {
  COVERAGE_MAX_LATITUDE_DEG,
  LANDING_CLOSEUP_START_PROGRESS,
  LAUNCH_CLOSEUP_END_PROGRESS,
  MISSION_PHASES,
  buildTransferPath,
  phaseAt,
  type SiteSelection,
} from "../missionPath";
import type { CameraMode, CameraView, SiteCondition } from "./MissionScene";
import { useTranslate, type Translate } from "../i18n";

const MissionScene = lazy(() => import("./MissionScene"));
/** Wall-clock length of one illustrative flight playback. */
const FLIGHT_PLAYBACK_S = 45;
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

export type StageModule = { id: string; label: string; content: ReactNode };

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
  busy,
  webgl,
  reduced,
  visible,
  compact,
  launchKey,
  analysisId,
  condition,
  siteStatus,
  onSelect,
  onPreset,
  onSceneFailure,
  timeline,
  status,
  modules,
  activeModule,
  onModuleChange,
  children,
}: {
  lat: number;
  lon: number;
  sites: Preset[];
  busy: boolean;
  webgl: boolean;
  reduced: boolean;
  visible: boolean;
  compact: boolean;
  launchKey: number;
  /** Completed analysis used for post-landing sky checks and space weather. */
  analysisId?: string;
  condition: SiteCondition;
  siteStatus?: ReactNode;
  onSelect: (latitude_deg: number, longitude_deg_east: number) => void;
  onPreset: (site: Preset) => void;
  onSceneFailure: () => void;
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
  const { t } = useTranslate();
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
  const tabs = [{ id: "plan", label: t("missionStage.plan") }, ...modules];
  const [progress, setProgress] = useState(0);
  const [flying, setFlying] = useState(false);
  const [cameraMode, setCameraMode] = useState<CameraMode>("auto");
  const [notice, setNotice] = useState("");
  const [focusMoon, setFocusMoon] = useState(false);
  const [rover, setRover] = useState<{ path: RoutePoint[]; drive: number } | null>(
    null,
  );
  const path = useMemo(() => {
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90)
      return null;
    return buildTransferPath(lat, lon);
  }, [lat, lon]);
  const active = phaseAt(progress);
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
  // Post-landing operations start once the touchdown close-up is complete.
  const landed =
    webgl && cameraView === "landing" && active.phase.id === "landed" && progress >= 1;

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
        {webgl ? (
          <SceneBoundary fallback={fallback}>
            <Suspense fallback={<div className="stage-loading">{t("missionStage.loading")}</div>}>
              <MissionScene
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
                roverPath={landed ? (rover?.path ?? null) : null}
                roverDrive={rover?.drive ?? 0}
              />
            </Suspense>
          </SceneBoundary>
        ) : (
          fallback
        )}
        <div className="stage-hud stage-phase" aria-live="polite">
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
        </div>
        {webgl && (
          <div className="stage-hud stage-cameras" role="group" aria-label={t("missionStage.cameraView")}>
            {cameraModes.map((mode) => (
              <button
                key={mode}
                type="button"
                aria-pressed={cameraMode === mode}
                onClick={() => setCameraMode(mode)}
              >
                {t(`missionStage.camera.${mode}`)}
              </button>
            ))}
          </div>
        )}
        <div className="stage-hud stage-legend">
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
        <div className="stage-hud stage-target">
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
        <div className="panel-bar">
          <div className="panel-tabs" role="tablist" aria-label={t("missionStage.modules")}>
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                id={`tab-${tab.id}`}
                aria-selected={activeModule === tab.id}
                aria-controls={`tabpanel-${tab.id}`}
                onClick={() => onModuleChange(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
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
        <div
          className="panel-body"
          role="tabpanel"
          id="tabpanel-plan"
          aria-labelledby="tab-plan"
          hidden={activeModule !== "plan"}
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
        {modules.map((module) => (
          <div
            key={module.id}
            className="panel-body"
            role="tabpanel"
            id={`tabpanel-${module.id}`}
            aria-labelledby={`tab-${module.id}`}
            hidden={activeModule !== module.id}
          >
            {module.content}
          </div>
        ))}
      </aside>
    </div>
  );
}
