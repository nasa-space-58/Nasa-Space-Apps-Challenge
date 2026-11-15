import { Component, lazy, Suspense, type ReactNode } from "react";
import "./results-live.css";
import "./split-simulator.css";
import type { Frame, Result } from "../types";
import { useTranslate } from "../i18n";
import { tallyUntil, visibilityOf } from "../resultProgress";
import {
  PLAYBACK_RATES,
  SKY_ELEVATION_DISPLAY_FACTOR,
  setComparisonIndex,
  setComparisonPlaying,
  setComparisonSpeed,
  type ComparisonPlayback,
} from "../comparisonPlayback";

const SplitSky = lazy(() => import("./SplitSky"));
const MARK = { visible: "●", blocked: "○", unknown: "◌" } as const;

class PaneBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function Pane({
  label,
  result,
  frame,
  elapsed_s,
  age_s,
  webgl,
  visible,
  animate,
  compact,
}: {
  label: "A" | "B";
  result: Result;
  frame?: Frame;
  elapsed_s: number;
  age_s: number | null;
  webgl: boolean;
  visible: boolean;
  animate: boolean;
  compact: boolean;
}) {
  const { t, locale } = useTranslate();
  const request = result.analysis.request;
  const start = Date.parse(request.start_utc),
    end = Date.parse(request.end_utc);
  const now = Math.min(end, start + elapsed_s * 1000);
  const clock = new Date(now).toLocaleString(locale, {
    timeZone: "UTC",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const sun = visibilityOf(frame?.sun.state),
    earth = visibilityOf(frame?.earth.state);
  const h = (seconds: number) => t("units.hours", { value: (seconds / 3600).toFixed(1) });
  const sunSoFar = tallyUntil(result.analysis.sun.intervals, start, now).visible_s;
  const earthSoFar = tallyUntil(result.analysis.earth.intervals, start, now).visible_s;
  const fallback = (
    <div className="split-fallback">
      <p>{t("splitSim.noScene")}</p>
    </div>
  );
  const elevation = (value: number | null | undefined) =>
    value === null || value === undefined ? "—" : `${value.toFixed(2)}°`;
  return (
    <div className={`split-pane pane-${label.toLowerCase()} sun-${sun}`}>
      {webgl ? (
        <PaneBoundary fallback={fallback}>
          <Suspense fallback={<div className="split-fallback">{t("missionStage.loading")}</div>}>
            <SplitSky
              result={result}
              frame={frame}
              label={t("splitSim.paneLabel", { label })}
              visible={visible}
              animate={animate}
              compact={compact}
            />
          </Suspense>
        </PaneBoundary>
      ) : (
        fallback
      )}
      <div className="split-hud" aria-live="polite">
        <div className="split-title">
          <b>{label}</b>
          <span>{request.site.site_id.replaceAll("-", " ")}</span>
        </div>
        <strong className="split-clock">{clock} UTC</strong>
        <small>
          {request.site.latitude_deg.toFixed(3)}° · {request.site.longitude_deg_east.toFixed(3)}°E
        </small>
        <div className="split-chips">
          <span className={`now-chip sun ${sun}`}>
            {MARK[sun]} {t(`liveResults.state.sun.${sun}`)} · {elevation(frame?.sun.elevation_deg)}
          </span>
          <span className={`now-chip earth ${earth}`}>
            {MARK[earth]} {t(`liveResults.state.earth.${earth}`)} · {elevation(frame?.earth.elevation_deg)}
          </span>
        </div>
        <small className="split-sofar">
          <i className="sun-dot" /> {h(sunSoFar)} · <i className="earth-dot" /> {h(earthSoFar)}{" "}
          {t("splitSim.soFar")}
        </small>
        {!frame ? (
          <small className="split-age">{t("comparison.noSample")}</small>
        ) : age_s ? (
          <small className="split-age">{t("comparison.beforeCursor", { hours: h(age_s) })}</small>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Two synchronized local-sky views of a saved A/B pair. Both scenarios
 * advance by the same elapsed time from their own start, so each pane plays
 * its own day; the panes only draw backend samples at the shared cursor.
 */
export default function SplitSimulator({
  playback,
  webgl,
  visible,
  reduced,
  compact,
}: {
  playback: ComparisonPlayback;
  webgl: boolean;
  visible: boolean;
  reduced: boolean;
  compact: boolean;
}) {
  const { t } = useTranslate();
  const { a, b, cursor, index, playing, speed } = playback;
  const point = cursor[index];
  if (!point) return null;
  const last = cursor.length - 1;
  const hours = (seconds: number) => t("units.hours", { value: (seconds / 3600).toFixed(1) });
  const aFrame = point.a_index === null ? undefined : a.frames[point.a_index];
  const bFrame = point.b_index === null ? undefined : b.frames[point.b_index];
  const animate = visible && !reduced;
  const differs =
    visibilityOf(aFrame?.sun.state) !== visibilityOf(bFrame?.sun.state) ||
    visibilityOf(aFrame?.earth.state) !== visibilityOf(bFrame?.earth.state);
  return (
    <div className="split-simulator" role="region" aria-label={t("splitSim.label")}>
      <div className="split-panes">
        <Pane
          label="A"
          result={a}
          frame={aFrame}
          elapsed_s={point.elapsed_s}
          age_s={point.a_sample_age_s}
          webgl={webgl}
          visible={visible}
          animate={animate}
          compact={compact}
        />
        <Pane
          label="B"
          result={b}
          frame={bFrame}
          elapsed_s={point.elapsed_s}
          age_s={point.b_sample_age_s}
          webgl={webgl}
          visible={visible}
          animate={animate}
          compact={compact}
        />
      </div>
      <div className={`split-diff ${differs ? "differs" : "same"}`} role="status">
        {differs ? t("comparisonLive.differs") : t("comparisonLive.same")}
      </div>
      <div className="stage-hud scene-dock split-dock">
        <button
          aria-label={playing ? t("comparison.pause") : t("comparison.play")}
          disabled={reduced && !playing && index >= last}
          onClick={() => {
            if (reduced) {
              setComparisonIndex(last);
              return;
            }
            if (index >= last) setComparisonIndex(0);
            setComparisonPlaying(!playing);
          }}
        >
          {playing ? "Ⅱ" : "▷"}
        </button>
        <input
          type="range"
          min="0"
          max={last}
          value={index}
          aria-label={t("comparison.time")}
          aria-valuetext={t("comparison.sinceStart", { hours: hours(point.elapsed_s) })}
          onChange={(e) => {
            setComparisonPlaying(false);
            setComparisonIndex(Number(e.target.value));
          }}
        />
        <span>+{hours(point.elapsed_s)}</span>
        <div className="split-speed" role="group" aria-label={t("splitSim.speed")}>
          {PLAYBACK_RATES.map((rate) => (
            <button key={rate} aria-pressed={speed === rate} onClick={() => setComparisonSpeed(rate)}>
              {rate}×
            </button>
          ))}
        </div>
      </div>
      <p className="split-note">
        {playback.alignment === "shared_utc" ? t("comparison.sameUtc") : t("comparison.sameElapsed")} ·{" "}
        {t("splitSim.note", { factor: SKY_ELEVATION_DISPLAY_FACTOR })}
      </p>
    </div>
  );
}
