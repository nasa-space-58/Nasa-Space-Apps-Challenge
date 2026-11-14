import { useState } from "react";
import { useTranslate, type Translate } from "../i18n";

// R5 (S7): renders the backend route grid; fuel and safety come from the API.
export type Route = {
  id: string;
  orbit_altitude_km: number;
  approach_s: number;
  terminal_s: number;
  assessment_time_s: number;
  feasible: boolean;
  rejection_reasons: { reason: string; detail: string }[];
  propellant_used_kg?: number;
  propellant_remaining_kg?: number;
  reserve_required_kg?: number;
  reserve_margin_kg?: number;
  hover_margin_s?: number;
  delta_v_mps?: number;
};
export type RouteResult = {
  reserve: { delta_v_mps: number; source: string };
  propellant_at_doi_kg: number;
  routes: Route[];
  feasible_ids: string[];
  rejected_ids: string[];
  pareto_ids: string[];
  lowest_fuel_id: string | null;
  longest_assessment_id: string | null;
  reference_route_id: string | null;
  assumptions: string[];
};

// Reference categorical slots 1-3 (dark), validated on #10171e; scatter caps at three.
const ORBIT_COLORS = ["#3987e5", "#d95926", "#199e70"];
const NMI_KM = 1.852;
const nmi = (t: Translate, km: number) =>
  t("routes.nmi", { value: Math.round(km / NMI_KM) });

