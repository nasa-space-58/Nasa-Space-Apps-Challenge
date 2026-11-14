import { useEffect, useMemo, useState } from "react";
import type { Preset } from "../types";

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
  { key: "braking", label: "Braking", color: "#3987e5" },
  { key: "approach", label: "Approach", color: "#d95926" },
  { key: "terminal", label: "Terminal descent", color: "#199e70" },
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
          : "Request failed",
    );
  }
  return body;
}

function ProfileChart({ samples, zoom }: { samples: Sample[]; zoom: boolean }) {
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
  const gateNames = ["High gate", "Low gate", "Touchdown"];
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
        aria-label={`Altitude above the site versus downrange, ${zoom ? "final 10 km" : "full powered descent"}. Phase details are in the table below.`}
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
          Downrange to site · km
        </text>
        <text x={8} y={14} className="axis-title">
          Altitude above site · km
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
          <b>T+{minutes(hover.t_s)} after PDI</b>
          <span>
            {PHASES.find((p) => p.key === hover.phase)?.label ?? "Touchdown"}
          </span>
          <span>Altitude {(hover.altitude_m / 1000).toFixed(2)} km</span>
          <span>Downrange {(hover.downrange_m / 1000).toFixed(2)} km</span>
          <span>Speed {hover.speed_mps.toFixed(1)} m/s</span>
          <span>Throttle {(hover.throttle_fraction * 100).toFixed(0)} % of design</span>
        </div>
      )}
    </div>
  );
}

