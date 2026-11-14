import { useState } from "react";
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

const PRESETS: { id: string; label: string; events: object[] }[] = [
  { id: "nominal", label: "No fault", events: [] },
  {
    id: "lost",
    label: "Navigation sensor lost at 450 s",
    events: [{ kind: "sensor_degradation", t_s: 450, health: "lost" }],
  },
  {
    id: "thrust",
    label: "Engine −25 % at 520 s",
    events: [{ kind: "thrust_loss", t_s: 520, available_fraction: 0.75 }],
  },
  {
    id: "leak",
    label: "700 kg propellant leak at 600 s",
    events: [{ kind: "propellant_leak", t_s: 600, leaked_kg: 700 }],
  },
  {
    id: "obstacle",
    label: "Boulder field on the target at 640 s",
    events: [{ kind: "obstacle_detected", t_s: 640, down_m: 10, cross_m: 0, radius_m: 60 }],
  },
];
const EVENT_LABEL: Record<string, string> = {
  thrust_loss: "Engine thrust loss",
  sensor_degradation: "Navigation sensor change",
  propellant_leak: "Propellant leak",
  comm_outage: "Ground link outage",
  obstacle_detected: "Obstacle detected (synthetic)",
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
          : "Request failed",
    );
  }
  return body;
}

function ErrorChart({ result }: { result: Result }) {
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
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Navigation estimate error and its expected size over the descent, with fault times marked.">
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
              {EVENT_LABEL[e.event?.kind ?? ""] ?? e.event?.kind}
            </text>
          </g>
        ))}
        <polyline points={line("estimate_std_m")} className="twin-std" />
        <polyline points={line("estimate_error_m")} className="twin-error" />
        <text x={L} y={H - 8} className="axis-title">
          time since powered descent start · 0–{Math.round(tMax)} s
        </text>
      </svg>
      <div className="risk-legend">
        <span>
          <i className="twin-swatch error" /> actual estimate error (known only in simulation)
        </span>
        <span>
          <i className="twin-swatch std" /> expected error the vehicle believes
        </span>
      </div>
    </div>
  );
}

export default function TwinPanel({ base }: { base: Record<string, unknown> }) {
  const [preset, setPreset] = useState("lost");
  const [seed, setSeed] = useState("2026");
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const { allow_plane_change: _ignored, ...plan } = base;
      setResult(
        await call("/twin/runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...plan,
            seed: Number(seed),
            events: PRESETS.find((p) => p.id === preset)?.events ?? [],
          }),
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const check = result?.run.touchdown_check;
  const notes = result?.run.log.filter((e) => e.kind !== "touchdown") ?? [];

  return (
    <div className="risk-panel twin-panel" aria-label="Descent digital twin">
      <h3>Digital twin · what the lander knows vs what is true</h3>
      <form
        className="risk-controls"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <fieldset disabled={busy}>
          <label>
            Fault scenario
            <select aria-label="Fault scenario" value={preset} onChange={(e) => setPreset(e.target.value)}>
              {PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Seed
            <input aria-label="Twin seed" type="number" min="0" step="1" value={seed} onChange={(e) => setSeed(e.target.value)} />
          </label>
          <button type="submit" className="text-button">
            {busy ? "Flying the twin…" : "Fly the twin ↗"}
          </button>
        </fieldset>
        <p className="field-help">
          Guidance steers on its own noisy estimate, not the truth. The twin does not replan, so you see what a fault does
          to the original plan. All telemetry is simulated.
        </p>
      </form>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {result && check && (
        <div className="risk-result">
          <div className={`panel descent-verdict ${check.success ? "ok" : "blocked"}`} role="status">
            <strong>
              {check.success ? "✓ Lands within tolerance" : "✕ Landing fails"} ·{" "}
              {result.run.final.position_error_m.toFixed(result.run.final.position_error_m < 10 ? 2 : 0)} m from the target
            </strong>
            {check.reasons.map((r) => (
              <p key={r.reason}>{r.detail}</p>
            ))}
            <p>
              {result.run.plan.valid
                ? "The original plan stayed valid."
                : `The original plan became invalid: ${result.run.plan.invalidated_by.map((d) => d.replaceAll("_", " ")).join(", ")}.`}{" "}
              Replay ID {result.run.log_hash.slice(0, 10)}.
            </p>
          </div>
          <div className="communication-metrics">
            <div>
              <span className="metric-label">VS. NO-FAULT FLIGHT</span>
              <strong>
                {result.differences.position_error_m >= 0 ? "+" : ""}
                {result.differences.position_error_m.toFixed(1)} m
              </strong>
              <small>landing-point error change</small>
            </div>
            <div>
              <span className="metric-label">PROPELLANT</span>
              <strong>
                {result.differences.propellant_remaining_kg >= 0 ? "+" : ""}
                {result.differences.propellant_remaining_kg.toFixed(0)} kg
              </strong>
              <small>
                {result.run.propellant_remaining_kg.toFixed(0)} kg left · reserve margin {check.reserve_margin_kg.toFixed(0)} kg
              </small>
            </div>
            <div>
              <span className="metric-label">NAVIGATION ERROR AT TOUCHDOWN</span>
              <strong>{result.run.final_estimate_error_m.toFixed(result.run.final_estimate_error_m < 10 ? 2 : 0)} m</strong>
              <small>
                vertical {Math.abs(result.run.final.vertical_speed_mps).toFixed(2)} m/s · horizontal{" "}
                {result.run.final.horizontal_speed_mps.toFixed(2)} m/s
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
                    ? `${EVENT_LABEL[e.event?.kind ?? ""] ?? e.event?.kind} applied`
                    : e.kind === "plan_invalidated"
                      ? `Plan invalidated — ${e.reason}`
                      : e.kind === "no_plan_effect"
                        ? `No effect on the plan — ${e.reason}`
                        : `${e.event?.kind ?? e.kind} not reached before touchdown`}
                </li>
              ))}
            </ul>
          )}
          <details className="communication-limits">
            <summary>
              Twin model & assumptions <span>+</span>
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
