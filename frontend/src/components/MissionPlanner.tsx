import { useEffect, useMemo, useState } from "react";
import { errorText, useTranslate } from "../i18n";
import "./mission-planner.css";

// R11: renders the backend's multi-mission campaign plan. Candidate generation,
// scheduling, validation and explanations all come from the API; this file only
// maps UTC times to pixels.
type Window = { start_utc: string; end_utc: string };
type Lane = {
  mission_id: string;
  mission_type: string;
  priority: number;
  status: "nominal" | "deferred" | "cancelled" | "unplanned";
  option_id: string | null;
  site_id: string | null;
  landing_utc: string | null;
  surface_window: Window | null;
  comm_window: Window | null;
  landing_window: Window;
  deadline_utc: string;
  deferred: boolean;
  required_bits: number;
  delivered_bits: number;
  relayed_bits: number;
  cancel_allowed: boolean;
  defer_allowed: boolean;
  requires_exclusive_link: boolean;
};
type StationLane = {
  station_id: string;
  capacity_links: number | null;
  availability: Window[];
  slots: (Window & { slot_index: number; used: number; capacity: number | null; mission_ids: string[] })[];
};
type Allocation = Window & {
  mission_id: string;
  station_id: string;
  slot_index: number;
  via_relay_mission_id: string | null;
  bits: number;
};
type Conflict = {
  code: string;
  mission_ids: string[];
  station_id: string | null;
  start_utc: string | null;
  end_utc: string | null;
  detail: string;
};
type Dependency = {
  predecessor_mission_id: string;
  successor_mission_id: string;
  min_lag_s: number;
  satisfied: boolean | null;
};
type Timeline = {
  window: Window;
  slot_s: number;
  missions: Lane[];
  stations: StationLane[];
  allocations: Allocation[];
  dependencies: Dependency[];
  conflicts: Conflict[];
  independent: { mission_id: string; option_id: string | null; site_id: string | null; landing_utc: string | null; surface_window: Window | null }[];
};
type Alternative = {
  option_id: string | null;
  status: "selected" | "not_selected" | "cancel_selected" | "cancel_not_selected";
  reason: "best_joint_score" | "forcing_infeasible" | "lower_joint_score" | "not_evaluated";
  score_delta: number | null;
  detail: string;
};
type Explanation = {
  mission_id: string;
  status: Lane["status"];
  detail: string;
  rejection_counts: Record<string, number>;
  alternatives: Alternative[];
};
type Result = {
  joint: {
    status: "optimal" | "infeasible" | "limit_reached";
    validation: { score: number | null } | null;
    variables: number;
    constraints: number;
    elapsed_s: number;
  };
  independent_validation: { status: string; score: number | null };
  candidate_set: { candidates: unknown[]; rejections: unknown[] };
  explanations: Explanation[];
  conflict_sets: { mission_ids: string[]; detail: string }[];
  timeline: Timeline;
  limitations: string[];
};
type Example = { name: string; campaign_id: string; description: string };

const COLORS = ["#edbb66", "#9ac9e5", "#b5d99c", "#e59ac0", "#c3a6f0", "#f0a37a"];
const LABEL_W = 170;
const WIDTH = 900;
const LANE_H = 34;

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

const ms = (v: string) => Date.parse(v);
const utc = (v: string) => `${v.replace("T", " ").slice(0, 16)} UTC`;
const gbit = (bits: number) => `${(bits / 1e9).toFixed(2)} Gbit`;

export default function MissionPlanner() {
  const { t } = useTranslate();
  const [examples, setExamples] = useState<Example[]>([]);
  const [name, setName] = useState("shared-dsn-conflict");
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showIndependent, setShowIndependent] = useState(true);

  useEffect(() => {
    call("/campaigns/examples").then(setExamples, (e) => setError(errorText(e, t, "campaign.error")));
  }, [t]);

  async function plan(selected: string) {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const problem = await call(`/campaigns/examples/${encodeURIComponent(selected)}`);
      const planned = await call("/campaigns/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(problem),
      });
      setResult(planned);
    } catch (e) {
      setError(errorText(e, t, "campaign.error"));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    plan(name);
    // Re-plan only when the chosen example changes.
  }, [name]);

  const description = examples.find((e) => e.name === name)?.description;
  return (
    <section className="campaign-panel" aria-labelledby="campaign-title">
      <h3 id="campaign-title">{t("campaign.title")}</h3>
      <p className="campaign-intro">{t("campaign.intro")}</p>
      <div className="campaign-controls">
        <label>
          {t("campaign.example")}
          <select value={name} onChange={(e) => setName(e.target.value)} disabled={busy}>
            {examples.map((e) => (
              <option key={e.name} value={e.name}>
                {t(`campaign.examples.${e.name}`) || e.name}
              </option>
            ))}
          </select>
        </label>
        <label className="campaign-toggle">
          <input
            type="checkbox"
            checked={showIndependent}
            onChange={(e) => setShowIndependent(e.target.checked)}
          />
          {t("campaign.showIndependent")}
        </label>
        <span className="campaign-badge">{t("campaign.illustrative")}</span>
      </div>
      {description && <p className="campaign-description">{description}</p>}
      {busy && <p role="status">{t("campaign.planning")}</p>}
      {error && <p role="alert" className="campaign-error">{error}</p>}
      {result && <CampaignView result={result} showIndependent={showIndependent} />}
    </section>
  );
}

