import { useEffect, useState } from "react";
import HorizonPlot from "./HorizonPlot";
import type { Result, Preset, Frame } from "../types";

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
const names: Record<string, string> = {
  full: "Full sunlight",
  partial: "Partial sunlight",
  dark: "Terrain-blocked",
  visible: "Center visible",
  blocked: "Center blocked",
  unknown: "Unknown",
  boundary: "At horizon",
};
const date = (value: string) =>
  new Date(value).toISOString().replace("T", " ").replace(".000Z", " UTC");
const hours = (value: number) => `${(value / 3600).toFixed(2)} h`;
async function api(path: string, init?: RequestInit) {
  const response = await fetch("/api" + path, init);
  const body = await response.json();
  if (!response.ok)
    throw Error(
      typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((x: { msg: string }) => x.msg).join("; ")
          : "Request unavailable. Please retry.",
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
  const start = Date.parse(result.analysis.request.start_utc),
    end = Date.parse(result.analysis.request.end_utc),
    duration = end - start;
  return (
    <article
      className="panel compare-scenario"
      aria-label={`Scenario ${label} results`}
    >
      <div className="panel-heading">
        <span>SCENARIO {label}</span>
        <span>{result.analysis.request.site.site_id.replaceAll("-", " ")}</span>
      </div>
      <div className="compare-dates">
        <b>{date(result.analysis.request.start_utc)}</b>
        <span>to {date(result.analysis.request.end_utc)}</span>
        <span>
          {result.analysis.request.site.latitude_deg}° ·{" "}
          {result.analysis.request.site.longitude_deg_east}°E
        </span>
      </div>
      <div className="compare-totals">
        <span>
          Sunlight
          <strong>
            {hours(result.analysis.sun.metrics.visible_duration_s)}
          </strong>
        </span>
        <span>
          Earth visible
          <strong>
            {hours(result.analysis.earth.metrics.visible_duration_s)}
          </strong>
        </span>
        <span>
          Longest dark
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
              Sun: {names[frame.sun.state]} ·{" "}
              {frame.sun.elevation_deg?.toFixed(2) ?? "—"}°
            </span>
            <span>
              Earth: {names[frame.earth.state]} ·{" "}
              {frame.earth.elevation_deg?.toFixed(2) ?? "—"}°
            </span>
            <small>
              Displayed sample: {date(frame.utc)}
              {age ? ` · ${hours(age)} before cursor` : " · at cursor"}
            </small>
          </div>
        </>
      ) : (
        <p className="compare-readout">
          No preceding sample. Sky positions are unknown.
        </p>
      )}
      <div className="compare-timelines">
        {(["sun", "earth"] as const).map((target) => (
          <div className="timeline-row" key={target}>
            <span>{target.toUpperCase()}</span>
            <div
              className="timeline-track"
              role="img"
              aria-label={`${label} ${target} visibility timeline`}
            >
              {result.analysis[target].intervals.map((row, index) => (
                <div
                  key={index}
                  className={`time-segment ${row.state}`}
                  style={{
                    left: `${((Date.parse(row.start_utc) - start) / duration) * 100}%`,
                    width: `${((Date.parse(row.end_utc) - Date.parse(row.start_utc)) / duration) * 100}%`,
                  }}
                  title={`${names[row.state]}: ${date(row.start_utc)} – ${date(row.end_utc)}`}
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
        Unknown: Sun {hours(result.analysis.sun.metrics.unknown_duration_s)} ·
        Earth {hours(result.analysis.earth.metrics.unknown_duration_s)}
      </p>
      <details className="compare-intervals">
        <summary>Read visibility intervals</summary>
        {(["sun", "earth"] as const).map((target) => (
          <div key={target}>
            <h4>{target.toUpperCase()}</h4>
            {result.analysis[target].intervals.map((row, index) => (
              <p key={index}>
                {names[row.state]} · {date(row.start_utc)} → {date(row.end_utc)}
              </p>
            ))}
          </div>
        ))}
      </details>
    </article>
  );
}

export default function Comparison({
  aId,
  a,
  sites,
  onClose,
}: {
  aId: string;
  a: Result;
  sites: Preset[];
  onClose: () => void;
}) {
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
        setBId(saved?.bId || null);
      })
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [aId, key, retry]);
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
          setError(job.error || "Scenario B failed");
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
            setCursor(0);
            setBusy(false);
            setPhase(match.status);
            setError("");
          }
        } else timer = setTimeout(poll, 1800);
      } catch (e) {
        if (active) {
          setError(
            e instanceof Error ? e.message : "Unable to load comparison",
          );
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
      setError(e instanceof Error ? e.message : "Unable to submit scenario B");
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
  const metrics = [
    ["sun", "visible_duration_s", "Sunlight", "h"],
    ["sun", "longest_blocked_s", "Longest darkness", "h"],
    ["earth", "visible_duration_s", "Earth visibility", "h"],
    ["sun", "visible_percent_of_interval", "Sunlight percentage", "pp"],
  ];
  return (
    <section
      id="comparison"
      className="comparison-section"
      aria-label="Scenario comparison"
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">04 / TWO PERSPECTIVES</p>
          <h2>
            What changes<span>?</span>
          </h2>
          <p className="muted">
            Keep your baseline. Explore a different place or a later start.
          </p>
        </div>
        <button className="text-button" onClick={onClose}>
          Close comparison ×
        </button>
      </div>
      <div className="panel comparison-setup">
        <div>
          <span className="outline-tag">A · SAVED BASELINE</span>
          <h3>{a.analysis.request.site.site_id.replaceAll("-", " ")}</h3>
          <p>{date(a.analysis.request.start_utc)}</p>
          <p>
            {hours(
              (Date.parse(a.analysis.request.end_utc) -
                Date.parse(a.analysis.request.start_utc)) /
                1000,
            )}{" "}
            observation · {a.horizon.observer_height_above_terrain_m.toFixed(1)}{" "}
            m above terrain
          </p>
          <small>
            Changes below apply to B only. A's inputs and result remain saved.
          </small>
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
                Same place, different date
              </button>
              <button
                type="button"
                aria-pressed={mode === "sites"}
                onClick={() => chooseMode("sites")}
              >
                Same date, different place
              </button>
            </div>
            {draft && (
              <>
                {mode === "dates" ? (
                  <>
                    <label>
                      Scenario B start · UTC
                      <input
                        aria-label="Scenario B start UTC"
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
                      Use +48-hour delay ↗
                    </button>
                  </>
                ) : (
                  <>
                    <label>
                      Scenario B region
                      <select
                        aria-label="Scenario B region"
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
                        <option value="custom">Custom coordinates</option>
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
                            {site.display_name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="field-pair">
                      <label>
                        Latitude · °
                        <input
                          aria-label="Scenario B latitude"
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
                        Longitude · °E
                        <input
                          aria-label="Scenario B longitude"
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
                <p className="field-help">
                  Same duration, observer height, horizon and sampling settings
                  as A.
                </p>
                <button className="primary" type="submit">
                  {busy ? "Processing scenario B…" : "Run scenario B"} ↗
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
            Retry connection
          </button>
        </div>
      )}
      {busy && (
        <p role="status" className="job-progress">
          Scenario B: {phase.replaceAll("_", " ")}. A remains available.
        </p>
      )}
      {draftChanged && (
        <p className="alert" role="status">
          B inputs have changed. Run scenario B to update the displayed results;
          the cards below still show the saved run.
        </p>
      )}
      {comparison && b && point ? (
        <>
          <div className="compare-deltas">
            {metrics.map(([target, metric, label, unit]) => {
              const raw = comparison.deltas[target][metric];
              const value = unit === "h" ? raw / 3600 : raw;
              return (
                <div className="metric-card" key={metric + target}>
                  <span className="metric-label">{label} · B − A</span>
                  <strong>
                    {value > 0 ? "+" : ""}
                    {value.toFixed(2)} <small>{unit}</small>
                  </strong>
                  <small>
                    {unit === "pp"
                      ? "percentage points"
                      : "over the full observation window"}
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
          <div className="panel comparison-player">
            <div className="playback-heading">
              <div>
                <p className="eyebrow">
                  {comparison.alignment === "shared_utc"
                    ? "SAME UTC · TWO LOCATIONS"
                    : "SAME ELAPSED TIME · TWO UTC DATES"}
                </p>
                <strong>+{hours(point.elapsed_s)} since each start</strong>
              </div>
              <button
                className="play-button"
                aria-label={playing ? "Pause comparison" : "Play comparison"}
                onClick={() => {
                  if (cursor === comparison.cursor.length - 1) setCursor(0);
                  setPlaying(!playing);
                }}
              >
                {playing ? "Ⅱ" : "▷"}
              </button>
            </div>
            <input
              aria-label="Comparison time"
              aria-valuetext={`${hours(point.elapsed_s)} since each start`}
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
                A cursor ·{" "}
                {date(
                  new Date(
                    Date.parse(a.analysis.request.start_utc) +
                      point.elapsed_s * 1000,
                  ).toISOString(),
                )}
              </span>
              <span>
                B cursor ·{" "}
                {date(
                  new Date(
                    Date.parse(b.analysis.request.start_utc) +
                      point.elapsed_s * 1000,
                  ).toISOString(),
                )}
              </span>
            </div>
            <p className="field-help">
              Sample-by-sample playback. Each sky view shows its preceding
              calculated sample and age; no interpolated sky position is
              implied.
            </p>
          </div>
          <div
            className="compare-tabs"
            role="tablist"
            aria-label="Scenario results"
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
              Scenario A
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
              Scenario B
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
            <summary>Which intervals changed?</summary>
            <p>
              Offsets use elapsed time since each scenario's own start. UTC
              dates are shown above.
            </p>
            {(["sun", "earth"] as const).map((target) => (
              <div key={target}>
                <h4>{target.toUpperCase()}</h4>
                {comparison.changes[target].length ? (
                  comparison.changes[target].map((row, index) => (
                    <p key={index}>
                      +{hours(row.start_elapsed_s)} → +
                      {hours(row.end_elapsed_s)}: A {names[row.a_state]} / B{" "}
                      {names[row.b_state]}
                    </p>
                  ))
                ) : (
                  <p>No state differences in the computed interval.</p>
                )}
              </div>
            ))}
          </details>
        </>
      ) : (
        <p className="comparison-empty">
          Run B to compare two saved results. The original single-site analysis
          remains above.
        </p>
      )}
      <details className="panel comparison-glossary">
        <summary>Horizon, elevation and visibility — a short guide</summary>
        <p>
          <b>Horizon:</b> the angle of the terrain skyline in each compass
          direction.
        </p>
        <p>
          <b>Elevation:</b> how high the Sun or Earth is above the local
          horizontal plane.
        </p>
        <p>
          <b>Visibility:</b> the modeled object clears that terrain skyline.
          Partial sunlight means only part of the Sun's disk is visible.
          Earth-center visibility does not guarantee a communication link.
        </p>
        <p>
          Differences describe this model and interval, not a safe landing
          recommendation. Fine time sampling does not remove terrain
          uncertainty.
        </p>
      </details>
    </section>
  );
}
