import { useEffect, useState } from "react";
import { useTranslate } from "../i18n";
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

const readMarks = (id: string): string[] => {
  try {
    return JSON.parse(localStorage.getItem(`backups:${id}`) ?? "[]");
  } catch {
    return [];
  }
};

function BackupMap({ result }: { result: Result }) {
  const { t } = useTranslate();
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
      aria-label={t("backups.mapLabel", { count: result.candidates.length, reachable: result.reachable_ids.length })}
    >
      <circle cx={C} cy={C} r={result.settings.search_radius_m * scale} className="backup-radius" />
      <line x1={C} x2={C} y1={PAD} y2={S - PAD} className="risk-axis" />
      <line x1={PAD} x2={S - PAD} y1={C} y2={C} className="risk-axis" />
      <text x={C + 6} y={PAD + 8} className="risk-tick">
        ↑ {t("risk.travel")}
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
        {t("backups.primary")}
      </text>
    </svg>
  );
}

export default function BackupPlans({ base }: { base: Record<string, unknown> }) {
  const { t } = useTranslate();
  // Advanced inputs; defaults match the backend AlternativesRequest and SafetySettings.
  const [spacing, setSpacing] = useState("160");
  const [separation, setSeparation] = useState("400");
  const [slope, setSlope] = useState("10");
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
            candidate_spacing_m: Number(spacing),
            min_separation_m: Number(separation),
            safety: { slope_limit_deg: Number(slope) },
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

  const rows = result ? [result.primary, ...result.candidates] : [];

  return (
    <div className="risk-panel backup-panel" aria-label={t("backups.label")}>
      <h3>{t("backups.title")}</h3>
      <form
        className="risk-controls"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <fieldset disabled={busy}>
          <label>
            {t("backups.divertFrom")}
            <select
              aria-label={t("backups.divertFrom")}
              value={divertFrom}
              onChange={(e) => setDivertFrom(e.target.value as "high_gate" | "low_gate")}
            >
              <option value="high_gate">{t("backups.highGate")}</option>
              <option value="low_gate">{t("backups.lowGate")}</option>
            </select>
          </label>
          <label>
            {t("backups.radius")}
            <input type="number" min="200" max="10000" step="100" value={radius} onChange={(e) => setRadius(e.target.value)} />
          </label>
          <label>
            {t("backups.count")}
            <input type="number" min="1" max="12" step="1" value={count} onChange={(e) => setCount(e.target.value)} />
          </label>
          <details className="advanced-settings">
            <summary>{t("common.advancedSettings")}</summary>
            <label>
              {t("risk.azimuth")}
              <input type="number" min="0" max="359.9" step="any" value={azimuth} onChange={(e) => setAzimuth(e.target.value)} />
            </label>
            <label>
              {t("backups.spacing")}
              <input type="number" min="80" max="1000" step="10" value={spacing} onChange={(e) => setSpacing(e.target.value)} />
            </label>
            <label>
              {t("backups.separation")}
              <input type="number" min="0" max="5000" step="50" value={separation} onChange={(e) => setSeparation(e.target.value)} />
            </label>
            <label>
              {t("backups.slope")}
              <input type="number" min="1" max="45" step="0.5" value={slope} onChange={(e) => setSlope(e.target.value)} />
            </label>
          </details>
          <button type="submit" className="text-button">
            {busy ? t("backups.running") : t("backups.run")}
          </button>
        </fieldset>
        <p className="field-help">{t("backups.help")}</p>
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
              {t("backups.verdict", {
                reachable: result.reachable_ids.length,
                count: result.candidates.length,
                gate: t(`backups.gates.${result.divert_from}`),
              })}
            </strong>
            <p>
              {t("backups.divertState", {
                altitude: result.divert_state.altitude_m.toFixed(0),
                speed: result.divert_state.speed_mps.toFixed(0),
                seconds: result.divert_state.time_to_go_s.toFixed(0),
                marked: marked.length ? marked.join(", ") : t("backups.none"),
              })}
            </p>
          </div>
          <div className="backup-grid">
            <BackupMap result={result} />
            <div className="table-scroll">
              <table className="ranking-table">
                <caption>{t("backups.table.caption")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("backups.table.site")}</th>
                    <th scope="col">{t("backups.table.safety")}</th>
                    <th scope="col">{t("backups.table.divert")}</th>
                    <th scope="col">{t("backups.table.fuel")}</th>
                    <th scope="col">{t("backups.table.status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className={r.reachable ? "" : "route-rejected"}>
                      <th scope="row">
                        {r.id === "PRIMARY" ? t("backups.primary") : r.id}
                        <small>
                          {r.role === "primary"
                            ? t("backups.plannedSite")
                            : `${(r.distance_m / 1000).toFixed(2)} km · ${r.bearing_deg.toFixed(0)}°`}
                        </small>
                      </th>
                      <td>
                        {r.safety_score !== null ? r.safety_score.toFixed(2) : "—"}
                        <small>
                          {t("backups.slopeShort")}{" "}
                          {r.safety.max_landing_slope_deg !== null
                            ? `${r.safety.max_landing_slope_deg.toFixed(1)}°`
                            : t("backups.unknown")}
                        </small>
                      </td>
                      <td>
                        {r.role === "primary" ? "—" : `${r.divert_delta_v_mps.toFixed(1)} m/s`}
                        <small>{t("backups.toTouchdown", { seconds: r.time_to_touchdown_s.toFixed(0) })}</small>
                      </td>
                      <td>
                        {r.reserve_margin_kg.toFixed(0)} kg
                        <small>{t("backups.hover", { seconds: r.hover_margin_s.toFixed(0) })}</small>
                      </td>
                      <td>
                        {r.reachable ? `✓ ${t("backups.reachable")}` : `✕ ${t("backups.unreachable")}`}
                        {r.rejection_reasons.map((x) => (
                          <small key={x.reason}>{x.detail}</small>
                        ))}
                        {r.role === "backup" && r.reachable && (
                          <label className="backup-mark">
                            <input type="checkbox" checked={marked.includes(r.id)} onChange={() => toggle(r.id)} />
                            {t("backups.mark")}
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
              {t("backups.assumptions")} <span>+</span>
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
