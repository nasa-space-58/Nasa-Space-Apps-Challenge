import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslate } from "../i18n";
import type { RoutePoint } from "./Rover";
import "./surface-ops.css";

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
type RouteResult = { sky_evaluated: boolean; routes: Route[] };
type Profile = {
  name: string;
  mass_at_pdi_lb: number;
  usable_propellant_lb: number;
  max_thrust_lbf: number;
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
const POLL_MS = 2000;
const routeCache = new Map<string, Promise<RouteResult>>();

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body?.detail;
    throw Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg: string }) => d.msg).join("; ")
          : `HTTP ${response.status}`,
    );
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
  });
  let pending = routeCache.get(body);
  if (!pending) {
    pending = call<RouteResult>("/surface/routes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    pending.catch(() => routeCache.delete(body));
    routeCache.set(body, pending);
  }
  return pending;
}

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
  onRover: (rover: { path: RoutePoint[]; drive: number } | null) => void;
}) {
  const { t } = useTranslate();
  const [open, setOpen] = useState(true);
  const [routes, setRoutes] = useState<RouteResult | null>(null);
  const [routeError, setRouteError] = useState("");
  const [choice, setChoice] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [playing, setPlaying] = useState(!reduced);
  const [rate, setRate] = useState(PLAYBACK_RATES[1]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [weather, setWeather] = useState<WeatherJob | null>(null);
  const [weatherError, setWeatherError] = useState("");
  const [weatherAttempt, setWeatherAttempt] = useState(0);

  const covered = Number.isFinite(lat) && lat <= -80 && Number.isFinite(lon);
  useEffect(() => {
    setRoutes(null);
    setRouteError("");
    setChoice(0);
    setElapsed(0);
    if (!covered) return;
    let live = true;
    loadRoutes(lat, lon, analysisId).then(
      (result) => live && setRoutes(result),
      (error: Error) => live && setRouteError(error.message),
    );
    return () => {
      live = false;
    };
  }, [lat, lon, analysisId, covered]);

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

  const reachable = useMemo(
    () => routes?.routes.filter((r) => r.reachable && r.path.length > 0) ?? [],
    [routes],
  );
  const route = reachable[Math.min(choice, reachable.length - 1)] ?? null;
  const duration = route?.duration_s ?? 0;
  const drive = duration > 0 ? Math.min(1, elapsed / duration) : 0;

  useEffect(() => {
    if (!playing || !route || duration <= 0) return;
    const timer = setInterval(() => {
      setElapsed((value) => {
        const next = Math.min(duration, value + (TICK_MS / 1000) * rate);
        if (next >= duration) setPlaying(false);
        return next;
      });
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [playing, route, duration, rate]);

  const report = useRef(onRover);
  report.current = onRover;
  useEffect(() => {
    report.current(route ? { path: route.path, drive } : null);
  }, [route, drive]);
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
        <span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="surface-ops-body">
          <article className="ops-card">
            <h3>{t("surfaceOps.rover.title")}</h3>
            {!covered ? (
              <p className="ops-note">{t("surfaceOps.rover.outside")}</p>
            ) : routeError ? (
              <p className="ops-error" role="alert">{routeError}</p>
            ) : !routes ? (
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
                    setElapsed(Number(e.target.value));
                  }}
                />
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
                {routes.sky_evaluated ? (
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
            <p className="ops-foot">{t("surfaceOps.rover.note")}</p>
          </article>

          <article className="ops-card">
            <h3>{t("surfaceOps.lander.title")}</h3>
            {profile ? (
              <>
                <p className="ops-name">{profile.name}</p>
                <dl className="ops-grid">
                  <dt>{t("surfaceOps.lander.mass")}</dt>
                  <dd>{profile.mass_at_pdi_lb.toLocaleString()} lb</dd>
                  <dt>{t("surfaceOps.lander.propellant")}</dt>
                  <dd>{profile.usable_propellant_lb.toLocaleString()} lb</dd>
                  <dt>{t("surfaceOps.lander.thrust")}</dt>
                  <dd>{profile.max_thrust_lbf.toLocaleString()} lbf</dd>
                  <dt>{t("surfaceOps.lander.isp")}</dt>
                  <dd>{profile.isp_s} s</dd>
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
