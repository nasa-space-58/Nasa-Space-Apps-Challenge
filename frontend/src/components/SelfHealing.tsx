import { useEffect, useMemo, useState } from "react";
import { errorText, useTranslate } from "../i18n";
import "./self-healing.css";

// R12: renders the backend's recovery replay view (H5 fault replay, H6 guarded
// hybrid recovery). Physics, screening, explanations and timing all come from
// the API; this file only maps values to pixels.
type Point = {
  elapsed_s: number;
  downrange_m: number;
  altitude_m: number;
  target_id: string;
};
type Dispersion = {
  count: number;
  min_downrange_m: number;
  max_downrange_m: number;
  mean_downrange_m: number;
  std_downrange_m: number;
  min_propellant_kg: number;
  max_propellant_kg: number;
  samples: { sample: number; accepted: boolean; downrange_m: number }[];
};
type Candidate = {
  target_id: string;
  accepted: boolean;
  reasons: string[];
  predicted_propellant_kg: number | null;
  predicted_touchdown_downrange_m: number | null;
  sample_count: number;
  pass_count: number;
  dispersion: Dispersion | null;
};
type HybridInfo = {
  status: string;
  source: string;
  reason: string;
  classical_status: string | null;
  selected_option_ids: string[];
  preparation_s: number;
  revalidation_s: number;
  elapsed_since_submission_s: number;
  quantum_elapsed_s: number | null;
  logical_qubits: number | null;
  deadline_s: number;
};
type Decision = {
  index: number;
  elapsed_s: number;
  revision: number;
  action: "continue" | "retarget" | "no_feasible_continuation";
  previous_target_id: string | null;
  selected_target_id: string | null;
  available_thrust_n: number;
  active_hazards: [number, number][];
  sample_count: number;
  candidates: Candidate[];
  explanation: string;
  planning_wall_s: number;
  hybrid: HybridInfo | null;
};
type Branch = {
  branch: "original" | "classical" | "hybrid";
  status: string;
  selected_target_id: string | null;
  final_reasons: string[];
  final_elapsed_s: number;
  delay_vs_nominal_s: number;
  touchdown_downrange_m: number | null;
  propellant_burned_kg: number;
  propellant_lost_kg: number;
  propellant_remaining_kg: number;
  reserve_margin_kg: number;
  unapplied_event_ids: string[];
  trajectory: Point[];
  decisions: Decision[];
  replay_wall_s: number;
};
type EventRow = {
  event_id: string;
  kind: string;
  elapsed_s: number;
  sequence: number;
  summary: string;
  payload: { kind: string; start_downrange_m?: number; end_downrange_m?: number };
};
type View = {
  scenario_id: string;
  scenario_sha256: string;
  reserve_kg: number;
  targets: { target_id: string; downrange_m: number; deadline_elapsed_s: number; nominal: boolean }[];
  events: EventRow[];
  uncertainty: { position_std_m: [number, number]; velocity_std_mps: [number, number]; samples: number };
  branches: Branch[];
  hybrid: null | {
    execution_mode: string;
    hardware_jobs: number;
    simulated_clock_paused_during_planning: boolean;
    final_selected_option_ids: string[] | null;
  };
  limitations: string[];
};
type Example = { name: string; scenario_id: string; hybrid: boolean };

const COLORS: Record<Branch["branch"], string> = {
  original: "#e59a7a",
  classical: "#9ac9e5",
  hybrid: "#c3a6f0",
};
const W = 900;
const H = 300;
const PAD = { left: 56, right: 16, top: 16, bottom: 34 };

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

const fmt = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "—" : v.toFixed(digits);
const ms = (s: number) => `${(s * 1000).toFixed(1)} ms`;

