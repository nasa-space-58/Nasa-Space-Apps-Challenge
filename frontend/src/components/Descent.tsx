import { useScenarioResult, ResultSceneLink, useAnalysisView } from "../analysisView";
import { useEffect, useMemo, useState } from "react";
import type { MissionCandidate } from "../missionCandidates";
import type { Preset } from "../types";
import RouteOptimizer, { type RouteResult } from "./RouteOptimizer";
import RiskPanel from "./RiskPanel";
import BackupPlans from "./BackupPlans";
import SurfaceRoutes from "./SurfaceRoutes";
import TerrainRecoveryPanel from "./TerrainRecoveryPanel";
import TwinPanel from "./TwinPanel";
import { errorText, useTranslate } from "../i18n";

// R4 (S6): renders backend descent plans; all physics runs in the API.
type Sample = {
  t_s: number;
  phase: string;
  downrange_m: number;
  altitude_m: number;
  speed_mps: number;
  vertical_speed_mps: number;
  horizontal_speed_mps: number;
  throttle_fraction: number;
  mass_kg: number;
};
type Phase = {
  name: string;
  start_s: number;
  end_s: number;
  delta_v_mps: number;
  propellant_kg: number;
};
type Factor = { factor: string; detail: string };
type Plan = {
  feasible: boolean;
  limiting_factors: Factor[];
  site: { site_id: string; terrain_height_m: number };
  deorbit: { delta_v_mps: number; periapsis_speed_mps: number; coast_s: number };
  plane_change: { required_deg: number; delta_v_mps: number; applied: boolean };
  pdi_range_m: number;
  phases: Phase[];
  totals: {
    powered_delta_v_mps: number;
    total_delta_v_mps: number;
    duration_s: number;
    propellant_used_kg: number;
    propellant_margin_kg: number;
  };
  touchdown: { position_error_m: number; vertical_speed_mps: number; horizontal_speed_mps: number };
  reference_comparison: { model_powered_delta_v_fps: number; difference_percent: number };
  samples: Sample[];
  assumptions: string[];
};
type Profile = {
  profile_id: string;
  name: string;
  mass_at_pdi_kg: number;
  usable_propellant_kg: number;
  max_thrust_n: number;
  design_thrust_n: number;
  min_throttle_fraction: number;
  max_throttle_fraction: number;
  isp_s: number;
  sources?: Record<string, string>;
  urls?: string[];
};

// Reference categorical slots 1-3 (dark), validated on the #10171e surface.
const PHASES = [
  { key: "braking", color: "#3987e5" },
  { key: "approach", color: "#d95926" },
  { key: "terminal", color: "#199e70" },
];
const FT = 0.3048;
const minutes = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

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
          : "",
    );
  }
  return body;
}

