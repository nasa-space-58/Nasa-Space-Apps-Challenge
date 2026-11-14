import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import HorizonPlot from "./components/HorizonPlot";
import PolarMap from "./components/PolarMap";
import Comparison from "./components/Comparison";
import WindowSearch from "./components/WindowSearch";
import type { Job, Preset, Result, Setting, Interval } from "./types";

const MoonScene = lazy(() => import("./components/MoonScene"));
const activeStates = ["pending", "preparing", "analyzing", "refining"];
const stateLabel: Record<string, string> = {
  pending: "Waiting in queue",
  preparing: "Acquiring & checking NASA data",
  analyzing: "Computing your horizon",
  refining: "Computing & refining visibility",
  ready: "Analysis ready",
  insufficient_data: "Partial result · missing coverage",
  failed: "Analysis could not complete",
};
const displayState: Record<string, string> = {
  full: "Full sunlight",
  partial: "Partial sunlight",
  dark: "Terrain-blocked",
  visible: "Center visible",
  blocked: "Center blocked",
  unknown: "Unknown",
  boundary: "At the horizon",
};
const hours = (seconds: number | undefined) =>
  seconds === undefined ? "—" : `${(seconds / 3600).toFixed(1)} h`;
const utcLabel = (value: string) =>
  new Date(value).toLocaleString("en-GB", {
    timeZone: "UTC",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
const jump = (id: string) =>
  document.getElementById(id)?.scrollIntoView({
    behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
  });

class SceneBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="scene-fallback">
        <img
          src="/nasa/moon.jpg"
          alt="NASA lunar surface texture. The 3D scene is unavailable; mission analysis remains usable."
        />
      </div>
    ) : (
      this.props.children
    );
  }
}
async function api(path: string, init?: RequestInit) {
  const response = await fetch(`/api${path}`, init);
  const body = await response.json();
  if (!response.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((item: { msg: string }) => item.msg).join("; ")
          : "Request failed. Please try again.";
    throw new Error(detail);
  }
  return body;
}
function SettingText({ value }: { value?: Setting }) {
  if (!value) return <>—</>;
  if (!value.estimated_at_utc)
    return (
      <span className="small-value">
        {value.status === "insufficient_data"
          ? "Insufficient data"
          : "No event in range"}
      </span>
    );
  return (
    <>
      <span>{utcLabel(value.estimated_at_utc)}</span>
      <small>
        {value.status === "candidate_after_unknown"
          ? "Earlier event may be missing"
          : value.status === "transition_overlaps_selected_time"
            ? "Transition overlaps selected time"
            : `Estimated · UTC · in ${hours(value.time_until_estimate_s)}`}
        {value.bracket_start_utc &&
          value.bracket_end_utc &&
          ` · ${utcLabel(value.bracket_start_utc)}–${utcLabel(value.bracket_end_utc)}`}
      </small>
    </>
  );
}
function Timeline({
  title,
  intervals,
  start,
  end,
  current,
}: {
  title: string;
  intervals: Interval[];
  start: string;
  end: string;
  current: string;
}) {
  const from = Date.parse(start),
    duration = Date.parse(end) - from;
  return (
    <div className="timeline-row">
      <span>{title}</span>
      <div
        className="timeline-track"
        role="img"
        aria-label={`${title}: ${intervals.map((i) => `${displayState[i.state]}, ${hours(i.duration_s)}`).join("; ")}`}
      >
        {intervals.map((interval, i) => (
          <div
            key={i}
            className={`time-segment ${interval.state}`}
            style={{
              left: `${((Date.parse(interval.start_utc) - from) / duration) * 100}%`,
              width: `${((Date.parse(interval.end_utc) - Date.parse(interval.start_utc)) / duration) * 100}%`,
            }}
            title={`${displayState[interval.state]} · ${utcLabel(interval.start_utc)} to ${utcLabel(interval.end_utc)} UTC`}
          />
        ))}
        <i
          className="time-cursor"
          style={{
            left: `${((Date.parse(current) - from) / duration) * 100}%`,
          }}
        />
      </div>
    </div>
  );
}
export default function App() {
  const intro = useRef<HTMLElement>(null);
  const sourcesOpener = useRef<HTMLElement | null>(null);
  const openSources = () => {
    sourcesOpener.current = document.activeElement as HTMLElement;
    setSources(true);
  };
  const [progress, setProgress] = useState(0);
  const [sceneVisible, setSceneVisible] = useState(true);
  const [reduced, setReduced] = useState(false);
  const [webgl, setWebgl] = useState(false);
  const [sites, setSites] = useState<Preset[]>([]);
  const [siteId, setSiteId] = useState("malapert-massif-r23");
  const [lat, setLat] = useState("-85.9948");
  const [lon, setLon] = useState("359.76422");
  const [height, setHeight] = useState("2");
  const [start, setStart] = useState("2026-11-04T00:00");
  const [duration, setDuration] = useState("24");
  const [refine, setRefine] = useState(true);
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [comparisonBase, setComparisonBase] = useState<{
    id: string;
    result: Result;
  } | null>(null);
  const [frameIndex, setFrameIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState("");
  const [apiError, setApiError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sources, setSources] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [resolved, setResolved] = useState("");
  const busy = submitting || (!!job && activeStates.includes(job.status));
  const frame = result?.frames[frameIndex];

  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    setReduced(mq?.matches ?? false);
    const changed = () => setReduced(mq?.matches ?? false);
    mq?.addEventListener("change", changed);
    setWebgl(typeof window.WebGLRenderingContext !== "undefined");
    let scheduled = false;
    const scroll = () => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => {
        scheduled = false;
        if (!intro.current) return;
        const box = intro.current.getBoundingClientRect();
        setProgress(
          Math.min(
            1,
            Math.max(0, -box.top / (box.height - window.innerHeight)),
          ),
        );
        setSceneVisible(box.bottom > 0);
      });
    };
    window.addEventListener("scroll", scroll, { passive: true });
    scroll();
    return () => {
      window.removeEventListener("scroll", scroll);
      mq?.removeEventListener("change", changed);
    };
  }, []);
  const loadSites = () => {
    api("/presets")
      .then((data) => {
        setSites(data.sites);
        setApiError("");
      })
      .catch((e) => setApiError(e.message));
  };
  useEffect(() => {
    loadSites();
    const saved = localStorage.getItem("lunar-analysis-id");
    if (saved)
      api(`/analyses/${saved}`)
        .then((savedJob) => {
          setJob(savedJob);
          const submitted = savedJob.submission;
          if (submitted) {
            setSiteId(submitted.site_id);
            setLat(String(submitted.latitude_deg));
            setLon(String(submitted.longitude_deg_east));
            setHeight(String(submitted.observer_height_m));
            setStart(new Date(submitted.start_utc).toISOString().slice(0, 16));
            setDuration(
              String(
                (Date.parse(submitted.end_utc) -
                  Date.parse(submitted.start_utc)) /
                  3600000,
              ),
            );
            setRefine(submitted.refine);
          }
        })
        .catch(() => localStorage.removeItem("lunar-analysis-id"));
  }, []);
  useEffect(() => {
    if (!job) return;
    let canceled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next: Job = await api(`/analyses/${job.id}`);
        if (canceled) return;
        setJob(next);
        if (next.status === "ready" || next.status === "insufficient_data") {
          const view: Result = await api(`/analyses/${job.id}/result`);
          if (!canceled) {
            setResult(view);
            setFrameIndex(0);
            setError("");
          }
        } else if (next.status === "failed")
          setError(
            next.error || "The analysis failed. You can try a new request.",
          );
        else timer = setTimeout(poll, 1800);
      } catch (e) {
        if (!canceled) {
          setError(
            `Connection interrupted. Retrying… ${e instanceof Error ? e.message : ""}`,
          );
          timer = setTimeout(poll, 4000);
        }
      }
    };
    poll();
    return () => {
      canceled = true;
      clearTimeout(timer);
    };
  }, [job?.id]);
  useEffect(() => {
    if (!playing || !result) return;
    const timer = setInterval(
      () =>
        setFrameIndex((index) => {
          if (index >= result.frames.length - 1) {
            setPlaying(false);
            return index;
          }
          return index + 1;
        }),
      600,
    );
    return () => clearInterval(timer);
  }, [playing, result]);
  useEffect(() => {
    if (reduced) setPlaying(false);
  }, [reduced]);

  useEffect(() => {
    if (!sources) return;
    const opener = sourcesOpener.current;
    const dialog = document.querySelector<HTMLElement>(".sources-modal");
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const trap = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSources(false);
        return;
      }
      if (event.key !== "Tab" || !dialog) return;
      const controls = Array.from(
        dialog.querySelectorAll<HTMLElement>("button, a[href]"),
      );
      const first = controls[0],
        last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      }
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => {
      document.removeEventListener("keydown", trap);
      document.body.style.overflow = previousOverflow;
      opener?.focus();
    };
  }, [sources]);

  const selectSite = (site: Preset) => {
    setSiteId(site.site_id);
    setLat(String(site.latitude_deg));
    setLon(String(site.longitude_deg_east));
    setHeight(String(site.proposed_observer_height_above_terrain_m));
    setResolved("");
  };
  const makeBody = () => ({
    site_id: siteId,
    latitude_deg: Number(lat),
    longitude_deg_east: Number(lon),
    observer_height_m: Number(height),
    start_utc: new Date(start + "Z").toISOString(),
    end_utc: new Date(
      Date.parse(start + "Z") + Number(duration) * 3600000,
    ).toISOString(),
    step_s: 3600,
    refine,
  });
  const launch = async (example = false) => {
    setError("");
    setSubmitting(true);
    setPlaying(false);
    setResult(null);
    jump("mission");
    try {
      let body: ReturnType<typeof makeBody>;
      if (example) {
        setLat("-85.9948");
        setLon("359.76422");
        setHeight("2");
        setStart("2026-11-04T00:00");
        setDuration("24");
        setSiteId("malapert-massif-r23");
        setRefine(true);
        body = {
          site_id: "malapert-massif-r23",
          latitude_deg: -85.9948,
          longitude_deg_east: 359.76422,
          observer_height_m: 2,
          start_utc: "2026-11-04T00:00:00Z",
          end_utc: "2026-11-05T00:00:00Z",
          step_s: 3600,
          refine: true,
        };
      } else {
        body = makeBody();
      }
      const next: Job = await api("/analyses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setJob(next);
      localStorage.setItem("lunar-analysis-id", next.id);
    } catch (e) {
      setJob(null);
      setError(e instanceof Error ? e.message : "Unable to start analysis");
    } finally {
      setSubmitting(false);
    }
  };
  const openSearchResult = async (id: string, compare: boolean) => {
    const [selected, view] = await Promise.all([
      api(`/analyses/${id}`),
      api(`/analyses/${id}/result`),
    ]);
    const submitted = selected.submission;
    setSiteId(submitted.site_id);
    setLat(String(submitted.latitude_deg));
    setLon(String(submitted.longitude_deg_east));
    setHeight(String(submitted.observer_height_m));
    setStart(new Date(submitted.start_utc).toISOString().slice(0, 16));
    setDuration(
      String(
        (Date.parse(submitted.end_utc) - Date.parse(submitted.start_utc)) /
          3600000,
      ),
    );
    setRefine(submitted.refine);
    setSearchOpen(false);
    setPlaying(false);
    setJob(selected);
    setResult(view);
    setFrameIndex(0);
    setError("");
    localStorage.setItem("lunar-analysis-id", id);
    setComparisonBase(compare ? { id, result: view } : null);
    setTimeout(() => jump(compare ? "comparison" : "mission"), 0);
  };

  const resolve = async () => {
    setResolved("Checking cached terrain…");
    try {
      const observer = await api("/observers/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(makeBody()),
      });
      setResolved(
        `Reference-sphere altitude: ${observer.altitude_km.toFixed(4)} km. Containing 80 m pixel.`,
      );
    } catch (e) {
      setResolved(
        e instanceof Error ? e.message : "Terrain is not available yet.",
      );
    }
  };

  return (
    <main>
      <header className="navigation">
        <button
          className="brand"
          onClick={() =>
            window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" })
          }
          aria-label="Back to introduction"
        >
          <span className="brand-mark">◒</span>
          <span>
            LUNAR<span className="brand-sub">LANDING NAVIGATOR</span>
          </span>
        </button>
        <nav aria-label="Main navigation">
          <button
            aria-pressed={!searchOpen}
            onClick={() => {
              setSearchOpen(false);
              setTimeout(() => jump("mission"), 0);
            }}
          >
            Mission control
          </button>
          <button
            aria-pressed={searchOpen}
            onClick={() => {
              setSearchOpen(true);
              setTimeout(() => jump("window-search"), 0);
            }}
          >
            Time search
          </button>
          <button onClick={openSources}>
            Sources <span>↗</span>
          </button>
        </nav>
        <span className="team-label">
          2GBRAM <i /> NASA SPACE APPS
        </span>
      </header>
      <section
        className="introduction"
        ref={intro}
        aria-label="From orbit to the lunar surface"
      >
        <div className="scene-pin" aria-hidden="true">
          {webgl ? (
            <SceneBoundary>
              <Suspense
                fallback={
                  <div className="scene-loading">Loading lunar scene…</div>
                }
              >
                <MoonScene
                  progress={progress}
                  reduced={reduced}
                  visible={sceneVisible}
                />
              </Suspense>
            </SceneBoundary>
          ) : (
            <div className="scene-fallback">
              <img src="/nasa/moon.jpg" alt="" />
            </div>
          )}
          <div className="scene-vignette" />
          <div className="scene-caption">
            <span>NASA 3D ASSETS</span>
            <span>
              Illustrative orbit & descent · not a trajectory simulation
            </span>
          </div>
          <div className="scene-index">
            <span>0{progress < 0.3 ? 1 : progress < 0.75 ? 2 : 3}</span>
            <div>
              <i style={{ height: `${Math.max(4, progress * 100)}%` }} />
            </div>
            <small>03</small>
          </div>
        </div>
        <section className="story hero" id="intro">
          <div className="story-copy">
            <p className="eyebrow">
              <i /> A DIFFERENT PERSPECTIVE ON LANDING
            </p>
            <h1>
              Lunar Landing
              <br />
              <em>Navigator</em>
            </h1>
            <p className="intro-copy">
              A place on the Moon.
              <br />
              The right moment to be there.
            </p>
            <p className="muted story-description">
              Explore the lunar south pole. Follow the light.
              <br />
              Plan around the terrain that shapes your mission.
            </p>
            <div className="hero-actions">
              <button
                className="primary"
                onClick={() => {
                  setSearchOpen(false);
                  setTimeout(() => jump("mission"), 0);
                }}
              >
                Plan your observation <span>↗</span>
              </button>
              <button
                className="text-button"
                onClick={() =>
                  intro.current &&
                  window.scrollTo({
                    top: window.innerHeight,
                    behavior: reduced ? "auto" : "smooth",
                  })
                }
              >
                Explore the descent <span>↓</span>
              </button>
            </div>
            <div className="hero-meta">
              <span>
                80–90° S <small>EXPLORATION REGION</small>
              </span>
              <span>
                NASA / LOLA <small>REAL TERRAIN DATA</small>
              </span>
            </div>
          </div>
        </section>
        <section className="story orbit">
          <div className="story-copy">
            <p className="eyebrow">01 / ORBITAL RECONNAISSANCE</p>
            <h2>
              First, see
              <br />
              the whole picture.
            </h2>
            <p className="intro-copy">Every ridge changes the horizon.</p>
            <p className="muted story-description">
              Lunar Reconnaissance Orbiter data gives us the terrain. Sun and
              Earth positions reveal how that terrain changes what you can see.
            </p>
            <div className="story-note">
              <span className="note-line" /> LRO MODEL · NASA / CHRISTOPHER R.
              MEANEY
            </div>
          </div>
        </section>
        <section className="story descent">
          <div className="story-copy">
            <p className="eyebrow">02 / SURFACE PERSPECTIVE</p>
            <h2>
              Make every
              <br />
              <em>hour count.</em>
            </h2>
            <p className="muted story-description">
              Set your location and observation window. See when sunlight
              reaches your horizon—and when Earth slips behind it.
            </p>
            <button
              className="primary"
              onClick={() => {
                setSearchOpen(false);
                setTimeout(() => jump("mission"), 0);
              }}
            >
              Enter mission control <span>↓</span>
            </button>
            <div className="story-note">
              APOLLO MODEL · ILLUSTRATIVE LANDING
            </div>
          </div>
        </section>
      </section>

      <section id="mission" className="mission-section" hidden={searchOpen}>
        <div className="section-heading">
          <div>
            <p className="eyebrow">03 / YOUR OBSERVATION WINDOW</p>
            <h2>
              Mission control<span>.</span>
            </h2>
          </div>
          <div className={`connection ${busy ? "working" : ""}`}>
            <i /> {job ? stateLabel[job.status] : "Ready to plan"}
          </div>
        </div>
        {apiError && (
          <div className="alert" role="alert">
            The API is unavailable. {apiError}{" "}
            <button onClick={loadSites}>Reconnect</button>
          </div>
        )}
        <div className="mission-layout">
          <aside className="mission-sidebar">
            <div className="panel map-panel">
              <div className="panel-heading">
                <span>SOUTH POLE</span>
                <small>Coordinate selector</small>
              </div>
              <PolarMap
                lat={Number(lat)}
                lon={Number(lon)}
                sites={sites}
                onPreset={(site) => !busy && selectSite(site)}
                onSelect={(latitude, longitude) => {
                  if (!busy) {
                    setLat(String(latitude));
                    setLon(String(longitude));
                    setSiteId("custom");
                    setResolved("");
                  }
                }}
              />
              <p className="map-caption">
                80–90° S · schematic polar grid
                <br />
                Click to select, or enter coordinates below.
              </p>
            </div>
            <form
              className="panel parameters"
              onSubmit={(e) => {
                e.preventDefault();
                launch();
              }}
            >
              <div className="panel-heading">
                <span>OBSERVATION SETUP</span>
                <span>01</span>
              </div>
              <fieldset disabled={busy}>
                <label>
                  Landing region
                  <select
                    value={siteId}
                    onChange={(e) => {
                      const site = sites.find(
                        (s) => s.site_id === e.target.value,
                      );
                      if (site) selectSite(site);
                      else setSiteId("custom");
                    }}
                  >
                    <option value="custom">Custom coordinates</option>
                    {sites.length ? (
                      sites.map((site) => (
                        <option key={site.site_id} value={site.site_id}>
                          {site.display_name}
                        </option>
                      ))
                    ) : (
                      <option value="malapert-massif-r23">
                        Malapert Massif — R23
                      </option>
                    )}
                  </select>
                </label>
                <div className="field-pair">
                  <label>
                    Latitude · °
                    <input
                      aria-label="Latitude"
                      type="number"
                      min="-90"
                      max="-80"
                      step="any"
                      required
                      value={lat}
                      onChange={(e) => {
                        setLat(e.target.value);
                        setSiteId("custom");
                        setResolved("");
                      }}
                    />
                  </label>
                  <label>
                    Longitude · °E
                    <input
                      aria-label="Longitude"
                      type="number"
                      min="0"
                      max="359.999999"
                      step="any"
                      required
                      value={lon}
                      onChange={(e) => {
                        setLon(e.target.value);
                        setSiteId("custom");
                        setResolved("");
                      }}
                    />
                  </label>
                </div>
                <label>
                  Observer height above terrain · m
                  <input
                    type="number"
                    min="0.1"
                    max="100"
                    step="0.1"
                    required
                    value={height}
                    onChange={(e) => {
                      setHeight(e.target.value);
                      setResolved("");
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="text-button height-check"
                  onClick={resolve}
                >
                  Check terrain height ↗
                </button>
                {resolved && (
                  <p className="field-help" role="status">
                    {resolved}
                  </p>
                )}
                <label>
                  Start date & time · UTC
                  <input
                    type="datetime-local"
                    min="2026-01-01T00:00"
                    max="2027-12-31T23:59"
                    required
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label>
                  Observation duration
                  <select
                    value={duration}
                    onChange={(e) => setDuration(e.target.value)}
                  >
                    <option value="24">24 hours</option>
                    <option value="72">3 days</option>
                    <option value="168">7 days</option>
                    <option value="720">30 days</option>
                    {!["24", "72", "168", "720"].includes(duration) && (
                      <option value={duration}>{duration} hours</option>
                    )}
                  </select>
                </label>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={refine}
                    onChange={(e) => setRefine(e.target.checked)}
                  />
                  <span>
                    Refine detected transitions
                    <small>5-minute → 1-minute samples</small>
                  </span>
                </label>
                <button className="primary run-button" type="submit">
                  {busy ? "Analysis in progress" : "Run analysis"}{" "}
                  <span>{busy ? "◌" : "↗"}</span>
                </button>
                <p className="field-help">
                  NASA source data · estimated geometric visibility
                </p>
              </fieldset>
            </form>
          </aside>
          <div className="mission-results">
            {error && (
              <div className="alert" role="alert">
                {error}
              </div>
            )}
            {busy && (
              <div className="job-progress" role="status">
                <span className="pulse" />
                <div>
                  <strong>{stateLabel[job?.status || "pending"]}</strong>
                  <p>
                    You can leave this page. Your analysis ID is saved on this
                    device.
                  </p>
                </div>
                <span className="mono">{job?.id.slice(0, 8)}</span>
              </div>
            )}
            <div className="result-heading">
              <div>
                <p className="eyebrow">TERRAIN-RELATIVE VIEW</p>
                <h3>
                  {result
                    ? result.analysis.request.site.site_id.replaceAll("-", " ")
                    : "Your window to the surface"}
                </h3>
              </div>
              <span className="outline-tag">MOON_ME / UTC</span>
            </div>
            <div className="metric-grid">
              <div className="metric-card">
                <span className="metric-label">
                  <i className="sun-dot" /> SUNLIGHT AVAILABILITY
                </span>
                <strong>
                  {result
                    ? `${result.analysis.sun.metrics.visible_percent_of_interval.toFixed(1)}%`
                    : "—"}
                </strong>
                <small>
                  {result
                    ? `${hours(result.analysis.sun.metrics.visible_duration_s)} · full + partial disk`
                    : "of the observation window"}
                </small>
              </div>
              <div className="metric-card">
                <span className="metric-label">
                  <i className="earth-dot" /> EARTH VISIBILITY
                </span>
                <strong>
                  {result
                    ? `${result.analysis.earth.metrics.visible_percent_of_interval.toFixed(1)}%`
                    : "—"}
                </strong>
                <small>
                  {result
                    ? `${hours(result.analysis.earth.metrics.visible_duration_s)} · center visibility`
                    : "geometric center visibility"}
                </small>
              </div>
              <div className="metric-card">
                <span className="metric-label">LONGEST DARKNESS</span>
                <strong>
                  {hours(result?.analysis.sun.metrics.longest_blocked_s)}
                </strong>
                <small>continuous terrain-blocked sunlight</small>
              </div>
            </div>
            <div className="panel horizon-panel">
              <div className="panel-heading">
                <span>LOCAL HORIZON</span>
                <div className="legend">
                  <span>
                    <i className="sun-dot" /> Sun
                  </span>
                  <span>
                    <i className="earth-dot" /> Earth
                  </span>
                  <span>
                    <i className="terrain-dot" /> Terrain
                  </span>
                </div>
              </div>
              {result && frame ? (
                <>
                  <HorizonPlot result={result} frame={frame} />
                  <div className="body-readouts">
                    <span>
                      <i className="sun-dot" /> Sun{" "}
                      {frame.sun.elevation_deg?.toFixed(2) ?? "—"}°{" "}
                      <b>{displayState[frame.sun.state]}</b>
                    </span>
                    <span>
                      <i className="earth-dot" /> Earth{" "}
                      {frame.earth.elevation_deg?.toFixed(2) ?? "—"}°{" "}
                      <b>{displayState[frame.earth.state]}</b>
                    </span>
                  </div>
                </>
              ) : (
                <div className="empty-horizon">
                  <div className="empty-orbit">
                    <i />
                  </div>
                  <h3>
                    {busy
                      ? "Tracing your horizon…"
                      : "A new perspective awaits."}
                  </h3>
                  <p>
                    {busy
                      ? "Checking terrain, observer height and Sun / Earth positions."
                      : "Choose a region and time to reveal the terrain, light and visibility."}
                  </p>
                  {!busy && (
                    <button
                      className="text-button"
                      onClick={() => launch(true)}
                    >
                      Run the Malapert example ↗
                    </button>
                  )}
                </div>
              )}
            </div>
            <div className="panel playback-panel">
              <div className="playback-heading">
                <div>
                  <p className="eyebrow">OBSERVATION TIME</p>
                  <strong>
                    {frame ? `${utcLabel(frame.utc)} UTC` : "Select a moment"}
                  </strong>
                </div>
                <button
                  className="play-button"
                  disabled={!result?.frames.length}
                  onClick={() => {
                    if (result && frameIndex === result.frames.length - 1)
                      setFrameIndex(0);
                    setPlaying(!playing);
                  }}
                  aria-label={playing ? "Pause playback" : "Play observation"}
                >
                  {playing ? "Ⅱ" : "▷"}
                </button>
              </div>
              <input
                aria-label="Observation time"
                aria-valuetext={
                  frame ? `${utcLabel(frame.utc)} UTC` : undefined
                }
                type="range"
                min="0"
                max={Math.max(0, (result?.frames.length || 1) - 1)}
                value={frameIndex}
                disabled={!result?.frames.length}
                onChange={(e) => {
                  setPlaying(false);
                  setFrameIndex(Number(e.target.value));
                }}
              />
              <div className="range-labels">
                <span>
                  {result
                    ? `${utcLabel(result.analysis.request.start_utc)} UTC`
                    : "START"}
                </span>
                <span>Sample-by-sample playback</span>
                <span>
                  {result
                    ? `${utcLabel(result.analysis.request.end_utc)} UTC`
                    : "END"}
                </span>
              </div>
              {result && frame && (
                <div className="timelines">
                  <Timeline
                    title="SUN"
                    intervals={result.analysis.sun.intervals}
                    start={result.analysis.request.start_utc}
                    end={result.analysis.request.end_utc}
                    current={frame.utc}
                  />
                  <Timeline
                    title="EARTH"
                    intervals={result.analysis.earth.intervals}
                    start={result.analysis.request.start_utc}
                    end={result.analysis.request.end_utc}
                    current={frame.utc}
                  />
                  <div className="timeline-legend">
                    <span>▰ Full / visible</span>
                    <span>▧ Partial</span>
                    <span>▰ Blocked</span>
                    <span>▨ Unknown</span>
                  </div>
                </div>
              )}
            </div>
            <div className="event-grid">
              <div className="panel event-card">
                <span className="metric-label">NEXT SUNSET · LAST LIMB</span>
                <strong>
                  <SettingText value={frame?.sun.next_setting} />
                </strong>
              </div>
              <div className="panel event-card">
                <span className="metric-label">NEXT EARTHSET · CENTER</span>
                <strong>
                  <SettingText value={frame?.earth.next_setting} />
                </strong>
              </div>
            </div>
            {result && (
              <div className="panel trace-panel">
                <details>
                  <summary>
                    Observation details & model limits <span>+</span>
                  </summary>
                  <div className="trace-content">
                    <p>
                      <b>Sun:</b> longest continuous visibility{" "}
                      {hours(result.analysis.sun.metrics.longest_visible_s)} ·
                      unknown{" "}
                      {hours(result.analysis.sun.metrics.unknown_duration_s)}.
                    </p>
                    <p>
                      <b>Earth:</b> longest continuous visibility{" "}
                      {hours(result.analysis.earth.metrics.longest_visible_s)} ·
                      longest blocked period{" "}
                      {hours(result.analysis.earth.metrics.longest_blocked_s)} ·
                      unknown{" "}
                      {hours(result.analysis.earth.metrics.unknown_duration_s)}.
                    </p>
                    <p>
                      80 m terrain grid ·{" "}
                      {(
                        result.horizon.settings.search_distance_m / 1000
                      ).toFixed(0)}{" "}
                      km search radius ·{" "}
                      {result.horizon.settings.azimuth_step_deg}° horizon
                      spacing. Ground height uses the containing pixel; observer{" "}
                      {result.horizon.observer_height_above_terrain_m.toFixed(
                        1,
                      )}{" "}
                      m above it.
                    </p>
                    <p>
                      Durations are estimates. Fine time sampling does not
                      remove terrain uncertainty. The ±
                      {result.analysis.spatial_sensitivity.offset_deg}°
                      sensitivity scenarios are not error bounds. No eclipse,
                      link-budget or landing-safety model.
                    </p>
                    <p>
                      Refinement:{" "}
                      {result.analysis.refinement.status.replaceAll("_", " ")} ·{" "}
                      {result.analysis.provenance.ephemerides.length} source
                      bundles.
                    </p>
                    <code>Analysis {result.analysis.analysis_id}</code>
                    <button className="text-button" onClick={openSources}>
                      View NASA sources ↗
                    </button>
                  </div>
                </details>
              </div>
            )}
            {result && job && (
              <button
                className="primary add-comparison"
                onClick={() => {
                  setPlaying(false);
                  setComparisonBase({ id: job.id, result });
                  setTimeout(() => jump("comparison"), 0);
                }}
              >
                Add comparison ↗
              </button>
            )}
            <p className="results-footnote">
              A horizon is a perspective, not a promise. Results describe the
              sampled terrain and observation window.
            </p>
          </div>
        </div>
      </section>
      {comparisonBase && !searchOpen && (
        <Comparison
          key={comparisonBase.id}
          aId={comparisonBase.id}
          a={comparisonBase.result}
          sites={sites}
          onClose={() => setComparisonBase(null)}
        />
      )}
      {searchOpen && <WindowSearch sites={sites} onChoose={openSearchResult} />}
      <footer>
        <span>
          2GBRAM <i> / </i> LUNAR LANDING NAVIGATOR
        </span>
        <span>Built with NASA open data.</span>
        <a href="/api/docs" target="_blank" rel="noreferrer">
          Open API documentation ↗
        </a>
      </footer>
      {sources && (
        <div className="modal-backdrop" onClick={() => setSources(false)}>
          <section
            className="sources-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="sources-title"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") setSources(false);
            }}
          >
            <button
              autoFocus
              className="modal-close"
              onClick={() => setSources(false)}
              aria-label="Close sources"
            >
              ×
            </button>
            <p className="eyebrow">PROVENANCE</p>
            <h2 id="sources-title">A view built on real data.</h2>
            <p>
              NASA models illustrate the journey. Scientific results use cached
              Horizons ephemerides and the verified LOLA terrain product.
            </p>
            <ul>
              <li>
                <a
                  href="https://science.nasa.gov/3d-resources/lunar-reconnaissance-orbiter-a/"
                  target="_blank"
                  rel="noreferrer"
                >
                  LRO model
                </a>{" "}
                · NASA / Christopher R. Meaney
              </li>
              <li>
                <a
                  href="https://science.nasa.gov/3d-resources/apollo-lunar-module/"
                  target="_blank"
                  rel="noreferrer"
                >
                  Apollo Lunar Module
                </a>{" "}
                · NASA / Michael D. Carbajal
              </li>
              <li>
                <a
                  href="https://svs.gsfc.nasa.gov/4720/"
                  target="_blank"
                  rel="noreferrer"
                >
                  CGI Moon Kit
                </a>{" "}
                · NASA / Goddard / Scientific Visualization Studio
              </li>
              <li>
                <a
                  href="https://pgda.gsfc.nasa.gov/products/90"
                  target="_blank"
                  rel="noreferrer"
                >
                  LOLA south-pole terrain
                </a>{" "}
                · Barker et al. (2023)
              </li>
              <li>
                <a
                  href="https://ssd.jpl.nasa.gov/horizons/"
                  target="_blank"
                  rel="noreferrer"
                >
                  JPL Horizons
                </a>{" "}
                · Sun and Earth observer ephemerides
              </li>
              {sites.map((site) => (
                <li key={site.site_id}>
                  <a
                    href={site.coordinate_source_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {site.display_name} coordinate source
                  </a>
                </li>
              ))}
            </ul>
            <small>
              The introduction is an illustrative animation. Apollo is not shown
              as a historical south-pole mission. NASA does not endorse this
              application.
            </small>
          </section>
        </div>
      )}
    </main>
  );
}