export default function SelfHealing() {
  const { t } = useTranslate();
  const [examples, setExamples] = useState<Example[]>([]);
  const [name, setName] = useState("combined_recovery");
  const [view, setView] = useState<View | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    call("/recovery/examples").then(setExamples, (e) => setError(errorText(e, t, "recovery.error")));
  }, [t]);

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setError("");
    setView(null);
    (async () => {
      try {
        const body = await call(`/recovery/examples/${encodeURIComponent(name)}`);
        const result = await call("/recovery/replays", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!cancelled) setView(result);
      } catch (e) {
        if (!cancelled) setError(errorText(e, t, "recovery.error"));
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // Replay only when the chosen example changes.
  }, [name]);

  return (
    <section className="recovery-panel" aria-labelledby="recovery-title">
      <h3 id="recovery-title">{t("recovery.title")}</h3>
      <p className="recovery-intro">{t("recovery.intro")}</p>
      <div className="recovery-controls">
        <label>
          {t("recovery.example")}
          <select value={name} onChange={(e) => setName(e.target.value)} disabled={busy}>
            {examples.map((e) => (
              <option key={e.name} value={e.name}>
                {t(`recovery.examples.${e.name}`)}
              </option>
            ))}
          </select>
        </label>
        <span className="recovery-badge">{t("recovery.synthetic")}</span>
      </div>
      {busy && <p role="status">{t("recovery.running")}</p>}
      {error && (
        <p role="alert" className="recovery-error">
          {error}
        </p>
      )}
      {view && <ReplayView view={view} />}
    </section>
  );
}

function ReplayView({ view }: { view: View }) {
  const { t } = useTranslate();
  const planning = view.branches.filter((b) => b.decisions.length > 0);
  const [branchName, setBranchName] = useState<Branch["branch"]>(
    planning[planning.length - 1]?.branch ?? "classical",
  );
  const branch = view.branches.find((b) => b.branch === branchName) ?? view.branches[0];
  const [decisionIndex, setDecisionIndex] = useState(branch.decisions.length - 1);
  const decision = branch.decisions[Math.min(decisionIndex, branch.decisions.length - 1)];

  function chooseBranch(next: Branch["branch"]) {
    setBranchName(next);
    const target = view.branches.find((b) => b.branch === next);
    setDecisionIndex((target?.decisions.length ?? 1) - 1);
  }

  return (
    <>
      <h4>{t("recovery.outcomes")}</h4>
      <div className="recovery-cards">
        {view.branches.map((b) => (
          <OutcomeCard key={b.branch} branch={b} reserve={view.reserve_kg} />
        ))}
      </div>

      <h4>{t("recovery.trajectories")}</h4>
      <TrajectoryChart view={view} decision={decision} />

      <h4>{t("recovery.timeline")}</h4>
      <Timeline
        view={view}
        branch={branch}
        selected={decision?.index}
        onSelect={setDecisionIndex}
      />

      <h4>{t("recovery.events")}</h4>
      <table className="recovery-table">
        <thead>
          <tr>
            <th>{t("recovery.col.time")}</th>
            <th>{t("recovery.col.event")}</th>
            <th>{t("recovery.col.kind")}</th>
            <th>{t("recovery.col.effect")}</th>
          </tr>
        </thead>
        <tbody>
          {view.events.map((e) => (
            <tr key={e.event_id}>
              <td>
                T+{fmt(e.elapsed_s)} s <small>#{e.sequence}</small>
              </td>
              <td>{e.event_id}</td>
              <td>{t(`recovery.kinds.${e.kind}`)}</td>
              <td>{e.summary}</td>
            </tr>
          ))}
          {view.events.length === 0 && (
            <tr>
              <td colSpan={4}>{t("recovery.noEvents")}</td>
            </tr>
          )}
        </tbody>
      </table>

      <h4>{t("recovery.decisions")}</h4>
      <div className="recovery-controls">
        {planning.map((b) => (
          <label key={b.branch} className="recovery-toggle">
            <input
              type="radio"
              name="recovery-branch"
              checked={b.branch === branch.branch}
              onChange={() => chooseBranch(b.branch)}
            />
            {t(`recovery.branches.${b.branch}`)}
          </label>
        ))}
      </div>
      {decision ? <DecisionDetail decision={decision} /> : <p>{t("recovery.noDecisions")}</p>}

      {view.hybrid && (
        <p className="recovery-note">
          {t("recovery.hybridNote", {
            mode: view.hybrid.execution_mode,
            jobs: view.hybrid.hardware_jobs,
            options: (view.hybrid.final_selected_option_ids ?? []).join(", ") || "—",
          })}
        </p>
      )}

      <h4>{t("recovery.limitations")}</h4>
      <ul className="recovery-limits">
        {view.limitations.map((text) => (
          <li key={text}>{text}</li>
        ))}
      </ul>
      <p className="recovery-hash">
        {view.scenario_id} · sha256 {view.scenario_sha256.slice(0, 16)}…
      </p>
    </>
  );
}

