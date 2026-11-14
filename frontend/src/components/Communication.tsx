import { useEffect, useRef, useState } from "react";

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
const STATUS: Record<string, string> = {
  pending: "Waiting in queue",
  acquiring: "Requesting DSN station geometry from NASA/JPL",
  computing: "Intersecting station and Earth visibility",
  ready: "Communication windows ready",
  insufficient_data: "Partial result · unknown coverage",
  failed: "Communication analysis could not complete",
};
const LIMITS = [
  { value: 6, label: "6° · DSN receive (generic)" },
  { value: 10, label: "10° · low-power transmit (generic)" },
];
const STATE_LABEL = {
  available: "Communication possible",
  unavailable: "No communication",
  unknown: "Unknown",
};
const hours = (s: number) => `${(s / 3600).toFixed(1)} h`;
const utc = (value: string) =>
  new Date(value).toLocaleString("en-GB", {
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
    throw Error(typeof detail === "string" ? detail : "Request failed");
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
          .map((i) => `${STATE_LABEL[i.state]} ${utc(i.start_utc)} to ${utc(i.end_utc)} UTC`)
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
            title={`${STATE_LABEL[interval.state]}${
              interval.station_ids?.length ? ` via ${interval.station_ids.join(", ")}` : ""
            } · ${utc(interval.start_utc)} to ${utc(interval.end_utc)} UTC`}
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
      .catch((e: Error) => setError(e.message));
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
      setError(e instanceof Error ? e.message : "Request failed");
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
        <span id="communication-title">DIRECT-TO-EARTH COMMUNICATION</span>
        <small>NASA DSN · GEOMETRIC</small>
      </div>
      <div className="communication-body">
        <p className="communication-intro">
          A window needs Earth above your local horizon <b>and</b> the Moon above a
          Deep Space Network antenna's elevation limit.
        </p>
        <div className="communication-controls">
          <label>
            Station elevation limit
            <select
              aria-label="Station elevation limit"
              value={limit}
              disabled={busy || running}
              onChange={(e) => setLimit(Number(e.target.value))}
            >
              {LIMITS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            className="primary"
            disabled={busy || running}
            onClick={start}
          >
            {result ? "Recalculate windows" : "Check DSN communication"} ↗
          </button>
        </div>
        {job && (
          <p className={`communication-status ${job.status}`} role="status">
            {STATUS[job.status] ?? job.status}
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
                <span className="metric-label">COMMUNICATION</span>
                <strong>{combined.metrics.available_percent_of_interval.toFixed(1)}%</strong>
                <small>{hours(combined.metrics.available_duration_s)} of the window</small>
              </div>
              <div>
                <span className="metric-label">WINDOWS</span>
                <strong>{combined.metrics.window_count}</strong>
                <small>longest {hours(combined.metrics.longest_available_s)}</small>
              </div>
              <div>
                <span className="metric-label">UNKNOWN</span>
                <strong>{hours(combined.metrics.unknown_duration_s)}</strong>
                <small>at {result.min_elevation_deg}° station limit</small>
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
                title="ANY"
                intervals={combined.intervals}
                start={result.start_utc}
                end={result.end_utc}
                current={current}
              />
              <div className="timeline-legend">
                <span>▰ Communication possible</span>
                <span>▰ No communication</span>
                <span>▨ Unknown</span>
              </div>
            </div>
            <ul className="station-key">
              {result.stations.map((station) => (
                <li key={station.station_id}>
                  <b>{station.station_id}</b> {station.complex} · {station.antenna} ·{" "}
                  {hours(result.windows.stations[station.station_id].metrics.available_duration_s)}
                </li>
              ))}
            </ul>
            {windows.length ? (
              <ol className="window-list" aria-label="Communication windows">
                {windows.map((w, i) => (
                  <li key={i}>
                    {utc(w.start_utc)} – {utc(w.end_utc)} UTC · {hours(w.duration_s)} ·{" "}
                    {w.station_ids?.join(" + ")}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="communication-empty">
                No direct-to-Earth window in this observation interval.
              </p>
            )}
            <details className="communication-limits">
              <summary>
                Model limits & sources <span>+</span>
              </summary>
              <ul>
                {result.limitations.map((text) => (
                  <li key={text}>{text}</li>
                ))}
                <li>
                  Station rise/set is interpolated between {result.source.sample_step_s / 60}-minute
                  JPL Horizons samples.
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