function CampaignView({ result, showIndependent }: { result: Result; showIndependent: boolean }) {
  const { t } = useTranslate();
  const timeline = result.timeline;
  const colors = useMemo(() => {
    const map: Record<string, string> = {};
    timeline.missions.forEach((m, i) => (map[m.mission_id] = COLORS[i % COLORS.length]));
    return map;
  }, [timeline]);
  const joint = result.joint;
  return (
    <>
      <dl className="campaign-summary">
        <div>
          <dt>{t("campaign.jointPlan")}</dt>
          <dd className={`status-${joint.status}`}>
            {t(`campaign.solveStatus.${joint.status}`)}
            {joint.validation?.score != null && ` · ${t("campaign.score")} ${joint.validation.score.toFixed(3)}`}
          </dd>
        </div>
        <div>
          <dt>{t("campaign.independentPlan")}</dt>
          <dd className={`status-${result.independent_validation.status}`}>
            {t(`campaign.validation.${result.independent_validation.status}`)}
            {result.independent_validation.status === "infeasible" &&
              ` · ${t("campaign.conflictCount", { count: timeline.conflicts.length })}`}
          </dd>
        </div>
        <div>
          <dt>{t("campaign.candidates")}</dt>
          <dd>
            {t("campaign.candidateCounts", {
              accepted: result.candidate_set.candidates.length,
              rejected: result.candidate_set.rejections.length,
            })}
          </dd>
        </div>
        <div>
          <dt>{t("campaign.model")}</dt>
          <dd>
            {t("campaign.modelSize", {
              variables: joint.variables,
              constraints: joint.constraints,
              seconds: joint.elapsed_s.toFixed(2),
            })}
          </dd>
        </div>
      </dl>
      {result.conflict_sets.map((set) => (
        <div key={set.mission_ids.join("+")} className="campaign-infeasible" role="note">
          <strong>{t("campaign.noJointPlan", { missions: set.mission_ids.join(", ") })}</strong>
          <p>{set.detail}</p>
        </div>
      ))}
      <TimelineChart timeline={timeline} colors={colors} showIndependent={showIndependent} />
      <h4>{t("campaign.decisions")}</h4>
      <div className="campaign-explanations">
        {result.explanations.map((e) => (
          <MissionCard key={e.mission_id} explanation={e} lane={timeline.missions.find((m) => m.mission_id === e.mission_id)!} color={colors[e.mission_id]} />
        ))}
      </div>
      {timeline.conflicts.length > 0 && (
        <>
          <h4>{t("campaign.independentConflicts")}</h4>
          <ul className="campaign-conflicts">
            {timeline.conflicts.map((c, i) => (
              <li key={i}>
                <span className="conflict-code">{t(`campaign.issue.${c.code}`)}</span>{" "}
                {c.start_utc && <span className="conflict-time">{utc(c.start_utc)} · </span>}
                {c.detail}
              </li>
            ))}
          </ul>
        </>
      )}
      <details className="campaign-limits">
        <summary>{t("campaign.limitations")}</summary>
        <ul>
          {result.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </details>
    </>
  );
}

function TimelineChart({
  timeline,
  colors,
  showIndependent,
}: {
  timeline: Timeline;
  colors: Record<string, string>;
  showIndependent: boolean;
}) {
  const { t } = useTranslate();
  const start = ms(timeline.window.start_utc);
  const end = ms(timeline.window.end_utc);
  const x = (v: string) => LABEL_W + ((ms(v) - start) / (end - start)) * (WIDTH - LABEL_W - 10);
  const span = (w: Window) => ({ x: x(w.start_utc), width: Math.max(x(w.end_utc) - x(w.start_utc), 1) });
  const missionRows = timeline.missions.length;
  const stationTop = 30 + missionRows * LANE_H + 26;
  const height = stationTop + timeline.stations.length * LANE_H + 24;
  const days: number[] = [];
  for (let d = start; d <= end; d += 86400000) days.push(d);
  const laneY = (id: string) => 30 + timeline.missions.findIndex((m) => m.mission_id === id) * LANE_H;
  const landingX = (id: string) => {
    const lane = timeline.missions.find((m) => m.mission_id === id);
    return lane?.landing_utc ? x(lane.landing_utc) : null;
  };
  return (
    <figure className="campaign-timeline">
      <svg viewBox={`0 0 ${WIDTH} ${height}`} role="img" aria-label={t("campaign.timelineLabel")}>
        <defs>
          <pattern id="campaign-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="#e4675f" strokeWidth="2" />
          </pattern>
          <marker id="campaign-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="#c9d1d6" />
          </marker>
        </defs>
        {days.map((d) => {
          const label = new Date(d).toISOString();
          return (
            <g key={d}>
              <line x1={x(label)} x2={x(label)} y1={18} y2={height - 18} className="tick" />
              <text x={x(label) + 3} y={14} className="axis">
                {label.slice(5, 10)} 00:00
              </text>
            </g>
          );
        })}
        {timeline.missions.map((m) => {
          const y = laneY(m.mission_id);
          const independent = timeline.independent.find((i) => i.mission_id === m.mission_id);
          return (
            <g key={m.mission_id}>
              <text x={4} y={y + 14} className="lane-label">
                {m.mission_id}
              </text>
              <text x={4} y={y + 27} className="lane-sub">
                {t(`campaign.missionType.${m.mission_type}`)} · P{m.priority} · {t(`campaign.status.${m.status}`)}
              </text>
              <rect {...span(m.landing_window)} y={y + 2} height={LANE_H - 6} className="landing-band">
                <title>{t("campaign.landingWindow")}</title>
              </rect>
              <line x1={x(m.deadline_utc)} x2={x(m.deadline_utc)} y1={y} y2={y + LANE_H - 4} className="deadline">
                <title>{t("campaign.deadline", { time: utc(m.deadline_utc) })}</title>
              </line>
              {showIndependent && independent?.surface_window && (
                <rect {...span(independent.surface_window)} y={y + 4} height={LANE_H - 10} className="independent-bar">
                  <title>{t("campaign.independentChoice", { option: independent.option_id ?? "" })}</title>
                </rect>
              )}
              {m.comm_window && (
                <line x1={x(m.comm_window.start_utc)} x2={x(m.comm_window.end_utc)} y1={y + LANE_H - 7} y2={y + LANE_H - 7} stroke={colors[m.mission_id]} className="comm-line">
                  <title>{t("campaign.commWindow")}</title>
                </line>
              )}
              {m.surface_window && (
                <rect {...span(m.surface_window)} y={y + 7} height={LANE_H - 18} fill={colors[m.mission_id]} className={`surface-bar ${m.status}`}>
                  <title>
                    {t("campaign.surfaceOps", { site: m.site_id ?? "", time: utc(m.landing_utc ?? "") })}
                  </title>
                </rect>
              )}
              {m.status === "cancelled" && (
                <text x={LABEL_W + 6} y={y + 20} className="cancelled-label">
                  {t("campaign.status.cancelled")}
                </text>
              )}
            </g>
          );
        })}
        {timeline.dependencies.map((d) => {
          const a = landingX(d.predecessor_mission_id);
          const b = landingX(d.successor_mission_id);
          if (a == null || b == null) return null;
          const ya = laneY(d.predecessor_mission_id) + LANE_H / 2;
          const yb = laneY(d.successor_mission_id) + LANE_H / 2;
          return (
            <path
              key={`${d.predecessor_mission_id}-${d.successor_mission_id}`}
              d={`M${a},${ya} C${a + 30},${ya} ${b - 30},${yb} ${b},${yb}`}
              className={`dependency ${d.satisfied ? "ok" : "broken"}`}
              markerEnd="url(#campaign-arrow)"
            >
              <title>
                {t("campaign.dependency", {
                  a: d.predecessor_mission_id,
                  b: d.successor_mission_id,
                  hours: (d.min_lag_s / 3600).toFixed(1),
                })}
              </title>
            </path>
          );
        })}
        <text x={4} y={stationTop - 8} className="section-label">
          {t("campaign.stations")}
        </text>
        {timeline.stations.map((s, i) => {
          const y = stationTop + i * LANE_H;
          const capacity = Math.max(s.capacity_links ?? 1, 1);
          const cell = (LANE_H - 8) / capacity;
          return (
            <g key={s.station_id}>
              <text x={4} y={y + 14} className="lane-label">
                {s.station_id}
              </text>
              <text x={4} y={y + 27} className="lane-sub">
                {s.capacity_links == null
                  ? t("campaign.capacityUnknown")
                  : t("campaign.capacity", { count: s.capacity_links })}
              </text>
              {s.availability.map((w) => (
                <rect key={w.start_utc} {...span(w)} y={y + 2} height={LANE_H - 6} className="availability">
                  <title>{t("campaign.calendar")}</title>
                </rect>
              ))}
              {timeline.allocations
                .filter((a) => a.station_id === s.station_id)
                .map((a) => {
                  const users = s.slots.find((u) => u.slot_index === a.slot_index)?.mission_ids ?? [];
                  const row = users.indexOf(a.mission_id);
                  return (
                    <rect
                      key={`${a.mission_id}-${a.slot_index}`}
                      {...span(a)}
                      y={y + 4 + Math.max(row, 0) * cell}
                      height={cell - 1}
                      fill={colors[a.mission_id]}
                      className={a.via_relay_mission_id ? "slot relay" : "slot"}
                    >
                      <title>
                        {a.via_relay_mission_id
                          ? t("campaign.relayedSlot", { mission: a.mission_id, relay: a.via_relay_mission_id, time: utc(a.start_utc), bits: gbit(a.bits) })
                          : t("campaign.directSlot", { mission: a.mission_id, time: utc(a.start_utc), bits: gbit(a.bits) })}
                      </title>
                    </rect>
                  );
                })}
              {showIndependent &&
                timeline.conflicts
                  .filter((c) => c.station_id === s.station_id && c.start_utc && c.end_utc)
                  .map((c, j) => (
                    <rect
                      key={`${c.code}-${j}`}
                      {...span({ start_utc: c.start_utc!, end_utc: c.end_utc! })}
                      y={y + LANE_H - 7}
                      height={4}
                      fill="url(#campaign-hatch)"
                    >
                      <title>{c.detail}</title>
                    </rect>
                  ))}
            </g>
          );
        })}
      </svg>
      <figcaption>
        <ul className="campaign-legend">
          {timeline.missions.map((m) => (
            <li key={m.mission_id}>
              <i style={{ background: colors[m.mission_id] }} /> {m.mission_id}
            </li>
          ))}
          <li>
            <i className="legend-landing" /> {t("campaign.landingWindow")}
          </li>
          <li>
            <i className="legend-deadline" /> {t("campaign.deadlineLegend")}
          </li>
          <li>
            <i className="legend-independent" /> {t("campaign.independentLegend")}
          </li>
          <li>
            <i className="legend-relay" /> {t("campaign.relayLegend")}
          </li>
          <li>
            <i className="legend-conflict" /> {t("campaign.conflictLegend")}
          </li>
        </ul>
        {t("campaign.caption")}
      </figcaption>
    </figure>
  );
}

function MissionCard({ explanation, lane, color }: { explanation: Explanation; lane: Lane; color: string }) {
  const { t } = useTranslate();
  const rejections = Object.entries(explanation.rejection_counts);
  return (
    <article className="campaign-card" style={{ borderLeftColor: color }}>
      <header>
        <strong>{explanation.mission_id}</strong>{" "}
        <span className={`pill ${explanation.status}`}>{t(`campaign.status.${explanation.status}`)}</span>
      </header>
      <p>{explanation.detail}</p>
      <p className="campaign-meta">
        {t("campaign.downlink", { delivered: gbit(lane.delivered_bits), required: gbit(lane.required_bits) })}
        {lane.relayed_bits > 0 && ` · ${t("campaign.relayed", { bits: gbit(lane.relayed_bits) })}`}
        {" · "}
        {lane.cancel_allowed ? t("campaign.cancelAllowed") : t("campaign.mandatory")}
        {lane.defer_allowed && ` · ${t("campaign.deferAllowed")}`}
        {lane.requires_exclusive_link && ` · ${t("campaign.exclusive")}`}
      </p>
      {explanation.alternatives.length > 0 && (
        <table className="campaign-alternatives">
          <thead>
            <tr>
              <th>{t("campaign.option")}</th>
              <th>{t("campaign.outcome")}</th>
              <th>{t("campaign.scoreChange")}</th>
            </tr>
          </thead>
          <tbody>
            {explanation.alternatives.map((a) => (
              <tr key={`${a.status}-${a.option_id}`} className={a.status}>
                <td title={a.detail}>{a.option_id ?? t("campaign.cancelOption")}</td>
                <td>{t(`campaign.reason.${a.reason}`)}</td>
                <td>{a.score_delta == null ? "—" : a.score_delta.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rejections.length > 0 && (
        <p className="campaign-meta">
          {t("campaign.rejected")}{" "}
          {rejections.map(([code, count]) => `${t(`campaign.rejection.${code}`)} ×${count}`).join(", ")}
        </p>
      )}
    </article>
  );
}
