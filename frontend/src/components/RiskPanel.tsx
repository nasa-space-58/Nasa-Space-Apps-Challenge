import ServerExplanation from "./ServerExplanation";
import { useScenarioResult, ResultSceneLink } from "../analysisView";
import { useEffect, useState } from "react";
import { useTranslate, type Translate } from "../i18n";
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
// Labels for backend source and cause ids; unknown ids fall back to the raw id.
const sourceLabel = (t: Translate, id: string) => {
  const text = t(`risk.sources.${id}`);
  return text === `risk.sources.${id}` ? id : text;
};
const causeLabel = (t: Translate, id: string) => {
  const text = t(`risk.causes.${id}`);
  return text === `risk.causes.${id}` ? id : text;
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
          : "request_failed",
    );
  }
  return body;
}

function DispersionPlot({ result }: { result: RiskResult }) {
  const { t } = useTranslate();
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
        aria-label={t("risk.plotLabel", { points: points.length, ok: result.success.count, runs: result.runs })}
      >
        <line x1={PAD} x2={S - PAD} y1={C} y2={C} className="risk-axis" />
        <line x1={C} x2={C} y1={PAD} y2={S - PAD} className="risk-axis" />
        <text x={C + 6} y={PAD + 10} className="risk-tick">
          ↑ {t("risk.travel")}
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
          <i className="dot" style={{ background: OK }} /> {t("risk.legend.success")}
        </span>
        <span>
          <b style={{ color: FAIL }}>✕</b> {t("risk.legend.failure")}
        </span>
        <span>
          <i className="ring" /> {t("risk.legend.ellipses")}
        </span>
      </figcaption>
    </figure>
  );
}

