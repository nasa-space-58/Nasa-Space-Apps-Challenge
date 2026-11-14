import { useEffect, useState } from "react";
import "./alternatives.css";

// R7 (S11): renders backend backup plans; reachability is computed in the API.
type Reason = { reason: string; detail: string };
type Row = {
  id: string;
  role: "primary" | "backup";
  down_m: number;
  cross_m: number;
  distance_m: number;
  bearing_deg: number;
  latitude_deg: number;
  longitude_deg_east: number;
  terrain_height_m: number;
  safety: { max_landing_slope_deg: number | null; hazard_distance_m: number | null };
  safety_score: number | null;
  reachable: boolean;
  rejection_reasons: Reason[];
  divert_delta_v_mps: number;
  lateral_delta_v_mps: number;
  propellant_remaining_kg: number;
  reserve_margin_kg: number;
  hover_margin_s: number;
  time_to_touchdown_s: number;
};
type Result = {
  result_id: string;
  divert_from: "high_gate" | "low_gate";
  divert_state: { altitude_m: number; speed_mps: number; time_to_go_s: number; t_s: number };
  primary: Row;
  candidates: Row[];
  reachable_ids: string[];
  assumptions: string[];
  settings: { approach_azimuth_deg: number; search_radius_m: number };
};

const OK = "#199e70";
const FAIL = "#e66767";
const GATE = { high_gate: "high gate (start of approach)", low_gate: "low gate (start of terminal descent)" };

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

const readMarks = (id: string): string[] => {
  try {
    return JSON.parse(localStorage.getItem(`backups:${id}`) ?? "[]");
  } catch {
    return [];
  }
};

function BackupMap({ result }: { result: Result }) {
  const rows = [result.primary, ...result.candidates];
  const S = 320,
    C = S / 2,
    PAD = 26;
  const reach = Math.max(200, ...rows.map((r) => Math.max(Math.abs(r.down_m), Math.abs(r.cross_m))));
  const scale = (C - PAD) / (reach * 1.15);
  const px = (cross: number) => C + cross * scale;
  const py = (down: number) => C - down * scale;
  return (
    <svg
      className="backup-map"
      viewBox={`0 0 ${S} ${S}`}
      role="img"
      aria-label={`Primary site at the centre and ${result.candidates.length} backup sites; ${result.reachable_ids.length} are reachable. Details are in the table.`}
    >
      <circle cx={C} cy={C} r={result.settings.search_radius_m * scale} className="backup-radius" />
      <line x1={C} x2={C} y1={PAD} y2={S - PAD} className="risk-axis" />
      <line x1={PAD} x2={S - PAD} y1={C} y2={C} className="risk-axis" />
      <text x={C + 6} y={PAD + 8} className="risk-tick">
        ↑ direction of travel
      </text>
      {result.candidates.map((r) => (
        <g key={r.id}>
          <line x1={C} y1={C} x2={px(r.cross_m)} y2={py(r.down_m)} className="backup-ray" />
          {r.reachable ? (
            <circle cx={px(r.cross_m)} cy={py(r.down_m)} r={6} fill={OK} />
          ) : (
            <g stroke={FAIL} strokeWidth={2}>
              <line x1={px(r.cross_m) - 5} x2={px(r.cross_m) + 5} y1={py(r.down_m) - 5} y2={py(r.down_m) + 5} />
              <line x1={px(r.cross_m) - 5} x2={px(r.cross_m) + 5} y1={py(r.down_m) + 5} y2={py(r.down_m) - 5} />
            </g>
          )}
          <text x={px(r.cross_m) + 9} y={py(r.down_m) + 4} className="backup-label">
            {r.id}
          </text>
        </g>
      ))}
      <rect x={C - 6} y={C - 6} width={12} height={12} className="backup-primary" />
      <text x={C + 10} y={C - 8} className="backup-label">
        Primary
      </text>
    </svg>
  );
}

