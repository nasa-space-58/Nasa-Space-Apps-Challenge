import ServerExplanation from "./ServerExplanation";
import { useScenarioResult, ResultSceneLink } from "../analysisView";
import { useEffect, useState } from "react";
import { useTranslate, type Translate } from "../i18n";
import "./twin.css";

// R9 (S13): renders the backend digital twin; physics and estimation run in the API.
type Stream = { downrange_m: number; altitude_m: number; vertical_speed_mps: number; horizontal_speed_mps: number };
type Row = {
  t_s: number;
  phase: string;
  truth: Stream;
  observation: Stream | null;
  estimate: Stream;
  estimate_error_m: number;
  estimate_std_m: number;
  sensor_health: string;
  available_thrust_n: number;
};
type LogEntry = {
  t_s: number;
  kind: string;
  event?: { kind: string };
  event_kind?: string;
  dependency?: string;
  reason?: string;
};
type Check = { success: boolean; reasons: { reason: string; detail: string }[]; reserve_margin_kg: number };
type Result = {
  result_id: string;
  run: {
    timeline: Row[];
    final: { position_error_m: number; vertical_speed_mps: number; horizontal_speed_mps: number };
    touchdown_check: Check;
    log: LogEntry[];
    plan: { valid: boolean; invalidated_by: string[] };
    final_estimate_error_m: number;
    propellant_remaining_kg: number;
    log_hash: string;
  };
  baseline: { touchdown_check: Check; final: { position_error_m: number } };
  differences: Record<string, number>;
  assumptions: string[];
};

// Labels come from twin.presets.<id>; fault-free flight is the default.
const PRESETS: { id: string; events: object[] }[] = [
  { id: "nominal", events: [] },
  {
    id: "lost",
    events: [{ kind: "sensor_degradation", t_s: 450, health: "lost" }],
  },
  {
    id: "thrust",
    events: [{ kind: "thrust_loss", t_s: 520, available_fraction: 0.75 }],
  },
  {
    id: "leak",
    events: [{ kind: "propellant_leak", t_s: 600, leaked_kg: 700 }],
  },
  {
    id: "obstacle",
    events: [{ kind: "obstacle_detected", t_s: 640, down_m: 10, cross_m: 0, radius_m: 60 }],
  },
];
const eventLabel = (t: Translate, kind: string) => {
  const text = t(`twin.events.${kind}`);
  return text === `twin.events.${kind}` ? kind : text;
};

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

function ErrorChart({ result }: { result: Result }) {
  const { t } = useTranslate();
  const rows = result.run.timeline;
  const W = 720,
    H = 220,
    L = 56,
    R = 14,
    T = 14,
    B = 32;
  const tMax = rows[rows.length - 1].t_s || 1;
  // log10 scale keeps centimetres and kilometres readable together.
  const lo = -2,
    hi = Math.max(1, Math.ceil(Math.log10(Math.max(...rows.map((r) => Math.max(r.estimate_error_m, r.estimate_std_m)), 1))));
  const x = (t: number) => L + (t / tMax) * (W - L - R);
  const y = (v: number) => {
    const l = Math.min(Math.max(Math.log10(Math.max(v, 1e-2)), lo), hi);
    return T + (1 - (l - lo) / (hi - lo)) * (H - T - B);
  };
  const line = (key: "estimate_error_m" | "estimate_std_m") => rows.map((r) => `${x(r.t_s)},${y(r[key])}`).join(" ");
  const events = result.run.log.filter((e) => e.kind === "event_applied");
  const ticks = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
  return (
    <div className="descent-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("twin.chartLabel")}>
        {ticks.map((p) => (
          <g key={p}>
            <line x1={L} x2={W - R} y1={y(10 ** p)} y2={y(10 ** p)} className="grid" />
            <text x={L - 6} y={y(10 ** p) + 4} className="tick" textAnchor="end">
              {10 ** p >= 1000 ? `${10 ** p / 1000} km` : `${10 ** p} m`}
            </text>
          </g>
        ))}
        {events.map((e, i) => (
          <g key={i}>
            <line x1={x(e.t_s)} x2={x(e.t_s)} y1={T} y2={H - B} className="twin-event-line" />
            <text x={x(e.t_s) + 4} y={T + 10 + 12 * i} className="direct-label">
              {eventLabel(t, e.event?.kind ?? "")}
            </text>
          </g>
        ))}
        <polyline points={line("estimate_std_m")} className="twin-std" />
        <polyline points={line("estimate_error_m")} className="twin-error" />
        <text x={L} y={H - 8} className="axis-title">
          {t("twin.axis", { seconds: Math.round(tMax) })}
        </text>
      </svg>
      <div className="risk-legend">
        <span>
          <i className="twin-swatch error" /> {t("twin.legend.error")}
        </span>
        <span>
          <i className="twin-swatch std" /> {t("twin.legend.std")}
        </span>
      </div>
    </div>
  );
}

