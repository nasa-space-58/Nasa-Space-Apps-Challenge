import { useMemo } from "react";
import "./results-live.css";
import type { Frame, Result } from "../types";
import { useTranslate } from "../i18n";
import {
  jointBreakdown,
  situationOf,
  tallyUntil,
  visibilityOf,
  windowOf,
  type Visibility,
} from "../resultProgress";

export const PLAYBACK_SPEEDS = [1, 4, 16] as const;
export type PlaybackSpeed = (typeof PLAYBACK_SPEEDS)[number];

const ICON: Record<Visibility, string> = { visible: "●", blocked: "○", unknown: "◌" };

function Meter({
  fraction,
  final,
  kind,
}: {
  fraction: number;
  final?: number;
  kind: "sun" | "earth" | "dark";
}) {
  const clamp = (x: number) => Math.max(0, Math.min(1, x)) * 100;
  return (
    <span className={`live-meter ${kind}`} aria-hidden="true">
      <i style={{ width: `${clamp(fraction)}%` }} />
      {final !== undefined && <b style={{ left: `${clamp(final)}%` }} />}
    </span>
  );
}

/**
 * Results that follow the playback cursor: the site's situation at the
 * shown moment, totals accumulated so far and, once playback reaches the
 * end of the window, the final backend metrics with a joint breakdown.
 */