function OutcomeCard({ branch, reserve }: { branch: Branch; reserve: number }) {
  const { t } = useTranslate();
  const ok = branch.status === "landed";
  return (
    <div className={`recovery-card ${ok ? "ok" : "bad"}`} style={{ borderTopColor: COLORS[branch.branch] }}>
      <strong>{t(`recovery.branches.${branch.branch}`)}</strong>
      <span className="recovery-status">{t(`recovery.status.${branch.status}`)}</span>
      <dl>
        <dt>{t("recovery.card.target")}</dt>
        <dd>{branch.selected_target_id ?? "—"}</dd>
        <dt>{t("recovery.card.end")}</dt>
        <dd>
          T+{fmt(branch.final_elapsed_s)} s ({branch.delay_vs_nominal_s >= 0 ? "+" : ""}
          {fmt(branch.delay_vs_nominal_s)} s)
        </dd>
        <dt>{t("recovery.card.downrange")}</dt>
        <dd>{fmt(branch.touchdown_downrange_m)} m</dd>
        <dt>{t("recovery.card.fuel")}</dt>
        <dd>
          {fmt(branch.propellant_remaining_kg, 2)} kg ({t("recovery.card.reserve", { reserve: fmt(reserve, 0) })}{" "}
          {branch.reserve_margin_kg >= 0 ? "+" : ""}
          {fmt(branch.reserve_margin_kg, 2)})
        </dd>
        <dt>{t("recovery.card.burnLoss")}</dt>
        <dd>
          {fmt(branch.propellant_burned_kg, 2)} / {fmt(branch.propellant_lost_kg, 2)} kg
        </dd>
        <dt>{t("recovery.card.wall")}</dt>
        <dd>{ms(branch.replay_wall_s)}</dd>
      </dl>
      {branch.final_reasons.length > 0 && (
        <p className="recovery-reasons">{branch.final_reasons.join(", ")}</p>
      )}
      {branch.unapplied_event_ids.length > 0 && (
        <p className="recovery-reasons">
          {t("recovery.card.unapplied", { ids: branch.unapplied_event_ids.join(", ") })}
        </p>
      )}
    </div>
  );
}

