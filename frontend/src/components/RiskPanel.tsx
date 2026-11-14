import { useState } from "react";
import "./risk.css";

// R6 (S10): renders the backend Monte Carlo; every number comes from the API.
type Ellipse = {
  center_m: [number, number];
  semi_major_m: number;
  semi_minor_m: number;
  orientation_deg: number;
};
type Stats = { min: number; p05: number; p50: number; p95: number; max: number };
type Sensitivity = {
  source: string;
  sigma: number;
  unit: string;
  enabled: boolean;
  failure_association: number | null;
  failure_correlation: number | null;
  significant: boolean;
};
export type RiskResult = {
  result_id: string;
  runs: number;
  seed: number;
  nominal: { feasible: boolean };
  success: { count: number; rate: number; wilson95: [number, number] };
  failure_causes: Record<string, number>;
  landing: {
    approach_azimuth_deg: number;
    points: { down_m: number; cross_m: number; success: boolean }[];
    ellipse_1sigma: Ellipse;
    ellipse_3sigma: Ellipse;
    within_3sigma_fraction: number;
  };
  propellant: {
    remaining_kg: Stats;
    margin_above_reserve_kg: Stats;
    histogram: { edges_kg: number[]; counts: number[] };
  };
  hazard: {
    evaluated: boolean;
    slope_limit_deg: number;
    ellipse_3sigma_cells: { count: number; steep_fraction: number | null; unknown_fraction: number | null } | null;
  };
  sensitivity: Sensitivity[];
  dominant_failure_source: string | null;
  noise_threshold: number;
  assumptions: string[];
};

const OK = "#199e70";
const FAIL = "#e66767";
const SOURCE_LABELS: Record<string, string> = {
  thrust: "Engine thrust",
  isp: "Specific impulse",
  pdi_downrange: "PDI position",
  pdi_speed: "PDI speed",
  terrain_height: "Terrain height (DEM)",
  navigation_down: "Navigation · downrange",
  navigation_cross: "Navigation · crossrange",
};
const CAUSE_LABELS: Record<string, string> = {
  propellant_exhausted: "Propellant exhausted",
  propellant_reserve: "Reserve not kept",
  touchdown_position: "Missed guidance target",
  vertical_speed: "Hard landing (vertical speed)",
  horizontal_speed: "Sliding landing (horizontal speed)",
  hazard_slope: "Landed on steep terrain",
  hazard_unknown: "Landed on unknown terrain",
};
const pct = (v: number) => `${(100 * v).toFixed(v > 0 && v < 0.01 ? 1 : 0)} %`;

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

function DispersionPlot({ result }: { result: RiskResult }) {
  const { points, ellipse_1sigma: one, ellipse_3sigma: three } = result.landing;
  const S = 360,
    C = S / 2,
    PAD = 28;
  const reach = Math.max(
    50,
    ...points.map((p) => Math.max(Math.abs(p.down_m), Math.abs(p.cross_m))),
    Math.abs(three.center_m[0]) + three.semi_major_m,
    Math.abs(three.center_m[1]) + three.semi_major_m,
  );
  const scale = (C - PAD) / (reach * 1.05);
  // Travel (downrange) points up the plot; crossrange is to the right.
  const px = (cross: number) => C + cross * scale;
  const py = (down: number) => C - down * scale;
  const ellipse = (e: Ellipse, cls: string) => {
    const theta = (e.orientation_deg * Math.PI) / 180;
    const angle = (Math.atan2(-Math.cos(theta), Math.sin(theta)) * 180) / Math.PI;
    const x = px(e.center_m[1]);
    const y = py(e.center_m[0]);
    return (
      <ellipse
        className={cls}
        cx={x}
        cy={y}
        rx={Math.max(e.semi_major_m * scale, 0.5)}
        ry={Math.max(e.semi_minor_m * scale, 0.5)}
        transform={`rotate(${angle} ${x} ${y})`}
      />
    );
  };
  const bar = Math.pow(10, Math.floor(Math.log10(reach)));
  return (
    <figure className="risk-plot">
      <svg
        viewBox={`0 0 ${S} ${S}`}
        role="img"
        aria-label={`Landing dispersion of ${points.length} simulated touchdowns around the target, with 1-sigma and 3-sigma ellipses; ${result.success.count} of ${result.runs} runs succeeded.`}
      >
        <line x1={PAD} x2={S - PAD} y1={C} y2={C} className="risk-axis" />
        <line x1={C} x2={C} y1={PAD} y2={S - PAD} className="risk-axis" />
        <text x={C + 6} y={PAD + 10} className="risk-tick">
          ↑ direction of travel
        </text>
        {ellipse(three, "risk-ellipse three")}
        {ellipse(one, "risk-ellipse one")}
        {points.map((p, i) =>
          p.success ? (
            <circle key={i} cx={px(p.cross_m)} cy={py(p.down_m)} r={2.6} fill={OK} fillOpacity={0.75} />
          ) : (
            <g key={i} stroke={FAIL} strokeWidth={1.6}>
              <line x1={px(p.cross_m) - 3} x2={px(p.cross_m) + 3} y1={py(p.down_m) - 3} y2={py(p.down_m) + 3} />
              <line x1={px(p.cross_m) - 3} x2={px(p.cross_m) + 3} y1={py(p.down_m) + 3} y2={py(p.down_m) - 3} />
            </g>
          ),
        )}
        <circle cx={C} cy={C} r={5} className="risk-target" />
        <line x1={PAD} x2={PAD + bar * scale} y1={S - 12} y2={S - 12} className="risk-scale" />
        <text x={PAD} y={S - 18} className="risk-tick">
          {bar >= 1000 ? `${bar / 1000} km` : `${bar} m`}
        </text>
      </svg>
      <figcaption className="risk-legend">
        <span>
          <i className="dot" style={{ background: OK }} /> successful touchdown
        </span>
        <span>
          <b style={{ color: FAIL }}>✕</b> failed touchdown
        </span>
        <span>
          <i className="ring" /> target · dashed 1σ · solid 3σ ellipse
        </span>
      </figcaption>
    </figure>
  );
}

