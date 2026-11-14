import { useEffect, useRef, useState } from "react";
import { errorText, useTranslate } from "../i18n";

// Displays the backend's NOAA SWPC assessment; no space weather logic lives here.
type XrayReading = {
  time_utc: string;
  flux_w_m2: number;
  flare_class: string;
  radio_blackout_level: number;
} | null;
type ProtonReading = {
  time_utc: string;
  flux_pfu: number;
  radiation_storm_level: number;
} | null;
type ForecastDay = {
  start_utc: string;
  end_utc: string;
  c_class_percent: number;
  m_class_percent: number;
  x_class_percent: number;
  proton_10mev_percent: number;
};
type Region = {
  region: number;
  location: string | null;
  spot_class: string | null;
  c_flare_percent: number | null;
  m_flare_percent: number | null;
  x_flare_percent: number | null;
};
type SpaceWeatherJob = {
  id: string;
  status: string;
  error: string | null;
  result: null | {
    start_utc: string;
    end_utc: string;
    acquired_at_utc: string;
    current: {
      xray: XrayReading;
      xray_peak: XrayReading;
      protons_10mev: ProtonReading;
      protons_10mev_peak: ProtonReading;
    };
    forecast: {
      forecast_start_utc: string;
      forecast_end_utc: string;
      coverage: "full" | "partial" | "none";
      covered_duration_s: number;
      days: ForecastDay[];
      max_daily_m_class_percent: number | null;
      max_daily_x_class_percent: number | null;
    };
    active_regions: {
      observed_date_utc: string;
      region_count: number;
      regions: Region[];
    };
    limitations: string[];
    source: { provider: string; products: Record<string, string> };
  };
};

const ACTIVE = ["pending", "acquiring", "computing"];
const STATUSES = [...ACTIVE, "ready", "failed"];
const SCALES_URL = "https://www.swpc.noaa.gov/noaa-scales-explanation";

