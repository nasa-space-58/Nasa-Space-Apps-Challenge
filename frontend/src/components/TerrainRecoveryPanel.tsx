import ServerExplanation from "./ServerExplanation";
import { useAnalysisView, previewFor, ResultSceneLink } from "../analysisView";
import { useEffect, useRef, useState } from "react";
import "./healing.css";
import { useTranslate } from "../i18n";
import "./twin.css";

// R12: renders backend self-healing decisions; replanning and validation run in the API.
type Reason = { reason: string; detail: string };
type Option = {
  id: string;
  down_m: number;
  cross_m: number;
  reachable: boolean;
  reasons: Reason[];
  total_delta_v_mps: number;
  reserve_margin_kg: number;
  safety_score: number | null;
};
type Decision = {
  t_s: number;
  applied_at_s: number | null;
  trigger: string[];
  action: string;
  chosen: string | null;
  evaluations: number;
  budget: number;
  margin_m: number;
  estimate_std_m: number;
  solver_wall_ms: number;
  considered: Option[];
  validation: { id: string; passed: boolean; reasons: Reason[] }[];
  explanation: string;
};
type Outcome = {
  success: boolean;
  reasons: Reason[];
  final: { position_error_m: number; vertical_speed_mps: number; horizontal_speed_mps: number };
  target_offset_m: { down_m: number; cross_m: number };
  target_safety_score: number | null;
  reserve_margin_kg: number;
  abort_required: boolean;
};
type Candidate = { id: string; down_m: number; cross_m: number; safety_score: number | null };
type TimelineRow = {
  t_s: number;
  truth: { downrange_m: number; altitude_m: number };
  target_down_m?: number;
};
type Result = {
  result_id: string;
  original: Outcome;
  replanned: Outcome;
  decisions: Decision[];
  backups_over_time: { t_s: number; label: string; reachable: number; evaluated: number }[];
  candidates: Candidate[];
  obstacles: { down_m: number; cross_m: number; radius_m: number }[];
  decision_deadline_s: number;
  timeline: TimelineRow[];
  original_timeline: TimelineRow[];
  assumptions: string[];
};

const PRESETS: { id: string; label: string; events: object[] }[] = [
  {
    id: "boulder",
    label: "Boulder field on the target at 560 s",
    events: [{ kind: "obstacle_detected", t_s: 560, down_m: 0, cross_m: 0, radius_m: 150 }],
  },
  {
    id: "engine",
    label: "Engine −25 % at 520 s",
    events: [{ kind: "thrust_loss", t_s: 520, available_fraction: 0.75 }],
  },
  {
    id: "sensor",
    label: "Navigation sensor lost at 450 s",
    events: [{ kind: "sensor_degradation", t_s: 450, health: "lost" }],
  },
  {
    id: "double",
    label: "Engine −50 % and 1.5 t leak at 550 s",
    events: [
      { kind: "thrust_loss", t_s: 550, available_fraction: 0.5 },
      { kind: "propellant_leak", t_s: 550, leaked_kg: 1500 },
    ],
  },
];
const ACTION_LABEL: Record<string, string> = {
  continue: "Continue — plan re-validated",
  retarget: "Retarget to a backup",
  abort_required: "Abort required (not simulated)",
  budget_exhausted: "Budget exhausted — kept current plan",
  no_time_to_replan: "Too late to replan",
  stale_result_rejected: "Stale result rejected",
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
          : "Request failed",
    );
  }
  return body;
}