function TrajectoryChart({ view, decision }: { view: View; decision: Decision | undefined }) {
  const { t } = useTranslate();
  const { x, y, xTicks, yTicks } = useMemo(() => {
    const points = view.branches.flatMap((b) => b.trajectory);
    const xs = [
      ...points.map((p) => p.downrange_m),
      ...view.targets.map((tg) => tg.downrange_m),
    ];
    let x0 = Math.min(...xs);
    let x1 = Math.max(...xs);
    const span = Math.max(x1 - x0, 10);
    x0 -= span * 0.08;
    x1 += span * 0.08;
    const y1 = Math.max(...points.map((p) => p.altitude_m), 1) * 1.08;
    const x = (v: number) => PAD.left + ((v - x0) / (x1 - x0)) * (W - PAD.left - PAD.right);
    const y = (v: number) => H - PAD.bottom - (v / y1) * (H - PAD.top - PAD.bottom);
    const ticks = (a: number, b: number, n: number) =>
      Array.from({ length: n + 1 }, (_, i) => a + ((b - a) * i) / n);
    return { x, y, xTicks: ticks(x0, x1, 6), yTicks: ticks(0, y1, 4) };
  }, [view]);
  const hazards = view.events
    .filter((e) => e.payload.kind === "landing_hazard")
    .map((e) => [e.payload.start_downrange_m ?? 0, e.payload.end_downrange_m ?? 0, e.event_id] as const);
  const ground = H - PAD.bottom;
  return (
    <figure className="recovery-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("recovery.trajectoryAria")}>
        {yTicks.map((v) => (
          <g key={`y${v}`}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} className="grid" />
            <text x={PAD.left - 6} y={y(v) + 4} textAnchor="end">
              {v.toFixed(0)}
            </text>
          </g>
        ))}
        {xTicks.map((v) => (
          <text key={`x${v}`} x={x(v)} y={ground + 16} textAnchor="middle">
            {v.toFixed(0)}
          </text>
        ))}
        <text x={W - PAD.right} y={H - 4} textAnchor="end" className="axis">
          {t("recovery.axisDownrange")}
        </text>
        <text x={6} y={PAD.top + 4} className="axis">
          {t("recovery.axisAltitude")}
        </text>
        {hazards.map(([a, b, id]) => (
          <rect key={id} x={x(a)} width={Math.max(2, x(b) - x(a))} y={PAD.top} height={ground - PAD.top} className="hazard">
            <title>{`${id}: ${a} … ${b} m`}</title>
          </rect>
        ))}
        <line x1={PAD.left} x2={W - PAD.right} y1={ground} y2={ground} className="ground" />
        {decision?.candidates.map(
          (c) =>
            c.dispersion && (
              <g key={`d${c.target_id}`} className="dispersion">
                <line
                  x1={x(c.dispersion.min_downrange_m)}
                  x2={x(c.dispersion.max_downrange_m)}
                  y1={ground - 8}
                  y2={ground - 8}
                />
                {c.dispersion.samples.map((s) => (
                  <circle
                    key={s.sample}
                    cx={x(s.downrange_m)}
                    cy={ground - 8}
                    r={2.5}
                    className={s.accepted ? "pass" : "fail"}
                  />
                ))}
                <title>
                  {t("recovery.dispersionTitle", {
                    target: c.target_id,
                    pass: c.pass_count,
                    count: c.sample_count,
                    std: fmt(c.dispersion.std_downrange_m, 2),
                  })}
                </title>
              </g>
            ),
        )}
        {view.targets.map((tg) => (
          <g key={tg.target_id} className={tg.nominal ? "target nominal" : "target"}>
            <path d={`M${x(tg.downrange_m)},${ground} l-6,10 h12 z`} />
            <text x={x(tg.downrange_m)} y={ground - 16} textAnchor="middle">
              {tg.target_id}
            </text>
          </g>
        ))}
        {view.branches.map((b) => (
          <polyline
            key={b.branch}
            fill="none"
            stroke={COLORS[b.branch]}
            strokeWidth={2}
            strokeDasharray={b.branch === "original" ? "6 4" : undefined}
            points={b.trajectory.map((p) => `${x(p.downrange_m)},${y(p.altitude_m)}`).join(" ")}
          />
        ))}
        {view.branches.map((b) =>
          view.events.map((e) => {
            const p = b.trajectory.find((q) => q.elapsed_s >= e.elapsed_s - 1e-9);
            return (
              p && (
                <circle key={`${b.branch}-${e.event_id}`} cx={x(p.downrange_m)} cy={y(p.altitude_m)} r={4} className="event-dot">
                  <title>{`${e.event_id} · T+${fmt(e.elapsed_s)} s`}</title>
                </circle>
              )
            );
          }),
        )}
      </svg>
      <figcaption>
        {view.branches.map((b) => (
          <span key={b.branch}>
            <i style={{ background: COLORS[b.branch] }} />
            {t(`recovery.branches.${b.branch}`)}
          </span>
        ))}
        <span>
          <i className="hazard-key" />
          {t("recovery.hazardKey")}
        </span>
        <span>{t("recovery.dispersionKey", { count: view.uncertainty.samples })}</span>
      </figcaption>
    </figure>
  );
}

function Timeline({
  view,
  branch,
  selected,
  onSelect,
}: {
  view: View;
  branch: Branch;
  selected: number | undefined;
  onSelect: (index: number) => void;
}) {
  const { t } = useTranslate();
  const end = Math.max(
    ...view.branches.map((b) => b.final_elapsed_s),
    ...view.targets.map((tg) => tg.deadline_elapsed_s),
    1,
  );
  const x = (s: number) => PAD.left + (s / end) * (W - PAD.left - PAD.right);
  const rows = view.branches.map((b, i) => ({ b, yy: 30 + i * 22 }));
  const height = 40 + rows.length * 22;
  return (
    <svg className="recovery-timeline" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={t("recovery.timelineAria")}>
      {view.targets.map((tg) => (
        <g key={tg.target_id}>
          <line x1={x(tg.deadline_elapsed_s)} x2={x(tg.deadline_elapsed_s)} y1={10} y2={height - 6} className="deadline" />
          <text x={x(tg.deadline_elapsed_s)} y={10} textAnchor="middle">
            {tg.target_id}
          </text>
        </g>
      ))}
      {view.events.map((e) => (
        <g key={e.event_id}>
          <line x1={x(e.elapsed_s)} x2={x(e.elapsed_s)} y1={16} y2={height - 6} className="event-line" />
          <title>{`${e.event_id}: ${e.summary}`}</title>
        </g>
      ))}
      {rows.map(({ b, yy }) => (
        <g key={b.branch}>
          <text x={PAD.left - 6} y={yy + 4} textAnchor="end">
            {t(`recovery.branches.${b.branch}`)}
          </text>
          <line x1={x(0)} x2={x(b.final_elapsed_s)} y1={yy} y2={yy} stroke={COLORS[b.branch]} strokeWidth={4} />
          {b.decisions.map((d) => (
            <rect
              key={d.index}
              x={x(d.elapsed_s) - 5}
              y={yy - 6}
              width={10}
              height={12}
              className={`decision ${d.action} ${b.branch === branch.branch && d.index === selected ? "selected" : ""}`}
              onClick={() => b.branch === branch.branch && onSelect(d.index)}
            >
              <title>{`${t(`recovery.actions.${d.action}`)} · T+${fmt(d.elapsed_s)} s`}</title>
            </rect>
          ))}
        </g>
      ))}
      <text x={W - PAD.right} y={height - 2} textAnchor="end" className="axis">
        T+{fmt(end)} s
      </text>
    </svg>
  );
}