function ProfileChart({ samples, zoom }: { samples: Sample[]; zoom: boolean }) {
  const { t } = useTranslate();
  const [hover, setHover] = useState<Sample | null>(null);
  const shown = zoom ? samples.filter((s) => s.downrange_m >= -10000) : samples;
  const W = 720,
    H = 300,
    L = 56,
    R = 16,
    T = 30,
    B = 40;
  const xMin = Math.min(...shown.map((s) => s.downrange_m)) / 1000;
  const yMax = Math.max(...shown.map((s) => s.altitude_m)) / 1000;
  const x = (km: number) => L + ((km - xMin) / (0 - xMin || 1)) * (W - L - R);
  const y = (km: number) => T + (1 - km / (yMax || 1)) * (H - T - B);
  const ticks = (max: number) => {
    const step = Math.pow(10, Math.floor(Math.log10(max || 1)));
    const nice = max / step > 5 ? step * 2 : max / step > 2 ? step : step / 2;
    const out = [];
    for (let v = 0; v <= max + 1e-9; v += nice) out.push(v);
    return out;
  };
  const xTicks = ticks(-xMin).map((v) => -v);
  const yTicks = ticks(yMax);
  const gates = PHASES.map((p) => [...samples].reverse().find((s) => s.phase === p.key)).filter(
    (s): s is Sample => !!s && (!zoom || s.downrange_m >= -10000),
  );
  const gateNames = [
    t("descent.gate.high"),
    t("descent.gate.low"),
    t("descent.gate.touchdown"),
  ];
  const move = (e: React.MouseEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const km = xMin + ((e.clientX - box.left) / box.width) * (0 - xMin);
    let best = shown[0];
    for (const s of shown) {
      if (Math.abs(s.downrange_m / 1000 - km) < Math.abs(best.downrange_m / 1000 - km)) best = s;
    }
    setHover(best);
  };
  return (
    <div className="descent-chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={t("descent.chartLabel", {
          range: zoom ? t("descent.rangeFinal") : t("descent.rangeFull"),
        })}
      >
        {yTicks.map((v) => (
          <g key={`y${v}`}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="grid" />
            <text x={L - 8} y={y(v) + 4} className="tick" textAnchor="end">
              {v}
            </text>
          </g>
        ))}
        {xTicks.map((v) => (
          <text key={`x${v}`} x={x(v)} y={H - B + 18} className="tick" textAnchor="middle">
            {v}
          </text>
        ))}
        <text x={L} y={H - 6} className="axis-title">
          {t("descent.xAxis")}
        </text>
        <text x={8} y={14} className="axis-title">
          {t("descent.yAxis")}
        </text>
        {PHASES.map((p) => {
          const own = shown.filter((s) => s.phase === p.key || (p.key === "terminal" && s.phase === "landed"));
          // Start each phase at the previous phase's last point so the path is continuous.
          const before = own.length ? shown[shown.indexOf(own[0]) - 1] : undefined;
          const pts = before ? [before, ...own] : own;
          return pts.length > 1 ? (
            <polyline
              key={p.key}
              points={pts.map((s) => `${x(s.downrange_m / 1000)},${y(s.altitude_m / 1000)}`).join(" ")}
              fill="none"
              stroke={p.color}
              strokeWidth={2}
              strokeLinejoin="round"
            />
          ) : null;
        })}
        {gates.map((s, i) => (
          <g key={s.phase}>
            <circle
              cx={x(s.downrange_m / 1000)}
              cy={y(s.altitude_m / 1000)}
              r={4.5}
              fill={PHASES[i].color}
              stroke="#10171e"
              strokeWidth={2}
            />
            <text
              x={x(s.downrange_m / 1000) - 8}
              // Touchdown sits on the baseline: label it below to clear the low-gate label.
              y={y(s.altitude_m / 1000) + (s.phase === "terminal" ? 20 : -10)}
              className="direct-label"
              textAnchor="end"
            >
              {gateNames[PHASES.findIndex((p) => p.key === s.phase)]}
            </text>
          </g>
        ))}
        {hover && (
          <g pointerEvents="none">
            <line x1={x(hover.downrange_m / 1000)} x2={x(hover.downrange_m / 1000)} y1={T} y2={H - B} className="crosshair" />
            <circle
              cx={x(hover.downrange_m / 1000)}
              cy={y(hover.altitude_m / 1000)}
              r={5}
              fill={PHASES.find((p) => p.key === hover.phase)?.color ?? "#199e70"}
              stroke="#10171e"
              strokeWidth={2}
            />
          </g>
        )}
        <rect
          x={L}
          y={T}
          width={W - L - R}
          height={H - T - B}
          fill="transparent"
          onMouseMove={move}
          onMouseLeave={() => setHover(null)}
        />
      </svg>
      {hover && (
        <div
          className="descent-tooltip"
          style={{ left: `${Math.min(85, Math.max(15, (x(hover.downrange_m / 1000) / W) * 100))}%` }}
          role="status"
        >
          <b>{t("descent.afterPdi", { time: minutes(hover.t_s) })}</b>
          <span>
            {PHASES.some((p) => p.key === hover.phase)
              ? t(`descent.phase.${hover.phase}`)
              : t("descent.gate.touchdown")}
          </span>
          <span>{t("descent.tip.altitude", { km: (hover.altitude_m / 1000).toFixed(2) })}</span>
          <span>{t("descent.tip.downrange", { km: (hover.downrange_m / 1000).toFixed(2) })}</span>
          <span>{t("descent.tip.speed", { v: hover.speed_mps.toFixed(1) })}</span>
          <span>
            {t("descent.tip.throttle", { pct: (hover.throttle_fraction * 100).toFixed(0) })}
          </span>
        </div>
      )}
    </div>
  );
}

