import ExportPanel from "./ExportPanel";
import "./results-live.css";
import { useEffect, useState } from "react";
import HorizonPlot from "./HorizonPlot";
import type { Result, Preset, Frame } from "../types";
import { errorText, siteLabel, useTranslate, type Translate } from "../i18n";
import { situationOf, tallyUntil, visibilityOf, type Visibility } from "../resultProgress";
import { saveComparison, savedName, useLibrary } from "../savedLibrary";

type Submission = {
  site_id: string;
  latitude_deg: number;
  longitude_deg_east: number;
  observer_height_m: number;
  start_utc: string;
  end_utc: string;
  step_s: number;
  refine: boolean;
  horizon: Record<string, unknown>;
};
type Change = {
  start_elapsed_s: number;
  end_elapsed_s: number;
  a_state: string;
  b_state: string;
};
type ComparisonResult = {
  mode: string;
  status: string;
  alignment: string;
  duration_s: number;
  start_offset_s: number;
  deltas: Record<string, Record<string, number>>;
  changes: Record<string, Change[]>;
  cursor: {
    elapsed_s: number;
    a_index: number | null;
    b_index: number | null;
    a_sample_age_s: number | null;
    b_sample_age_s: number | null;
  }[];
  explanations: string[];
};
const stateName = (t: Translate, state: string) => t(`displayState.${state}`);
const date = (value: string) =>
  new Date(value).toISOString().replace("T", " ").replace(".000Z", " UTC");
const hoursOf = (t: Translate, value: number) =>
  t("units.hours", { value: (value / 3600).toFixed(2) });