function Histogram({ result }: { result: RiskResult }) {
  const { t } = useTranslate();
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
      aria-label={t("risk.histogramLabel", { min: edges[0].toFixed(0), max: edges[edges.length - 1].toFixed(0) })}
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
  const { t } = useTranslate();
  const [runs, setRuns] = useState("200");
  // Advanced uncertainty inputs; defaults match the backend UncertaintySettings.
  const [isp, setIsp] = useState("0.5");
  const [pdiDown, setPdiDown] = useState("500");
  const [pdiSpeed, setPdiSpeed] = useState("1");
  const [seed, setSeed] = useState("2026");
  const [navigation, setNavigation] = useState("100");
  const [terrain, setTerrain] = useState("2");
  const [thrust, setThrust] = useState("1");
  const [azimuth, setAzimuth] = useState("180");
  const scenarioInputs = JSON.stringify([base,runs,isp,pdiDown,pdiSpeed,seed,navigation,terrain,thrust,azimuth]);
  const { result, begin, isCurrent, commit, show, stale } = useScenarioResult<RiskResult>("risk", scenarioInputs);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { setBusy(false); setError(""); }, [scenarioInputs]);

  const run = async () => {
    const ticket = begin();
    setBusy(true);
    setError("");
    try {
      commit(ticket,
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
              isp_sigma_fraction: Number(isp) / 100,
              pdi_downrange_sigma_m: Number(pdiDown),
              pdi_speed_sigma_mps: Number(pdiSpeed),
            },
          }),
        }),
      );
    } catch (e) {
      if (!isCurrent(ticket)) return;
      const message = e instanceof Error ? e.message : "request_failed";
      setError(message === "request_failed" ? t("risk.requestFailed") : message);
    } finally {
      if (isCurrent(ticket)) setBusy(false);
    }
  };

  const causes = result
    ? Object.entries(result.failure_causes)
        .filter(([, n]) => n > 0)
        .sort((a, b) => b[1] - a[1])
    : [];
  const cells = result?.hazard.ellipse_3sigma_cells;

  return (
    <div className="risk-panel" aria-label={t("risk.label")}>
      <h3>{t("risk.title")}</h3>
      <form
        className="risk-controls"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <fieldset disabled={busy}>
          <label>
            {t("risk.runs")}
            <select aria-label={t("risk.runs")} value={runs} onChange={(e) => setRuns(e.target.value)}>
              {["100", "200", "500"].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("risk.navigation")}
            <input type="number" min="0" max="5000" step="any" value={navigation} onChange={(e) => setNavigation(e.target.value)} />
          </label>
          <label>
            {t("risk.terrain")}
            <input type="number" min="0" max="50" step="any" value={terrain} onChange={(e) => setTerrain(e.target.value)} />
          </label>
          <label>
            {t("risk.thrust")}
            <input type="number" min="0" max="20" step="any" value={thrust} onChange={(e) => setThrust(e.target.value)} />
          </label>
          <details className="advanced-settings">
            <summary>{t("common.advancedSettings")}</summary>
            <label>
              {t("risk.isp")}
              <input type="number" min="0" max="10" step="any" value={isp} onChange={(e) => setIsp(e.target.value)} />
            </label>
            <label>
              {t("risk.pdiDown")}
              <input type="number" min="0" max="20000" step="any" value={pdiDown} onChange={(e) => setPdiDown(e.target.value)} />
            </label>
            <label>
              {t("risk.pdiSpeed")}
              <input type="number" min="0" max="50" step="any" value={pdiSpeed} onChange={(e) => setPdiSpeed(e.target.value)} />
            </label>
            <label>
              {t("risk.azimuth")}
              <input type="number" min="0" max="359.9" step="any" value={azimuth} onChange={(e) => setAzimuth(e.target.value)} />
            </label>
            <label>
              {t("risk.seed")}
              <input aria-label={t("risk.seed")} type="number" min="0" step="1" value={seed} onChange={(e) => setSeed(e.target.value)} />
            </label>
          </details>
          <button type="submit" className="text-button">
            {busy ? t("risk.running", { runs }) : t("risk.run")}
          </button>
        </fieldset>
        <p className="field-help">{t("risk.help")}</p>
      </form>
      {error && (
        <div className="alert" role="alert">
          <ServerExplanation text={error} />
        </div>
      )}
      {result && <ResultSceneLink stale={stale} show={show} />}
      {result && (
        <div className="risk-result">
          <div
            className={`panel descent-verdict ${result.success.rate >= 0.9 ? "ok" : "blocked"}`}
            role="status"
          >
            <strong>
              {t("risk.verdict", { rate: pct(result.success.rate), runs: result.runs })}
            </strong>
            <p>
              {t("risk.confidence", {
                low: pct(result.success.wilson95[0]),
                high: pct(result.success.wilson95[1]),
                seed: result.seed,
                id: result.result_id.slice(0, 10),
              })}
              {result.dominant_failure_source && (
                <>
                  {" "}
                  · {t("risk.mainDriver")}{" "}
                  <b>{sourceLabel(t, result.dominant_failure_source)}</b>
                </>
              )}
            </p>
          </div>
          <div className="communication-metrics">
            <div>
              <span className="metric-label">{t("risk.metrics.ellipse")}</span>
              <strong>
                {(2 * result.landing.ellipse_3sigma.semi_major_m).toFixed(0)} ×{" "}
                {(2 * result.landing.ellipse_3sigma.semi_minor_m).toFixed(0)} m
              </strong>
              <small>{t("risk.metrics.inside", { share: pct(result.landing.within_3sigma_fraction) })}</small>
            </div>
            <div>
              <span className="metric-label">{t("risk.metrics.steep")}</span>
              <strong>{cells && cells.steep_fraction !== null ? pct(cells.steep_fraction) : t("risk.metrics.notEvaluated")}</strong>
              <small>
                {cells
                  ? t("risk.metrics.cells", {
                      count: cells.count,
                      limit: result.hazard.slope_limit_deg,
                      unknown: pct(cells.unknown_fraction ?? 0),
                    })
                  : t("risk.metrics.noTerrain")}
              </small>
            </div>
            <div>
              <span className="metric-label">{t("risk.metrics.fuel")}</span>
              <strong>{result.propellant.margin_above_reserve_kg.p05.toFixed(0)} kg</strong>
              <small>{t("risk.metrics.median", { kg: result.propellant.margin_above_reserve_kg.p50.toFixed(0) })}</small>
            </div>
          </div>
          <div className="risk-grid">
            <DispersionPlot result={result} />
            <div>
              <h4>{t("risk.whyFail")}</h4>
              {causes.length ? (
                <ul className="risk-causes">
                  {causes.map(([cause, n]) => (
                    <li key={cause}>
                      <span>{causeLabel(t, cause)}</span>
                      <b>
                        {n} · {pct(n / result.runs)}
                      </b>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">{t("risk.noFailures")}</p>
              )}
              <h4>{t("risk.whichMatters")}</h4>
              <ul className="risk-sensitivity">
                {result.sensitivity.map((s) => (
                  <li key={s.source} className={s.enabled ? "" : "off"}>
                    <span>{sourceLabel(t, s.source)}</span>
                    {s.enabled && s.failure_association !== null ? (
                      <span className="risk-meter" aria-label={t("risk.association", { value: s.failure_association.toFixed(2) })}>
                        <i style={{ width: `${Math.min(s.failure_association, 1) * 100}%` }} />
                        <em style={{ left: `${Math.min(result.noise_threshold, 1) * 100}%` }} />
                      </span>
                    ) : (
                      <small>{s.enabled ? t("risk.nothingToExplain") : t("risk.off")}</small>
                    )}
                    {s.significant && <b>{t("risk.significant")}</b>}
                  </li>
                ))}
              </ul>
              <p className="field-help">{t("risk.barHelp")}</p>
              <h4>{t("risk.propellantLeft")}</h4>
              <Histogram result={result} />
            </div>
          </div>
          <details className="communication-limits">
            <summary>
              {t("risk.assumptions")} <span>+</span>
            </summary>
            <ul>
              {result.assumptions.map((a) => (
                <li key={a}><ServerExplanation text={a} /></li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}
