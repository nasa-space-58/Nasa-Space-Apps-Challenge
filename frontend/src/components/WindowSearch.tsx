import { useEffect, useState } from "react";
import type { Preset } from "../types";

type Condition = {
  name: string;
  actual: number;
  threshold: number;
  operator: string;
  passed: boolean | null;
};
type Candidate = {
  job_id: string;
  start_utc: string;
  end_utc: string;
  state: string;
  conditions?: Condition[];
  error?: string;
  unknown_seconds?: { sun: number; earth: number };
};
type SearchView = {
  id: string;
  status: string;
  completed: number;
  total: number;
  matches: number;
  limitation: string;
  candidates: Candidate[];
};
const labels: Record<string, string> = {
  waiting: "Queued",
  processing: "Analyzing",
  match: "Meets all conditions",
  not_met: "Conditions not met",
  unknown: "Unknown coverage",
  failed: "Analysis failed",
};
const conditionLabels: Record<string, string> = {
  sunlight_percent: "Sunlight · %",
  longest_darkness_hours: "Longest darkness · h",
  earth_visible_hours: "Earth visible · h",
};
const stamp = (value: string) =>
  new Date(value).toISOString().replace("T", " ").replace(".000Z", " UTC");
async function api(path: string, body?: unknown) {
  const response = await fetch(
    "/api" + path,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  const data = await response.json();
  if (!response.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail.map((x: { msg: string }) => x.msg).join("; ")
          : "Search unavailable",
    );
  return data;
}
export default function WindowSearch({
  sites,
  onChoose,
}: {
  sites: Preset[];
  onChoose: (id: string, compare: boolean) => Promise<void>;
}) {
  const [site, setSite] = useState("malapert-massif-r23");
  const [lat, setLat] = useState("-85.9948");
  const [lon, setLon] = useState("359.76422");
  const [height, setHeight] = useState("2");
  const [first, setFirst] = useState("2026-11-04T00:00");
  const [last, setLast] = useState("2026-11-06T00:00");
  const [duration, setDuration] = useState("24");
  const [step, setStep] = useState("24");
  const [sun, setSun] = useState("70");
  const [dark, setDark] = useState("6");
  const [earth, setEarth] = useState("4");
  const [refine, setRefine] = useState(true);
  const [view, setView] = useState<SearchView | null>(null);
  const [id, setId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [opening, setOpening] = useState(false);
  const [retry, setRetry] = useState(0);
  const [onlyMatches, setOnlyMatches] = useState(false);
  const busy =
    sending || (!!view && ["pending", "running"].includes(view.status));
  useEffect(() => {
    try {
      const saved = JSON.parse(
        localStorage.getItem("lunar-window-search") || "null",
      );
      if (saved) {
        setId(saved.id);
        const d = saved.draft;
        if (d) {
          setSite(d.site);
          setLat(d.lat);
          setLon(d.lon);
          setHeight(d.height);
          setFirst(d.first);
          setLast(d.last);
          setDuration(d.duration);
          setStep(d.step);
          setSun(d.sun);
          setDark(d.dark);
          setEarth(d.earth);
          setRefine(d.refine);
        }
      }
    } catch {
      localStorage.removeItem("lunar-window-search");
    }
  }, []);
  useEffect(() => {
    if (!id) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api("/searches/" + id);
        if (!active) return;
        setView(next);
        setError("");
        if (["pending", "running"].includes(next.status))
          timer = setTimeout(poll, 1800);
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "Connection interrupted");
          timer = setTimeout(poll, 5000);
        }
      }
    };
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [id, retry]);
  const submit = async () => {
    setSending(true);
    setError("");
    setOnlyMatches(false);
    try {
      const start = new Date(first + "Z");
      const body = {
        template: {
          site_id: site,
          latitude_deg: Number(lat),
          longitude_deg_east: Number(lon),
          observer_height_m: Number(height),
          start_utc: start.toISOString(),
          end_utc: new Date(
            start.getTime() + Number(duration) * 3600000,
          ).toISOString(),
          step_s: 3600,
          refine,
        },
        last_start_utc: new Date(last + "Z").toISOString(),
        start_step_hours: Number(step),
        min_sunlight_percent: Number(sun),
        max_darkness_hours: Number(dark),
        min_earth_hours: Number(earth),
      };
      const next = await api("/searches", body);
      setView(next);
      setId(next.id);
      localStorage.setItem(
        "lunar-window-search",
        JSON.stringify({
          id: next.id,
          draft: {
            site,
            lat,
            lon,
            height,
            first,
            last,
            duration,
            step,
            sun,
            dark,
            earth,
            refine,
          },
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to start search");
    } finally {
      setSending(false);
    }
  };
  const choose = async (candidate: Candidate, compare: boolean) => {
    setOpening(true);
    setError("");
    try {
      await onChoose(candidate.job_id, compare);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to open result");
    } finally {
      setOpening(false);
    }
  };
  const example = () => {
    setSite("malapert-massif-r23");
    setLat("-85.9948");
    setLon("359.76422");
    setHeight("2");
    setFirst("2026-11-04T00:00");
    setLast("2026-11-06T00:00");
    setDuration("24");
    setStep("24");
    setSun("70");
    setDark("6");
    setEarth("0");
    setRefine(true);
  };
  return (
    <section
      id="window-search"
      className="window-search-section"
      aria-label="Observation time search"
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">05 / FIND YOUR WINDOW</p>
          <h2>
            When could it work<span>?</span>
          </h2>
          <p className="muted">
            Explore candidate starts that meet your light and visibility
            conditions.
          </p>
        </div>
        <span className="outline-tag">UP TO 16 STARTS</span>
      </div>
      <div className="search-layout">
        <form
          className="panel search-form"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <fieldset disabled={busy}>
            <label>
              Search region
              <select
                aria-label="Search region"
                value={site}
                onChange={(e) => {
                  setSite(e.target.value);
                  const next = sites.find((s) => s.site_id === e.target.value);
                  if (next) {
                    setLat(String(next.latitude_deg));
                    setLon(String(next.longitude_deg_east));
                  }
                }}
              >
                <option value="custom">Custom coordinates</option>
                {!sites.some((s) => s.site_id === site) &&
                  site !== "custom" && <option value={site}>{site}</option>}
                {sites.map((s) => (
                  <option key={s.site_id} value={s.site_id}>
                    {s.display_name}
                  </option>
                ))}
              </select>
            </label>
            <div className="field-pair">
              <label>
                Latitude · °
                <input
                  aria-label="Search latitude"
                  type="number"
                  required
                  min="-90"
                  max="-80"
                  step="any"
                  value={lat}
                  onChange={(e) => {
                    setLat(e.target.value);
                    setSite("custom");
                  }}
                />
              </label>
              <label>
                Longitude · °E
                <input
                  aria-label="Search longitude"
                  type="number"
                  required
                  min="0"
                  max="359.999999"
                  step="any"
                  value={lon}
                  onChange={(e) => {
                    setLon(e.target.value);
                    setSite("custom");
                  }}
                />
              </label>
            </div>
            <label>
              Height above terrain · m
              <input
                aria-label="Search observer height"
                type="number"
                required
                min="0.1"
                max="100"
                step="0.1"
                value={height}
                onChange={(e) => setHeight(e.target.value)}
              />
            </label>
            <label>
              First candidate start · UTC
              <input
                aria-label="First candidate start UTC"
                type="datetime-local"
                required
                min="2026-01-01T00:00"
                max="2027-12-31T23:59"
                value={first}
                onChange={(e) => setFirst(e.target.value)}
              />
            </label>
            <label>
              Last candidate start · UTC
              <input
                aria-label="Last candidate start UTC"
                type="datetime-local"
                required
                min={first}
                max="2027-12-31T23:59"
                value={last}
                onChange={(e) => setLast(e.target.value)}
              />
            </label>
            <div className="field-pair">
              <label>
                Mission duration · h
                <input
                  aria-label="Search mission duration"
                  type="number"
                  required
                  min="1"
                  max="744"
                  step="1"
                  value={duration}
                  onChange={(e) => setDuration(e.target.value)}
                />
              </label>
              <label>
                Start spacing · h
                <input
                  aria-label="Candidate start spacing"
                  type="number"
                  required
                  min="1"
                  max="168"
                  step="1"
                  value={step}
                  onChange={(e) => setStep(e.target.value)}
                />
              </label>
            </div>
            <h3>Your conditions</h3>
            <label>
              Minimum sunlight · %
              <input
                aria-label="Minimum sunlight percentage"
                type="number"
                required
                min="0"
                max="100"
                step="0.1"
                value={sun}
                onChange={(e) => setSun(e.target.value)}
              />
            </label>
            <label>
              Maximum continuous darkness · h
              <input
                aria-label="Maximum darkness hours"
                type="number"
                required
                min="0"
                max="744"
                step="0.1"
                value={dark}
                onChange={(e) => setDark(e.target.value)}
              />
            </label>
            <label>
              Minimum Earth visibility · h
              <input
                aria-label="Minimum Earth visibility hours"
                type="number"
                required
                min="0"
                max="744"
                step="0.1"
                value={earth}
                onChange={(e) => setEarth(e.target.value)}
              />
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={refine}
                onChange={(e) => setRefine(e.target.checked)}
              />
              Refine detected transitions
            </label>
            <button type="submit" className="primary">
              {busy ? "Searching…" : "Search candidate starts"} ↗
            </button>
            <button className="text-button" type="button" onClick={example}>
              Set sunlight example · Earth minimum 0 h
            </button>
          </fieldset>
          <p className="field-help">
            Each candidate is a complete analysis. Start span ≤31 days; at most
            16 starts and 2 active searches. Initial data acquisition may take
            minutes.
          </p>
        </form>
        <div className="search-results">
          <p className="search-limitation">
            This checks a grid of possible starts, not every instant. Shorter
            opportunities between starts may be missed. Meeting these conditions
            is not a landing-safety recommendation.
          </p>
          {error && (
            <div className="alert" role="alert">
              {error}{" "}
              {id && (
                <button onClick={() => setRetry((n) => n + 1)}>
                  Reconnect search
                </button>
              )}
            </div>
          )}
          {view ? (
            <>
              <div className="panel search-progress" role="status">
                <div>
                  <b>
                    {view.completed} / {view.total} evaluated
                  </b>
                  <span>
                    {view.matches} meet all conditions · {view.status}
                  </span>
                </div>
                <progress
                  aria-label="Search progress"
                  max={view.total}
                  value={view.completed}
                />
                <small>
                  Search {view.id.slice(0, 8)} · saved on this device
                </small>
              </div>
              {view.completed === view.total && view.matches === 0 && (
                <div className="panel search-empty">
                  No confirmed match on this grid. Review each unmet condition
                  or unknown coverage below; try different dates or adjust your
                  thresholds.
                </div>
              )}
              <label className="checkbox-label search-filter">
                <input
                  type="checkbox"
                  checked={onlyMatches}
                  onChange={(e) => setOnlyMatches(e.target.checked)}
                />
                Show confirmed matches only
              </label>
              <div
                className="search-grid-strip"
                role="list"
                aria-label="Candidate start grid"
              >
                {view.candidates.map((c, i) => (
                  <button
                    role="listitem"
                    key={c.job_id}
                    className={c.state}
                    aria-label={`Candidate ${i + 1}: ${labels[c.state]}`}
                    title={stamp(c.start_utc)}
                    onClick={() =>
                      document
                        .getElementById("candidate-" + c.job_id)
                        ?.scrollIntoView({
                          behavior: window.matchMedia?.(
                            "(prefers-reduced-motion: reduce)",
                          ).matches
                            ? "auto"
                            : "smooth",
                        })
                    }
                  >
                    {i + 1}
                  </button>
                ))}
              </div>
              {view.candidates
                .filter((c) => !onlyMatches || c.state === "match")
                .map((c) => (
                  <article
                    className={`panel search-candidate ${c.state}`}
                    id={"candidate-" + c.job_id}
                    key={c.job_id}
                  >
                    <div className="panel-heading">
                      <span>{labels[c.state]}</span>
                      <small>{c.job_id.slice(0, 8)}</small>
                    </div>
                    <div className="search-candidate-content">
                      <h3>{stamp(c.start_utc)}</h3>
                      <p>Through {stamp(c.end_utc)}</p>
                      {c.conditions && (
                        <ul>
                          {c.conditions.map((condition) => (
                            <li key={condition.name}>
                              <span>
                                {condition.passed === null
                                  ? "?"
                                  : condition.passed
                                    ? "✓"
                                    : "×"}{" "}
                                {conditionLabels[condition.name]}
                              </span>
                              <b>
                                {condition.actual.toFixed(2)}{" "}
                                {condition.operator} {condition.threshold}{" "}
                                <small>
                                  ·{" "}
                                  {condition.passed === null
                                    ? "unknown"
                                    : condition.passed
                                      ? "met"
                                      : "not met"}
                                </small>
                              </b>
                            </li>
                          ))}
                        </ul>
                      )}
                      {c.unknown_seconds &&
                        (c.unknown_seconds.sun > 0 ||
                          c.unknown_seconds.earth > 0) && (
                          <p>
                            Unknown coverage: Sun {c.unknown_seconds.sun} s ·
                            Earth {c.unknown_seconds.earth} s. This candidate is
                            not confirmed suitable.
                          </p>
                        )}
                      {c.error && <p className="alert">{c.error}</p>}
                      {["match", "not_met", "unknown"].includes(c.state) && (
                        <div className="search-actions">
                          <button
                            className="text-button"
                            disabled={opening}
                            onClick={() => choose(c, false)}
                          >
                            Open analysis ↗
                          </button>
                          <button
                            className="text-button"
                            disabled={opening}
                            onClick={() => choose(c, true)}
                          >
                            Use as comparison A ↗
                          </button>
                        </div>
                      )}
                    </div>
                  </article>
                ))}
            </>
          ) : (
            <div className="panel search-empty">
              <h3>Define your observation window.</h3>
              <p>
                Choose where to observe and the conditions that matter. Results
                show both matches and the reasons other starts do not qualify.
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