const utc = (value: string, locale: string) =>
  new Date(value).toLocaleString(locale, {
    timeZone: "UTC",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
const utcDay = (value: string, locale: string) =>
  new Date(value).toLocaleDateString(locale, {
    timeZone: "UTC",
    day: "2-digit",
    month: "short",
  });

function storageKey(jobId: string) {
  return `lln.spaceWeather.${jobId}`;
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

export default function SpaceWeather({ jobId }: { jobId: string }) {
  const { t, tText, locale } = useTranslate();
  const [job, setJob] = useState<SpaceWeatherJob | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const poll = (id: string) => {
    window.clearTimeout(timer.current);
    call(`/space-weather/${encodeURIComponent(id)}`)
      .then((body: SpaceWeatherJob) => {
        setJob(body);
        if (ACTIVE.includes(body.status)) {
          timer.current = window.setTimeout(() => poll(id), 1500);
        }
      })
      .catch((e: Error) => setError(errorText(e, t, "spaceWeather.requestFailed")));
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
      const created: SpaceWeatherJob = await call(
        `/analyses/${encodeURIComponent(jobId)}/space-weather`,
        { method: "POST" },
      );
      remember(jobId, created.id);
      setJob(created);
      poll(created.id);
    } catch (e) {
      setError(errorText(e, t, "spaceWeather.requestFailed"));
    } finally {
      setBusy(false);
    }
  };

  const running = job ? ACTIVE.includes(job.status) : false;
  const result = job?.result;
  const percent = (value: number | null) =>
    value === null ? t("spaceWeather.unknownPercent") : value;

  return (
    <section className="panel communication-panel" aria-labelledby="space-weather-title">
      <div className="panel-heading">
        <span id="space-weather-title">{t("spaceWeather.title")}</span>
        <small>{t("spaceWeather.subtitle")}</small>
      </div>
      <div className="communication-body">
        <p className="communication-intro">{t("spaceWeather.intro")}</p>
        <div className="communication-controls">
          <button className="primary" disabled={busy || running} onClick={start}>
            {result ? t("spaceWeather.refresh") : t("spaceWeather.check")} ↗
          </button>
        </div>
        {job && (
          <p className={`communication-status ${job.status}`} role="status">
            {STATUSES.includes(job.status)
              ? t(`spaceWeather.status.${job.status}`)
              : job.status}
            {job.error ? ` — ${job.error}` : ""}
          </p>
        )}
        {error && (
          <p className="communication-status failed" role="alert">
            {error}
          </p>
        )}
        {result && (
          <>
            <div className="communication-metrics">
              <div>
                <span className="metric-label">{t("spaceWeather.xrayNow")}</span>
                <strong>
                  {result.current.xray?.flare_class ?? t("spaceWeather.noData")}
                </strong>
                <small>
                  {result.current.xray &&
                    t("spaceWeather.radio", {
                      level: result.current.xray.radio_blackout_level,
                    })}
                  {result.current.xray_peak &&
                    ` · ${t("spaceWeather.peak", {
                      value: result.current.xray_peak.flare_class,
                    })}`}
                </small>
              </div>
              <div>
                <span className="metric-label">{t("spaceWeather.protonsNow")}</span>
                <strong>
                  {result.current.protons_10mev
                    ? t("spaceWeather.pfu", {
                        value: result.current.protons_10mev.flux_pfu.toFixed(2),
                      })
                    : t("spaceWeather.noData")}
                </strong>
                <small>
                  {result.current.protons_10mev &&
                    t("spaceWeather.radiation", {
                      level: result.current.protons_10mev.radiation_storm_level,
                    })}
                  {result.current.protons_10mev_peak &&
                    ` · ${t("spaceWeather.peak", {
                      value: t("spaceWeather.pfu", {
                        value: result.current.protons_10mev_peak.flux_pfu.toFixed(2),
                      }),
                    })}`}
                </small>
              </div>
              <div>
                <span className="metric-label">{t("spaceWeather.forecast")}</span>
                <strong>
                  {result.forecast.coverage === "none"
                    ? t("spaceWeather.noForecast")
                    : t("spaceWeather.forecastValue", {
                        m: result.forecast.max_daily_m_class_percent ?? "?",
                        x: result.forecast.max_daily_x_class_percent ?? "?",
                      })}
                </strong>
                <small>
                  {t("spaceWeather.acquired", {
                    time: utc(result.acquired_at_utc, locale),
                  })}
                </small>
              </div>
            </div>
            <p className={`communication-status ${result.forecast.coverage === "full" ? "ready" : "insufficient_data"}`}>
              {t(`spaceWeather.coverage.${result.forecast.coverage}`, {
                start: utc(result.forecast.forecast_start_utc, locale),
                end: utc(result.forecast.forecast_end_utc, locale),
                hours: t("units.hours", {
                  value: (result.forecast.covered_duration_s / 3600).toFixed(1),
                }),
              })}
            </p>
            {result.forecast.days.length > 0 && (
              <ol className="window-list" aria-label={t("spaceWeather.daysTitle")}>
                {result.forecast.days.map((day) => (
                  <li key={day.start_utc}>
                    {t("spaceWeather.day", {
                      date: utcDay(day.start_utc, locale),
                      c: day.c_class_percent,
                      m: day.m_class_percent,
                      x: day.x_class_percent,
                      p: day.proton_10mev_percent,
                    })}
                  </li>
                ))}
              </ol>
            )}
            <p className="communication-intro">
              {t("spaceWeather.regionsTitle", {
                date: utcDay(result.active_regions.observed_date_utc, locale),
                count: result.active_regions.region_count,
              })}
            </p>
            {result.active_regions.regions.length ? (
              <ul className="station-key">
                {result.active_regions.regions.map((region) => (
                  <li key={region.region}>
                    {t("spaceWeather.region", {
                      region: region.region,
                      location: region.location ?? "",
                      spot: region.spot_class ?? "",
                      c: percent(region.c_flare_percent),
                      m: percent(region.m_flare_percent),
                      x: percent(region.x_flare_percent),
                    })}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="communication-empty">{t("spaceWeather.noRegions")}</p>
            )}
            <details className="communication-limits">
              <summary>
                {t("spaceWeather.limits")} <span>+</span>
              </summary>
              <ul>
                {result.limitations.map((text) => (
                  <li key={text}>{tText(text)}</li>
                ))}
              </ul>
              {Object.entries(result.source.products).map(([name, url]) => (
                <a key={name} href={url} target="_blank" rel="noreferrer">
                  {result.source.provider} · {name} ↗
                </a>
              ))}
              <a href={SCALES_URL} target="_blank" rel="noreferrer">
                {t("spaceWeather.scales")} ↗
              </a>
            </details>
          </>
        )}
      </div>
    </section>
  );
}
