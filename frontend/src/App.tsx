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
import MissionStage from "./components/MissionStage";
import Comparison from "./components/Comparison";
import WindowSearch from "./components/WindowSearch";
import DateScrubber from "./components/DateScrubber";
import ExportPanel from "./components/ExportPanel";
import ScenarioTransfer from "./components/ScenarioTransfer";
import Classroom from "./components/Classroom";
import Communication from "./components/Communication";
import SpaceWeather from "./components/SpaceWeather";
import Recommendations from "./components/Recommendations";
import Descent from "./components/Descent";
import MissionPlanner from "./components/MissionPlanner";
import type { Job, Preset, Result, Setting, Interval } from "./types";
import { LanguageToggle, useTranslate, type Translate } from "./i18n";

const MoonScene = lazy(() => import("./components/MoonScene"));
const activeStates = ["pending", "preparing", "analyzing", "refining"];
const stateLabel = (t: Translate, status: string) => t(`jobState.${status}`);
const displayState = (t: Translate, state: string) => t(`displayState.${state}`);
const hours = (t: Translate, seconds: number | undefined) =>
  seconds === undefined
    ? "—"
    : t("units.hours", { value: (seconds / 3600).toFixed(1) });
const utcLabel = (value: string, locale: string) =>
  new Date(value).toLocaleString(locale, {
    timeZone: "UTC",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
type View = "home" | "mission";
const routes: Record<View, string> = {
  home: "",
  mission: "#/mission-control",
};
/** Former standalone pages are mission-control panel tabs; their hashes still open them. */
const MODULE_HASHES: Record<string, string> = {
  "#/time-search": "window-search",
  "#/recommendations": "recommendations",
  "#/descent": "descent",
  "#/classroom": "classroom",
};
const moduleFromHash = (hash: string): string | undefined =>
  MODULE_HASHES[hash];
const viewFromHash = (hash: string): View =>
  moduleFromHash(hash) || hash === routes.mission ? "mission" : "home";
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
        <SceneFallbackImage />
      </div>
    ) : (
      this.props.children
    );
  }
}
function SceneFallbackImage() {
  const { t } = useTranslate();
  return <img src="/nasa/moon.jpg" alt={t("app.sceneFallbackAlt")} />;
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
          : "";
    throw new Error(detail || "request-failed");
  }
  return body;
}
function SettingText({ value }: { value?: Setting }) {
  const { t, locale } = useTranslate();
  if (!value) return <>—</>;
  if (!value.estimated_at_utc)
    return (
      <span className="small-value">
        {value.status === "insufficient_data"
          ? t("app.setting.insufficientData")
          : t("app.setting.noEvent")}
      </span>
    );
  return (
    <>
      <span>{utcLabel(value.estimated_at_utc, locale)}</span>
      <small>
        {value.status === "candidate_after_unknown"
          ? t("app.setting.earlierMissing")
          : value.status === "transition_overlaps_selected_time"
            ? t("app.setting.overlaps")
            : t("app.setting.estimated", {
                hours: hours(t, value.time_until_estimate_s),
              })}
        {value.bracket_start_utc &&
          value.bracket_end_utc &&
          ` · ${utcLabel(value.bracket_start_utc, locale)}–${utcLabel(value.bracket_end_utc, locale)}`}
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
  const { t, locale } = useTranslate();
  const from = Date.parse(start),
    duration = Date.parse(end) - from;
  return (
    <div className="timeline-row">
      <span>{title}</span>
      <div
        className="timeline-track"
        role="img"
        aria-label={`${title}: ${intervals.map((i) => `${displayState(t, i.state)}, ${hours(t, i.duration_s)}`).join("; ")}`}
      >
        {intervals.map((interval, i) => (
          <div
            key={i}
            className={`time-segment ${interval.state}`}
            style={{
              left: `${((Date.parse(interval.start_utc) - from) / duration) * 100}%`,
              width: `${((Date.parse(interval.end_utc) - Date.parse(interval.start_utc)) / duration) * 100}%`,
            }}
            title={t("app.timeline.segment", {
              state: displayState(t, interval.state),
              start: utcLabel(interval.start_utc, locale),
              end: utcLabel(interval.end_utc, locale),
            })}
          />
        ))}
        <i
          className="time-cursor"
          title={t("app.timeline.cursor")}
          style={{
            left: `${((Date.parse(current) - from) / duration) * 100}%`,
          }}
        />
      </div>
    </div>
  );
}
export default function App() {
  const { t, locale } = useTranslate();
  const h = (seconds: number | undefined) => hours(t, seconds);
  const utc = (value: string) => utcLabel(value, locale);
  const errorText = (e: unknown, fallback: string) =>
    e instanceof Error && e.message !== "request-failed"
      ? e.message
      : t(fallback);
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
  const [compactScene, setCompactScene] = useState(false);
  const [motionPaused, setMotionPaused] = useState(false);
  const [pageVisible, setPageVisible] = useState(true);
  const [sites, setSites] = useState<Preset[]>([]);
  const [siteId, setSiteId] = useState("malapert-massif-r23");
  const [lat, setLat] = useState("-85.9948");
  const [lon, setLon] = useState("359.76422");
  const [height, setHeight] = useState("2");
  const [start, setStart] = useState("2026-11-04T00:00");
  const [duration, setDuration] = useState("24");
  const [refine, setRefine] = useState(true);
  const [launchKey, setLaunchKey] = useState(0);
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
  const [sharedB, setSharedB] = useState<string | undefined>(undefined);
  const shared = useRef(new URLSearchParams(location.search));
  const [view, setView] = useState<View>(() =>
    shared.current.has("analysis") ? "mission" : viewFromHash(location.hash),
  );
  const goTo = (next: View) => {
    if (window.scrollY) window.scrollTo({ top: 0, behavior: "auto" });
    const url = `${location.pathname}${location.search}${routes[next]}`;
    if (location.hash !== routes[next]) window.history.pushState({}, "", url);
    setView(next);
  };
  const [panel, setPanel] = useState<string>(
    () => moduleFromHash(location.hash) ?? "plan",
  );
  const [resolved, setResolved] = useState("");
  const busy = submitting || (!!job && activeStates.includes(job.status));
  const frame = result?.frames[frameIndex];

  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    setReduced(mq?.matches ?? false);
    const changed = () => setReduced(mq?.matches ?? false);
    mq?.addEventListener("change", changed);
    setWebgl(typeof window.WebGLRenderingContext !== "undefined");
    const resize = () => setCompactScene(window.innerWidth < 760);
    const visibility = () => setPageVisible(!document.hidden);
    resize();
    visibility();
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", visibility);
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
            Math.max(
              0,
              -box.top / Math.max(1, box.height - window.innerHeight),
            ),
          ),
        );
        setSceneVisible(box.bottom > 0);
      });
    };
    window.addEventListener("scroll", scroll, { passive: true });
    window.addEventListener("resize", scroll);
    scroll();
    return () => {
      window.removeEventListener("scroll", scroll);
      window.removeEventListener("resize", scroll);
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", visibility);
      mq?.removeEventListener("change", changed);
    };
  }, []);
  const loadSites = () => {
    api("/presets")
      .then((data) => {
        setSites(data.sites);
        setApiError("");
      })
      .catch((e) => setApiError(errorText(e, "app.errors.requestFailed")));
  };
  useEffect(() => {
    loadSites();
    const saved =
      shared.current.get("analysis") ||
      localStorage.getItem("lunar-analysis-id");
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
        .catch(() => {
          if (shared.current.has("analysis"))
            setError(t("app.errors.sharedUnavailable"));
          else localStorage.removeItem("lunar-analysis-id");
        });
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
            const requested =
              shared.current.get("analysis") === job.id
                ? Number(shared.current.get("frame") || 0)
                : 0;
            setFrameIndex(
              Number.isInteger(requested)
                ? Math.max(0, Math.min(requested, view.frames.length - 1))
                : 0,
            );
            if (shared.current.get("analysis") === job.id) {
              const other = shared.current.get("compare");
              if (other) {
                setSharedB(other);
                setComparisonBase({ id: job.id, result: view });
              }
              setPanel(other ? "comparison" : "results");
            }
            setError("");
          }
        } else if (next.status === "failed")
          setError(next.error || t("app.errors.analysisFailed"));
        else timer = setTimeout(poll, 1800);
      } catch (e) {
        if (!canceled) {
          setError(
            t("app.errors.connectionInterrupted", {
              detail: e instanceof Error ? errorText(e, "app.errors.requestFailed") : "",
            }),
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
    const follow = () => {
      setView(viewFromHash(location.hash));
      const module = moduleFromHash(location.hash);
      if (module) setPanel(module);
    };
    window.addEventListener("popstate", follow);
    window.addEventListener("hashchange", follow);
    return () => {
      window.removeEventListener("popstate", follow);
      window.removeEventListener("hashchange", follow);
    };
  }, []);
  useEffect(() => {
    if (view !== "mission") setPlaying(false);
    if (view === "home") window.dispatchEvent(new Event("scroll"));
  }, [view]);

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
    clearSharedLink();
    setError("");
    setSubmitting(true);
    setPlaying(false);
    setResult(null);
    setLaunchKey((key) => key + 1);
    setPanel("plan");
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
      setError(errorText(e, "app.errors.unableToStart"));
    } finally {
      setSubmitting(false);
    }
  };
  const clearSharedLink = () => {
    shared.current = new URLSearchParams();
    window.history.replaceState(
      {},
      "",
      `${window.location.pathname}${window.location.hash}`,
    );
  };
  const openSearchResult = async (
    id: string,
    compare: boolean,
    otherId?: string,
  ) => {
    const [selected, view] = await Promise.all([
      api(`/analyses/${id}`),
      api(`/analyses/${id}/result`),
    ]);
    clearSharedLink();
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
    goTo("mission");
    setSharedB(otherId);
    setPlaying(false);
    setJob(selected);
    setResult(view);
    setFrameIndex(0);
    setError("");
    localStorage.setItem("lunar-analysis-id", id);
    setComparisonBase(compare ? { id, result: view } : null);
    setPanel(compare ? "comparison" : "results");
  };

  const resolve = async () => {
    setResolved(t("app.resolve.checking"));
    try {
      const observer = await api("/observers/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(makeBody()),
      });
      setResolved(
        t("app.resolve.altitude", { km: observer.altitude_km.toFixed(4) }),
      );
    } catch (e) {
      setResolved(errorText(e, "app.resolve.unavailable"));
    }
  };

  const resultsModule = (
    <section className="analysis-results" aria-labelledby="analysis-title">
      <div className="analysis-intro">
        <p className="eyebrow">{t("app.results.eyebrow")}</p>
        <h3 id="analysis-title">{t("app.results.title")}</h3>
        <p>{t("app.results.intro")}</p>
      </div>
      <div className="result-heading">
        <div>
          <p className="eyebrow">{t("app.results.terrainView")}</p>
          <h3>
            {result
              ? result.analysis.request.site.site_id.replaceAll("-", " ")
              : t("app.results.windowTitle")}
          </h3>
        </div>
        <span className="outline-tag">MOON_ME / UTC</span>
      </div>
      <div className="metric-grid">
        <div className="metric-card">
          <span className="metric-label">
            <i className="sun-dot" /> {t("app.results.sunlight")}
          </span>
          <strong>
            {result
              ? `${result.analysis.sun.metrics.visible_percent_of_interval.toFixed(1)}%`
              : "—"}
          </strong>
          <small>
            {result
              ? t("app.results.sunlightDetail", {
                  hours: h(result.analysis.sun.metrics.visible_duration_s),
                })
              : t("app.results.ofWindow")}
          </small>
        </div>
        <div className="metric-card">
          <span className="metric-label">
            <i className="earth-dot" /> {t("app.results.earth")}
          </span>
          <strong>
            {result
              ? `${result.analysis.earth.metrics.visible_percent_of_interval.toFixed(1)}%`
              : "—"}
          </strong>
          <small>
            {result
              ? t("app.results.earthDetail", {
                  hours: h(result.analysis.earth.metrics.visible_duration_s),
                })
              : t("app.results.earthEmpty")}
          </small>
        </div>
        <div className="metric-card">
          <span className="metric-label">{t("app.results.longestDark")}</span>
          <strong>{h(result?.analysis.sun.metrics.longest_blocked_s)}</strong>
          <small>{t("app.results.longestDarkDetail")}</small>
        </div>
      </div>
      <div className="panel horizon-panel">
        <div className="panel-heading">
          <span>{t("app.results.localHorizon")}</span>
          <div className="legend">
            <span>
              <i className="sun-dot" /> {t("common.sun")}
            </span>
            <span>
              <i className="earth-dot" /> {t("common.earth")}
            </span>
            <span>
              <i className="terrain-dot" /> {t("common.terrain")}
            </span>
          </div>
        </div>
        <p className="panel-help">{t("app.results.horizonHelp")}</p>
        {result && frame ? (
          <>
            <HorizonPlot result={result} frame={frame} />
            <div className="body-readouts">
              <span>
                <i className="sun-dot" /> {t("common.sun")}{" "}
                {frame.sun.elevation_deg?.toFixed(2) ?? "—"}°{" "}
                <b>{displayState(t, frame.sun.state)}</b>
              </span>
              <span>
                <i className="earth-dot" /> {t("common.earth")}{" "}
                {frame.earth.elevation_deg?.toFixed(2) ?? "—"}°{" "}
                <b>{displayState(t, frame.earth.state)}</b>
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
                ? t("app.results.tracing")
                : t("app.results.awaits")}
            </h3>
            <p>
              {busy
                ? t("app.results.tracingDetail")
                : t("app.results.awaitsDetail")}
            </p>
            {!busy && (
              <button
                className="text-button"
                onClick={() => launch(true)}
              >
                {t("app.results.runExample")} ↗
              </button>
            )}
          </div>
        )}
      </div>
      <div className="panel playback-panel">
        <div className="playback-heading">
          <div>
            <p className="eyebrow">{t("app.playback.eyebrow")}</p>
            <strong>
              {frame ? `${utc(frame.utc)} UTC` : t("app.playback.selectMoment")}
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
            aria-label={playing ? t("app.playback.pause") : t("app.playback.play")}
          >
            {playing ? "Ⅱ" : "▷"}
          </button>
        </div>
        <input
          aria-label={t("app.playback.time")}
          aria-valuetext={frame ? `${utc(frame.utc)} UTC` : undefined}
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
              ? `${utc(result.analysis.request.start_utc)} UTC`
              : t("app.playback.start")}
          </span>
          <span>{t("app.playback.drag")}</span>
          <span>
            {result
              ? `${utc(result.analysis.request.end_utc)} UTC`
              : t("app.playback.end")}
          </span>
        </div>
        {result && frame && (
          <div className="timelines">
            <Timeline
              title={t("app.playback.sunTrack")}
              intervals={result.analysis.sun.intervals}
              start={result.analysis.request.start_utc}
              end={result.analysis.request.end_utc}
              current={frame.utc}
            />
            <Timeline
              title={t("app.playback.earthTrack")}
              intervals={result.analysis.earth.intervals}
              start={result.analysis.request.start_utc}
              end={result.analysis.request.end_utc}
              current={frame.utc}
            />
            <div className="timeline-legend">
              <span className="cursor-key">│ {t("app.legend.selected")}</span>
              <span>▰ {t("app.legend.full")}</span>
              <span>▧ {t("app.legend.partial")}</span>
              <span>▰ {t("app.legend.blocked")}</span>
              <span>▨ {t("app.legend.unknown")}</span>
            </div>
          </div>
        )}
      </div>
      <div className="event-grid">
        <div className="panel event-card">
          <span className="metric-label">{t("app.events.sunset")}</span>
          <strong>
            <SettingText value={frame?.sun.next_setting} />
          </strong>
        </div>
        <div className="panel event-card">
          <span className="metric-label">{t("app.events.earthset")}</span>
          <strong>
            <SettingText value={frame?.earth.next_setting} />
          </strong>
        </div>
      </div>
      {result && job && (
        <Communication key={job.id} jobId={job.id} current={frame?.utc} />
      )}
      {result && job && <SpaceWeather key={`sw-${job.id}`} jobId={job.id} />}
      {result && (
        <div className="panel trace-panel">
          <details>
            <summary>
              {t("app.trace.summary")} <span>+</span>
            </summary>
            <div className="trace-content">
              <p>
                <b>{t("common.sun")}:</b>{" "}
                {t("app.trace.sun", {
                  longest: h(result.analysis.sun.metrics.longest_visible_s),
                  unknown: h(result.analysis.sun.metrics.unknown_duration_s),
                })}
              </p>
              <p>
                <b>{t("common.earth")}:</b>{" "}
                {t("app.trace.earth", {
                  longest: h(result.analysis.earth.metrics.longest_visible_s),
                  blocked: h(result.analysis.earth.metrics.longest_blocked_s),
                  unknown: h(result.analysis.earth.metrics.unknown_duration_s),
                })}
              </p>
              <p>
                {t("app.trace.grid", {
                  radius: (
                    result.horizon.settings.search_distance_m / 1000
                  ).toFixed(0),
                  spacing: result.horizon.settings.azimuth_step_deg,
                  height:
                    result.horizon.observer_height_above_terrain_m.toFixed(1),
                })}
              </p>
              <p>
                {t("app.trace.limits", {
                  offset: result.analysis.spatial_sensitivity.offset_deg,
                })}
              </p>
              <p>
                {t("app.trace.refinement", {
                  status: result.analysis.refinement.status.replaceAll("_", " "),
                  count: result.analysis.provenance.ephemerides.length,
                })}
              </p>
              <code>
                {t("app.trace.analysis")} {result.analysis.analysis_id}
              </code>
              <button className="text-button" onClick={openSources}>
                {t("app.trace.viewSources")} ↗
              </button>
            </div>
          </details>
        </div>
      )}
      {result && job && (
        <ExportPanel
          jobId={job.id}
          result={result}
          frameIndex={frameIndex}
        />
      )}
      {result && job && (
        <button
          className="primary add-comparison"
          onClick={() => {
            setPlaying(false);
            setSharedB(undefined);
            setComparisonBase({ id: job.id, result });
            setPanel("comparison");
          }}
        >
          {t("app.results.addComparison")} ↗
        </button>
      )}
      <p className="results-footnote">{t("app.results.footnote")}</p>
      <ScenarioTransfer onOpen={openSearchResult} />
    </section>
  );
  const stageModules = [
    { id: "results", label: t("app.modules.results"), content: resultsModule },
    ...(comparisonBase
      ? [{ id: "comparison", label: t("app.modules.compare"), content: comparisonBase ? (
      <Comparison
        key={comparisonBase.id}
        aId={comparisonBase.id}
        initialBId={sharedB}
        initialCursor={Number(shared.current.get("cursor") || 0)}
        a={comparisonBase.result}
        sites={sites}
        onClose={() => {
          setComparisonBase(null);
          setPanel("results");
        }}
      />
    ) : null }]
      : []),
    {
      id: "window-search",
      label: t("app.modules.timeSearch"),
      content: <WindowSearch sites={sites} onChoose={openSearchResult} />,
    },
    {
      id: "recommendations",
      label: t("app.modules.recommend"),
      content: <Recommendations sites={sites} />,
    },
    { id: "descent", label: t("app.modules.descent"), content: <Descent sites={sites} /> },
    { id: "campaign", label: t("app.modules.campaign"), content: <MissionPlanner /> },
    {
      id: "classroom",
      label: t("app.modules.classroom"),
      content: <Classroom onOpen={openSearchResult} />,
    },
  ];

  return (
    <main data-view={view}>
      <button
        className="skip-link"
        onClick={() => {
          goTo("mission");
          setPanel("plan");
          setTimeout(() => {
            document.getElementById("mission")?.focus({ preventScroll: true });
          }, 0);
        }}
      >
        {t("app.nav.skip")}
      </button>
      <header className="navigation">
        <button
          className="brand"
          onClick={() =>
            view === "home"
              ? window.scrollTo({
                  top: 0,
                  behavior: reduced ? "auto" : "smooth",
                })
              : goTo("home")
          }
          aria-label={t("app.nav.back")}
        >
          <span className="brand-mark">◒</span>
          <span>
            {t("app.brand.main")}
            <span className="brand-sub">{t("app.brand.sub")}</span>
          </span>
        </button>
        <nav aria-label={t("app.nav.label")}>
          <button
            aria-current={view === "home" ? "page" : undefined}
            onClick={() => goTo("home")}
          >
            {t("app.nav.home")}
          </button>
          <button
            aria-current={view === "mission" ? "page" : undefined}
            onClick={() => goTo("mission")}
          >
            {t("app.nav.mission")}
          </button>
          <button onClick={openSources}>
            {t("app.nav.sources")} <span>↗</span>
          </button>
        </nav>
        <div className="header-tools">
          <LanguageToggle />
          <span className="team-label">
            2GBRAM <i /> NASA SPACE APPS
          </span>
        </div>
      </header>
      <section
        className="introduction"
        hidden={view !== "home"}
        ref={intro}
        aria-label={t("app.intro.label")}
      >
        <div className="scene-pin" aria-hidden="true">
          {webgl ? (
            <SceneBoundary>
              <Suspense
                fallback={
                  <div className="scene-loading">{t("app.intro.loading")}</div>
                }
              >
                <MoonScene
                  progress={progress}
                  reduced={reduced || motionPaused}
                  visible={view === "home" && sceneVisible && pageVisible}
                  compact={compactScene}
                  onFailure={() => setWebgl(false)}
                />
              </Suspense>
            </SceneBoundary>
          ) : (
            <div className="scene-fallback">
              <img src="/nasa/moon.jpg" alt="" />
            </div>
          )}
          <div className="scene-vignette" />
          <div
            className="scene-cut"
            style={{
              opacity: Math.max(0, 1 - Math.abs(progress - 0.49) / 0.045),
            }}
          />
          <div className="scene-readout">
            <span className="readout-cross">+</span>
            <p>
              {progress < 0.49
                ? t("app.intro.readout.recon")
                : progress < 0.935
                  ? t("app.intro.readout.approach")
                  : t("app.intro.readout.contact")}
            </p>
            <strong>
              {progress < 0.49
                ? t("app.intro.readout.orbital")
                : t("app.intro.readout.concept")}
            </strong>
            <span className="readout-rule" />
            <small>
              {progress < 0.49
                ? t("app.intro.readout.perspective")
                : t("app.intro.readout.illustrative")}
            </small>
          </div>
          <div className="scene-caption">
            <span>{t("app.intro.captionModels")}</span>
            <span>{t("app.intro.captionNote")}</span>
          </div>
          <div className="scene-index">
            <span>
              0
              {progress < 0.22
                ? 1
                : progress < 0.49
                  ? 2
                  : progress < 0.9
                    ? 3
                    : 4}
            </span>
            <div>
              <i style={{ height: `${Math.max(4, progress * 100)}%` }} />
            </div>
            <small>04</small>
          </div>
        </div>
        <div className="intro-controls">
          <button
            className="motion-toggle"
            aria-pressed={motionPaused || reduced}
            disabled={reduced}
            onClick={() => setMotionPaused(!motionPaused)}
          >
            <span aria-hidden="true">
              {motionPaused || reduced ? "▷" : "Ⅱ"}
            </span>
            {reduced
              ? t("app.intro.reducedMotion")
              : motionPaused
                ? t("app.intro.resumeMotion")
                : t("app.intro.pauseMotion")}
          </button>
          <span className="scroll-cue">
            {t("app.intro.scroll")} <span aria-hidden="true">↓</span>
          </span>
        </div>
        <section className="story hero" id="intro">
          <div className="story-copy">
            <p className="eyebrow">
              <i /> {t("app.hero.eyebrow")}
            </p>
            <h1>
              <span className="hero-title-top">{t("app.hero.titleTop")}</span>
              <br />
              <em>{t("app.hero.titleEm")}</em>
            </h1>
            <p className="intro-copy">
              {t("app.hero.line1")}
              <br />
              {t("app.hero.line2")}
            </p>
            <p className="muted story-description">
              {t("app.hero.description")}
            </p>
            <div className="hero-actions">
              <button
                className="primary"
                onClick={() => goTo("mission")}
              >
                {t("app.hero.plan")} <span>↗</span>
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
                {t("app.hero.explore")} <span>↓</span>
              </button>
            </div>
            <div className="hero-meta">
              <span>
                80–90° S <small>{t("app.hero.region")}</small>
              </span>
              <span>
                NASA / LOLA <small>{t("app.hero.terrain")}</small>
              </span>
            </div>
          </div>
        </section>
        <section className="story orbit">
          <div className="story-copy">
            <p className="eyebrow">{t("app.story.orbit.eyebrow")}</p>
            <h2>
              {t("app.story.orbit.title1")}
              <br />
              {t("app.story.orbit.title2")}
            </h2>
            <p className="intro-copy">{t("app.story.orbit.copy")}</p>
            <p className="muted story-description">
              {t("app.story.orbit.description")}
            </p>
            <div className="story-note">
              <span className="note-line" /> {t("app.story.orbit.note")}
            </div>
          </div>
        </section>
        <section className="story descent">
          <div className="story-copy">
            <p className="eyebrow">{t("app.story.descent.eyebrow")}</p>
            <h2>
              {t("app.story.descent.title1")}
              <br />
              <em>{t("app.story.descent.title2")}</em>
            </h2>
            <p className="intro-copy">{t("app.story.descent.copy")}</p>
            <p className="muted story-description">
              {t("app.story.descent.description")}
            </p>
            <div className="story-note">
              <span className="note-line" />
              {t("app.story.descent.note")}
            </div>
          </div>
        </section>
        <section className="story touchdown">
          <div className="story-copy">
            <p className="eyebrow">{t("app.story.touchdown.eyebrow")}</p>
            <h2>
              {t("app.story.touchdown.title1")}
              <br />
              <em>{t("app.story.touchdown.title2")}</em>
            </h2>
            <p className="muted story-description">
              {t("app.story.touchdown.description")}
            </p>
            <button
              className="primary"
              onClick={() => goTo("mission")}
            >
              {t("app.story.touchdown.enter")} <span>↓</span>
            </button>
            <div className="story-note">{t("app.story.touchdown.note")}</div>
          </div>
        </section>
      </section>

      <section
        id="mission"
        tabIndex={-1}
        className="mission-section"
        hidden={view !== "mission"}
      >
        <MissionStage
          lat={Number(lat)}
          lon={Number(lon)}
          sites={sites}
          busy={busy}
          webgl={webgl}
          reduced={reduced}
          visible={view === "mission" && pageVisible}
          compact={compactScene}
          launchKey={launchKey}
          condition={
            frame && result
              ? frame.sun.state === "full" || frame.sun.state === "partial"
                ? "sunlit"
                : frame.sun.state === "dark"
                  ? "dark"
                  : "unknown"
              : "unknown"
          }
          siteStatus={
            frame && result ? (
              <>
                <span>
                  <i className="sun-dot" /> {displayState(t, frame.sun.state)}
                </span>
                <span>
                  <i className="earth-dot" /> {displayState(t, frame.earth.state)}
                </span>
                <small>{t("app.analyzedSite", { time: utc(frame.utc) })}</small>
              </>
            ) : undefined
          }
          onPreset={selectSite}
          onSelect={(latitude, longitude) => {
            setLat(String(latitude));
            setLon(String(longitude));
            setSiteId("custom");
            setResolved("");
          }}
          onSceneFailure={() => setWebgl(false)}
          modules={stageModules}
          activeModule={panel}
          onModuleChange={setPanel}
          status={
            <span className={`connection ${busy ? "working" : ""}`}>
              <i /> {job ? stateLabel(t, job.status) : t("app.ready")}
            </span>
          }
          timeline={
            <DateScrubber
              value={start}
              durationHours={Number(duration)}
              disabled={busy}
              onChange={setStart}
            />
          }
        >
          {apiError && (
            <div className="alert" role="alert">
              {t("app.errors.apiUnavailable")} {apiError}{" "}
              <button onClick={loadSites}>{t("app.errors.reconnect")}</button>
            </div>
          )}
          <form
            className="panel parameters"
            onSubmit={(e) => {
              e.preventDefault();
              launch();
            }}
          >
            <div className="panel-heading">
              <span>{t("app.form.heading")}</span>
              <small>{t("app.form.parameters")}</small>
            </div>
            <fieldset disabled={busy}>
              <label>
                {t("app.form.region")}
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
                  <option value="custom">{t("app.form.custom")}</option>
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
                  {t("app.form.latitude")} · °
                  <input
                    aria-label={t("app.form.latitude")}
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
                  {t("app.form.longitude")} · °E
                  <input
                    aria-label={t("app.form.longitude")}
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
                {t("app.form.height")} · m
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
                {t("app.form.checkHeight")} ↗
              </button>
              {resolved && (
                <p className="field-help" role="status">
                  {resolved}
                </p>
              )}
              <p className="field-help">
                {t("app.form.startHint", { start: start.replace("T", " ") })}
              </p>
              <label>
                {t("app.form.duration")}
                <select
                  value={duration}
                  onChange={(e) => setDuration(e.target.value)}
                >
                  <option value="24">{t("app.form.hours", { n: 24 })}</option>
                  <option value="72">{t("app.form.days", { n: 3 })}</option>
                  <option value="168">{t("app.form.days", { n: 7 })}</option>
                  <option value="720">{t("app.form.days", { n: 30 })}</option>
                  {!["24", "72", "168", "720"].includes(duration) && (
                    <option value={duration}>
                      {t("app.form.hours", { n: duration })}
                    </option>
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
                  {t("app.form.refine")}
                  <small>{t("app.form.refineDetail")}</small>
                </span>
              </label>
              <button className="primary run-button" type="submit">
                {busy ? t("app.form.running") : t("app.form.run")}{" "}
                <span>{busy ? "◌" : "↗"}</span>
              </button>
              <p className="field-help">
                {t("app.form.footnote")}
              </p>
            </fieldset>
          </form>
          {error && (
            <div className="alert" role="alert">
              {error}
            </div>
          )}
          {busy && (
            <div className="job-progress" role="status">
              <span className="pulse" />
              <div>
                <strong>{stateLabel(t, job?.status || "pending")}</strong>
                <p>{t("app.progressNote")}</p>
              </div>
              <span className="mono">{job?.id.slice(0, 8)}</span>
            </div>
          )}
        </MissionStage>
      </section>
      <footer>
        <span>
          2GBRAM <i> / </i> {t("app.footer.name")}
        </span>
        <span>{t("app.footer.built")}</span>
        <a href="/api/docs" target="_blank" rel="noreferrer">
          {t("app.footer.docs")} ↗
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
              aria-label={t("app.sources.close")}
            >
              ×
            </button>
            <p className="eyebrow">{t("app.sources.eyebrow")}</p>
            <h2 id="sources-title">{t("app.sources.title")}</h2>
            <p>{t("app.sources.body")}</p>
            <ul>
              <li>
                <a
                  href="https://science.nasa.gov/3d-resources/lunar-reconnaissance-orbiter-a/"
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("app.sources.lro")}
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
                  href="https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/"
                  target="_blank"
                  rel="noreferrer"
                >
                  Blue Marble Next Generation
                </a>{" "}
                · NASA Earth Observatory / Reto Stöckli
              </li>
              <li>
                <a
                  href="https://pgda.gsfc.nasa.gov/products/90"
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("app.sources.lola")}
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
                · {t("app.sources.horizons")}
              </li>
              {sites.map((site) => (
                <li key={site.site_id}>
                  <a
                    href={site.coordinate_source_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {t("app.sources.coordinate", { name: site.display_name })}
                  </a>
                </li>
              ))}
            </ul>
            <small>{t("app.sources.disclaimer")}</small>
          </section>
        </div>
      )}
    </main>
  );
}