function Histogram({ result }: { result: RiskResult }) {
  const { edges_kg: edges, counts } = result.propellant.histogram;
  const W = 360,
    H = 120,
    B = 22;
  const top = Math.max(...counts, 1);
  const width = (W - 8) / counts.length;
  return (
    <svg
      className="risk-histogram"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`Propellant remaining at touchdown, from ${edges[0].toFixed(0)} to ${edges[edges.length - 1].toFixed(0)} kg.`}
    >
      {counts.map((c, i) => (
        <rect
          key={i}
          x={4 + i * width + 1}
          width={Math.max(width - 2, 1)}
          y={H - B - (c / top) * (H - B - 6)}
          height={(c / top) * (H - B - 6)}
          className="risk-bar"
        />
      ))}
      <text x={4} y={H - 6} className="risk-tick">
        {edges[0].toFixed(0)} kg
      </text>
      <text x={W - 4} y={H - 6} className="risk-tick" textAnchor="end">
        {edges[edges.length - 1].toFixed(0)} kg
      </text>
    </svg>
  );
}

export default function RiskPanel({ base }: { base: Record<string, unknown> }) {
  const [runs, setRuns] = useState("200");
  const [seed, setSeed] = useState("2026");
  const [navigation, setNavigation] = useState("100");
  const [terrain, setTerrain] = useState("2");
  const [thrust, setThrust] = useState("1");
  const [azimuth, setAzimuth] = useState("180");
  const [result, setResult] = useState<RiskResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      setResult(
        await call("/descents/risk", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...base,
            runs: Number(runs),
            seed: Number(seed),
            approach_azimuth_deg: Number(azimuth),
            uncertainty: {
              navigation_sigma_m: Number(navigation),
              terrain_height_sigma_m: Number(terrain),
              thrust_sigma_fraction: Number(thrust) / 100,
            },
          }),
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const causes = result
    ? Object.entries(result.failure_causes)
        .filter(([, n]) => n > 0)
        .sort((a, b) => b[1] - a[1])
    : [];
  const cells = result?.hazard.ellipse_3sigma_cells;

  return (
    <div className="risk-panel" aria-label="Landing risk">
      <h3>Risk &amp; uncertainty · Monte Carlo</h3>
      <form
        className="risk-controls"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <fieldset disabled={busy}>
          <label>
            Runs
            <select aria-label="Monte Carlo runs" value={runs} onChange={(e) => setRuns(e.target.value)}>
              {["100", "200", "500"].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label>
            Seed
            <input aria-label="Random seed" type="number" min="0" step="1" value={seed} onChange={(e) => setSeed(e.target.value)} />
          </label>
          <label>
            Navigation error 1σ · m
            <input type="number" min="0" max="5000" step="any" value={navigation} onChange={(e) => setNavigation(e.target.value)} />
          </label>
          <label>
            Terrain height error 1σ · m
            <input type="number" min="0" max="50" step="any" value={terrain} onChange={(e) => setTerrain(e.target.value)} />
          </label>
          <label>
            Thrust error 1σ · %
            <input type="number" min="0" max="20" step="any" value={thrust} onChange={(e) => setThrust(e.target.value)} />
          </label>
          <label>
            Approach azimuth · °
            <input type="number" min="0" max="359.9" step="any" value={azimuth} onChange={(e) => setAzimuth(e.target.value)} />
          </label>
          <button type="submit" className="text-button">
            {busy ? `Flying ${runs} descents…` : "Run Monte Carlo ↗"}
          </button>
        </fieldset>
        <p className="field-help">
          Uses the site, vehicle, orbit and reserve above. Same seed and inputs reproduce the same
          result. Error sizes are planning assumptions, not certified vehicle values.
        </p>
      </form>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {result && (
        <div className="risk-result">
          <div
            className={`panel descent-verdict ${result.success.rate >= 0.9 ? "ok" : "blocked"}`}
            role="status"
          >
            <strong>
              {pct(result.success.rate)} of {result.runs} simulated landings succeed
            </strong>
            <p>
              95 % confidence {pct(result.success.wilson95[0])}–{pct(result.success.wilson95[1])} · seed{" "}
              {result.seed} · result {result.result_id.slice(0, 10)}
              {result.dominant_failure_source && (
                <>
                  {" "}
                  · main driver of failures:{" "}
                  <b>{SOURCE_LABELS[result.dominant_failure_source] ?? result.dominant_failure_source}</b>
                </>
              )}
            </p>
          </div>
          <div className="communication-metrics">
            <div>
              <span className="metric-label">3σ LANDING ELLIPSE</span>
              <strong>
                {(2 * result.landing.ellipse_3sigma.semi_major_m).toFixed(0)} ×{" "}
                {(2 * result.landing.ellipse_3sigma.semi_minor_m).toFixed(0)} m
              </strong>
              <small>{pct(result.landing.within_3sigma_fraction)} of landings inside</small>
            </div>
            <div>
              <span className="metric-label">STEEP TERRAIN IN ELLIPSE</span>
              <strong>{cells && cells.steep_fraction !== null ? pct(cells.steep_fraction) : "not evaluated"}</strong>
              <small>
                {cells
                  ? `${cells.count} DEM cells > ${result.hazard.slope_limit_deg}° · unknown ${pct(cells.unknown_fraction ?? 0)}`
                  : "terrain not cached"}
              </small>
            </div>
            <div>
              <span className="metric-label">FUEL ABOVE RESERVE (P5)</span>
              <strong>{result.propellant.margin_above_reserve_kg.p05.toFixed(0)} kg</strong>
              <small>median {result.propellant.margin_above_reserve_kg.p50.toFixed(0)} kg</small>
            </div>
          </div>
          <div className="risk-grid">
            <DispersionPlot result={result} />
            <div>
              <h4>Why landings fail</h4>
              {causes.length ? (
                <ul className="risk-causes">
                  {causes.map(([cause, n]) => (
                    <li key={cause}>
                      <span>{CAUSE_LABELS[cause] ?? cause}</span>
                      <b>
                        {n} · {pct(n / result.runs)}
                      </b>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">No failures in this sample.</p>
              )}
              <h4>Which uncertainty matters</h4>
              <ul className="risk-sensitivity">
                {result.sensitivity.map((s) => (
                  <li key={s.source} className={s.enabled ? "" : "off"}>
                    <span>{SOURCE_LABELS[s.source] ?? s.source}</span>
                    {s.enabled && s.failure_association !== null ? (
                      <span className="risk-meter" aria-label={`association ${s.failure_association.toFixed(2)}`}>
                        <i style={{ width: `${Math.min(s.failure_association, 1) * 100}%` }} />
                        <em style={{ left: `${Math.min(result.noise_threshold, 1) * 100}%` }} />
                      </span>
                    ) : (
                      <small>{s.enabled ? "no failures to explain" : "off"}</small>
                    )}
                    {s.significant && <b>significant</b>}
                  </li>
                ))}
              </ul>
              <p className="field-help">
                Bar: rank correlation with failure. The tick marks the noise level 2/√runs; shorter bars are
                not distinguishable from chance.
              </p>
              <h4>Propellant left at touchdown</h4>
              <Histogram result={result} />
            </div>
          </div>
          <details className="communication-limits">
            <summary>
              Monte Carlo model & assumptions <span>+</span>
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
