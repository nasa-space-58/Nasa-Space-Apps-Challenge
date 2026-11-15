import type { CSSProperties } from "react";
import { useTranslate, type Translate } from "../i18n";
import { ROUTE_COLORS } from "../analysisView";

/** Backend /analyses/{id}/mission-routes response, rendered as delivered. */
export type RouteReason = {
  code: string;
  detail: string;
  value: number | null;
  limit: number | null;
  unit: string | null;
};
export type MissionRoute = {
  id: string;
  role: "primary" | "backup" | "rejected";
  rank: number | null;
  reachable: boolean | null;
  evaluated: "flown" | "terrain_only";
  latitude_deg: number;
  longitude_deg_east: number;
  distance_m: number;
  bearing_deg: number;
  terrain_height_m: number | null;
  metrics: Record<string, number | null>;
  reasons: RouteReason[];
  trajectory: { t_s: number; down_m: number; cross_m: number; altitude_m: number }[];
};
export type MissionRouteSet = {
  analysis_id: string;
  routes: MissionRoute[];
  counts: { primary: number; backup: number; rejected: number };
  divert_from: "high_gate" | "low_gate";
  divert_state: { t_s: number; altitude_m: number };
  disclaimer: string;
};

const METRICS: [string, string, number][] = [
  ["distance_m", "m", 0],
  ["safety_score", "", 2],
  ["max_landing_slope_deg", "°", 1],
  ["hazard_distance_m", "m", 0],
  ["divert_delta_v_mps", "m/s", 1],
  ["propellant_remaining_kg", "kg", 0],
  ["reserve_margin_kg", "kg", 0],
  ["hover_margin_s", "s", 0],
  ["time_to_touchdown_s", "s", 0],
  ["touchdown_position_error_m", "m", 1],
  ["touchdown_vertical_speed_mps", "m/s", 2],
  ["touchdown_horizontal_speed_mps", "m/s", 2],
];

export const routeColor = (r: MissionRoute) =>
  r.role === "primary" && r.reachable !== false
    ? ROUTE_COLORS.primary
    : r.role === "backup"
      ? ROUTE_COLORS.backup
      : ROUTE_COLORS.rejected;

const fixed = (v: number | null | undefined, digits: number) =>
  typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "–";

export function routeLabel(t: Translate, r: MissionRoute) {
  if (r.role === "primary") return t("missionRun.routes.role.primary");
  if (r.role === "backup") return t("missionRun.routes.role.backup", { rank: r.rank ?? "" });
  return t("missionRun.routes.role.rejected", { id: r.id });
}

function reasonText(t: Translate, reason: RouteReason, route: MissionRoute) {
  const code =
    reason.code === "planned_target" && route.reachable === false
      ? "planned_target_unreachable"
      : reason.code === "thrust" && reason.unit === "m"
        ? "thrust_window"
        : reason.code;
  const key = `missionRun.routes.reason.${code}`;
  const text = t(key, {
    value: fixed(reason.value, reason.unit === "deg" ? 1 : 0),
    limit: fixed(reason.limit, reason.unit === "deg" ? 1 : 0),
    pct: fixed(reason.value === null ? null : reason.value * 100, 0),
    dv: fixed(route.metrics.divert_delta_v_mps, 1),
    score: fixed(route.metrics.safety_score, 2),
  });
  return text === key ? reason.detail : text;
}

/** Route list and the selected route's metrics and reasons; values come from the API. */
export default function MissionRoutes({
  set,
  selected,
  shown,
  onSelect,
  onShow,
}: {
  set: MissionRouteSet;
  selected: string | null;
  shown: boolean;
  onSelect: (id: string) => void;
  onShow: () => void;
}) {
  const { t } = useTranslate();
  const route = set.routes.find((r) => r.id === selected) ?? null;
  const legend: [string, string, boolean][] = [
    [t("missionRun.routes.legend.primary"), ROUTE_COLORS.primary, false],
    [t("missionRun.routes.legend.backup"), ROUTE_COLORS.backup, false],
    [t("missionRun.routes.legend.rejected"), ROUTE_COLORS.rejected, true],
    [t("missionRun.routes.legend.flown"), ROUTE_COLORS.flown, false],
  ];
  return (
    <section className="run-routes" aria-label={t("missionRun.routes.heading")}>
      <h4>{t("missionRun.routes.heading")}</h4>
      <p className="muted small">
        {t("missionRun.routes.intro", {
          primary: set.counts.primary,
          backup: set.counts.backup,
          rejected: set.counts.rejected,
          gate: t(`missionRun.routes.divert.${set.divert_from}`),
        })}
      </p>
      <ul className="route-legend">
        {legend.map(([label, color, dashed]) => (
          <li key={label}>
            <i className={dashed ? "dashed" : ""} style={{ "--route": color } as CSSProperties} />
            {label}
          </li>
        ))}
      </ul>
      {!shown && <button onClick={onShow}>{t("missionRun.routes.show3d")}</button>}
      <ul className="route-list">
        {set.routes.map((r) => (
          <li key={r.id}>
            <button
              aria-pressed={selected === r.id}
              onClick={() => onSelect(r.id)}
              style={{ "--route": routeColor(r) } as CSSProperties}
            >
              <i className={r.role === "rejected" ? "dashed" : ""} />
              <strong>{routeLabel(t, r)}</strong>
              <small>
                {r.evaluated === "terrain_only"
                  ? t("missionRun.routes.terrainOnly")
                  : `${fixed(r.distance_m, 0)} m · ${fixed(r.metrics.reserve_margin_kg, 0)} kg`}
              </small>
            </button>
          </li>
        ))}
      </ul>
      {route ? (
        <div className="route-detail" style={{ "--route": routeColor(route) } as CSSProperties}>
          <h5>
            {routeLabel(t, route)} <small>{route.id}</small>
          </h5>
          <h6>{t(`missionRun.routes.why.${route.role}`)}</h6>
          <ul className="route-reasons">
            {route.reasons.map((reason, i) => (
              <li key={i}>{reasonText(t, reason, route)}</li>
            ))}
          </ul>
          <h6>{t("missionRun.routes.metricsHeading")}</h6>
          <dl className="route-metrics">
            {METRICS.map(([key, unit, digits]) => {
              const value = key === "distance_m" ? route.distance_m : route.metrics[key];
              if (value === null || value === undefined) return null;
              return (
                <div key={key}>
                  <dt>{t(`missionRun.routes.metric.${key}`)}</dt>
                  <dd>
                    {fixed(value, digits)} {unit}
                  </dd>
                </div>
              );
            })}
          </dl>
          {route.evaluated === "terrain_only" && (
            <p className="muted small">{t("missionRun.routes.notFlown")}</p>
          )}
        </div>
      ) : (
        <p className="muted small">{t("missionRun.routes.pick")}</p>
      )}
      <p className="muted small">{t("missionRun.routes.disclaimer")}</p>
    </section>
  );
}