export default function Descent({ sites }: { sites: Preset[] }) {
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
  const [zoom, setZoom] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    call("/vehicle-profiles")
      .then((body) => setProfiles(body.profiles))
      .catch((e: Error) => setError(e.message));
  }, []);

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const body: Record<string, unknown> = {
        site_id: siteId,
        latitude_deg: Number(lat),
        longitude_deg_east: Number(lon),
        orbit: { altitude_km: Number(altitude), inclination_deg: Number(inclination) },
        allow_plane_change: allowTurn,
      };
      if (profileId === "custom") {
        body.custom_profile = Object.fromEntries(
          Object.entries(custom).map(([k, v]) => [k, Number(v)]),
        );
      } else {
        body.profile_id = profileId;
      }
      setPlan(
        await call("/descents", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const selected = profiles.find((p) => p.profile_id === profileId);
  const powered = useMemo(() => plan?.phases.filter((p) => p.name !== "coast") ?? [], [plan]);

  return (
    <section id="descent" className="window-search-section descent-section" aria-label="Descent trajectory">
      <div className="section-heading">
        <div>
          <p className="eyebrow">07 / PLAN THE DESCENT</p>
          <h2>
            How do we get down<span>?</span>
          </h2>
          <p className="muted">
            From lunar orbit to touchdown: descent orbit insertion, braking, approach and
            terminal descent, with timing and delta-v for each phase.
          </p>
        </div>
        <span className="outline-tag">APOLLO 11 PROFILE</span>
      </div>
      <div className="search-layout">
        <form
          className="panel search-form"
          onSubmit={(e) => {
            e.preventDefault();
            run();
          }}
        >
          <fieldset disabled={busy}>
            <h3>Landing site</h3>
            <label>
              Site
              <select
                aria-label="Descent site"
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
                <option value="custom">Custom coordinates</option>
                {sites.map((s) => (
                  <option key={s.site_id} value={s.site_id}>
                    {s.display_name}
                  </option>
                ))}
              </select>
            </label>
            <div className="field-pair">
              <label>
                Latitude · °
                <input type="number" step="any" min="-90" max="-80" value={lat} onChange={(e) => setLat(e.target.value)} />
              </label>
              <label>
                Longitude · °E
                <input type="number" step="any" min="0" max="359.999" value={lon} onChange={(e) => setLon(e.target.value)} />
              </label>
            </div>
            <h3>Vehicle</h3>
            <label>
              Profile
              <select aria-label="Vehicle profile" value={profileId} onChange={(e) => setProfileId(e.target.value)}>
                {profiles.map((p) => (
                  <option key={p.profile_id} value={p.profile_id}>
                    {p.name}
                  </option>
                ))}
                <option value="custom">Custom parameters</option>
              </select>
            </label>
            {profileId === "custom" &&
              (
                [
                  ["mass_at_pdi_kg", "Mass at PDI · kg"],
                  ["usable_propellant_kg", "Propellant at PDI · kg"],
                  ["max_thrust_n", "Maximum thrust · N"],
                  ["design_thrust_n", "Design thrust · N"],
                  ["min_throttle_fraction", "Minimum throttle · fraction"],
                  ["max_throttle_fraction", "Throttleable up to · fraction"],
                  ["isp_s", "Specific impulse · s"],
                ] as const
              ).map(([key, label]) => (
                <label key={key}>
                  {label}
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
                {(selected.mass_at_pdi_kg / 1000).toFixed(2)} t at PDI ·{" "}
                {(selected.usable_propellant_kg / 1000).toFixed(2)} t propellant ·{" "}
                {(selected.max_thrust_n / 1000).toFixed(1)} kN · Isp {selected.isp_s} s · throttle{" "}
                {selected.min_throttle_fraction * 100}–{selected.max_throttle_fraction * 100} % of design
              </p>
            )}
            <h3>Initial orbit</h3>
            <label>
              Circular orbit altitude · km
              <input type="number" step="any" min="30" max="500" value={altitude} onChange={(e) => setAltitude(e.target.value)} />
            </label>
            <label>
              Inclination · °
              <input
                aria-label="Orbit inclination"
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
              Allow a plane change if the orbit misses the site
            </label>
            <button type="submit" className="primary">
              {busy ? "Computing trajectory…" : "Compute descent ↗"}
            </button>
          </fieldset>
          <p className="field-help">
            Default: 60 n.mi. circular orbit and the Apollo 11 Lunar Module (NASA TN D-6846,
            Apollo 11 Mission Report). The site height comes from the cached LOLA terrain.
          </p>
        </form>
        <div className="search-results">
          {error && (
            <div className="alert" role="alert">
              {error}
            </div>
          )}
          {!plan && !error && <div className="panel search-empty">Choose a site and compute a descent.</div>}
          {plan && (
            <>
              <div className={`panel descent-verdict ${plan.feasible ? "ok" : "blocked"}`} role="status">
                <strong>{plan.feasible ? "✓ Reaches the site within tolerance" : "✕ Not feasible as specified"}</strong>
                {plan.limiting_factors.map((f) => (
                  <p key={f.factor}>
                    <b>{f.factor.replaceAll("_", " ")}:</b> {f.detail}
                  </p>
                ))}
              </div>
              <div className="communication-metrics descent-metrics">
                <div>
                  <span className="metric-label">POWERED DELTA-V</span>
                  <strong>{plan.totals.powered_delta_v_mps.toFixed(0)} m/s</strong>
                  <small>
                    {plan.reference_comparison.model_powered_delta_v_fps.toFixed(0)} fps ·{" "}
                    {plan.reference_comparison.difference_percent >= 0 ? "+" : ""}
                    {plan.reference_comparison.difference_percent.toFixed(1)} % vs Apollo 11 plan
                  </small>
                </div>
                <div>
                  <span className="metric-label">DESCENT TIME</span>
                  <strong>{minutes(plan.totals.duration_s)}</strong>
                  <small>
                    {minutes(plan.deorbit.coast_s)} coast + {minutes(plan.totals.duration_s - plan.deorbit.coast_s)} powered
                  </small>
                </div>
                <div>
                  <span className="metric-label">PROPELLANT</span>
                  <strong>{plan.totals.propellant_used_kg.toFixed(0)} kg</strong>
                  <small>margin {plan.totals.propellant_margin_kg.toFixed(0)} kg</small>
                </div>
              </div>
              <div className="descent-chart-head">
                <div className="contribution-legend">
                  {PHASES.map((p) => (
                    <span key={p.key}>
                      <i style={{ background: p.color }} /> {p.label}
                    </span>
                  ))}
                </div>
                <div role="group" aria-label="Chart range" className="chart-range">
                  <button type="button" className="text-button" aria-pressed={!zoom} onClick={() => setZoom(false)}>
                    Full descent
                  </button>
                  <button type="button" className="text-button" aria-pressed={zoom} onClick={() => setZoom(true)}>
                    Final 10 km
                  </button>
                </div>
              </div>
              <ProfileChart samples={plan.samples} zoom={zoom} />
              <table className="ranking-table">
                <caption>Phase timing and budget</caption>
                <thead>
                  <tr>
                    <th scope="col">Phase</th>
                    <th scope="col">Time</th>
                    <th scope="col">Delta-v</th>
                    <th scope="col">Propellant</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row">Descent orbit insertion</th>
                    <td>T+0:00 (impulsive)</td>
                    <td>{plan.deorbit.delta_v_mps.toFixed(1)} m/s</td>
                    <td>in mass at PDI</td>
                  </tr>
                  {plan.plane_change.required_deg > 0 && (
                    <tr>
                      <th scope="row">Plane change {plan.plane_change.applied ? "" : "(not applied)"}</th>
                      <td>before DOI</td>
                      <td>{plan.plane_change.delta_v_mps.toFixed(1)} m/s</td>
                      <td>{plan.plane_change.required_deg.toFixed(2)}° rotation</td>
                    </tr>
                  )}
                  {plan.phases.map((p) => (
                    <tr key={p.name}>
                      <th scope="row">{p.name === "coast" ? "Coast to PDI" : PHASES.find((x) => x.key === p.name)?.label}</th>
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
                  Powered descent starts {(plan.pdi_range_m / 1000).toFixed(0)} km uprange at{" "}
                  {plan.deorbit.periapsis_speed_mps.toFixed(0)} m/s; {powered.length} powered phases.
                </li>
                <li>
                  Touchdown: {plan.touchdown.position_error_m.toFixed(2)} m from the site, vertical{" "}
                  {Math.abs(plan.touchdown.vertical_speed_mps).toFixed(2)} m/s, horizontal{" "}
                  {plan.touchdown.horizontal_speed_mps.toFixed(2)} m/s. Site{" "}
                  {plan.site.terrain_height_m.toFixed(0)} m above the 1737.4 km sphere.
                </li>
              </ul>
              <details className="communication-limits">
                <summary>
                  Physical assumptions & sources <span>+</span>
                </summary>
                <ul>
                  {plan.assumptions.map((a) => (
                    <li key={a}>{a}</li>
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
        </div>
      </div>
    </section>
  );
}