function OutcomeCard({ title, outcome }: { title: string; outcome: Outcome }) {
  const { locale, t } = useTranslate();
  const tr = locale.startsWith("tr");
  const moved = Math.hypot(outcome.target_offset_m.down_m, outcome.target_offset_m.cross_m);
  return (
    <div className={`panel descent-verdict ${outcome.success ? "ok" : "blocked"}`}>
      <span className="metric-label">{title}</span>
      <strong>
        {outcome.abort_required
          ? (tr ? "✕ İptal gerekli · manevra modellenmedi" : "✕ Abort required · maneuver not modeled")
          : outcome.success
            ? (tr ? "✓ İniş kontrolleri geçti" : "✓ Landing checks passed")
            : (tr ? "✕ İniş başarısız" : "✕ Landing fails")}
      </strong>
      <p>
        {moved > 1 ? (tr ? `Hedef ${moved.toFixed(0)} m değişti. ` : `Target moved ${moved.toFixed(0)} m. `) : (tr ? "Özgün hedef. " : "Original target. ")}
        {outcome.final.position_error_m.toFixed(outcome.final.position_error_m < 10 ? 1 : 0)} m {tr ? "hedef sapması" : "from target"} ·{" "}
        {Math.abs(outcome.final.vertical_speed_mps).toFixed(2)} m/s {tr ? "düşey hız · arazi skoru" : "down · terrain score"}{" "}
        {outcome.target_safety_score !== null ? outcome.target_safety_score.toFixed(2) : "—"}
      </p>
      {outcome.reasons.slice(0, 3).map((r) => (
        <p key={r.reason}><ServerExplanation text={r.detail} reason={r.reason} /></p>
      ))}
    </div>
  );
}

function TrajectoryChart({ result }: { result: Result }) {
  const { locale, t } = useTranslate();
  const tr = locale.startsWith("tr");
  // Both flights in the original-target frame (downrange of the original target).
  const frame = (rows: TimelineRow[]) =>
    rows
      .map((r) => ({ x: r.truth.downrange_m + (r.target_down_m ?? 0), y: r.truth.altitude_m }))
      .filter((p) => p.y <= 2500);
  const original = frame(result.original_timeline);
  const healed = frame(result.timeline);
  const all = [...original, ...healed];
  if (all.length < 2) return null;
  const W = 720,
    H = 200,
    L = 54,
    R = 14,
    T = 12,
    B = 30;
  const x0 = Math.min(...all.map((p) => p.x)),
    x1 = Math.max(...all.map((p) => p.x), 0);
  const y1 = Math.max(...all.map((p) => p.y));
  const x = (v: number) => L + ((v - x0) / (x1 - x0 || 1)) * (W - L - R);
  const y = (v: number) => T + (1 - v / (y1 || 1)) * (H - T - B);
  const line = (points: { x: number; y: number }[]) => points.map((p) => `${x(p.x)},${y(p.y)}`).join(" ");
  const target = result.replanned.target_offset_m.down_m;
  return (
    <div className="descent-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Altitude against downrange for the original and the self-healing flights, final 2.5 km of altitude.">
        <line x1={L} x2={W - R} y1={y(0)} y2={y(0)} className="grid" />
        <line x1={x(0)} x2={x(0)} y1={T} y2={H - B} className="heal-target-line" />
        <text x={x(0) - 4} y={T + 10} className="direct-label" textAnchor="end">
          {tr ? "özgün hedef" : "original target"}
        </text>
        {Math.abs(target) > 1 && (
          <>
            <line x1={x(target)} x2={x(target)} y1={T} y2={H - B} className="heal-new-target-line" />
            <text x={x(target) - 4} y={T + 24} className="direct-label" textAnchor="end">
              {tr ? "yeni hedef" : "new target"}
            </text>
          </>
        )}
        <polyline points={line(original)} className="heal-original-path" />
        <polyline points={line(healed)} className="heal-healed-path" />
        <text x={L} y={H - 8} className="axis-title">
          {tr ? "uçuş doğrultusu" : "downrange"} · {(x0 / 1000).toFixed(1)} – {(x1 / 1000).toFixed(1)} km · {tr ? "yükseklik" : "altitude"} 0–{Math.round(y1)} m
        </text>
      </svg>
      <div className="risk-legend">
        <span>
          <i className="twin-swatch std" /> {tr ? "özgün plan" : "original plan"}
        </span>
        <span>
          <i className="heal-swatch" /> {tr ? "yeniden planlama" : "with recovery"}
        </span>
      </div>
    </div>
  );
}

