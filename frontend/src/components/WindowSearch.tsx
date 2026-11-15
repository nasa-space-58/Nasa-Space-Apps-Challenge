import { useEffect, useState } from "react";
import type { Preset } from "../types";
import { errorText, siteLabel, useTranslate } from "../i18n";

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
          : "",
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
  const { t, tText } = useTranslate();
  const labels = (state: string) => t(`windowSearch.state.${state}`);
  const conditionLabels = (name: string) => t(`windowSearch.condition.${name}`);
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
  const [ranking, setRanking] = useState<{
    method: string;
    ranked: {
      job_id: string;
      score: number;
      state: string;
      deficits: { name: string; shortfall: number; unit: string }[];
    }[];
    excluded: { job_id: string; reason: string }[];
  } | null>(null);
  const [rankError, setRankError] = useState("");
  useEffect(() => {
    if (!id || !view?.completed) return;
    let active = true;
    api("/searches/" + id + "/ranking")
      .then((data) => {
        if (active && Array.isArray(data.ranked)) {
          setRanking(data);
          setRankError("");
        }
      })
      .catch((e) => active && setRankError(errorText(e, t, "windowSearch.unavailable")));
    return () => {
      active = false;
    };
  }, [id, view?.completed, retry]);
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
          setError(errorText(e, t, "scenario.connection"));
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
      setRanking(null);
      setRankError("");
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
      setError(errorText(e, t, "windowSearch.unableToStart"));
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
      setError(errorText(e, t, "windowSearch.unableToOpen"));
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
      aria-label={t("windowSearch.label")}
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t("windowSearch.eyebrow")}</p>
          <h2>
            {t("windowSearch.title")}<span>?</span>
          </h2>
          <p className="muted">{t("windowSearch.intro")}</p>
        </div>
        <span className="outline-tag">{t("windowSearch.tag")}</span>
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
              {t("windowSearch.region")}
              <select
                aria-label={t("windowSearch.region")}
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
                <option value="custom">{t("app.form.custom")}</option>
                {!sites.some((s) => s.site_id === site) &&
                  site !== "custom" && <option value={site}>{site}</option>}
                {sites.map((s) => (
                  <option key={s.site_id} value={s.site_id}>
                    {siteLabel(t, s)}
                  </option>
                ))}
              </select>
            </label>
            <div className="field-pair">
              <label>
                {t("app.form.latitude")} · °
                <input
                  aria-label={t("windowSearch.latitude")}
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
                {t("app.form.longitude")} · °E
                <input
                  aria-label={t("windowSearch.longitude")}
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
              {t("windowSearch.height")} · m
              <input
                aria-label={t("windowSearch.heightLabel")}
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
              {t("windowSearch.first")} · UTC
              <input
                aria-label={t("windowSearch.first")}
                type="datetime-local"
                required
                min="2026-01-01T00:00"
                max="2027-12-31T23:59"
                value={first}
                onChange={(e) => setFirst(e.target.value)}
              />
            </label>
            <label>
              {t("windowSearch.last")} · UTC
              <input
                aria-label={t("windowSearch.last")}
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
                {t("windowSearch.duration")} · {t("windowSearch.h")}
                <input
                  aria-label={t("windowSearch.duration")}
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
                {t("windowSearch.spacing")} · {t("windowSearch.h")}
                <input
                  aria-label={t("windowSearch.spacing")}
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
            <h3>{t("windowSearch.conditions")}</h3>
            <label>
              {t("windowSearch.minSun")} · %
              <input
                aria-label={t("windowSearch.minSun")}
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
              {t("windowSearch.maxDark")} · {t("windowSearch.h")}
              <input
                aria-label={t("windowSearch.maxDark")}
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
              {t("windowSearch.minEarth")} · {t("windowSearch.h")}
              <input
                aria-label={t("windowSearch.minEarth")}
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
              {t("app.form.refine")}
            </label>
            <button type="submit" className="primary">
              {busy ? t("windowSearch.searching") : t("windowSearch.search")} ↗
            </button>
            <button className="text-button" type="button" onClick={example}>
              {t("windowSearch.example")}
            </button>
          </fieldset>
          <p className="field-help">{t("windowSearch.formNote")}</p>
        </form>
        <div className="search-results">
          <p className="search-limitation">{t("windowSearch.limitation")}</p>
          {error && (
            <div className="alert" role="alert">
              {error}{" "}
              {id && (
                <button onClick={() => setRetry((n) => n + 1)}>
                  {t("windowSearch.reconnect")}
                </button>
              )}
            </div>
          )}
          {view ? (
            <>
              <div className="panel search-progress" role="status">
                <div>
                  <b>
                    {t("windowSearch.evaluated", {
                      done: view.completed,
                      total: view.total,
                    })}
                  </b>
                  <span>
                    {t("windowSearch.meetAll", { n: view.matches })} ·{" "}
                    {t(`windowSearch.searchStatus.${view.status}`)}
                  </span>
                </div>
                <progress
                  aria-label={t("windowSearch.progress")}
                  max={view.total}
                  value={view.completed}
                />
                <small>{t("windowSearch.saved", { id: view.id.slice(0, 8) })}</small>
              </div>
              {view.completed === view.total && view.matches === 0 && (
                <div className="panel search-empty">
                  {t("windowSearch.noMatch")}
                </div>
              )}
              {rankError && (
                <p className="alert">
                  {t("windowSearch.rankUnavailable")}: {rankError}{" "}
                  <button onClick={() => setRetry((n) => n + 1)}>
                    {t("windowSearch.retryRanking")}
                  </button>
                </p>
              )}
              {ranking &&
                view.completed === view.total &&
                view.matches === 0 && (
                  <div className="panel closest-results">
                    <h3>{t("windowSearch.closest")}</h3>
                    <p>{tText(ranking.method)}</p>
                    {ranking.ranked.length ? (
                      <ol>
                        {ranking.ranked.slice(0, 3).map((row) => (
                          <li key={row.job_id}>
                            <strong>
                              {stamp(
                                view.candidates.find(
                                  (c) => c.job_id === row.job_id,
                                )!.start_utc,
                              )}{" "}
                              · {t("windowSearch.deficit")} {row.score.toFixed(4)}
                            </strong>
                            <ul>
                              {row.deficits
                                .filter((d) => d.shortfall > 0)
                                .map((d) => (
                                  <li key={d.name}>
                                    {conditionLabels(d.name)}:{" "}
                                    {d.shortfall.toFixed(2)} {d.unit}{" "}
                                    {d.name === "longest_darkness_hours"
                                      ? t("windowSearch.overMax")
                                      : t("windowSearch.belowMin")}
                                  </li>
                                ))}
                            </ul>
                            <button
                              className="text-button"
                              disabled={opening}
                              onClick={() =>
                                choose(
                                  view.candidates.find(
                                    (c) => c.job_id === row.job_id,
                                  )!,
                                  false,
                                )
                              }
                            >
                              {t("windowSearch.review")} ↗
                            </button>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p>{t("windowSearch.noRank")}</p>
                    )}
                    {ranking.excluded.length > 0 && (
                      <p>
                        {t("windowSearch.excluded", { n: ranking.excluded.length })}
                      </p>
                    )}
                  </div>
                )}
              <label className="checkbox-label search-filter">
                <input
                  type="checkbox"
                  checked={onlyMatches}
                  onChange={(e) => setOnlyMatches(e.target.checked)}
                />
                {t("windowSearch.onlyMatches")}
              </label>
              <div
                className="search-grid-strip"
                role="list"
                aria-label={t("windowSearch.grid")}
              >
                {view.candidates.map((c, i) => (
                  <button
                    role="listitem"
                    key={c.job_id}
                    className={c.state}
                    aria-label={t("windowSearch.candidate", {
                      n: i + 1,
                      state: labels(c.state),
                    })}
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
                      <span>{labels(c.state)}</span>
                      <small>{c.job_id.slice(0, 8)}</small>
                    </div>
                    <div className="search-candidate-content">
                      <h3>{stamp(c.start_utc)}</h3>
                      <p>{t("windowSearch.through", { end: stamp(c.end_utc) })}</p>
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
                                {conditionLabels(condition.name)}
                              </span>
                              <b>
                                {condition.actual.toFixed(2)}{" "}
                                {condition.operator} {condition.threshold}{" "}
                                <small>
                                  ·{" "}
                                  {condition.passed === null
                                    ? t("windowSearch.unknown")
                                    : condition.passed
                                      ? t("windowSearch.met")
                                      : t("windowSearch.notMet")}
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
                            {t("windowSearch.unknownCoverage", {
                              sun: c.unknown_seconds.sun,
                              earth: c.unknown_seconds.earth,
                            })}
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
                            {t("windowSearch.open")} ↗
                          </button>
                          <button
                            className="text-button"
                            disabled={opening}
                            onClick={() => choose(c, true)}
                          >
                            {t("windowSearch.useA")} ↗
                          </button>
                        </div>
                      )}
                    </div>
                  </article>
                ))}
            </>
          ) : (
            <div className="panel search-empty">
              <h3>{t("windowSearch.emptyTitle")}</h3>
              <p>{t("windowSearch.emptyText")}</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