export default function LiveResults({
  result,
  frame,
  frameIndex,
  playing,
  speed,
  onTogglePlay,
  onSpeed,
  onJumpEnd,
}: {
  result: Result;
  frame?: Frame;
  frameIndex: number;
  playing: boolean;
  speed: PlaybackSpeed;
  onTogglePlay: () => void;
  onSpeed: (speed: PlaybackSpeed) => void;
  onJumpEnd: () => void;
}) {
  const { t } = useTranslate();
  const h = (seconds: number) => t("units.hours", { value: (seconds / 3600).toFixed(1) });
  const { startMs, endMs } = windowOf(result);
  const window_s = Math.max(1, (endMs - startMs) / 1000);
  const atEnd = frameIndex >= result.frames.length - 1;
  const cursorMs = atEnd ? endMs : frame ? Date.parse(frame.utc) : startMs;
  const sun = tallyUntil(result.analysis.sun.intervals, startMs, cursorMs);
  const earth = tallyUntil(result.analysis.earth.intervals, startMs, cursorMs);
  const joint = useMemo(
    () => jointBreakdown(result.analysis.sun.intervals, result.analysis.earth.intervals, startMs, endMs),
    [result, startMs, endMs],
  );
  const finalSun = result.analysis.sun.metrics,
    finalEarth = result.analysis.earth.metrics;
  const sunNow = visibilityOf(frame?.sun.state),
    earthNow = visibilityOf(frame?.earth.state);
  const situation = situationOf(sunNow, earthNow);
  const pct = (part: number, whole: number) => (whole > 0 ? `${((100 * part) / whole).toFixed(0)}%` : "—");
  const remaining = (seconds: number) =>
    seconds > 1 ? t("liveResults.remaining", { hours: h(seconds) }) : t("liveResults.untilEnd");

  const bodyChip = (target: "sun" | "earth", now: Visibility, tally: typeof sun) => {
    const sample = frame?.[target];
    return (
      <div className={`live-body ${target} ${now}`}>
        <span className="live-body-icon" aria-hidden="true">{ICON[now]}</span>
        <div>
          <span className="metric-label">
            <i className={`${target}-dot`} /> {t(`common.${target}`)}
          </span>
          <strong>{t(`liveResults.state.${target}.${now}`)}</strong>
          <small>
            {sample?.elevation_deg !== null && sample?.elevation_deg !== undefined
              ? t("liveResults.elevation", {
                  elevation: sample.elevation_deg.toFixed(1),
                  horizon: sample.horizon_deg?.toFixed(1) ?? "—",
                })
              : t("liveResults.noElevation")}
          </small>
          {!atEnd && tally.current === now && now !== "unknown" && (
            <small className="live-remaining">{remaining(tally.current_remaining_s)}</small>
          )}
        </div>
      </div>
    );
  };

  const segments = [
    ["both", joint.both_s],
    ["sun_only", joint.sun_only_s],
    ["earth_only", joint.earth_only_s],
    ["neither", joint.neither_s],
    ["unknown", joint.unknown_s],
  ] as const;

  return (
    <div className="live-results" data-mode={atEnd ? "final" : "live"}>
      <div className={`panel live-now situation-${situation}`} aria-live="polite">
        <div className="live-now-heading">
          <div>
            <p className="eyebrow">{atEnd ? t("liveResults.endEyebrow") : t("liveResults.nowEyebrow")}</p>
            <span className="live-clock">
              +{h(sun.elapsed_s)} / {h(window_s)}
            </span>
          </div>
          <div className="live-controls">
            <div className="live-speed" role="group" aria-label={t("liveResults.speed")}>
              {PLAYBACK_SPEEDS.map((value) => (
                <button key={value} type="button" aria-pressed={speed === value} onClick={() => onSpeed(value)}>
                  {value}×
                </button>
              ))}
            </div>
            <button
              type="button"
              className="play-button"
              onClick={onTogglePlay}
              aria-label={playing ? t("app.playback.pause") : t("app.playback.play")}
            >
              {playing ? "Ⅱ" : "▷"}
            </button>
          </div>
        </div>
        <Meter fraction={sun.elapsed_s / window_s} kind="dark" />
        {frame && (
          <>
            <div className="live-bodies">
              {bodyChip("sun", sunNow, sun)}
              {bodyChip("earth", earthNow, earth)}
            </div>
            <div className="live-situation">
              <strong>{t(`liveResults.situation.${situation}.title`)}</strong>
              <p>{t(`liveResults.situation.${situation}.detail`)}</p>
            </div>
          </>
        )}
      </div>

      <div className="metric-grid live-metrics">
        <div className="metric-card">
          <span className="metric-label">
            <i className="sun-dot" /> {t("app.results.sunlight")} ·{" "}
            {atEnd ? t("liveResults.final") : t("liveResults.soFar")}
          </span>
          <strong>{atEnd ? `${finalSun.visible_percent_of_interval.toFixed(1)}%` : h(sun.visible_s)}</strong>
          <Meter
            kind="sun"
            fraction={(atEnd ? finalSun.visible_duration_s : sun.visible_s) / window_s}
            final={atEnd ? undefined : finalSun.visible_duration_s / window_s}
          />
          <small>
            {atEnd
              ? t("app.results.sunlightDetail", { hours: h(finalSun.visible_duration_s) })
              : t("liveResults.ofElapsed", {
                  percent: pct(sun.visible_s, sun.elapsed_s),
                  total: `${finalSun.visible_percent_of_interval.toFixed(1)}%`,
                })}
          </small>
        </div>
        <div className="metric-card">
          <span className="metric-label">
            <i className="earth-dot" /> {t("app.results.earth")} ·{" "}
            {atEnd ? t("liveResults.final") : t("liveResults.soFar")}
          </span>
          <strong>{atEnd ? `${finalEarth.visible_percent_of_interval.toFixed(1)}%` : h(earth.visible_s)}</strong>
          <Meter
            kind="earth"
            fraction={(atEnd ? finalEarth.visible_duration_s : earth.visible_s) / window_s}
            final={atEnd ? undefined : finalEarth.visible_duration_s / window_s}
          />
          <small>
            {atEnd
              ? t("app.results.earthDetail", { hours: h(finalEarth.visible_duration_s) })
              : t("liveResults.ofElapsed", {
                  percent: pct(earth.visible_s, earth.elapsed_s),
                  total: `${finalEarth.visible_percent_of_interval.toFixed(1)}%`,
                })}
          </small>
        </div>
        <div className="metric-card">
          <span className="metric-label">
            {t("app.results.longestDark")} · {atEnd ? t("liveResults.final") : t("liveResults.soFar")}
          </span>
          <strong>{h(atEnd ? finalSun.longest_blocked_s : sun.longest_blocked_s)}</strong>
          <Meter
            kind="dark"
            fraction={(atEnd ? finalSun.longest_blocked_s : sun.longest_blocked_s) / window_s}
            final={atEnd ? undefined : finalSun.longest_blocked_s / window_s}
          />
          <small>
            {atEnd
              ? t("app.results.longestDarkDetail")
              : t("liveResults.sunsetsSoFar", { count: sun.losses })}
          </small>
        </div>
      </div>

      {atEnd ? (
        <section className="panel final-summary" aria-label={t("liveResults.summaryTitle")}>
          <div className="panel-heading">
            <span>{t("liveResults.summaryTitle")}</span>
            <small>{t("liveResults.summaryWindow", { hours: h(window_s) })}</small>
          </div>
          <div className="joint-bar" role="img" aria-label={segments.map(([k, s]) => `${t(`liveResults.joint.${k}`)} ${h(s)}`).join("; ")}>
            {segments.map(([key, seconds]) =>
              seconds > 0 ? (
                <i key={key} className={`joint-${key}`} style={{ width: `${(100 * seconds) / window_s}%` }} title={`${t(`liveResults.joint.${key}`)} · ${h(seconds)}`} />
              ) : null,
            )}
          </div>
          <ul className="joint-legend">
            {segments.map(([key, seconds]) => (
              <li key={key}>
                <i className={`joint-${key}`} />
                <span>{t(`liveResults.joint.${key}`)}</span>
                <b>{h(seconds)}</b>
                <small>{pct(seconds, window_s)}</small>
              </li>
            ))}
          </ul>
          <dl className="summary-facts">
            <div>
              <dt>{t("liveResults.facts.longestSun")}</dt>
              <dd>{h(finalSun.longest_visible_s)}</dd>
            </div>
            <div>
              <dt>{t("liveResults.facts.longestEarthGap")}</dt>
              <dd>{h(finalEarth.longest_blocked_s)}</dd>
            </div>
            <div>
              <dt>{t("liveResults.facts.sunsets")}</dt>
              <dd>{sun.losses}</dd>
            </div>
            <div>
              <dt>{t("liveResults.facts.earthsets")}</dt>
              <dd>{earth.losses}</dd>
            </div>
            <div>
              <dt>{t("liveResults.facts.unknown")}</dt>
              <dd>{h(finalSun.unknown_duration_s + finalEarth.unknown_duration_s)}</dd>
            </div>
          </dl>
          <p className="field-help">{t("liveResults.summaryNote")}</p>
        </section>
      ) : (
        <button type="button" className="text-button live-jump" onClick={onJumpEnd}>
          {t("liveResults.jumpEnd")} ↘
        </button>
      )}
    </div>
  );
}
