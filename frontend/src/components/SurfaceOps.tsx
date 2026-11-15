import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslate } from "../i18n";
import type { RoutePoint, RoverState } from "./Rover";
import { DEPLOY_END, PULSE_END, SEARCH_END, type SurfaceScene } from "./surfaceScene";
import "./surface-ops.css";
import { demoRover } from "./demoRover";

// Post-landing HUD: the rover drives a backend R8 route, and the cards show
// backend values only (vehicle profile, route metrics, DONKI space weather).

type Sky = { state: string; clearance_deg: number | null } | null;
type Waypoint = {
  distance_along_m: number;
  arrival_utc: string | null;
  sun: Sky;
  earth: Sky;
};
type Route = {
  target_id: string;
  reachable: boolean;
  length_m: number | null;
  duration_s: number | null;
  max_slope_deg: number | null;
  elevation_gain_m: number | null;
  detour_ratio: number | null;
  path: RoutePoint[];
  waypoints: Waypoint[];
};
export type RouteResult = { sky_evaluated: boolean; routes: Route[]; scene: SurfaceScene | null };
type Profile = {
  name: string;
  mass_at_pdi_kg: number;
  usable_propellant_kg: number;
  max_thrust_n: number;
  isp_s: number;
  urls: string[];
};
// NOAA SWPC assessment from the space-weather API (see SpaceWeather.tsx).
type Weather = {
  acquired_at_utc: string;
  current: {
    xray: { flare_class: string; radio_blackout_level: number } | null;
    protons_10mev: { flux_pfu: number; radiation_storm_level: number } | null;
  };
  forecast: {
    coverage: "full" | "partial" | "none";
    max_daily_m_class_percent: number | null;
    max_daily_x_class_percent: number | null;
  };
};
type WeatherJob = { id: string; status: string; result: Weather | null; error: string | null };

/** The documented R8 example targets (docs/surface-exploration.md). */
const TARGETS = [
  { target_id: "North rim", bearing_deg: 0, distance_m: 2500 },
  { target_id: "East slope", bearing_deg: 90, distance_m: 3000 },
  { target_id: "South", bearing_deg: 200, distance_m: 4000 },
];
const SPEED_MPS = 0.1;
/** Simulated seconds per wall-clock second. */
const PLAYBACK_RATES = [60, 600, 3600];
const TICK_MS = 100;
/** Wall-clock seconds of the antenna scan and A* playback before driving. */
const SCAN_SECONDS = 10;
/** DEM margin around the routes in the 3-D scene (backend default is 400 m). */
const SCENE_MARGIN_M = 1000;
const POLL_MS = 2000;
const routeCache = new Map<string, Promise<RouteResult>>();

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body?.detail;
    throw Object.assign(new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg: string }) => d.msg).join("; ")
          : `HTTP ${response.status}`,
    ), { status: response.status, detail });
  }
  return body as T;
}

function loadRoutes(lat: number, lon: number, analysisId?: string) {
  const body = JSON.stringify({
    site_id: "custom",
    latitude_deg: lat,
    longitude_deg_east: lon,
    targets: TARGETS,
    speed_mps: SPEED_MPS,
    analysis_job_id: analysisId ?? null,
    include_scene: true,
    scene_margin_m: SCENE_MARGIN_M,
  });
  let pending = routeCache.get(body);
  if (!pending) {
    pending = call<RouteResult>("/surface/routes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }).catch((error) => {
      // Older preview APIs predate the optional scene payload. Retry only those
      // explicitly rejected fields, never suppress terrain/validation failures.
      const details = error.detail as {type?: string; loc?: (string | number)[]}[] | undefined;
      if (error.status !== 422 || !Array.isArray(details) || !details.length ||
          !details.every(d => d.type === "extra_forbidden" && ["include_scene", "scene_margin_m"].includes(String(d.loc?.at(-1))))) throw error;
      const legacy = JSON.parse(body);
      for (const d of details) delete legacy[String(d.loc!.at(-1))];
      return call<RouteResult>("/surface/routes", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(legacy)});
    });
    pending.catch(() => routeCache.delete(body));
    routeCache.set(body, pending);
  }
  return pending;
}

/** Rounded display of a backend value; a missing field shows a dash, never throws. */
const whole = (value: number | undefined) =>
  typeof value === "number" && Number.isFinite(value)
    ? Math.round(value).toLocaleString()
    : "—";
const km = (m: number) => (m / 1000).toFixed(2);
const hours = (s: number) => (s / 3600).toFixed(1);
const utcLabel = (iso: string) => iso.replace("T", " ").replace(/:\d\dZ$/, " UTC");

