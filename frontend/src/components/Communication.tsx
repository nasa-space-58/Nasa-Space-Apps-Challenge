import { useEffect, useRef, useState } from "react";
import { errorText, useTranslate, type Translate } from "../i18n";

// R2/S4: displays backend communication windows; no geometry is computed here.
type Window = {
  start_utc: string;
  end_utc: string;
  state: "available" | "unavailable" | "unknown";
  duration_s: number;
  station_ids?: string[];
};
type Metrics = {
  available_duration_s: number;
  unknown_duration_s: number;
  available_percent_of_interval: number;
  longest_available_s: number;
  window_count: number;
};
type Station = { station_id: string; complex: string; antenna: string };
type CommunicationJob = {
  id: string;
  status: string;
  error: string | null;
  request: { min_elevation_deg: number };
  result: null | {
    min_elevation_deg: number;
    start_utc: string;
    end_utc: string;
    stations: Station[];
    windows: {
      stations: Record<string, { intervals: Window[]; metrics: Metrics }>;
      combined: { intervals: Window[]; metrics: Metrics };
    };
    source: { station_document: string; station_url: string; sample_step_s: number };
    limitations: string[];
  };
};

const ACTIVE = ["pending", "acquiring", "computing"];
const STATUSES = ["pending", "acquiring", "computing", "ready", "insufficient_data", "failed"];
const LIMITS = [
  { value: 6, key: "communication.limit6" },
  { value: 10, key: "communication.limit10" },
];
const stateLabel = (t: Translate, state: Window["state"]) =>
  t(`communication.state.${state}`);
const hours = (t: Translate, s: number) =>
  t("units.hours", { value: (s / 3600).toFixed(1) });
const utc = (value: string, locale: string) =>
  new Date(value).toLocaleString(locale, {
    timeZone: "UTC",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });

function storageKey(jobId: string) {
  return `lln.communication.${jobId}`;
}
function remembered(jobId: string) {
  try {
    return localStorage.getItem(storageKey(jobId)) || undefined;
  } catch {
    return undefined;
  }
}
function remember(jobId: string, id: string) {
  try {
    localStorage.setItem(storageKey(jobId), id);
  } catch {
    // Storage is a convenience; the saved job stays available on the server.
  }
}

async function call(path: string, init?: RequestInit) {
  const response = await fetch(`/api${path}`, init);
  const body = await response.json();
  if (!response.ok) {
    const detail = body?.detail;
    throw Error(typeof detail === "string" ? detail : "");
  }
  return body;
}

function Band({
  title,
  intervals,
  start,
  end,
  current,
}: {
  title: string;
  intervals: Window[];
  start: string;
  end: string;
  current?: string;
}) {
  const { t, locale } = useTranslate();
  const from = Date.parse(start);
  const span = Date.parse(end) - from;
  const pct = (value: string) => ((Date.parse(value) - from) / span) * 100;
  return (
    <div className="timeline-row">
      <span>{title}</span>
      <div
        className="timeline-track"
        role="img"
        aria-label={`${title}: ${intervals
          .map((i) =>
            t("communication.segment", {
              state: stateLabel(t, i.state),
              via: "",
              start: utc(i.start_utc, locale),
              end: utc(i.end_utc, locale),
            }),
          )
          .join("; ")}`}
      >
        {intervals.map((interval, i) => (
          <div
            key={i}
            className={`time-segment link-${interval.state}`}
            style={{
              left: `${pct(interval.start_utc)}%`,
              width: `${pct(interval.end_utc) - pct(interval.start_utc)}%`,
            }}
            title={t("communication.segment", {
              state: stateLabel(t, interval.state),
              via: interval.station_ids?.length
                ? t("communication.via", { stations: interval.station_ids.join(", ") })
                : "",
              start: utc(interval.start_utc, locale),
              end: utc(interval.end_utc, locale),
            })}
          />
        ))}
        {current && <i className="time-cursor" style={{ left: `${pct(current)}%` }} />}
      </div>
    </div>
  );
}