export default function TwinPanel({ base }: { base: Record<string, unknown> }) {
  const { t } = useTranslate();
  const [preset, setPreset] = useState("nominal");
  // Advanced inputs; defaults match the backend SensorSettings and BatterySettings.
  const [positionSigma, setPositionSigma] = useState("5");
  const [speedSigma, setSpeedSigma] = useState("0.1");
  const [thrustNoise, setThrustNoise] = useState("0.5");
  const [energy, setEnergy] = useState("50");
  const [load, setLoad] = useState("2000");
  const [seed, setSeed] = useState("2026");
  const scenarioInputs = JSON.stringify([base,preset,positionSigma,speedSigma,thrustNoise,energy,load,seed]);
  const { result, begin, isCurrent, commit, show, stale } = useScenarioResult<Result>("twin", scenarioInputs);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { setBusy(false); setError(""); }, [scenarioInputs]);

  const run = async () => {
    const ticket = begin();
    setBusy(true);
    setError("");
    try {
      const { allow_plane_change: _ignored, ...plan } = base;
      commit(ticket,
        await call("/twin/runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...plan,
            seed: Number(seed),
            events: PRESETS.find((p) => p.id === preset)?.events ?? [],
            sensor: {
              position_sigma_m: Number(positionSigma),
              speed_sigma_mps: Number(speedSigma),
              thrust_noise_fraction: Number(thrustNoise) / 100,
            },
            battery: { initial_energy_j: Number(energy) * 1e6, load_w: Number(load) },
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

  const check = result?.run.touchdown_check;
  const notes = result?.run.log.filter((e) => e.kind !== "touchdown") ?? [];

  return (
    <div className="risk-panel twin-panel" aria-label={t("twin.label")}>
      <h3>{t("twin.title")}</h3>
      <form
        className="risk-controls"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <fieldset disabled={busy}>
          <label>
            {t("twin.scenario")}
            <select aria-label={t("twin.scenario")} value={preset} onChange={(e) => setPreset(e.target.value)}>
              {PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {t(`twin.presets.${p.id}`)}
                </option>
              ))}
            </select>
          </label>
          <details className="advanced-settings">
            <summary>{t("common.advancedSettings")}</summary>
            <label>
              {t("twin.positionSigma")}
              <input type="number" min="0" max="5000" step="any" value={positionSigma} onChange={(e) => setPositionSigma(e.target.value)} />
            </label>
            <label>
              {t("twin.speedSigma")}
              <input type="number" min="0" max="50" step="any" value={speedSigma} onChange={(e) => setSpeedSigma(e.target.value)} />
            </label>
            <label>
              {t("twin.thrustNoise")}
              <input type="number" min="0" max="10" step="any" value={thrustNoise} onChange={(e) => setThrustNoise(e.target.value)} />
            </label>
            <label>
              {t("twin.energy")}
              <input type="number" min="0.001" max="10000" step="any" value={energy} onChange={(e) => setEnergy(e.target.value)} />
            </label>
            <label>
              {t("twin.load")}
              <input type="number" min="0" max="100000" step="any" value={load} onChange={(e) => setLoad(e.target.value)} />
            </label>
            <label>
              {t("risk.seed")}
              <input aria-label={t("risk.seed")} type="number" min="0" step="1" value={seed} onChange={(e) => setSeed(e.target.value)} />
            </label>
          </details>
          <button type="submit" className="text-button">
            {busy ? t("twin.running") : t("twin.run")}
          </button>
        </fieldset>
        <p className="field-help">{t("twin.help")}</p>
      </form>
      {error && (
        <div className="alert" role="alert">
          <ServerExplanation text={error} />
        </div>
      )}
      {result && <ResultSceneLink stale={stale} show={show} />}
      {result && check && (
        <div className="risk-result">
          <div className={`panel descent-verdict ${check.success ? "ok" : "blocked"}`} role="status">
            <strong>
              {check.success ? `✓ ${t("twin.success")}` : `✕ ${t("twin.failure")}`} ·{" "}
              {t("twin.fromTarget", {
                m: result.run.final.position_error_m.toFixed(result.run.final.position_error_m < 10 ? 2 : 0),
              })}
            </strong>
            {check.reasons.map((r) => (
              <p key={r.reason}><ServerExplanation text={r.detail} reason={r.reason} /></p>
            ))}
            <p>
              {result.run.plan.valid
                ? t("twin.planValid")
                : t("twin.planInvalid", {
                    reasons: result.run.plan.invalidated_by.map((d) => eventLabel(t,d)).join(", "),
                  })}{" "}
              {t("twin.replay", { id: result.run.log_hash.slice(0, 10) })}
            </p>
          </div>
          <div className="communication-metrics">
            <div>
              <span className="metric-label">{t("twin.metrics.versus")}</span>
              <strong>
                {result.differences.position_error_m >= 0 ? "+" : ""}
                {result.differences.position_error_m.toFixed(1)} m
              </strong>
              <small>{t("twin.metrics.errorChange")}</small>
            </div>
            <div>
              <span className="metric-label">{t("twin.metrics.propellant")}</span>
              <strong>
                {result.differences.propellant_remaining_kg >= 0 ? "+" : ""}
                {result.differences.propellant_remaining_kg.toFixed(0)} kg
              </strong>
              <small>
                {t("twin.metrics.left", {
                  kg: result.run.propellant_remaining_kg.toFixed(0),
                  margin: check.reserve_margin_kg.toFixed(0),
                })}
              </small>
            </div>
            <div>
              <span className="metric-label">{t("twin.metrics.navError")}</span>
              <strong>{result.run.final_estimate_error_m.toFixed(result.run.final_estimate_error_m < 10 ? 2 : 0)} m</strong>
              <small>
                {t("twin.metrics.speeds", {
                  vertical: Math.abs(result.run.final.vertical_speed_mps).toFixed(2),
                  horizontal: result.run.final.horizontal_speed_mps.toFixed(2),
                })}
              </small>
            </div>
          </div>
          <ErrorChart result={result} />
          {notes.length > 0 && (
            <ul className="twin-log">
              {notes.map((e, i) => (
                <li key={i} className={e.kind}>
                  <b>{e.t_s.toFixed(1)} s</b>{" "}
                  {e.kind === "event_applied"
                    ? t("twin.log.applied", { event: eventLabel(t, e.event?.kind ?? "") })
                    : e.kind === "plan_invalidated"
                      ? <>{t("twin.log.invalidated", { reason: "" })}<ServerExplanation text={e.reason} /></>
                      : e.kind === "no_plan_effect"
                        ? <>{t("twin.log.noEffect", { reason: "" })}<ServerExplanation text={e.reason} /></>
                        : t("twin.log.notReached", { event: eventLabel(t, e.event?.kind ?? e.kind) })}
                </li>
              ))}
            </ul>
          )}
          <details className="communication-limits">
            <summary>
              {t("twin.assumptions")} <span>+</span>
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