function Scatter({ result }: { result: RouteResult }) {
  const { t } = useTranslate();
  const [hover, setHover] = useState<Route | null>(null);
  const orbits = [...new Set(result.routes.map((r) => r.orbit_altitude_km))].sort((a, b) => a - b);
  const color = (r: Route) => ORBIT_COLORS[orbits.indexOf(r.orbit_altitude_km) % ORBIT_COLORS.length];
  const plotted = result.routes.filter((r) => r.propellant_used_kg !== undefined);
  if (!plotted.length) return null;
  const W = 720,
    H = 320,
    L = 60,
    R = 16,
    T = 30,
    B = 44;
  const fuels = plotted.map((r) => r.propellant_used_kg!);
  const times = plotted.map((r) => r.assessment_time_s);
  const pad = (lo: number, hi: number) => {
    const span = hi - lo || 1;
    return [lo - span * 0.08, hi + span * 0.08];
  };
  const [x0, x1] = pad(Math.min(...fuels), Math.max(...fuels));
  const [y0, y1] = pad(Math.min(...times), Math.max(...times));
  const x = (v: number) => L + ((v - x0) / (x1 - x0)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const ticks = (lo: number, hi: number, n = 5) => {
    const raw = (hi - lo) / n;
    const step = Math.pow(10, Math.floor(Math.log10(raw)));
    const nice = raw / step > 5 ? step * 10 : raw / step > 2 ? step * 5 : raw / step > 1 ? step * 2 : step;
    const out = [];
    for (let v = Math.ceil(lo / nice) * nice; v <= hi; v += nice) out.push(v);
    return out;
  };
  const front = result.pareto_ids
    .map((id) => plotted.find((r) => r.id === id))
    .filter((r): r is Route => !!r);
  return (
    <div className="descent-chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={t("routes.chartLabel")}
      >
        {ticks(y0, y1).map((v) => (
          <g key={`y${v}`}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="grid" />
            <text x={L - 8} y={y(v) + 4} className="tick" textAnchor="end">
              {v}
            </text>
          </g>
        ))}
        {ticks(x0, x1).map((v) => (
          <text key={`x${v}`} x={x(v)} y={H - B + 18} className="tick" textAnchor="middle">
            {v}
          </text>
        ))}
        <text x={L} y={H - 6} className="axis-title">
          {t("routes.xAxis")}
        </text>
        <text x={8} y={14} className="axis-title">
          {t("routes.yAxis")}
        </text>
        {front.length > 1 && (
          <polyline
            points={front.map((r) => `${x(r.propellant_used_kg!)},${y(r.assessment_time_s)}`).join(" ")}
            fill="none"
            className="pareto-line"
          />
        )}
        {plotted.map((r) =>
          r.feasible ? (
            <circle
              key={r.id}
              cx={x(r.propellant_used_kg!)}
              cy={y(r.assessment_time_s)}
              r={result.pareto_ids.includes(r.id) ? 6 : 4.5}
              fill={color(r)}
              stroke="#10171e"
              strokeWidth={2}
            />
          ) : (
            <g key={r.id} className="rejected-mark">
              <line x1={x(r.propellant_used_kg!) - 5} y1={y(r.assessment_time_s) - 5} x2={x(r.propellant_used_kg!) + 5} y2={y(r.assessment_time_s) + 5} />
              <line x1={x(r.propellant_used_kg!) - 5} y1={y(r.assessment_time_s) + 5} x2={x(r.propellant_used_kg!) + 5} y2={y(r.assessment_time_s) - 5} />
            </g>
          ),
        )}
        {[result.lowest_fuel_id, result.longest_assessment_id].map((id, i) => {
          const r = plotted.find((p) => p.id === id);
          return r ? (
            <text
              key={`label${i}`}
              x={x(r.propellant_used_kg!) + (i === 0 ? -6 : -10)}
              // Lowest fuel sits bottom-left: label below it, clear of neighbouring dots.
              y={y(r.assessment_time_s) + (i === 0 ? 20 : -10)}
              className="direct-label"
              textAnchor={i === 0 ? "start" : "end"}
            >
              {i === 0 ? t("routes.lowestFuel") : t("routes.longestAssessment")}
            </text>
          ) : null;
        })}
        {plotted.map((r) => (
          <circle
            key={`hit${r.id}`}
            cx={x(r.propellant_used_kg!)}
            cy={y(r.assessment_time_s)}
            r={11}
            fill="transparent"
            onMouseEnter={() => setHover(r)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </svg>
      {hover && (
        <div
          className="descent-tooltip"
          style={{ left: `${Math.min(85, Math.max(15, (x(hover.propellant_used_kg!) / W) * 100))}%` }}
          role="status"
        >
          <b>
            {hover.id} · {nmi(t, hover.orbit_altitude_km)} · {hover.approach_s}s + {hover.terminal_s}s
          </b>
          <span>{t("routes.propellant", { kg: hover.propellant_used_kg!.toFixed(0) })}</span>
          <span>
            {t("routes.marginHover", {
              kg: hover.reserve_margin_kg!.toFixed(0),
              s: hover.hover_margin_s!.toFixed(0),
            })}
          </span>
          <span>
            {hover.feasible
              ? t("routes.feasible")
              : t("routes.rejectedReason", {
                  reason: hover.rejection_reasons[0]?.reason.replaceAll("_", " ") ?? "",
                })}
          </span>
        </div>
      )}
      <div className="contribution-legend scatter-legend">
        {orbits.map((o, i) => (
          <span key={o}>
            <i style={{ background: ORBIT_COLORS[i % ORBIT_COLORS.length] }} />{" "}
            {t("routes.orbit", { nmi: nmi(t, o) })}
          </span>
        ))}
        <span>
          <i className="legend-front" /> {t("routes.paretoFront")}
        </span>
        <span>✕ {t("routes.rejected")}</span>
      </div>
    </div>
  );
}

export default function RouteOptimizer({ result }: { result: RouteResult }) {
  const { t, tText } = useTranslate();
  const tag = (id: string) =>
    [
      id === result.lowest_fuel_id && t("routes.tag.lowestFuel"),
      id === result.longest_assessment_id && t("routes.tag.longestAssessment"),
      id === result.reference_route_id && t("routes.tag.apollo"),
      result.pareto_ids.includes(id) && t("routes.tag.pareto"),
    ]
      .filter(Boolean)
      .join(" · ");
  const sorted = [...result.routes].sort(
    (a, b) =>
      Number(b.feasible) - Number(a.feasible) ||
      (a.propellant_used_kg ?? Infinity) - (b.propellant_used_kg ?? Infinity),
  );
  return (
    <div className="route-optimizer">
      <div className="panel descent-verdict ok" role="status">
        <strong>
          {t("routes.verdict", {
            n: result.feasible_ids.length,
            total: result.routes.length,
            fps: (result.reserve.delta_v_mps / 0.3048).toFixed(0),
          })}
        </strong>
        <p>
          {t("routes.reserveSource", {
            source: result.reserve.source,
            kg: result.propellant_at_doi_kg.toFixed(0),
          })}
        </p>
      </div>
      <Scatter result={result} />
      <div className="table-scroll">
      <table className="ranking-table">
        <caption>{t("routes.caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("routes.col.route")}</th>
            <th scope="col">{t("routes.col.propellant")}</th>
            <th scope="col">{t("routes.col.margin")}</th>
            <th scope="col">{t("routes.col.assessment")}</th>
            <th scope="col">{t("routes.col.status")}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.id} className={r.feasible ? "" : "route-rejected"}>
              <th scope="row">
                {r.id} · {nmi(t, r.orbit_altitude_km)} · {r.approach_s} s + {r.terminal_s} s
                {tag(r.id) && <small>{tag(r.id)}</small>}
              </th>
              <td>
                {r.propellant_used_kg !== undefined ? `${r.propellant_used_kg.toFixed(0)} kg` : "—"}
                {r.delta_v_mps !== undefined && <small>{r.delta_v_mps.toFixed(0)} m/s</small>}
              </td>
              <td>
                {r.reserve_margin_kg !== undefined ? `${r.reserve_margin_kg.toFixed(0)} kg` : "—"}
                {r.hover_margin_s !== undefined && (
                  <small>{t("routes.hover", { s: r.hover_margin_s.toFixed(0) })}</small>
                )}
              </td>
              <td>{r.assessment_time_s} s</td>
              <td>
                {r.feasible ? `✓ ${t("routes.feasible")}` : `✕ ${t("routes.rejected")}`}
                {r.rejection_reasons.map((x) => (
                  <small key={x.reason}>{x.detail}</small>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <details className="communication-limits">
        <summary>
          {t("routes.assumptions")} <span>+</span>
        </summary>
        <ul>
          {result.assumptions.map((a) => (
            <li key={a}>{tText(a)}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}