async function api(path: string, init?: RequestInit) {
  const response = await fetch("/api" + path, init);
  const body = await response.json();
  if (!response.ok)
    throw Error(
      typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((x: { msg: string }) => x.msg).join("; ")
          : "",
    );
  return body;
}
function post(path: string, body: unknown) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function Scenario({
  label,
  result,
  frame,
  elapsed,
  age,
  scaleResults,
}: {
  label: string;
  result: Result;
  frame?: Frame;
  elapsed: number;
  age: number | null;
  scaleResults: Result[];
}) {
  const { t } = useTranslate();
  const hours = (value: number) => hoursOf(t, value);
  const names = (state: string) => stateName(t, state);
  const start = Date.parse(result.analysis.request.start_utc),
    end = Date.parse(result.analysis.request.end_utc),
    duration = end - start;
  return (
    <article
      className="panel compare-scenario"
      aria-label={t("comparison.scenarioResults", { label })}
    >
      <div className="panel-heading">
        <span>{t("comparison.scenarioUpper", { label })}</span>
        <span>{result.analysis.request.site.site_id.replaceAll("-", " ")}</span>
      </div>
      <div className="compare-dates">
        <b>{date(result.analysis.request.start_utc)}</b>
        <span>{t("comparison.to", { end: date(result.analysis.request.end_utc) })}</span>
        <span>
          {result.analysis.request.site.latitude_deg}° ·{" "}
          {result.analysis.request.site.longitude_deg_east}°E
        </span>
      </div>
      <div className="compare-totals">
        <span>
          {t("comparison.sunlight")}
          <strong>
            {hours(result.analysis.sun.metrics.visible_duration_s)}
          </strong>
        </span>
        <span>
          {t("comparison.earthVisible")}
          <strong>
            {hours(result.analysis.earth.metrics.visible_duration_s)}
          </strong>
        </span>
        <span>
          {t("comparison.longestDark")}
          <strong>
            {hours(result.analysis.sun.metrics.longest_blocked_s)}
          </strong>
        </span>
      </div>
      {frame ? (
        <>
          <HorizonPlot
            result={result}
            frame={frame}
            scaleResults={scaleResults}
          />
          <div className="compare-readout">
            <span>
              {t("common.sun")}: {names(frame.sun.state)} ·{" "}
              {frame.sun.elevation_deg?.toFixed(2) ?? "—"}°
            </span>
            <span>
              {t("common.earth")}: {names(frame.earth.state)} ·{" "}
              {frame.earth.elevation_deg?.toFixed(2) ?? "—"}°
            </span>
            <small>
              {t("comparison.displayedSample", { utc: date(frame.utc) })}
              {age
                ? ` · ${t("comparison.beforeCursor", { hours: hours(age) })}`
                : ` · ${t("comparison.atCursor")}`}
            </small>
          </div>
        </>
      ) : (
        <p className="compare-readout">
          {t("comparison.noSample")}
        </p>
      )}
      <div className="compare-timelines">
        {(["sun", "earth"] as const).map((target) => (
          <div className="timeline-row" key={target}>
            <span>{t(`comparison.target.${target}`)}</span>
            <div
              className="timeline-track"
              role="img"
              aria-label={t("comparison.timelineLabel", {
                label,
                target: t(`common.${target}`),
              })}
            >
              {result.analysis[target].intervals.map((row, index) => (
                <div
                  key={index}
                  className={`time-segment ${row.state}`}
                  style={{
                    left: `${((Date.parse(row.start_utc) - start) / duration) * 100}%`,
                    width: `${((Date.parse(row.end_utc) - Date.parse(row.start_utc)) / duration) * 100}%`,
                  }}
                  title={`${names(row.state)}: ${date(row.start_utc)} – ${date(row.end_utc)}`}
                />
              ))}
              <i
                className="time-cursor"
                style={{ left: `${((elapsed * 1000) / duration) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
      <p className="compare-unknown">
        {t("comparison.unknownLine", {
          sun: hours(result.analysis.sun.metrics.unknown_duration_s),
          earth: hours(result.analysis.earth.metrics.unknown_duration_s),
        })}
      </p>
      <details className="compare-intervals">
        <summary>{t("comparison.readIntervals")}</summary>
        {(["sun", "earth"] as const).map((target) => (
          <div key={target}>
            <h4>{t(`comparison.target.${target}`)}</h4>
            {result.analysis[target].intervals.map((row, index) => (
              <p key={index}>
                {names(row.state)} · {date(row.start_utc)} → {date(row.end_utc)}
              </p>
            ))}
          </div>
        ))}
      </details>
    </article>
  );
}

const MARK: Record<Visibility, string> = { visible: "●", blocked: "○", unknown: "◌" };

/** What each scenario sees at the shared cursor and what it has gathered so far. */
function CompareNow({
  a,
  b,
  aFrame,
  bFrame,
  elapsed_s,
  atEnd,
}: {
  a: Result;
  b: Result;
  aFrame?: Frame;
  bFrame?: Frame;
  elapsed_s: number;
  atEnd: boolean;
}) {
  const { t } = useTranslate();
  const h = (seconds: number) => t("units.hours", { value: (seconds / 3600).toFixed(1) });
  const rows = (["A", "B"] as const).map((label) => {
    const result = label === "A" ? a : b;
    const frame = label === "A" ? aFrame : bFrame;
    const start = Date.parse(result.analysis.request.start_utc),
      end = Date.parse(result.analysis.request.end_utc);
    const cursor = atEnd ? end : Math.min(end, start + elapsed_s * 1000);
    const sun = visibilityOf(frame?.sun.state),
      earth = visibilityOf(frame?.earth.state);
    return {
      label,
      sun,
      earth,
      situation: situationOf(sun, earth),
      sunSoFar: tallyUntil(result.analysis.sun.intervals, start, cursor).visible_s,
      earthSoFar: tallyUntil(result.analysis.earth.intervals, start, cursor).visible_s,
    };
  });
  const [ra, rb] = rows;
  const lead = (x: number, y: number) => (Math.abs(x - y) < 60 ? "tie" : x > y ? "A" : "B");
  const sunLead = lead(ra.sunSoFar, rb.sunSoFar),
    earthLead = lead(ra.earthSoFar, rb.earthSoFar);
  const differs = ra.sun !== rb.sun || ra.earth !== rb.earth;
  return (
    <div className="panel compare-now" aria-live="polite">
      <div className="panel-heading">
        <span>{atEnd ? t("comparisonLive.endTitle") : t("comparisonLive.title")}</span>
        <small>{differs ? t("comparisonLive.differs") : t("comparisonLive.same")}</small>
      </div>
      <div className="compare-now-grid">
        {rows.map((row) => (
          <div key={row.label} className={`compare-now-row situation-${row.situation}`}>
            <b className="compare-now-label">{row.label}</b>
            <span className={`now-chip sun ${row.sun}`}>
              {MARK[row.sun]} {t(`liveResults.state.sun.${row.sun}`)}
            </span>
            <span className={`now-chip earth ${row.earth}`}>
              {MARK[row.earth]} {t(`liveResults.state.earth.${row.earth}`)}
            </span>
            <small>{t(`liveResults.situation.${row.situation}.title`)}</small>
            <span className="now-sofar">
              <i className="sun-dot" /> {h(row.sunSoFar)} · <i className="earth-dot" /> {h(row.earthSoFar)}
            </span>
          </div>
        ))}
      </div>
      <p className="compare-now-lead">
        {t(`comparisonLive.sunLead.${sunLead}`)} {t(`comparisonLive.earthLead.${earthLead}`)}
      </p>
    </div>
  );
}

export default function Comparison({
  aId,
  a,
  sites,
  onClose,
  initialBId,
  initialCursor = 0,
}: {
  aId: string;
  initialBId?: string;
  initialCursor?: number;
  a: Result;
  sites: Preset[];
  onClose: () => void;
}) {
  const { t } = useTranslate();
  const hours = (value: number) => hoursOf(t, value);
  const names = (state: string) => stateName(t, state);
  const key = `lunar-comparison:${aId}`;
  const [base, setBase] = useState<Submission | null>(null);
  const [draft, setDraft] = useState<Submission | null>(null);
  const [mode, setMode] = useState<"dates" | "sites">("dates");
  const [bId, setBId] = useState<string | null>(null);
  const [b, setB] = useState<Result | null>(null);
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const [phase, setPhase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [tab, setTab] = useState("A");
  const [retry, setRetry] = useState(0);
  const library = useLibrary();
  const [saveName, setSaveName] = useState("");
  const [savedMessage, setSavedMessage] = useState("");
  const pickSaved = (id: string) => {
    if (!id || id === aId) return;
    setError("");
    setB(null);
    setComparison(null);
    setPlaying(false);
    api(`/analyses/${id}`)
      .then((other) => {
        setDraft(other.submission);
        if (base)
          setMode(other.submission.start_utc === base.start_utc ? "sites" : "dates");
        setBId(id);
      })
      .catch((e) => setError(errorText(e, t, "comparison.loadFailed")));
  };
  useEffect(() => {
    let active = true;
    api(`/analyses/${aId}`)
      .then((job) => {
        if (!active) return;
        setBase(job.submission);
        let saved: null | {
          draft: Submission;
          mode: "dates" | "sites";
          bId: string | null;
        } = null;
        try {
          saved = JSON.parse(localStorage.getItem(key) || "null");
        } catch {
          localStorage.removeItem(key);
        }
        setDraft(saved?.draft || { ...job.submission });
        setMode(saved?.mode || "dates");
        setBId(initialBId || saved?.bId || null);
        if (initialBId)
          api(`/analyses/${initialBId}`)
            .then((other) => {
              if (active) {
                setDraft(other.submission);
                setMode(
                  other.submission.start_utc === job.submission.start_utc
                    ? "sites"
                    : "dates",
                );
              }
            })
            .catch((e) => active && setError(errorText(e, t, "comparison.requestFailed")));
      })
      .catch((e) => active && setError(errorText(e, t, "comparison.requestFailed")));
    return () => {
      active = false;
    };
  }, [aId, key, retry, initialBId]);
  useEffect(() => {
    if (draft) localStorage.setItem(key, JSON.stringify({ draft, mode, bId }));
  }, [key, draft, mode, bId]);
  useEffect(() => {
    if (!bId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setBusy(true);
    setError("");
    const poll = async () => {
      try {
        const job = await api(`/analyses/${bId}`);
        if (!active) return;
        setPhase(job.status);
        if (job.status === "failed") {
          setError(job.error || t("comparison.bFailed"));
          setBusy(false);
          return;
        }
        if (job.status === "ready" || job.status === "insufficient_data") {
          const [view, match] = await Promise.all([
            api(`/analyses/${bId}/result`),
            post("/comparisons", { a_id: aId, b_id: bId }),
          ]);
          if (active) {
            setB(view);
            setComparison(match);
            setCursor(
              Number.isInteger(initialCursor)
                ? Math.max(0, Math.min(initialCursor, match.cursor.length - 1))
                : 0,
            );
            setBusy(false);
            setPhase(match.status);
            setError("");
          }
        } else timer = setTimeout(poll, 1800);
      } catch (e) {
        if (active) {
          setError(errorText(e, t, "comparison.loadFailed"));
          setBusy(false);
        }
      }
    };
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [aId, bId, retry]);
  useEffect(() => {
    if (!playing || !comparison) return;
    const timer = setInterval(
      () =>
        setCursor((index) => {
          if (index >= comparison.cursor.length - 1) {
            setPlaying(false);
            return index;
          }
          return index + 1;
        }),
      700,
    );
    return () => clearInterval(timer);
  }, [playing, comparison]);
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const stop = () => setPlaying(false);
    media?.addEventListener("change", stop);
    return () => media?.removeEventListener("change", stop);
  }, []);
  const chooseMode = (next: "dates" | "sites") => {
    if (!base) return;
    setMode(next);
    setDraft({ ...base });
    setError("");
  };
  const setStart = (value: string) => {
    if (!base || !draft || !value) return;
    const start = new Date(value + "Z");
    if (!Number.isFinite(start.getTime())) return;
    const duration = Date.parse(base.end_utc) - Date.parse(base.start_utc);
    setDraft({
      ...draft,
      start_utc: start.toISOString(),
      end_utc: new Date(start.getTime() + duration).toISOString(),
    });
  };
  const delay = () => {
    if (!base) return;
    setMode("dates");
    setDraft({
      ...base,
      start_utc: new Date(
        Date.parse(base.start_utc) + 48 * 3600000,
      ).toISOString(),
      end_utc: new Date(Date.parse(base.end_utc) + 48 * 3600000).toISOString(),
    });
  };
  const submit = async () => {
    if (!draft) return;
    setBusy(true);
    setError("");
    setB(null);
    setComparison(null);
    setPlaying(false);
    setPhase("submitting");
    try {
      const job = await post("/analyses", draft);
      setBId(job.id);
      setPhase(job.status);
    } catch (e) {
      setBusy(false);
      setError(errorText(e, t, "comparison.submitFailed"));
    }
  };
  const draftChanged = !!(
    b &&
    draft &&
    (Date.parse(draft.start_utc) !== Date.parse(b.analysis.request.start_utc) ||
      draft.latitude_deg !== b.analysis.request.site.latitude_deg ||
      draft.longitude_deg_east !== b.analysis.request.site.longitude_deg_east)
  );
  const point = comparison?.cursor[cursor];
  // Last element: whether a larger value is better for the mission.
  const metrics: [string, string, string, string, boolean][] = [
    ["sun", "visible_duration_s", t("comparison.metric.sunlight"), "h", true],
    ["sun", "longest_blocked_s", t("comparison.metric.darkness"), "h", false],
    ["earth", "visible_duration_s", t("comparison.metric.earth"), "h", true],
    ["sun", "visible_percent_of_interval", t("comparison.metric.percent"), "pp", true],
  ];
  const winnerOf = (raw: number, higherBetter: boolean, unit: string) =>
    Math.abs(unit === "h" ? raw / 3600 : raw) < 0.005 ? "tie" : raw > 0 === higherBetter ? "B" : "A";
  const wins = comparison
    ? metrics.slice(0, 3).map(([target, metric, , unit, better]) =>
        winnerOf(comparison.deltas[target][metric], better, unit),
      )
    : [];
  const aWins = wins.filter((w) => w === "A").length,
    bWins = wins.filter((w) => w === "B").length;
  const atEnd = !!comparison && cursor >= comparison.cursor.length - 1;
  const pairSaved = !!bId && library.comparisons.some((c) => c.a_id === aId && c.b_id === bId);
  const pairName = () =>
    `${savedName(library, aId) ?? a.analysis.request.site.site_id.replaceAll("-", " ")} ↔ ${
      (bId && savedName(library, bId)) ?? b?.analysis.request.site.site_id.replaceAll("-", " ") ?? "B"
    }`;
  return (
    <section
      id="comparison"
      className="comparison-section"
      aria-label={t("comparison.label")}
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t("comparison.eyebrow")}</p>
          <h2>
            {t("comparison.title")}<span>?</span>
          </h2>
          <p className="muted">{t("comparison.intro")}</p>
        </div>
        <button className="text-button" onClick={onClose}>
          {t("comparison.close")} ×
        </button>
      </div>
      <div className="panel comparison-setup">
        <div>
          <span className="outline-tag">{t("comparison.baseline")}</span>
          <h3>{a.analysis.request.site.site_id.replaceAll("-", " ")}</h3>
          <p>{date(a.analysis.request.start_utc)}</p>
          <p>
            {hours(
              (Date.parse(a.analysis.request.end_utc) -
                Date.parse(a.analysis.request.start_utc)) /
                1000,
            )}{" "}
            {t("comparison.observation", {
              height: a.horizon.observer_height_above_terrain_m.toFixed(1),
            })}
          </p>
          <small>{t("comparison.bOnly")}</small>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <fieldset disabled={busy || !draft}>
            <div className="compare-modes">
              <button
                type="button"
                aria-pressed={mode === "dates"}
                onClick={() => chooseMode("dates")}
              >
                {t("comparison.modeDates")}
              </button>
              <button
                type="button"
                aria-pressed={mode === "sites"}
                onClick={() => chooseMode("sites")}
              >
                {t("comparison.modeSites")}
              </button>
            </div>
            {draft && (
              <>
                {mode === "dates" ? (
                  <>
                    <label>
                      {t("comparison.bStart")} · UTC
                      <input
                        aria-label={t("comparison.bStart")}
                        type="datetime-local"
                        step="1"
                        min="2026-01-01T00:00"
                        max="2027-12-31T23:59"
                        required
                        value={new Date(draft.start_utc)
                          .toISOString()
                          .slice(0, 19)}
                        onChange={(e) => setStart(e.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      className="text-button"
                      onClick={delay}
                    >
                      {t("comparison.delay")} ↗
                    </button>
                  </>
                ) : (
                  <>
                    <label>
                      {t("comparison.bRegion")}
                      <select
                        aria-label={t("comparison.bRegion")}
                        value={draft.site_id}
                        onChange={(e) => {
                          const site = sites.find(
                            (site) => site.site_id === e.target.value,
                          );
                          if (site)
                            setDraft({
                              ...draft,
                              site_id: site.site_id,
                              latitude_deg: site.latitude_deg,
                              longitude_deg_east: site.longitude_deg_east,
                            });
                          else setDraft({ ...draft, site_id: "custom" });
                        }}
                      >
                        <option value="custom">{t("app.form.custom")}</option>
                        {!sites.some(
                          (site) => site.site_id === draft.site_id,
                        ) &&
                          draft.site_id !== "custom" && (
                            <option value={draft.site_id}>
                              {draft.site_id}
                            </option>
                          )}
                        {sites.map((site) => (
                          <option key={site.site_id} value={site.site_id}>
                            {siteLabel(t, site)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="field-pair">
                      <label>
                        {t("app.form.latitude")} · °
                        <input
                          aria-label={t("comparison.bLatitude")}
                          type="number"
                          required
                          min="-90"
                          max="-80"
                          step="any"
                          value={draft.latitude_deg}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              site_id: "custom",
                              latitude_deg: Number(e.target.value),
                            })
                          }
                        />
                      </label>
                      <label>
                        {t("app.form.longitude")} · °E
                        <input
                          aria-label={t("comparison.bLongitude")}
                          type="number"
                          required
                          min="0"
                          max="359.999999"
                          step="any"
                          value={draft.longitude_deg_east}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              site_id: "custom",
                              longitude_deg_east: Number(e.target.value),
                            })
                          }
                        />
                      </label>
                    </div>
                  </>
                )}
                {library.results.some((r) => r.id !== aId) && (
                  <label>
                    {t("comparisonLive.pickSaved")}
                    <select
                      value={bId && library.results.some((r) => r.id === bId) ? bId : ""}
                      onChange={(e) => pickSaved(e.target.value)}
                    >
                      <option value="">{t("comparisonLive.pickSavedNone")}</option>
                      {library.results
                        .filter((r) => r.id !== aId)
                        .map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.name}
                          </option>
                        ))}
                    </select>
                  </label>
                )}
                <p className="field-help">{t("comparison.sameSettings")}</p>
                <button className="primary" type="submit">
                  {busy ? t("comparison.processing") : t("comparison.run")} ↗
                </button>
              </>
            )}
          </fieldset>
        </form>
      </div>
      {error && (
        <div className="alert" role="alert">
          {error}{" "}
          <button onClick={() => setRetry((value) => value + 1)}>
            {t("comparison.retry")}
          </button>
        </div>
      )}
      {busy && (
        <p role="status" className="job-progress">
          {t("comparison.bPhase", {
            phase: t(`jobState.${phase}`),
          })}
        </p>
      )}
      {draftChanged && (
        <p className="alert" role="status">
          {t("comparison.changed")}
        </p>
      )}
      {comparison && b && point ? (
        <>
          {bId && (
            <ExportPanel
              jobId={aId}
              result={a}
              frameIndex={point.a_index ?? 0}
              bId={bId}
              b={b}
              bFrameIndex={point.b_index ?? 0}
              cursor={cursor}
            />
          )}
          <div className="compare-deltas">
            {metrics.map(([target, metric, label, unit, better]) => {
              const raw = comparison.deltas[target][metric];
              const value = unit === "h" ? raw / 3600 : raw;
              const winner = winnerOf(raw, better, unit);
              return (
                <div className={`metric-card winner-${winner}`} key={metric + target}>
                  <span className="metric-label">
                    {label} · B − A
                    <span className="winner-badge">
                      {winner === "tie" ? t("comparisonLive.tie") : t("comparisonLive.better", { label: winner })}
                    </span>
                  </span>
                  <strong>
                    {value > 0 ? "+" : ""}
                    {value.toFixed(2)}{" "}
                    <small>{t(`comparison.unit.${unit}`)}</small>
                  </strong>
                  <small>
                    {unit === "pp"
                      ? t("comparison.pp")
                      : t("comparison.fullWindow")}
                  </small>
                </div>
              );
            })}
          </div>
          <div
            className={`panel compare-explanation ${comparison.status === "insufficient_data" ? "has-unknown" : ""}`}
            role="status"
          >
            {comparison.explanations.map((text) => (
              <p key={text}>{text}</p>
            ))}
          </div>
          <div className="panel compare-verdict">
            <div>
              <p className="eyebrow">{t("comparisonLive.verdictEyebrow")}</p>
              <strong>
                {aWins === bWins
                  ? t("comparisonLive.verdictTie")
                  : t("comparisonLive.verdictWinner", {
                      label: aWins > bWins ? "A" : "B",
                      wins: Math.max(aWins, bWins),
                      total: wins.length,
                    })}
              </strong>
              <small>{t("comparisonLive.verdictNote")}</small>
            </div>
            <form
              className="saved-save"
              onSubmit={(e) => {
                e.preventDefault();
                if (!bId) return;
                saveComparison(aId, bId, saveName || pairName());
                setSaveName("");
                setSavedMessage(t("comparisonLive.savedMessage"));
              }}
            >
              <input
                aria-label={t("comparisonLive.saveName")}
                placeholder={pairName()}
                maxLength={80}
                value={saveName}
                onChange={(e) => {
                  setSaveName(e.target.value);
                  setSavedMessage("");
                }}
              />
              <button type="submit" className="primary">
                {pairSaved ? t("comparisonLive.update") : t("comparisonLive.save")}
              </button>
            </form>
            {savedMessage && (
              <p className="field-help" role="status">
                {savedMessage}
              </p>
            )}
          </div>
          <div className="panel comparison-player">
            <div className="playback-heading">
              <div>
                <p className="eyebrow">
                  {comparison.alignment === "shared_utc"
                    ? t("comparison.sameUtc")
                    : t("comparison.sameElapsed")}
                </p>
                <strong>
                  +{t("comparison.sinceStart", { hours: hours(point.elapsed_s) })}
                </strong>
              </div>
              <button
                className="play-button"
                aria-label={playing ? t("comparison.pause") : t("comparison.play")}
                onClick={() => {
                  if (cursor === comparison.cursor.length - 1) setCursor(0);
                  setPlaying(!playing);
                }}
              >
                {playing ? "Ⅱ" : "▷"}
              </button>
            </div>
            <input
              aria-label={t("comparison.time")}
              aria-valuetext={t("comparison.sinceStart", {
                hours: hours(point.elapsed_s),
              })}
              type="range"
              min="0"
              max={comparison.cursor.length - 1}
              value={cursor}
              onChange={(e) => {
                setPlaying(false);
                setCursor(Number(e.target.value));
              }}
            />
            <div className="comparison-cursor-dates">
              <span>
                {t("comparison.cursor", { label: "A" })} ·{" "}
                {date(
                  new Date(
                    Date.parse(a.analysis.request.start_utc) +
                      point.elapsed_s * 1000,
                  ).toISOString(),
                )}
              </span>
              <span>
                {t("comparison.cursor", { label: "B" })} ·{" "}
                {date(
                  new Date(
                    Date.parse(b.analysis.request.start_utc) +
                      point.elapsed_s * 1000,
                  ).toISOString(),
                )}
              </span>
            </div>
            <p className="field-help">{t("comparison.playbackNote")}</p>
          </div>
          <CompareNow
            a={a}
            b={b}
            aFrame={point.a_index === null ? undefined : a.frames[point.a_index]}
            bFrame={point.b_index === null ? undefined : b.frames[point.b_index]}
            elapsed_s={point.elapsed_s}
            atEnd={atEnd}
          />
          <div
            className="compare-tabs"
            role="tablist"
            aria-label={t("comparison.results")}
          >
            <button
              role="tab"
              id="scenario-a-tab"
              aria-controls="scenario-a"
              aria-selected={tab === "A"}
              onClick={() => setTab("A")}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight") {
                  setTab("B");
                  document.getElementById("scenario-b-tab")?.focus();
                }
              }}
            >
              {t("scenario.title", { name: "A" })}
            </button>
            <button
              role="tab"
              id="scenario-b-tab"
              aria-controls="scenario-b"
              aria-selected={tab === "B"}
              onClick={() => setTab("B")}
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft") {
                  setTab("A");
                  document.getElementById("scenario-a-tab")?.focus();
                }
              }}
            >
              {t("scenario.title", { name: "B" })}
            </button>
          </div>
          <div className="compare-columns">
            <div id="scenario-a" className={tab === "A" ? "selected" : ""}>
              <Scenario
                label="A"
                scaleResults={[a, b]}
                result={a}
                frame={
                  point.a_index === null ? undefined : a.frames[point.a_index]
                }
                elapsed={point.elapsed_s}
                age={point.a_sample_age_s}
              />
            </div>
            <div id="scenario-b" className={tab === "B" ? "selected" : ""}>
              <Scenario
                label="B"
                scaleResults={[a, b]}
                result={b}
                frame={
                  point.b_index === null ? undefined : b.frames[point.b_index]
                }
                elapsed={point.elapsed_s}
                age={point.b_sample_age_s}
              />
            </div>
          </div>
          <details className="panel comparison-changes">
            <summary>{t("comparison.whichChanged")}</summary>
            <p>{t("comparison.offsets")}</p>
            {(["sun", "earth"] as const).map((target) => (
              <div key={target}>
                <h4>{t(`comparison.target.${target}`)}</h4>
                {comparison.changes[target].length ? (
                  comparison.changes[target].map((row, index) => (
                    <p key={index}>
                      +{hours(row.start_elapsed_s)} → +
                      {hours(row.end_elapsed_s)}: A {names(row.a_state)} / B{" "}
                      {names(row.b_state)}
                    </p>
                  ))
                ) : (
                  <p>{t("comparison.noDiff")}</p>
                )}
              </div>
            ))}
          </details>
        </>
      ) : (
        <p className="comparison-empty">
          {t("comparison.empty")}
        </p>
      )}
      <details className="panel comparison-glossary">
        <summary>{t("comparison.glossary.summary")}</summary>
        <p>
          <b>{t("comparison.glossary.horizon")}:</b>{" "}
          {t("comparison.glossary.horizonText")}
        </p>
        <p>
          <b>{t("comparison.glossary.elevation")}:</b>{" "}
          {t("comparison.glossary.elevationText")}
        </p>
        <p>
          <b>{t("comparison.glossary.visibility")}:</b>{" "}
          {t("comparison.glossary.visibilityText")}
        </p>
        <p>{t("comparison.glossary.note")}</p>
      </details>
    </section>
  );
}
