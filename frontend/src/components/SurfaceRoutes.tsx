import { useEffect, useState } from "react";
import { useTranslate } from "../i18n";
import "./surface.css";

// R8 (S12): renders backend surface routes; routing and sky checks run in the API.
type Sky = { state: string; clearance_deg: number | null } | null;
type Waypoint = {
  distance_along_m: number;
  arrival_utc: string | null;
  north_m: number;
  east_m: number;
  sun: Sky;
  earth: Sky;
};
type Route = {
  target_id: string;
  bearing_deg: number;
  distance_m: number;
  reachable: boolean;
  reason: string | null;
  detail: string | null;
  length_m: number | null;
  duration_s: number | null;
  max_slope_deg: number | null;
  elevation_gain_m: number | null;
  detour_ratio: number | null;
  path: { north_m: number; east_m: number }[];
  waypoints: Waypoint[];
  shadowed_waypoints: number | null;
  no_earth_waypoints: number | null;
};
type Result = {
  result_id: string;
  sky_evaluated: boolean;
  departure_utc: string | null;
  routes: Route[];
  efficiency: {
    score: number;
    reachable_fraction: number;
    detour_efficiency: number;
    sunlit_fraction: number | null;
  };
  assumptions: string[];
};
type TargetRow = { target_id: string; bearing_deg: string; distance_m: string };

// Reference categorical slots (dark), one hue per route.
const ROUTE_COLORS = ["#3987e5", "#d95926", "#199e70", "#a87bd6", "#c9a227"];
const hours = (s: number) => `${(s / 3600).toFixed(1)} h`;

async function call(path: string, init?: RequestInit) {
  const response = await fetch(`/api${path}`, init);
  const body = await response.json();
  if (!response.ok) {
    const detail = body?.detail;
    throw Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg: string }) => d.msg).join("; ")
          : "request_failed",
    );
  }
  return body;
}

function latestAnalysis(): string | null {
  try {
    return localStorage.getItem("lunar-analysis-id");
  } catch {
    return null;
  }
}

function RouteMap({ result }: { result: Result }) {
  const { t } = useTranslate();
  const S = 340,
    C = S / 2,
    PAD = 24;
  const points = result.routes.flatMap((r) => r.path);
  const reach = Math.max(500, ...points.map((p) => Math.max(Math.abs(p.north_m), Math.abs(p.east_m))));
  const scale = (C - PAD) / (reach * 1.1);
  const px = (east: number) => C + east * scale;
  const py = (north: number) => C - north * scale;
  return (
    <svg
      className="surface-map"
      viewBox={`0 0 ${S} ${S}`}
      role="img"
      aria-label={t("surfaceRoutes.mapLabel", { count: result.routes.length })}
    >
      <line x1={C} x2={C} y1={PAD} y2={S - PAD} className="risk-axis" />
      <line x1={PAD} x2={S - PAD} y1={C} y2={C} className="risk-axis" />
      <text x={C + 6} y={PAD + 8} className="risk-tick">
        {t("surfaceRoutes.north")} ↑
      </text>
      {result.routes.map((r, i) => {
        const color = ROUTE_COLORS[i % ROUTE_COLORS.length];
        if (!r.reachable) {
          const b = (r.bearing_deg * Math.PI) / 180;
          const x = px(Math.min(r.distance_m, reach) * Math.sin(b));
          const y = py(Math.min(r.distance_m, reach) * Math.cos(b));
          return (
            <g key={r.target_id}>
              <line x1={C} y1={C} x2={x} y2={y} stroke={color} strokeDasharray="3 4" strokeOpacity={0.6} />
              <text x={x + 6} y={y + 4} className="surface-label">
                ✕ {r.target_id}
              </text>
            </g>
          );
        }
        const end = r.path[r.path.length - 1];
        return (
          <g key={r.target_id}>
            <polyline
              points={r.path.map((p) => `${px(p.east_m)},${py(p.north_m)}`).join(" ")}
              fill="none"
              stroke={color}
              strokeWidth={2}
            />
            {r.waypoints.map((w, j) =>
              w.sun?.state === "dark" ? (
                <rect key={j} x={px(w.east_m) - 4} y={py(w.north_m) - 4} width={8} height={8} className="wp-dark" />
              ) : (
                <circle
                  key={j}
                  cx={px(w.east_m)}
                  cy={py(w.north_m)}
                  r={4}
                  className={w.sun ? `wp-${w.sun.state}` : "wp-none"}
                />
              ),
            )}
            <text x={px(end.east_m) + 7} y={py(end.north_m) + 4} className="surface-label">
              {r.target_id}
            </text>
          </g>
        );
      })}
      <rect x={C - 6} y={C - 6} width={12} height={12} className="backup-primary" />
    </svg>
  );
}