function DecisionMap({ result, selected }: { result: Result; selected: number }) {
  const { locale, t } = useTranslate();
  const tr = locale.startsWith("tr");
  const first = result.decisions[selected];
  const status = new Map(first?.considered.map((c) => [c.id, c.reachable]) ?? []);
  const selectedTarget = result.candidates.find(c => c.id === first?.chosen);
  const chosen = selectedTarget ?? { down_m: 0, cross_m: 0 };
  const points = [
    ...result.candidates.map((c) => [c.down_m, c.cross_m]),
    ...result.obstacles.map((o) => [Math.abs(o.down_m) + o.radius_m, Math.abs(o.cross_m) + o.radius_m]),
  ];
  const S = 320,
    C = S / 2,
    PAD = 24;
  const reach = Math.max(300, ...points.map(([d, c]) => Math.max(Math.abs(d), Math.abs(c))));
  const scale = (C - PAD) / (reach * 1.1);
  const px = (cross: number) => C + cross * scale;
  const py = (down: number) => C - down * scale;
  return (
    <figure className="risk-plot">
      <svg
        className="backup-map"
        viewBox={`0 0 ${S} ${S}`}
        role="img"
        aria-label="Original target, detected obstacles, evaluated backups and the chosen target; details are in the decision log."
      >
        <line x1={C} x2={C} y1={PAD} y2={S - PAD} className="risk-axis" />
        <line x1={PAD} x2={S - PAD} y1={C} y2={C} className="risk-axis" />
        <text x={C + 6} y={PAD + 8} className="risk-tick">
          ↑ {tr ? "uçuş yönü" : "direction of travel"}
        </text>
        {result.obstacles.map((o, i) => (
          <circle key={i} cx={px(o.cross_m)} cy={py(o.down_m)} r={o.radius_m * scale} className="heal-obstacle" />
        ))}
        {result.candidates.map((c) => {
          const ok = status.get(c.id);
          const x = px(c.cross_m),
            y = py(c.down_m);
          return (
            <g key={c.id}>
              {ok === false ? (
                <g stroke={FAIL} strokeWidth={1.8}>
                  <line x1={x - 4} x2={x + 4} y1={y - 4} y2={y + 4} />
                  <line x1={x - 4} x2={x + 4} y1={y + 4} y2={y - 4} />
                </g>
              ) : (
                <circle cx={x} cy={y} r={4.5} fill={ok ? OK : "#6c7a84"} />
              )}
              <text x={x + 7} y={y + 4} className="backup-label">
                {c.id}
              </text>
            </g>
          );
        })}
        <rect x={C - 5} y={C - 5} width={10} height={10} className="heal-original" />
        {Math.hypot(chosen.down_m, chosen.cross_m) > 1 && (
          <g>
            <line x1={C} y1={C} x2={px(chosen.cross_m)} y2={py(chosen.down_m)} className="heal-divert" />
            <circle cx={px(chosen.cross_m)} cy={py(chosen.down_m)} r={9} className="heal-chosen" />
          </g>
        )}
      </svg>
      <figcaption className="risk-legend">
        <span>
          <i className="square" /> {tr ? "özgün hedef" : "original target"}
        </span>
        <span>
          <i className="dot" style={{ background: OK }} /> {tr ? "erişilebilir yedek" : "reachable backup"}
        </span>
        <span>
          <b style={{ color: FAIL }}>✕</b> {tr ? "elendi" : "rejected"}
        </span>
        <span>
          <i className="ring" /> {tr ? "seçilen hedef · taralı: senaryo engeli" : "chosen target · shaded: simulated obstacle"}
        </span>
      </figcaption>
    </figure>
  );
}