export default function Communication({
  jobId,
  current,
}: {
  jobId: string;
  current?: string;
}) {
  const { t, tText, locale } = useTranslate();
  const h = (s: number) => hours(t, s);
  const u = (value: string) => utc(value, locale);
  const [limit, setLimit] = useState(6);
  const [job, setJob] = useState<CommunicationJob | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const poll = (id: string) => {
    window.clearTimeout(timer.current);
    call(`/communications/${encodeURIComponent(id)}`)
      .then((body: CommunicationJob) => {
        setJob(body);
        setLimit(body.request.min_elevation_deg);
        if (ACTIVE.includes(body.status)) {
          timer.current = window.setTimeout(() => poll(id), 1500);
        }
      })
      .catch((e: Error) => setError(errorText(e, t, "communication.requestFailed")));
  };

  useEffect(() => {
    const saved = remembered(jobId);
    if (saved) poll(saved);
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  const start = async () => {
    setError("");
    setBusy(true);
    try {
      const created: CommunicationJob = await call(
        `/analyses/${encodeURIComponent(jobId)}/communication`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ min_elevation_deg: limit }),
        },
      );
      remember(jobId, created.id);
      setJob(created);
      poll(created.id);
    } catch (e) {
      setError(errorText(e, t, "communication.requestFailed"));
    } finally {
      setBusy(false);
    }
  };

  const running = job ? ACTIVE.includes(job.status) : false;
  const result = job?.result;
  const combined = result?.windows.combined;
  const windows = combined?.intervals.filter((i) => i.state === "available") ?? [];

  return (
    <section className="panel communication-panel" aria-labelledby="communication-title">
      <div className="panel-heading">
        <span id="communication-title">{t("communication.title")}</span>
        <small>{t("communication.subtitle")}</small>
      </div>
      <div className="communication-body">
        <p className="communication-intro">
          {t("communication.introBefore")} <b>{t("communication.introAnd")}</b>{" "}
          {t("communication.introAfter")}
        </p>
        <div className="communication-controls">
          <label>
            {t("communication.limit")}
            <select
              aria-label={t("communication.limit")}
              value={limit}
              disabled={busy || running}
              onChange={(e) => setLimit(Number(e.target.value))}
            >
              {LIMITS.map((option) => (
                <option key={option.value} value={option.value}>
                  {t(option.key)}
                </option>
              ))}
            </select>
          </label>
          <button
            className="primary"
            disabled={busy || running}
            onClick={start}
          >
            {result ? t("communication.recalculate") : t("communication.check")} ↗
          </button>
        </div>
        {job && (
          <p className={`communication-status ${job.status}`} role="status">
            {STATUSES.includes(job.status)
              ? t(`communication.status.${job.status}`)
              : job.status}
            {job.error ? ` — ${job.error}` : ""}
          </p>
        )}
        {error && (
          <p className="communication-status failed" role="alert">
            {error}
          </p>
        )}
        {result && combined && (
          <>
            <div className="communication-metrics">
              <div>
                <span className="metric-label">{t("communication.metric")}</span>
                <strong>{combined.metrics.available_percent_of_interval.toFixed(1)}%</strong>
                <small>
                  {t("communication.ofWindow", {
                    hours: h(combined.metrics.available_duration_s),
                  })}
                </small>
              </div>
              <div>
                <span className="metric-label">{t("communication.windows")}</span>
                <strong>{combined.metrics.window_count}</strong>
                <small>
                  {t("communication.longest", {
                    hours: h(combined.metrics.longest_available_s),
                  })}
                </small>
              </div>
              <div>
                <span className="metric-label">{t("communication.unknown")}</span>
                <strong>{h(combined.metrics.unknown_duration_s)}</strong>
                <small>
                  {t("communication.atLimit", { deg: result.min_elevation_deg })}
                </small>
              </div>
            </div>
            <div className="timelines communication-bands">
              {result.stations.map((station) => (
                <Band
                  key={station.station_id}
                  title={station.station_id}
                  intervals={result.windows.stations[station.station_id].intervals}
                  start={result.start_utc}
                  end={result.end_utc}
                  current={current}
                />
              ))}
              <Band
                title={t("communication.any")}
                intervals={combined.intervals}
                start={result.start_utc}
                end={result.end_utc}
                current={current}
              />
              <div className="timeline-legend">
                <span>▰ {t("communication.state.available")}</span>
                <span>▰ {t("communication.state.unavailable")}</span>
                <span>▨ {t("communication.state.unknown")}</span>
              </div>
            </div>
            <ul className="station-key">
              {result.stations.map((station) => (
                <li key={station.station_id}>
                  <b>{station.station_id}</b> {station.complex} · {station.antenna} ·{" "}
                  {h(result.windows.stations[station.station_id].metrics.available_duration_s)}
                </li>
              ))}
            </ul>
            {windows.length ? (
              <ol className="window-list" aria-label={t("communication.windowList")}>
                {windows.map((w, i) => (
                  <li key={i}>
                    {u(w.start_utc)} – {u(w.end_utc)} UTC · {h(w.duration_s)} ·{" "}
                    {w.station_ids?.join(" + ")}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="communication-empty">
                {t("communication.empty")}
              </p>
            )}
            <details className="communication-limits">
              <summary>
                {t("communication.limits")} <span>+</span>
              </summary>
              <ul>
                {result.limitations.map((text) => (
                  <li key={text}>{tText(text)}</li>
                ))}
                <li>
                  {t("communication.interpolated", {
                    minutes: result.source.sample_step_s / 60,
                  })}
                </li>
              </ul>
              <a href={result.source.station_url} target="_blank" rel="noreferrer">
                {result.source.station_document} ↗
              </a>
            </details>
          </>
        )}
      </div>
    </section>
  );
}