export default function SurfaceOps({
  lat,
  lon,
  analysisId,
  reduced,
  onRover,
}: {
  lat: number;
  lon: number;
  analysisId?: string;
  reduced: boolean;
  onRover: (rover: RoverState | null) => void;
}) {
  const { t, locale } = useTranslate();
  const [demoSeed, setDemoSeed] = useState<number | null>(null);
  const demo = useMemo(() => demoSeed === null ? null : demoRover(demoSeed), [demoSeed]);
  const tr = locale.startsWith("tr");
  const [open, setOpen] = useState(true);
  const [routes, setRoutes] = useState<RouteResult | null>(null);
  const [routeError, setRouteError] = useState("");
  const [choice, setChoice] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [scan, setScan] = useState(reduced ? 1 : 0);
  const [playing, setPlaying] = useState(!reduced);
  const [rate, setRate] = useState(PLAYBACK_RATES[1]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [weather, setWeather] = useState<WeatherJob | null>(null);
  const [weatherError, setWeatherError] = useState("");
  const [weatherAttempt, setWeatherAttempt] = useState(0);

  const covered = Number.isFinite(lat) && lat <= -80 && Number.isFinite(lon);
  useEffect(() => {
    setRoutes(null);
    setDemoSeed(null);
    setPlaying(!reduced);
    setRouteError("");
    setChoice(0);
    setElapsed(0);
    setScan(reduced ? 1 : 0);
    if (!covered) { setDemoSeed(Math.floor(Math.random() * 1000000)); return; }
    let live = true;
    loadRoutes(lat, lon, analysisId).then(
      (result) => {
        if (!live) return;
        setRoutes(result);
        // A route without its terrain cannot be draped onto unrelated craters:
        // choose the explicitly labelled, scene-aware planner instead.
        if (!result.scene) setDemoSeed(Math.floor(Math.random() * 1000000));
      },
      (error: Error) => { if (live) { setRouteError(error.message); setDemoSeed(Math.floor(Math.random() * 1000000)); } },
    );
    return () => {
      live = false;
    };
  }, [lat, lon, analysisId, covered, reduced]);

  useEffect(() => {
    call<{ profiles: Profile[] }>("/vehicle-profiles").then(
      (body) => setProfile(body.profiles[0] ?? null),
      () => setProfile(null),
    );
  }, []);

  // Space weather: reuse this analysis' job, poll it, retry on demand.
  useEffect(() => {
    setWeather(null);
    setWeatherError("");
    if (!analysisId) return;
    // Same key as the Solar activity panel, so both share one job.
    const key = `lln.spaceWeather.${analysisId}`;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = (id: string) =>
      call<WeatherJob>(`/space-weather/${id}`).then(
        (job) => {
          if (!live) return;
          setWeather(job);
          if (!["ready", "failed"].includes(job.status))
            timer = setTimeout(() => poll(id), POLL_MS);
        },
        (error: Error) => {
          if (!live) return;
          try {
            localStorage.removeItem(key);
          } catch {
            /* per-browser convenience only */
          }
          setWeatherError(error.message);
        },
      );
    let stored: string | null = null;
    try {
      stored = weatherAttempt ? null : localStorage.getItem(key);
    } catch {
      stored = null;
    }
    if (stored) poll(stored);
    else
      call<WeatherJob>(`/analyses/${encodeURIComponent(analysisId)}/space-weather`, {
        method: "POST",
      }).then(
        (job) => {
          if (!live) return;
          try {
            localStorage.setItem(key, job.id);
          } catch {
            /* per-browser convenience only */
          }
          setWeather(job);
          poll(job.id);
        },
        (error: Error) => live && setWeatherError(error.message),
      );
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [analysisId, weatherAttempt]);

  const shownRoutes = demo ?? routes;
  const reachable = useMemo(
    () => shownRoutes?.routes.filter((r) => r.reachable && r.path.length > 0) ?? [],
    [shownRoutes],
  );
  const alternatives = useMemo(() => shownRoutes?.routes.filter(r => r.reachable).map(r => ({id:r.target_id,path:r.path})), [shownRoutes]);
  const route = reachable[Math.min(choice, reachable.length - 1)] ?? null;
  const duration = route?.duration_s ?? 0;
  const drive = duration > 0 ? Math.min(1, elapsed / duration) : 0;

  const scanning = scan < 1;
  useEffect(() => {
    if (!playing || !route || duration <= 0) return;
    const timer = setInterval(() => {
      // The antenna scan and search playback run first, then the drive.
      if (scanning) {
        setScan((value) => Math.min(1, value + TICK_MS / 1000 / SCAN_SECONDS));
        return;
      }
      setElapsed((value) => {
        const next = Math.min(duration, value + (TICK_MS / 1000) * rate);
        if (next >= duration) setPlaying(false);
        return next;
      });
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [playing, route, duration, rate, scanning]);

  const report = useRef(onRover);
  report.current = onRover;
  useEffect(() => {
    report.current(
      route
        ? {
            path: route.path,
            drive,
            scan,
            scene: shownRoutes?.scene ?? null,
            targetId: route.target_id,
            alternatives,
          }
        : null,
    );
  }, [route, drive, scan, shownRoutes, alternatives]);
  const scene = shownRoutes?.scene ?? null;
  const searchedCells = scene && route ? (scene.explored[route.target_id] ?? []).length : 0;
  const truncated = Boolean(scene && route && scene.explored_truncated[route.target_id]);
  const scanStage =
    scan < DEPLOY_END
      ? "deploy"
      : scan < PULSE_END
        ? "pulse"
        : scan < SEARCH_END ? "search" : scan < 1 ? "route" : "done";
  useEffect(() => () => report.current(null), []);

  // Latest backend-sampled waypoint the rover has passed (no interpolation).
  const travelled = (route?.length_m ?? 0) * drive;
  const sample = route?.waypoints.reduce<Waypoint | null>(
    (last, w) => (w.distance_along_m <= travelled + 1e-6 ? w : last),
    null,
  );
  const sunState = sample?.sun?.state ?? "unknown";
  const earthState = sample?.earth?.state ?? "unknown";
  const result = weather?.result;

  return (
    <section className={`surface-ops ${open ? "" : "closed"}`} aria-label={t("surfaceOps.label")}>
      <button
        type="button"
        className="surface-ops-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="eyebrow">{t("surfaceOps.eyebrow")}</span>
        <strong>{t("surfaceOps.title")}</strong>
        {route && <small className="ops-summary">{route.target_id} · {Math.round(drive * 100)}% · {scanning ? (tr ? "Tarama" : "Scanning") : playing ? (tr ? "Keşif" : "Exploring") : (tr ? "Beklemede" : "Standby")}</small>}
        <span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="surface-ops-body">
          <article className="ops-card">
            <h3>{t("surfaceOps.rover.title")}</h3>
            <div className="ops-controls">
              <button type="button" onClick={() => { setDemoSeed(Math.floor(Math.random() * 1000000)); setChoice(0); setElapsed(0); setScan(reduced ? 1 : 0); setPlaying(!reduced); }}>{tr ? "Yeni krater keşfi · Demo" : "New crater exploration · Demo"}</button>
              {demo && routes?.scene && <button type="button" onClick={() => { setDemoSeed(null); setChoice(0); setElapsed(0); setScan(reduced ? 1 : 0); }}>{tr ? "API rotalarına dön" : "Return to API routes"}</button>}
            </div>
            {demo && routes && !routes.scene && <p className="ops-note">{tr ? "API arazi tarama haritası sağlamıyor. 3D keşif, görünen kraterleri tarayan demo planlayıcıyla çalışıyor; kırmızı bölgelerden kaçınılıyor." : "The API does not provide a terrain scan map. 3D exploration uses the labelled demo planner on the visible craters, avoiding red areas."}</p>}
            {demo && routeError && <p className="ops-note">API: {routeError}</p>}
            {demo && <p className="ops-note">{tr ? "DEMO · Sahnedeki kraterler üzerinde A* ve rastgele maliyet örnekleme. Kuantum hesaplama değildir. Mesafe ve süre temsili ölçek kullanır." : "DEMO · A* and sampled costs on the scene’s crater terrain. Not quantum computation. Distances and times use illustrative scale."}</p>}
            {!covered && !demo ? (
              <p className="ops-note">{t("surfaceOps.rover.outside")}</p>
            ) : routeError && !demo ? (
              <p className="ops-error" role="alert">{routeError}</p>
            ) : !shownRoutes ? (
              <p className="ops-note" aria-live="polite">{t("surfaceOps.rover.loading")}</p>
            ) : !route ? (
              <p className="ops-note">{t("surfaceOps.rover.none")}</p>
            ) : (
              <>
                <label className="ops-field">
                  <span>{t("surfaceOps.rover.target")}</span>
                  <select
                    value={Math.min(choice, reachable.length - 1)}
                    onChange={(e) => {
                      setChoice(Number(e.target.value));
                      setElapsed(0);
                      setScan(reduced ? 1 : 0);
                      setPlaying(!reduced);
                    }}
                  >
                    {reachable.map((r, i) => (
                      <option key={r.target_id} value={i}>
                        {r.target_id} · {km(r.length_m ?? 0)} km
                      </option>
                    ))}
                  </select>
                </label>
                <div className="ops-controls">
                  <button
                    type="button"
                    onClick={() => {
                      if (elapsed >= duration) setElapsed(0);
                      setPlaying(!playing);
                    }}
                  >
                    {playing ? t("surfaceOps.rover.pause") : t("surfaceOps.rover.play")}
                  </button>
                  {scene && (
                    <button
                      type="button"
                      onClick={() => {
                        setElapsed(0);
                        setScan(0);
                        setPlaying(true);
                      }}
                    >
                      {t("surfaceOps.scan.replay")}
                    </button>
                  )}
                  <div role="group" aria-label={t("surfaceOps.rover.rate")}>
                    {PLAYBACK_RATES.map((value) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={rate === value}
                        onClick={() => setRate(value)}
                      >
                        ×{value}
                      </button>
                    ))}
                  </div>
                </div>
                <input
                  type="range"
                  min={0}
                  max={duration}
                  step={Math.max(1, duration / 500)}
                  value={elapsed}
                  aria-label={t("surfaceOps.rover.scrub")}
                  onChange={(e) => {
                    setPlaying(false);
                    setScan(1);
                    setElapsed(Number(e.target.value));
                  }}
                />
                {scene && (
                  <div className="ops-scan" aria-live="polite">
                    <p>
                      <b>{t(`surfaceOps.scan.${scanStage}`, { cells: searchedCells.toLocaleString() })}</b>
                    </p>
                    <ul className="ops-legend">
                      <li><i className="swatch open" />{t("surfaceOps.scan.legendOpen", { deg: scene.slope_limit_deg })}</li>
                      <li><i className="swatch steep" />{t("surfaceOps.scan.legendSteep", { deg: scene.slope_limit_deg })}</li>
                      <li><i className="swatch searched" />{t("surfaceOps.scan.legendSearched")}</li>
                      <li><i className="swatch route" />{t("surfaceOps.scan.legendRoute")}</li>
                    </ul>
                    {truncated && <p className="ops-foot">{t("surfaceOps.scan.truncated")}</p>}
                  </div>
                )}
                <p className="ops-foot">{tr ? "Altın: seçilen rota · Turkuaz: diğer hesaplanan rotalar" : "Gold: selected route · Teal: other calculated routes"}</p>
                <p className="ops-telemetry" aria-live="off">● {scan < 1 ? (tr ? "Anten taraması" : "Antenna sweep") : drive >= 1 ? (tr ? "Hedefe ulaşıldı" : "Target reached") : (drive * 8) % 1 < .24 ? (tr ? "Telemetri aktarımı · animasyon" : "Telemetry uplink · animation") : (tr ? "Yüzey keşfi" : "Surface traverse")}</p>
                <dl className="ops-grid">
                  <dt>{t("surfaceOps.rover.distance")}</dt>
                  <dd>
                    {km(travelled)} / {km(route.length_m ?? 0)} km
                  </dd>
                  <dt>{t("surfaceOps.rover.time")}</dt>
                  <dd>
                    {hours(elapsed)} / {hours(duration)} {t("surfaceOps.hoursUnit")}
                  </dd>
                  <dt>{t("surfaceOps.rover.speed")}</dt>
                  <dd>{SPEED_MPS} m/s</dd>
                  <dt>{t("surfaceOps.rover.maxSlope")}</dt>
                  <dd>{route.max_slope_deg?.toFixed(1) ?? "—"}°</dd>
                  <dt>{t("surfaceOps.rover.climb")}</dt>
                  <dd>{route.elevation_gain_m?.toFixed(0) ?? "—"} m</dd>
                  <dt>{t("surfaceOps.rover.detour")}</dt>
                  <dd>×{route.detour_ratio?.toFixed(2) ?? "—"}</dd>
                </dl>
                {shownRoutes?.sky_evaluated && !demo ? (
                  <div className="ops-sky">
                    <span className={`ops-pill sun-${sunState}`}>
                      ☀ {t(`surfaceOps.sun.${sunState}`)}
                    </span>
                    <span className={`ops-pill earth-${earthState}`}>
                      ◐ {t(`surfaceOps.earth.${earthState}`)}
                    </span>
                    <small>
                      {sample
                        ? t("surfaceOps.rover.sample", {
                            km: km(sample.distance_along_m),
                            utc: sample.arrival_utc ? utcLabel(sample.arrival_utc) : "—",
                          })
                        : t("surfaceOps.rover.noSample")}
                    </small>
                  </div>
                ) : (
                  <p className="ops-note">{t("surfaceOps.rover.noAnalysis")}</p>
                )}
              </>
            )}
            {!demo && <p className="ops-foot">{t("surfaceOps.rover.note")}</p>}
            <p className="ops-foot">
              <a
                className="ops-link"
                href="https://sketchfab.com/3d-models/lunar-rover-82cab6e9ada74d12a98bd6a342e83048"
                target="_blank"
                rel="noreferrer"
              >
                {t("surfaceOps.rover.modelCredit")}
              </a>{" "}
              <a
                className="ops-link"
                href="https://creativecommons.org/licenses/by/4.0/"
                target="_blank"
                rel="noreferrer"
              >
                ↗ CC BY 4.0
              </a>
            </p>
          </article>

          <article className="ops-card">
            <h3>{t("surfaceOps.lander.title")}</h3>
            {profile ? (
              <>
                <p className="ops-name">{profile.name}</p>
                <dl className="ops-grid">
                  <dt>{t("surfaceOps.lander.mass")}</dt>
                  <dd>{whole(profile.mass_at_pdi_kg)} kg</dd>
                  <dt>{t("surfaceOps.lander.propellant")}</dt>
                  <dd>{whole(profile.usable_propellant_kg)} kg</dd>
                  <dt>{t("surfaceOps.lander.thrust")}</dt>
                  <dd>{whole(profile.max_thrust_n)} N</dd>
                  <dt>{t("surfaceOps.lander.isp")}</dt>
                  <dd>{profile.isp_s ?? "—"} s</dd>
                </dl>
                {profile.urls[0] && (
                  <a className="ops-link" href={profile.urls[0]} target="_blank" rel="noreferrer">
                    {t("surfaceOps.source")} ↗
                  </a>
                )}
              </>
            ) : (
              <p className="ops-note">{t("surfaceOps.loading")}</p>
            )}
          </article>

          <article className="ops-card">
            <h3>{t("surfaceOps.weather.title")}</h3>
            {!analysisId ? (
              <p className="ops-note">{t("surfaceOps.weather.needsAnalysis")}</p>
            ) : weatherError ? (
              <p className="ops-error" role="alert">{weatherError}</p>
            ) : !weather || !["ready", "failed"].includes(weather.status) ? (
              <p className="ops-note" aria-live="polite">{t("surfaceOps.weather.loading")}</p>
            ) : weather.status === "failed" ? (
              <>
                <p className="ops-error" role="alert">{weather.error}</p>
                <button type="button" onClick={() => setWeatherAttempt((n) => n + 1)}>
                  {t("spaceWeather.refresh")}
                </button>
              </>
            ) : result ? (
              <>
                <dl className="ops-grid">
                  <dt>{t("surfaceOps.weather.xray")}</dt>
                  <dd>
                    {result.current.xray
                      ? `${result.current.xray.flare_class} · R${result.current.xray.radio_blackout_level}`
                      : t("spaceWeather.noData")}
                  </dd>
                  <dt>{t("surfaceOps.weather.protons")}</dt>
                  <dd>
                    {result.current.protons_10mev
                      ? `${result.current.protons_10mev.flux_pfu.toFixed(2)} pfu · S${result.current.protons_10mev.radiation_storm_level}`
                      : t("spaceWeather.noData")}
                  </dd>
                  <dt>{t("surfaceOps.weather.forecast")}</dt>
                  <dd>
                    {result.forecast.coverage === "none"
                      ? t("spaceWeather.noForecast")
                      : t("spaceWeather.forecastValue", {
                          m: result.forecast.max_daily_m_class_percent ?? "?",
                          x: result.forecast.max_daily_x_class_percent ?? "?",
                        })}
                  </dd>
                </dl>
                <p className="ops-foot">
                  {t("surfaceOps.weather.acquired", { utc: utcLabel(result.acquired_at_utc) })}
                </p>
                <button type="button" onClick={() => setWeatherAttempt((n) => n + 1)}>
                  {t("spaceWeather.refresh")}
                </button>
              </>
            ) : null}
            <p className="ops-foot">{t("surfaceOps.weather.note")}</p>
          </article>
        </div>
      )}
    </section>
  );
}