export default function SurfaceRoutes({ site }: { site: Record<string, unknown> }) {
  const { t } = useTranslate();
  const [targets, setTargets] = useState<TargetRow[]>(() => [
    { target_id: t("surfaceRoutes.defaults.north"), bearing_deg: "0", distance_m: "2500" },
    { target_id: t("surfaceRoutes.defaults.east"), bearing_deg: "90", distance_m: "3000" },
    { target_id: t("surfaceRoutes.defaults.south"), bearing_deg: "200", distance_m: "4000" },
  ]);
  // Advanced inputs; defaults match the backend RouteRequest.
  const [observerHeight, setObserverHeight] = useState("2");
  const [waypoints, setWaypoints] = useState("5");
  const [slope, setSlope] = useState("15");
  const [speed, setSpeed] = useState("0.1");
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [useSky, setUseSky] = useState(true);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setAnalysisId(latestAnalysis());
  }, []);

  const update = (i: number, key: keyof TargetRow, value: string) =>
    setTargets(targets.map((row, j) => (j === i ? { ...row, [key]: value } : row)));

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      setResult(
        await call("/surface/routes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            site_id: site.site_id,
            latitude_deg: site.latitude_deg,
            longitude_deg_east: site.longitude_deg_east,
            slope_limit_deg: Number(slope),
            speed_mps: Number(speed),
            observer_height_m: Number(observerHeight),
            waypoints_per_route: Number(waypoints),
            targets: targets
              .filter((row) => row.target_id.trim())
              .map((row) => ({
                target_id: row.target_id.trim(),
                bearing_deg: Number(row.bearing_deg),
                distance_m: Number(row.distance_m),
              })),
            ...(useSky && analysisId ? { analysis_job_id: analysisId } : {}),
          }),
        }),
      );
    } catch (e) {
      const message = e instanceof Error ? e.message : "request_failed";
      setError(message === "request_failed" ? t("risk.requestFailed") : message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="risk-panel surface-panel" aria-label={t("surfaceRoutes.label")}>
      <h3>{t("surfaceRoutes.title")}</h3>
      <form
        className="surface-form"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <fieldset disabled={busy}>
          <table className="surface-targets">
            <thead>
              <tr>
                <th scope="col">{t("surfaceRoutes.target")}</th>
                <th scope="col">{t("surfaceRoutes.bearing")}</th>
                <th scope="col">{t("surfaceRoutes.distance")}</th>
                <th scope="col">
                  <span className="sr-only">{t("surfaceRoutes.remove")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {targets.map((row, i) => (
                <tr key={i}>
                  <td>
                    <input aria-label={t("surfaceRoutes.nameLabel", { n: i + 1 })} value={row.target_id} onChange={(e) => update(i, "target_id", e.target.value)} />
                  </td>
                  <td>
                    <input aria-label={t("surfaceRoutes.bearingLabel", { n: i + 1 })} type="number" min="0" max="359.9" step="any" value={row.bearing_deg} onChange={(e) => update(i, "bearing_deg", e.target.value)} />
                  </td>
                  <td>
                    <input aria-label={t("surfaceRoutes.distanceLabel", { n: i + 1 })} type="number" min="1" max="8000" step="any" value={row.distance_m} onChange={(e) => update(i, "distance_m", e.target.value)} />
                  </td>
                  <td>
                    <button type="button" className="text-button" aria-label={t("surfaceRoutes.removeLabel", { n: i + 1 })} onClick={() => setTargets(targets.filter((_, j) => j !== i))}>
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {targets.length < 5 && (
            <button
              type="button"
              className="text-button"
              onClick={() => setTargets([...targets, { target_id: `T${targets.length + 1}`, bearing_deg: "45", distance_m: "1500" }])}
            >
              + {t("surfaceRoutes.add")}
            </button>
          )}
          <div className="risk-controls">
            <fieldset>
              <label>
                {t("surfaceRoutes.slope")}
                <input type="number" min="1" max="35" step="any" value={slope} onChange={(e) => setSlope(e.target.value)} />
              </label>
              <label>
                {t("surfaceRoutes.speed")}
                <input type="number" min="0.01" max="2" step="any" value={speed} onChange={(e) => setSpeed(e.target.value)} />
              </label>
              <label className="checkbox-label">
                <input type="checkbox" checked={useSky && !!analysisId} disabled={!analysisId} onChange={(e) => setUseSky(e.target.checked)} />
                {analysisId ? t("surfaceRoutes.useSky") : t("surfaceRoutes.needAnalysis")}
              </label>
              <details className="advanced-settings">
                <summary>{t("common.advancedSettings")}</summary>
                <label>
                  {t("surfaceRoutes.observerHeight")}
                  <input type="number" min="0.1" max="10" step="any" value={observerHeight} onChange={(e) => setObserverHeight(e.target.value)} />
                </label>
                <label>
                  {t("surfaceRoutes.waypoints")}
                  <input type="number" min="2" max="8" step="1" value={waypoints} onChange={(e) => setWaypoints(e.target.value)} />
                </label>
              </details>
              <button type="submit" className="text-button">
                {busy ? t("surfaceRoutes.running") : t("surfaceRoutes.run")}
              </button>
            </fieldset>
          </div>
        </fieldset>
        <p className="field-help">{t("surfaceRoutes.help")}</p>
      </form>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {result && (
        <div className="risk-result">
          <div className="communication-metrics">
            <div>
              <span className="metric-label">{t("surfaceRoutes.metrics.efficiency")}</span>
              <strong>{result.efficiency.score.toFixed(2)}</strong>
              <small>
                {result.efficiency.sunlit_fraction !== null
                  ? t("surfaceRoutes.metrics.efficiencyWithSun")
                  : t("surfaceRoutes.metrics.efficiencyHelp")}
              </small>
            </div>
            <div>
              <span className="metric-label">{t("surfaceRoutes.metrics.reachable")}</span>
              <strong>
                {result.routes.filter((r) => r.reachable).length} / {result.routes.length}
              </strong>
              <small>{t("surfaceRoutes.metrics.directness", { pct: (100 * result.efficiency.detour_efficiency).toFixed(0) })}</small>
            </div>
            <div>
              <span className="metric-label">{t("surfaceRoutes.metrics.sunlit")}</span>
              <strong>
                {result.efficiency.sunlit_fraction !== null
                  ? `${(100 * result.efficiency.sunlit_fraction).toFixed(0)} %`
                  : t("surfaceRoutes.notChecked")}
              </strong>
              <small>
                {result.departure_utc
                  ? t("surfaceRoutes.metrics.departure", { utc: result.departure_utc.replace("T", " ").slice(0, 16) })
                  : t("surfaceRoutes.metrics.noAnalysis")}
              </small>
            </div>
          </div>
          <div className="backup-grid">
            <figure className="risk-plot">
              <RouteMap result={result} />
              <figcaption className="risk-legend">
                <span>
                  <i className="dot wp-full-swatch" /> {t("surfaceRoutes.legend.sunlit")}
                </span>
                <span>
                  <i className="square" /> {t("surfaceRoutes.legend.shadow")}
                </span>
                <span>
                  <i className="dot wp-unknown-swatch" /> {t("surfaceRoutes.legend.unknown")}
                </span>
              </figcaption>
            </figure>
            <div className="table-scroll">
              <table className="ranking-table">
                <caption>{t("surfaceRoutes.table.caption")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("surfaceRoutes.target")}</th>
                    <th scope="col">{t("surfaceRoutes.table.route")}</th>
                    <th scope="col">{t("surfaceRoutes.table.terrain")}</th>
                    <th scope="col">{t("surfaceRoutes.table.sky")}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.routes.map((r, i) => (
                    <tr key={r.target_id} className={r.reachable ? "" : "route-rejected"}>
                      <th scope="row">
                        <i className="route-swatch" style={{ background: ROUTE_COLORS[i % ROUTE_COLORS.length] }} />
                        {r.target_id}
                        <small>
                          {r.bearing_deg}° · {(r.distance_m / 1000).toFixed(1)} km
                        </small>
                      </th>
                      {r.reachable ? (
                        <>
                          <td>
                            {(r.length_m! / 1000).toFixed(2)} km
                            <small>
                              {hours(r.duration_s!)} · {t("surfaceRoutes.detour")} ×{r.detour_ratio!.toFixed(2)}
                            </small>
                          </td>
                          <td>
                            {t("surfaceRoutes.maxSlope", { deg: r.max_slope_deg!.toFixed(1) })}
                            <small>{t("surfaceRoutes.climb", { m: r.elevation_gain_m!.toFixed(0) })}</small>
                          </td>
                          <td>
                            {r.shadowed_waypoints !== null ? (
                              <>
                                {t("surfaceRoutes.sunlitPoints", {
                                  lit: r.waypoints.length - r.shadowed_waypoints,
                                  total: r.waypoints.length,
                                })}
                                <small>
                                  {t("surfaceRoutes.earthHidden", { hidden: r.no_earth_waypoints ?? 0, total: r.waypoints.length })}
                                </small>
                                <small>
                                  {r.waypoints.map((w) => t(`surfaceRoutes.sun.${w.sun?.state ?? "unknown"}`)).join(" → ")}
                                </small>
                              </>
                            ) : (
                              t("surfaceRoutes.notChecked")
                            )}
                          </td>
                        </>
                      ) : (
                        <td colSpan={3}>
                          ✕ {t("backups.unreachable")}<small>{r.detail}</small>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <details className="communication-limits">
            <summary>
              {t("surfaceRoutes.assumptions")} <span>+</span>
            </summary>
            <ul>
              {result.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}