export default function BackupPlans({ base }: { base: Record<string, unknown> }) {
  const [divertFrom, setDivertFrom] = useState<"high_gate" | "low_gate">("high_gate");
  const [radius, setRadius] = useState("3000");
  const [count, setCount] = useState("6");
  const [azimuth, setAzimuth] = useState("180");
  const [result, setResult] = useState<Result | null>(null);
  const [marked, setMarked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (result) setMarked(readMarks(result.result_id));
  }, [result]);

  const toggle = (id: string) => {
    if (!result) return;
    const next = marked.includes(id) ? marked.filter((m) => m !== id) : [...marked, id];
    setMarked(next);
    try {
      localStorage.setItem(`backups:${result.result_id}`, JSON.stringify(next));
    } catch {
      /* marks stay in memory when storage is unavailable */
    }
  };

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      setResult(
        await call("/descents/alternatives", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...base,
            divert_from: divertFrom,
            search_radius_m: Number(radius),
            max_candidates: Number(count),
            approach_azimuth_deg: Number(azimuth),
          }),
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const rows = result ? [result.primary, ...result.candidates] : [];

  return (
    <div className="risk-panel backup-panel" aria-label="Backup landing plans">
      <h3>Alternative landing plans · backups</h3>
      <form
        className="risk-controls"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <fieldset disabled={busy}>
          <label>
            Divert from
            <select
              aria-label="Divert point"
              value={divertFrom}
              onChange={(e) => setDivertFrom(e.target.value as "high_gate" | "low_gate")}
            >
              <option value="high_gate">High gate (≈2 km up)</option>
              <option value="low_gate">Low gate (≈150 m up)</option>
            </select>
          </label>
          <label>
            Search radius · m
            <input type="number" min="200" max="10000" step="100" value={radius} onChange={(e) => setRadius(e.target.value)} />
          </label>
          <label>
            Backups
            <input type="number" min="1" max="12" step="1" value={count} onChange={(e) => setCount(e.target.value)} />
          </label>
          <label>
            Approach azimuth · °
            <input type="number" min="0" max="359.9" step="any" value={azimuth} onChange={(e) => setAzimuth(e.target.value)} />
          </label>
          <button type="submit" className="text-button">
            {busy ? "Searching backups…" : "Find backup sites ↗"}
          </button>
        </fieldset>
        <p className="field-help">
          Backups pass the terrain hard constraints and are checked for fuel, reserve, touchdown
          tolerance and engine thrust from the divert point.
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
            className={`panel descent-verdict ${result.reachable_ids.length ? "ok" : "blocked"}`}
            role="status"
          >
            <strong>
              {result.reachable_ids.length} of {result.candidates.length} backups reachable from the{" "}
              {GATE[result.divert_from]}
            </strong>
            <p>
              Divert at {result.divert_state.altitude_m.toFixed(0)} m altitude,{" "}
              {result.divert_state.speed_mps.toFixed(0)} m/s, {result.divert_state.time_to_go_s.toFixed(0)} s
              before touchdown. Marked as backup: {marked.length ? marked.join(", ") : "none"}.
            </p>
          </div>
          <div className="backup-grid">
            <BackupMap result={result} />
            <div className="table-scroll">
              <table className="ranking-table">
                <caption>Primary and backups, same metrics</caption>
                <thead>
                  <tr>
                    <th scope="col">Site</th>
                    <th scope="col">Safety</th>
                    <th scope="col">Divert</th>
                    <th scope="col">Fuel above reserve</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className={r.reachable ? "" : "route-rejected"}>
                      <th scope="row">
                        {r.id === "PRIMARY" ? "Primary" : r.id}
                        <small>
                          {r.role === "primary"
                            ? "planned site"
                            : `${(r.distance_m / 1000).toFixed(2)} km · ${r.bearing_deg.toFixed(0)}°`}
                        </small>
                      </th>
                      <td>
                        {r.safety_score !== null ? r.safety_score.toFixed(2) : "—"}
                        <small>
                          slope{" "}
                          {r.safety.max_landing_slope_deg !== null
                            ? `${r.safety.max_landing_slope_deg.toFixed(1)}°`
                            : "unknown"}
                        </small>
                      </td>
                      <td>
                        {r.role === "primary" ? "—" : `${r.divert_delta_v_mps.toFixed(1)} m/s`}
                        <small>{r.time_to_touchdown_s.toFixed(0)} s to touchdown</small>
                      </td>
                      <td>
                        {r.reserve_margin_kg.toFixed(0)} kg
                        <small>hover {r.hover_margin_s.toFixed(0)} s</small>
                      </td>
                      <td>
                        {r.reachable ? "✓ Reachable" : "✕ Not reachable"}
                        {r.rejection_reasons.map((x) => (
                          <small key={x.reason}>{x.detail}</small>
                        ))}
                        {r.role === "backup" && r.reachable && (
                          <label className="backup-mark">
                            <input type="checkbox" checked={marked.includes(r.id)} onChange={() => toggle(r.id)} />
                            mark as backup
                          </label>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <details className="communication-limits">
            <summary>
              Backup model & assumptions <span>+</span>
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