export default function Descent({ sites, onCandidates, onLocation }: { sites: Preset[]; onLocation?: (lat:number,lon:number)=>void; onCandidates?: (items: MissionCandidate[]) => void }) {
  const { t, tText } = useTranslate();
  const {setCandidateId,setPreview} = useAnalysisView();
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [siteId, setSiteId] = useState("malapert-massif-r23");
  const [lat, setLat] = useState("-85.9948");
  const [lon, setLon] = useState("359.76422");
  const [profileId, setProfileId] = useState("apollo-lm-11");
  const [custom, setCustom] = useState({
    mass_at_pdi_kg: "15000",
    usable_propellant_kg: "8000",
    max_thrust_n: "44000",
    design_thrust_n: "46000",
    min_throttle_fraction: "0.1",
    max_throttle_fraction: "0.6",
    isp_s: "310",
  });
  const [altitude, setAltitude] = useState("111.12");
  const [inclination, setInclination] = useState("90");
  const [allowTurn, setAllowTurn] = useState(false);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [reserveFps, setReserveFps] = useState("145");
  const [routes, setRoutes] = useState<RouteResult | null>(null);
  const [optimizing, setOptimizing] = useState(false);
  const [zoom, setZoom] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    call("/vehicle-profiles")
      .then((body) => setProfiles(body.profiles))
      .catch((e: Error) => setError(errorText(e, t, "communication.requestFailed")));
  }, []);

  const vehicle = () =>
    profileId === "custom"
      ? {
          custom_profile: Object.fromEntries(
            Object.entries(custom).map(([k, v]) => [k, Number(v)]),
          ),
        }
      : { profile_id: profileId };

  const optimize = async () => {
    const ticket = planScene.begin();
    onLocation?.(Number(lat),Number(lon));
    setOptimizing(true);
    setError("");
    try {
      const coordinates = { lat: Number(lat), lon: Number(lon) };
      const result: RouteResult = await call("/descents/optimize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            site_id: siteId,
            latitude_deg: Number(lat),
            longitude_deg_east: Number(lon),
            inclination_deg: Number(inclination),
            allow_plane_change: allowTurn,
            reserve_delta_v_mps: Number(reserveFps) * 0.3048,
            ...vehicle(),
          }),
        });
      if (!planScene.isCurrent(ticket)) return;
      setRoutes(result);
      onCandidates?.(result.routes.map((r) => ({
        id: `descent:${r.id}`, label: r.id, ...coordinates,
        status: r.feasible ? "accepted" : "rejected", score: null,
        source: "Classical descent grid / API", altitudeKm: r.orbit_altitude_km,
        explanation: r.rejection_reasons.map((v) => `${v.reason}: ${v.detail}`).join("; ") || (result.pareto_ids.includes(r.id) ? "Pareto frontier" : "Feasible alternative"),
        metrics: [
          { label: "Orbit", value: `${r.orbit_altitude_km} km` },
          { label: "Propellant", value: `${r.propellant_used_kg?.toFixed(1) ?? "—"} kg` },
          { label: "Reserve margin", value: `${r.reserve_margin_kg?.toFixed(1) ?? "—"} kg` },
          { label: "Assessment", value: `${r.assessment_time_s} s` },
          { label: "Δv", value: `${r.delta_v_mps?.toFixed(1) ?? "—"} m/s` },
        ],
      })));

    } catch (e) {
      if (!planScene.isCurrent(ticket)) return;
      setError(errorText(e, t, "communication.requestFailed"));
    } finally {
      if (planScene.isCurrent(ticket)) setOptimizing(false);
    }
  };

  const planInputs = JSON.stringify([siteId,lat,lon,profileId,custom,altitude,inclination,allowTurn,reserveFps]);
  const planScene = useScenarioResult<Plan>("descent", planInputs);
  useEffect(() => { setPlan(null); setRoutes(null); setBusy(false); setOptimizing(false); onCandidates?.([]); }, [planInputs]);
  const run = async () => {
    const ticket = planScene.begin();
    onLocation?.(Number(lat),Number(lon));
    setBusy(true);
    setError("");
    try {
      const body: Record<string, unknown> = {
        site_id: siteId,
        latitude_deg: Number(lat),
        longitude_deg_east: Number(lon),
        orbit: { altitude_km: Number(altitude), inclination_deg: Number(inclination) },
        allow_plane_change: allowTurn,
        ...vehicle(),
      };
      const response: Plan = await call("/descents", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      if (!planScene.isCurrent(ticket)) return;
      setPlan(response);
      planScene.commit(ticket,response);
    } catch (e) {
      if (!planScene.isCurrent(ticket)) return;
      setError(errorText(e, t, "communication.requestFailed"));
    } finally {
      if (planScene.isCurrent(ticket)) setBusy(false);
    }
  };

  const selected = profiles.find((p) => p.profile_id === profileId);
  const powered = useMemo(() => plan?.phases.filter((p) => p.name !== "coast") ?? [], [plan]);
  // R6/R7 analyse the same plan as the form above.
  const planBase = {
    site_id: siteId,
    latitude_deg: Number(lat),
    longitude_deg_east: Number(lon),
    orbit: { altitude_km: Number(altitude), inclination_deg: Number(inclination) },
    allow_plane_change: allowTurn,
    reserve_delta_v_mps: Number(reserveFps) * FT,
    ...vehicle(),
  };

  return (
    <section id="descent" className="window-search-section descent-section" aria-label={t("descent.label")}>
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t("descent.eyebrow")}</p>
          <h2>
            {t("descent.title")}<span>?</span>
          </h2>
          <p className="muted">{t("descent.intro")}</p>
        </div>
        <span className="outline-tag">{t("descent.tag")}</span>
      </div>
      <div className="search-layout">
        <form
          className="panel search-form"
          onSubmit={(e) => {
            e.preventDefault();
            run();
          }}
        >
          <fieldset disabled={busy || optimizing}>
            <h3>{t("descent.landingSite")}</h3>
            <label>
              {t("descent.site")}
              <select
                aria-label={t("descent.siteLabel")}
                value={siteId}
                onChange={(e) => {
                  setSiteId(e.target.value);
                  const next = sites.find((s) => s.site_id === e.target.value);
                  if (next) {
                    setLat(String(next.latitude_deg));
                    setLon(String(next.longitude_deg_east));
                  }
                }}
              >
                <option value="custom">{t("app.form.custom")}</option>
                {sites.map((s) => (
                  <option key={s.site_id} value={s.site_id}>
                    {s.display_name}
                  </option>
                ))}
              </select>
            </label>
            <div className="field-pair">
              <label>
                {t("app.form.latitude")} · °
                <input type="number" step="any" min="-90" max="-80" value={lat} onChange={(e) => setLat(e.target.value)} />
              </label>
              <label>
                {t("app.form.longitude")} · °E
                <input type="number" step="any" min="0" max="359.999" value={lon} onChange={(e) => setLon(e.target.value)} />
              </label>
            </div>
            <h3>{t("descent.vehicle")}</h3>
            <label>
              {t("descent.profile")}
              <select aria-label={t("descent.profileLabel")} value={profileId} onChange={(e) => setProfileId(e.target.value)}>
                {profiles.map((p) => (
                  <option key={p.profile_id} value={p.profile_id}>
                    {p.name}
                  </option>
                ))}
                <option value="custom">{t("descent.customParams")}</option>
              </select>
            </label>
            {profileId === "custom" &&
              (
                [
                  "mass_at_pdi_kg",
                  "usable_propellant_kg",
                  "max_thrust_n",
                  "design_thrust_n",
                  "min_throttle_fraction",
                  "max_throttle_fraction",
                  "isp_s",
                ] as const
              ).map((key) => (
                <label key={key}>
                  {t(`descent.custom.${key}`)}
                  <input
                    type="number"
                    step="any"
                    value={custom[key]}
                    onChange={(e) => setCustom({ ...custom, [key]: e.target.value })}
                  />
                </label>
              ))}
            {selected && profileId !== "custom" && (
              <p className="field-help">
                {t("descent.profileSummary", {
                  mass: (selected.mass_at_pdi_kg / 1000).toFixed(2),
                  propellant: (selected.usable_propellant_kg / 1000).toFixed(2),
                  thrust: (selected.max_thrust_n / 1000).toFixed(1),
                  isp: selected.isp_s,
                  min: selected.min_throttle_fraction * 100,
                  max: selected.max_throttle_fraction * 100,
                })}
              </p>
            )}
            <details className="scenario-settings"><summary>{t("workspace.advanced")}</summary>
            <h3>{t("descent.initialOrbit")}</h3>
            <label>
              {t("descent.altitude")}
              <input type="number" step="any" min="30" max="500" value={altitude} onChange={(e) => setAltitude(e.target.value)} />
            </label>
            <label>
              {t("descent.inclination")} · °
              <input
                aria-label={t("descent.inclinationLabel")}
                type="number"
                step="any"
                min="0"
                max="180"
                value={inclination}
                onChange={(e) => setInclination(e.target.value)}
              />
            </label>
            <label className="checkbox-label">
              <input type="checkbox" checked={allowTurn} onChange={(e) => setAllowTurn(e.target.checked)} />
              {t("descent.planeChange")}
            </label>

            <h3>{t("descent.fuel")}</h3>
            <label>
              {t("descent.reserve")}
              <input
                aria-label={t("descent.reserveLabel")}
                type="number"
                min="0"
                max="1600"
                step="5"
                value={reserveFps}
                onChange={(e) => setReserveFps(e.target.value)}
              />
            </label>
            </details>
            <button type="submit" className="primary">
              {busy ? t("descent.computing") : `${t("descent.compute")} ↗`}
            </button>
            <button
              type="button"
              className="text-button"
              disabled={optimizing}
              onClick={optimize}
            >
              {optimizing ? t("descent.optimizing") : `${t("descent.optimize")} ↗`}
            </button>
            <p className="field-help">{t("descent.reserveNote")}</p>
          </fieldset>
          <p className="field-help">{t("descent.defaultNote")}</p>
        </form>
        <div className="search-results">
          {error && (
            <div className="alert" role="alert">
              {error}
            </div>
          )}
          {!plan && !routes && !error && (
            <div className="panel search-empty">{t("descent.empty")}</div>
          )}
          {plan && (
            <>
              <div className={`panel descent-verdict ${plan.feasible ? "ok" : "blocked"}`} role="status">
                <strong>
                  {plan.feasible ? `✓ ${t("descent.feasible")}` : `✕ ${t("descent.notFeasible")}`}
                </strong>
                {plan.limiting_factors.map((f) => (
                  <p key={f.factor}>
                    <b>{f.factor.replaceAll("_", " ")}:</b> {f.detail}
                  </p>
                ))}
              </div>
              <div className="communication-metrics descent-metrics">
                <div>
                  <span className="metric-label">{t("descent.metric.dv")}</span>
                  <strong>{plan.totals.powered_delta_v_mps.toFixed(0)} m/s</strong>
                  <small>
                    {plan.reference_comparison.model_powered_delta_v_fps.toFixed(0)} fps ·{" "}
                    {plan.reference_comparison.difference_percent >= 0 ? "+" : ""}
                    {t("descent.vsApollo", {
                      pct: plan.reference_comparison.difference_percent.toFixed(1),
                    })}
                  </small>
                </div>
                <div>
                  <span className="metric-label">{t("descent.metric.time")}</span>
                  <strong>{minutes(plan.totals.duration_s)}</strong>
                  <small>
                    {t("descent.coastPowered", {
                      coast: minutes(plan.deorbit.coast_s),
                      powered: minutes(plan.totals.duration_s - plan.deorbit.coast_s),
                    })}
                  </small>
                </div>
                <div>
                  <span className="metric-label">{t("descent.metric.propellant")}</span>
                  <strong>{plan.totals.propellant_used_kg.toFixed(0)} kg</strong>
                  <small>
                    {t("descent.margin", { kg: plan.totals.propellant_margin_kg.toFixed(0) })}
                  </small>
                </div>
              </div>
              <div className="descent-chart-head">
                <div className="contribution-legend">
                  {PHASES.map((p) => (
                    <span key={p.key}>
                      <i style={{ background: p.color }} /> {t(`descent.phase.${p.key}`)}
                    </span>
                  ))}
                </div>
                <div role="group" aria-label={t("descent.chartRange")} className="chart-range">
                  <button type="button" className="text-button" aria-pressed={!zoom} onClick={() => setZoom(false)}>
                    {t("descent.fullDescent")}
                  </button>
                  <button type="button" className="text-button" aria-pressed={zoom} onClick={() => setZoom(true)}>
                    {t("descent.final10")}
                  </button>
                </div>
              </div>
              <ProfileChart samples={plan.samples} zoom={zoom} />
              <table className="ranking-table">
                <caption>{t("descent.table.caption")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("descent.table.phase")}</th>
                    <th scope="col">{t("descent.table.time")}</th>
                    <th scope="col">{t("descent.table.dv")}</th>
                    <th scope="col">{t("descent.table.propellant")}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row">{t("descent.table.doi")}</th>
                    <td>{t("descent.table.impulsive")}</td>
                    <td>{plan.deorbit.delta_v_mps.toFixed(1)} m/s</td>
                    <td>{t("descent.table.inMass")}</td>
                  </tr>
                  {plan.plane_change.required_deg > 0 && (
                    <tr>
                      <th scope="row">
                        {t("descent.table.planeChange")}{" "}
                        {plan.plane_change.applied ? "" : t("descent.table.notApplied")}
                      </th>
                      <td>{t("descent.table.beforeDoi")}</td>
                      <td>{plan.plane_change.delta_v_mps.toFixed(1)} m/s</td>
                      <td>
                        {t("descent.table.rotation", {
                          deg: plan.plane_change.required_deg.toFixed(2),
                        })}
                      </td>
                    </tr>
                  )}
                  {plan.phases.map((p) => (
                    <tr key={p.name}>
                      <th scope="row">
                        {p.name === "coast"
                          ? t("descent.table.coast")
                          : t(`descent.phase.${p.name}`)}
                      </th>
                      <td>
                        {minutes(p.start_s)} → {minutes(p.end_s)}
                      </td>
                      <td>
                        {p.delta_v_mps.toFixed(1)} m/s · {(p.delta_v_mps / FT).toFixed(0)} fps
                      </td>
                      <td>{p.propellant_kg.toFixed(0)} kg</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <ul className="candidate-status">
                <li>
                  {t("descent.pdiStart", {
                    km: (plan.pdi_range_m / 1000).toFixed(0),
                    speed: plan.deorbit.periapsis_speed_mps.toFixed(0),
                    n: powered.length,
                  })}
                </li>
                <li>
                  {t("descent.touchdown", {
                    error: plan.touchdown.position_error_m.toFixed(2),
                    vertical: Math.abs(plan.touchdown.vertical_speed_mps).toFixed(2),
                    horizontal: plan.touchdown.horizontal_speed_mps.toFixed(2),
                    height: plan.site.terrain_height_m.toFixed(0),
                  })}
                </li>
              </ul>
              <details className="communication-limits">
                <summary>
                  {t("descent.assumptions")} <span>+</span>
                </summary>
                <ul>
                  {plan.assumptions.map((a) => (
                    <li key={a}>{tText(a)}</li>
                  ))}
                  {selected?.sources &&
                    Object.entries(selected.sources).map(([k, v]) => (
                      <li key={k}>
                        <b>{k}:</b> {v}
                      </li>
                    ))}
                </ul>
                {selected?.urls?.map((u) => (
                  <a key={u} href={u} target="_blank" rel="noreferrer">
                    {u} ↗
                  </a>
                ))}
              </details>
            </>
          )}
          {routes && <RouteOptimizer result={routes} onSelect={id=>{setPreview(null);setCandidateId(`descent:${id}`);}} />}
          {plan && <ResultSceneLink stale={planScene.stale} show={planScene.show} />}
          {/* Deeper analyses stay closed until the user opens one. */}
          <div className="tool-cards">
            <h3>{t("descent.more.title")}</h3>
            <p className="field-help">{t("descent.more.hint")}</p>
            {(
              [
                ["risk", <RiskPanel base={planBase} />],
                ["backups", <BackupPlans base={planBase} />],
                ["twin", <TwinPanel base={planBase} />],
                ["terrainRecovery", <TerrainRecoveryPanel base={planBase} />],
                ["surface", <SurfaceRoutes site={planBase} />],
              ] as const
            ).map(([id, panel]) => (
              <details className="tool-card" key={id}>
                <summary>
                  <b>{t(`descent.more.${id}.title`)}</b>
                  <span>{t(`descent.more.${id}.desc`)}</span>
                </summary>
                {panel}
              </details>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