export default function TerrainRecoveryPanel({ base }: { base: Record<string, unknown> }) {
  const { locale, t } = useTranslate();
  const {setPreview} = useAnalysisView();
  const tr = locale.startsWith("tr");
  const version = useRef(0);
  const baseKey = JSON.stringify(base);
  useEffect(() => { version.current += 1; setResult(null); setError(""); setBusy(false); setPreview(p => p?.kind === "recovery" ? {...p,stale:true} : p); return () => { version.current += 1; }; }, [baseKey]);
  const [selectedDecision, setSelectedDecision] = useState(0);
  const [preset, setPreset] = useState("boulder");
  const [seed, setSeed] = useState("2026");
  const [latency, setLatency] = useState("0");
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    version.current += 1;
    setResult(null);
    setError("");
    setBusy(false);
    setPreview(p => p?.kind === "recovery" ? {...p,stale:true} : p);
  }, [preset, seed, latency]);

  const run = async () => {
    const requestVersion = ++version.current;
    setBusy(true);
    setResult(null);
    setError("");
    try {
      const { allow_plane_change: _ignored, ...plan } = base;
      const response = await call("/descents/self-healing", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...plan,
            seed: Number(seed),
            solver_latency_s: Number(latency),
            events: PRESETS.find((p) => p.id === preset)?.events ?? [],
          }),
        });
      if (requestVersion !== version.current) return;
      setResult(response);
      setPreview(previewFor("recovery",response));
      setSelectedDecision(0);
    } catch (e) {
      if (requestVersion !== version.current) return;
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      if (requestVersion === version.current) setBusy(false);
    }
  };

  return (
    <div className="risk-panel heal-panel" aria-label="Self-healing landing">
      <h3>{tr ? "Araziye bağlı iniş kurtarma" : "Terrain-aware landing recovery"}</h3>
      <p className="field-help">{tr ? "Gerçek DEM üzerinde klasik yeniden hedefleme. Arızalar ve engeller senaryodur; bu çalışma hibrit görev planını değiştirmez. Hesaplama gecikmesi sırasında benzetim uçuşu devam eder." : "Classical retargeting on the real DEM. Faults and obstacles are simulated; this experiment does not replace the hybrid mission plan. Simulated flight advances during solver latency."}</p>
      <form
        className="risk-controls"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <fieldset disabled={busy}>
          <label>
            {tr ? "Arıza senaryosu" : "Fault scenario"}
            <select aria-label="Self-healing scenario" value={preset} onChange={(e) => setPreset(e.target.value)}>
              {PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {tr ? ({boulder:"560 s: hedefte kaya engeli", engine:"520 s: motor gücü −%25", sensor:"450 s: yön bulma sensörü kaybı", double:"550 s: motor −%50 ve 1,5 t yakıt kaybı"} as Record<string,string>)[p.id] : p.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            {tr ? "Rastgelelik tohumu" : "Seed"}
            <input aria-label="Self-healing seed" type="number" min="0" step="1" value={seed} onChange={(e) => setSeed(e.target.value)} />
          </label>
          <label>
            {tr ? "Çözücü gecikmesi · s" : "Solver latency · s"}
            <input type="number" min="0" max="30" step="any" value={latency} onChange={(e) => setLatency(e.target.value)} />
          </label>
          <button type="submit" className="text-button">
            {busy ? (tr ? "Uçuş ve yeniden planlama…" : "Flying and replanning…") : (tr ? "Kurtarmalı uçuşu karşılaştır ↗" : "Compare recovery flight ↗")}
          </button>
        </fieldset>
        <p className="field-help">
          {tr ? "Aynı arızalar özgün plan ve yeniden planlama ile ayrı ayrı uygulanır. Yeniden hedefleme seçenekleri benzetimin gerçek durumundan ayrıca doğrulanır. Bu, gerçek uçuş güvenliği onayı değildir." : "The same faults are replayed with the original plan and with replanning. Retargeting candidates are independently checked from simulation truth. This is not flight safety certification."}
        </p>
      </form>
      {error && (
        <div className="alert" role="alert">
          <ServerExplanation text={error} />
        </div>
      )}
      {result && <ResultSceneLink stale={false} show={() => setPreview(previewFor("recovery",result))} />}
      {result && (
        <div className="risk-result">
          <div className="heal-compare">
            <OutcomeCard title={tr ? "ÖZGÜN PLAN" : "ORIGINAL PLAN"} outcome={result.original} />
            <OutcomeCard title={tr ? "YENİDEN PLANLAMA" : "WITH RECOVERY"} outcome={result.replanned} />
          </div>
          <TrajectoryChart result={result} />
          <div className="backup-grid">
            <DecisionMap result={result} selected={selectedDecision} />
            <div>
              <h4>{tr ? "Karar günlüğü · harita için bir karar seçin" : "Decision log · select a decision for the map"}</h4>
              <ol className="heal-log">
                {result.decisions.map((d, i) => (
                  <li key={i} className={d.action}>
                    <button type="button" aria-pressed={selectedDecision === i} onClick={() => { setSelectedDecision(i); setPreview({...previewFor("recovery",result),selected:d.chosen??undefined}); }}><b>{d.t_s.toFixed(1)} s</b> {tr ? ({continue:"Devam · plan doğrulandı", retarget:"Yedek hedefe geç", abort_required:"İptal gerekli · modellenmedi", budget_exhausted:"Hesaplama bütçesi tükendi", no_time_to_replan:"Yeniden planlama için geç", stale_result_rejected:"Eski sonuç reddedildi"} as Record<string,string>)[d.action] ?? d.action : ACTION_LABEL[d.action] ?? d.action}</button>
                    {d.chosen ? ` → ${d.chosen}` : ""}
                    <small>
                      {tr ? "Tetikleyen olay" : "Trigger"}: {d.trigger.map((event) => t(`twin.events.${event}`) === `twin.events.${event}` ? (tr ? "Plan koşulları değişti" : event.replaceAll("_", " ")) : t(`twin.events.${event}`)).join(" + ")} · {d.evaluations}/{d.budget} {tr ? "seçenek değerlendirildi" : "options evaluated"} · {d.solver_wall_ms.toFixed(0)} ms · {tr ? "güvenlik payı" : "safety margin"} {d.margin_m.toFixed(0)} m
                      {d.applied_at_s !== null && d.applied_at_s > d.t_s ? ` · ${tr ? "uygulama zamanı" : "applied at"} ${d.applied_at_s.toFixed(1)} s` : ""}
                    </small>
                    <small><ServerExplanation text={d.explanation} /></small>
                    {d.validation.map((v) => (
                      <small key={v.id}>
                        {tr ? "Benzetim durumundan doğrulama" : "Validation from true state"}: {v.id} {v.passed ? (tr ? "✓ geçti" : "✓ passed") : (tr ? "✕ başarısız" : "✕ failed")}
                      </small>
                    ))}
                    <details>
                      <summary>{tr ? "Değerlendirilen tüm hedefler" : "All evaluated targets"} · {d.considered.length}</summary>
                      {d.considered.map(c => <div key={c.id} className="terrain-candidate-detail">
                        <strong>{c.id} · {c.reachable ? (tr ? "Uygun" : "Feasible") : (tr ? "Elendi" : "Rejected")}</strong>
                        <p>Δv {c.total_delta_v_mps.toFixed(2)} m/s · {tr ? "rezerv marjı" : "reserve margin"} {c.reserve_margin_kg.toFixed(2)} kg · {tr ? "arazi skoru" : "terrain score"} {c.safety_score?.toFixed(3) ?? "—"}</p>
                        {c.reasons.map((r, index) => <p key={index}><ServerExplanation text={r.detail} reason={r.reason} /></p>)}
                      </div>)}
                    </details>
                    {d.action === "abort_required" &&
                      d.considered.slice(0, 3).map((c) => (
                        <small key={c.id}>
                          {c.id}: <ServerExplanation text={c.reasons[0]?.detail} reason={c.reasons[0]?.reason} />
                        </small>
                      ))}
                  </li>
                ))}
                {result.decisions.length === 0 && <li>{tr ? "Hiçbir olay planı geçersiz kılmadı." : "No event invalidated the plan."}</li>}
              </ol>
              <h4>{tr ? "Uçuş boyunca erişilebilir yedekler" : "Reachable backups over time"}</h4>
              <ul className="heal-backups">
                {result.backups_over_time.map((b, i) => (
                  <li key={i}>
                    <span>
                      {b.t_s.toFixed(0)} s · {tr ? (b.label === "initial" ? "Başlangıç" : "Yedek hedef kontrolü") : b.label.replaceAll("_", " ")}
                    </span>
                    <span className="risk-meter" aria-label={`${b.reachable} of ${b.evaluated} reachable`}>
                      <i style={{ width: `${b.evaluated ? (100 * b.reachable) / b.evaluated : 0}%` }} />
                    </span>
                    <b>
                      {b.reachable}/{b.evaluated}
                    </b>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <details className="communication-limits">
            <summary>
              {tr ? "Model ve varsayımlar" : "Recovery model & assumptions"} <span>+</span>
            </summary>
            <ul>
              {result.assumptions.map((a) => (
                <li key={a}><ServerExplanation text={a} /></li>
              ))}
              <li>{tr ? "Yeniden planlama son zamanı (motorlu iniş başlangıcından):" : "Replanning deadline from powered descent start:"} {result.decision_deadline_s.toFixed(0)} s</li>
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}