function DecisionDetail({ decision }: { decision: Decision }) {
  const { t } = useTranslate();
  const h = decision.hybrid;
  return (
    <div className="recovery-decision">
      <p>
        <strong>
          T+{fmt(decision.elapsed_s)} s · {t(`recovery.actions.${decision.action}`)}
        </strong>{" "}
        <small>
          {t("recovery.revision", { revision: decision.revision })} ·{" "}
          {t("recovery.thrust", { value: decision.available_thrust_n.toFixed(0) })}
        </small>
      </p>
      <p className="recovery-explanation">{decision.explanation}</p>
      <dl className="recovery-timing">
        <dt>{t("recovery.timing.planning")}</dt>
        <dd>{ms(decision.planning_wall_s)}</dd>
        {h && (
          <>
            <dt>{t("recovery.timing.source")}</dt>
            <dd>
              {h.source} ({h.reason})
            </dd>
            <dt>{t("recovery.timing.preparation")}</dt>
            <dd>{ms(h.preparation_s)}</dd>
            <dt>{t("recovery.timing.quantum")}</dt>
            <dd>
              {h.quantum_elapsed_s === null ? "—" : ms(h.quantum_elapsed_s)}
              {h.logical_qubits !== null && ` · ${t("recovery.timing.qubits", { n: h.logical_qubits })}`}
            </dd>
            <dt>{t("recovery.timing.revalidation")}</dt>
            <dd>{ms(h.revalidation_s)}</dd>
            <dt>{t("recovery.timing.deadline")}</dt>
            <dd>
              {ms(h.elapsed_since_submission_s)} / {fmt(h.deadline_s)} s
            </dd>
            <dt>{t("recovery.timing.schedule")}</dt>
            <dd>{h.selected_option_ids.join(", ") || "—"}</dd>
          </>
        )}
      </dl>
      <table className="recovery-table">
        <thead>
          <tr>
            <th>{t("recovery.col.target")}</th>
            <th>{t("recovery.col.result")}</th>
            <th>{t("recovery.col.reasons")}</th>
            <th>{t("recovery.col.fuel")}</th>
            <th>{t("recovery.col.samples")}</th>
            <th>{t("recovery.col.dispersion")}</th>
          </tr>
        </thead>
        <tbody>
          {decision.candidates.map((c) => (
            <tr key={c.target_id} className={c.target_id === decision.selected_target_id ? "chosen" : ""}>
              <td>{c.target_id}</td>
              <td className={c.accepted ? "pass" : "fail"}>
                {c.accepted ? t("recovery.accepted") : t("recovery.rejected")}
              </td>
              <td>{c.reasons.join(", ") || "—"}</td>
              <td>{c.predicted_propellant_kg === null ? "—" : `${fmt(c.predicted_propellant_kg, 2)} kg`}</td>
              <td>
                {c.pass_count}/{c.sample_count}
              </td>
              <td>
                {c.dispersion
                  ? `${fmt(c.dispersion.min_downrange_m)} … ${fmt(c.dispersion.max_downrange_m)} m (σ ${fmt(c.dispersion.std_downrange_m, 2)})`
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {decision.active_hazards.length > 0 && (
        <p className="recovery-note">
          {t("recovery.activeHazards", {
            list: decision.active_hazards.map(([a, b]) => `${a} … ${b} m`).join("; "),
          })}
        </p>
      )}
    </div>
  );
}
