import { useEffect, useState } from "react";
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
const SUN_LABEL: Record<string, string> = {
  full: "sunlit",
  partial: "partly sunlit",
  dark: "in shadow",
  unknown: "unknown",
};
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
          : "Request failed",
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
      aria-label={`Routes from the landing site to ${result.routes.length} targets, north up; details are in the table.`}
    >
      <line x1={C} x2={C} y1={PAD} y2={S - PAD} className="risk-axis" />
      <line x1={PAD} x2={S - PAD} y1={C} y2={C} className="risk-axis" />
      <text x={C + 6} y={PAD + 8} className="risk-tick">
        N ↑
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
  const [targets, setTargets] = useState<TargetRow[]>([
    { target_id: "North rim", bearing_deg: "0", distance_m: "2500" },
    { target_id: "East slope", bearing_deg: "90", distance_m: "3000" },
    { target_id: "South", bearing_deg: "200", distance_m: "4000" },
  ]);
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
    setTargets(targets.map((t, j) => (j === i ? { ...t, [key]: value } : t)));

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
            targets: targets
              .filter((t) => t.target_id.trim())
              .map((t) => ({
                target_id: t.target_id.trim(),
                bearing_deg: Number(t.bearing_deg),
                distance_m: Number(t.distance_m),
              })),
            ...(useSky && analysisId ? { analysis_job_id: analysisId } : {}),
          }),
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="risk-panel surface-panel" aria-label="Post-landing exploration">
      <h3>After landing · surface routes to science targets</h3>
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
                <th scope="col">Target</th>
                <th scope="col">Bearing · °</th>
                <th scope="col">Distance · m</th>
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {targets.map((t, i) => (
                <tr key={i}>
                  <td>
                    <input aria-label={`Target ${i + 1} name`} value={t.target_id} onChange={(e) => update(i, "target_id", e.target.value)} />
                  </td>
                  <td>
                    <input aria-label={`Target ${i + 1} bearing`} type="number" min="0" max="359.9" step="any" value={t.bearing_deg} onChange={(e) => update(i, "bearing_deg", e.target.value)} />
                  </td>
                  <td>
                    <input aria-label={`Target ${i + 1} distance`} type="number" min="1" max="8000" step="any" value={t.distance_m} onChange={(e) => update(i, "distance_m", e.target.value)} />
                  </td>
                  <td>
                    <button type="button" className="text-button" aria-label={`Remove target ${i + 1}`} onClick={() => setTargets(targets.filter((_, j) => j !== i))}>
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
              + Add target
            </button>
          )}
          <div className="risk-controls">
            <fieldset>
              <label>
                Mobility slope limit · °
                <input type="number" min="1" max="35" step="any" value={slope} onChange={(e) => setSlope(e.target.value)} />
              </label>
              <label>
                Traverse speed · m/s
                <input type="number" min="0.01" max="2" step="any" value={speed} onChange={(e) => setSpeed(e.target.value)} />
              </label>
              <label className="checkbox-label">
                <input type="checkbox" checked={useSky && !!analysisId} disabled={!analysisId} onChange={(e) => setUseSky(e.target.checked)} />
                {analysisId ? "Check sunlight & Earth with the latest site analysis" : "Run a site analysis in Mission control to check sunlight & Earth"}
              </label>
              <button type="submit" className="text-button">
                {busy ? "Routing…" : "Plan routes ↗"}
              </button>
            </fieldset>
          </div>
        </fieldset>
        <p className="field-help">
          Bearings are clockwise from north. The slope limit and speed are planning assumptions, not a specific rover. With
          sunlight checks, each sampled point gets its own terrain horizon (about 2 s per point).
        </p>
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
              <span className="metric-label">MISSION EFFICIENCY</span>
              <strong>{result.efficiency.score.toFixed(2)}</strong>
              <small>0–1 · reachability, directness{result.efficiency.sunlit_fraction !== null ? ", sunlight" : ""}</small>
            </div>
            <div>
              <span className="metric-label">TARGETS REACHABLE</span>
              <strong>
                {result.routes.filter((r) => r.reachable).length} of {result.routes.length}
              </strong>
              <small>directness {(100 * result.efficiency.detour_efficiency).toFixed(0)} %</small>
            </div>
            <div>
              <span className="metric-label">SUNLIT ROUTE POINTS</span>
              <strong>
                {result.efficiency.sunlit_fraction !== null
                  ? `${(100 * result.efficiency.sunlit_fraction).toFixed(0)} %`
                  : "not checked"}
              </strong>
              <small>{result.departure_utc ? `departure ${result.departure_utc.replace("T", " ").slice(0, 16)} UTC` : "no site analysis"}</small>
            </div>
          </div>
          <div className="backup-grid">
            <figure className="risk-plot">
              <RouteMap result={result} />
              <figcaption className="risk-legend">
                <span>
                  <i className="dot wp-full-swatch" /> sunlit point
                </span>
                <span>
                  <i className="square" /> point in shadow
                </span>
                <span>
                  <i className="dot wp-unknown-swatch" /> unknown / not checked
                </span>
              </figcaption>
            </figure>
            <div className="table-scroll">
              <table className="ranking-table">
                <caption>Routes from the landing site</caption>
                <thead>
                  <tr>
                    <th scope="col">Target</th>
                    <th scope="col">Route</th>
                    <th scope="col">Terrain</th>
                    <th scope="col">Sun / Earth</th>
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
                              {hours(r.duration_s!)} · detour ×{r.detour_ratio!.toFixed(2)}
                            </small>
                          </td>
                          <td>
                            max {r.max_slope_deg!.toFixed(1)}°<small>climb {r.elevation_gain_m!.toFixed(0)} m</small>
                          </td>
                          <td>
                            {r.shadowed_waypoints !== null ? (
                              <>
                                {r.waypoints.length - r.shadowed_waypoints}/{r.waypoints.length} points sunlit
                                <small>
                                  Earth hidden at {r.no_earth_waypoints}/{r.waypoints.length}
                                </small>
                                <small>
                                  {r.waypoints.map((w) => SUN_LABEL[w.sun?.state ?? "unknown"]).join(" → ")}
                                </small>
                              </>
                            ) : (
                              "not checked"
                            )}
                          </td>
                        </>
                      ) : (
                        <td colSpan={3}>
                          ✕ Not reachable<small>{r.detail}</small>
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
              Route model & assumptions <span>+</span>
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
