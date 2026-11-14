import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import PolarMap from "./PolarMap";
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

const MissionScene = lazy(() => import("./MissionScene"));
/** Wall-clock length of one illustrative flight playback. */
const FLIGHT_PLAYBACK_S = 45;
const TICK_MS = 50;
const lockedNotice =
  "An analysis is running. The landing site is locked until it finishes.";

const cameraModes: { id: CameraMode; label: string }[] = [
  { id: "auto", label: "Auto" },
  { id: "system", label: "Earth–Moon" },
  { id: "moon", label: "Moon orbit" },
  { id: "pole", label: "South pole" },
  { id: "spacecraft", label: "Spacecraft" },
  { id: "launch", label: "Launch" },
  { id: "landing", label: "Landing" },
];

const coordinateLabel = (lat: number, lon: number) =>
  Number.isFinite(lat) && Number.isFinite(lon)
    ? `${Math.abs(lat).toFixed(5)}° ${lat < 0 ? "S" : "N"} · ${lon.toFixed(5)}° E`
    : "Enter valid coordinates";

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
  condition,
  siteStatus,
  onSelect,
  onPreset,
  onSceneFailure,
  timeline,
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
  condition: SiteCondition;
  siteStatus?: ReactNode;
  onSelect: (latitude_deg: number, longitude_deg_east: number) => void;
  onPreset: (site: Preset) => void;
  onSceneFailure: () => void;
  /** Rendered directly below the 3D viewport, e.g. the start-date strip. */
  timeline?: ReactNode;
  children: ReactNode;
}) {
  const [progress, setProgress] = useState(0);
  const [flying, setFlying] = useState(false);
  const [cameraMode, setCameraMode] = useState<CameraMode>("auto");
  const [notice, setNotice] = useState("");
  const [focusMoon, setFocusMoon] = useState(false);
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
        `That point is at ${selection.latitude_deg.toFixed(2)}°. Cached terrain covers ${Math.abs(COVERAGE_MAX_LATITUDE_DEG)}–90° S only: click inside the dashed ring.`,
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
      <p>
        The 3D view needs WebGL. Choose the site on this polar grid
        (80–90° S) or enter coordinates.
      </p>
    </div>
  );

  return (
    <div className="mission-stage">
      <div className="stage-main">
      <div
        className="stage-viewport"
        role="region"
        aria-label="Earth to Moon flight view"
      >
        {webgl ? (
          <SceneBoundary fallback={fallback}>
            <Suspense fallback={<div className="stage-loading">Loading scene…</div>}>
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
              />
            </Suspense>
          </SceneBoundary>
        ) : (
          fallback
        )}
        <div className="stage-hud stage-phase" aria-live="polite">
          {progress === 0 && !flying ? (
            <>
              <span className="eyebrow">PRE-LAUNCH</span>
              <strong>Choose a landing site</strong>
              <p>
                Click the Moon to zoom to its south pole and pick a point inside
                the dashed ring, then press Simulate flight or Run analysis.
              </p>
            </>
          ) : (
            <>
              <span className="eyebrow">
                PHASE {active.index + 1}/{MISSION_PHASES.length}
              </span>
              <strong>{active.phase.label}</strong>
              <p>{active.phase.description}</p>
            </>
          )}
        </div>
        {webgl && (
          <div className="stage-hud stage-cameras" role="group" aria-label="Camera view">
            {cameraModes.map((mode) => (
              <button
                key={mode.id}
                type="button"
                aria-pressed={cameraMode === mode.id}
                onClick={() => setCameraMode(mode.id)}
              >
                {mode.label}
              </button>
            ))}
          </div>
        )}
        <div className="stage-hud stage-legend">
          <span>
            <i className="legend-sun" /> Sun
          </span>
          <span>
            <i className="legend-earth" /> Earth
          </span>
          <span>
            <i className="legend-trail" /> Flown path
          </span>
          <span>
            <i className="legend-ring" /> Terrain coverage 80–90° S
          </span>
          <small>Earth, Moon &amp; distances to scale · spacecraft enlarged · not a computed trajectory</small>
        </div>
        <div className="stage-hud stage-target">
          <span className="eyebrow">TARGET SITE</span>
          <strong>{coordinateLabel(lat, lon)}</strong>
          {siteStatus && <div className="stage-status">{siteStatus}</div>}
          {webgl && !busy && (
            <small>
              {picking
                ? "Click inside the dashed ring to choose · drag to rotate · scroll to zoom"
                : cameraView === "landing"
                  ? "Illustrative landing close-up · drag to rotate · use South pole to choose another site"
                  : cameraView === "launch"
                    ? "Illustrative launch close-up · drag to rotate · scroll to zoom"
                  : "Click the Moon to zoom in and choose a site · drag to rotate"}
            </small>
          )}
        </div>
        {notice && (
          <div className="stage-notice" role="status">
            {notice}
          </div>
        )}
      </div>
      {timeline}
      </div>
      <aside className="stage-controls">
        {children}
        <section className="panel flight-panel" aria-labelledby="flight-title">
          <div className="panel-heading">
            <span id="flight-title">FLIGHT SIMULATION</span>
            <small>Illustrative</small>
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
                  ? "Jump to landing"
                  : flying
                    ? "Pause flight"
                    : progress >= 1
                      ? "Fly again"
                      : progress > 0
                        ? "Resume flight"
                        : "Simulate flight"}{" "}
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
                Restart
              </button>
            </div>
            <input
              type="range"
              aria-label="Mission progress"
              aria-valuetext={`${active.phase.label}, ${Math.round(progress * 100)}%`}
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
                    <b>{String(i + 1).padStart(2, "0")}</b> {phase.label}
                  </button>
                </li>
              ))}
            </ol>
            <p className="field-help">
              Shows the order of mission phases only. Sun and Earth visibility
              at the site come from the analysis below.
            </p>
          </div>
        </section>
      </aside>
    </div>
  );
}
