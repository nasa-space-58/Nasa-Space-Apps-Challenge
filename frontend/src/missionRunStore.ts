import { useSyncExternalStore } from "react";

/** Backend /analyses/{id}/mission-run response, rendered as delivered. */
export type GateStatus = "pass" | "warn" | "fail" | "unknown" | "not_reached";
export type MissionGate = {
  gate: "descent" | "touchdown_terrain" | "power" | "communication";
  status: GateStatus;
  code: string | null;
  detail: string;
  metrics?: Record<string, unknown> | null;
  evaluated_until_utc?: string;
};
export type MissionEvent = { utc: string; gate: string; kind: string; detail: string; fault?: string };
export type DescentSample = {
  t_s: number;
  phase: string;
  altitude_m: number;
  downrange_m: number;
  vertical_speed_mps: number;
  horizontal_speed_mps: number;
  thrust_n: number;
  mass_kg: number;
};
export type MissionRunResult = {
  analysis_id: string;
  outcome: "success" | "success_with_warnings" | "failure" | "inconclusive";
  failure: { gate: string; code: string; detail: string; at_utc: string } | null;
  gates: MissionGate[];
  warnings: { gate: string; code: string; detail: string }[];
  events: MissionEvent[];
  scenario_id: string | null;
  faults: Record<string, number | string>[];
  landing_utc: string | null;
  pdi_utc: string;
  mission_end_utc?: string;
  touchdown_site: { latitude_deg: number; longitude_deg_east: number; offset_m: number; bearing_deg: number } | null;
  descent: {
    nominal_propellant_margin_kg: number;
    propellant_margin_kg: number;
    propellant_leaked_kg: number;
    contact: { t_s: number; vertical_speed_mps: number; horizontal_speed_mps: number } | null;
    exhausted_at: { t_s: number; altitude_m: number } | null;
    samples: DescentSample[];
  } | null;
  power: {
    trace: { utc: string; energy_wh: number; capacity_wh: number }[];
    min_energy_wh: number;
    depleted_at_utc: string | null;
  } | null;
  communication: {
    windows: { start_utc: string; end_utc: string }[];
    first_contact_utc: string | null;
    longest_gap_h: number;
  } | null;
  settings: { terrain: { slope_limit_deg: number }; communication: { first_contact_deadline_h: number; max_contact_gap_h: number } };
  disclaimer: string;
  /** What-if continuation: the lander keeps running without contact until the window end. */
  surface_outlook?: SurfaceOutlook | null;
  /** Fault-free reference for the same analysis and the scenario's difference from it. */
  baseline?: MissionBaseline;
  /** Why runs in this window may look alike. */
  context?: MissionContext;
};
export type OutlookFinding = { utc: string; gate: string; kind: string; detail: string; fault?: string };
export type SurfaceOutlook = {
  landing_utc: string;
  evaluated_until_utc: string;
  power: {
    trace: { utc: string; energy_wh: number; capacity_wh: number }[];
    min_energy_wh: number;
    depleted_at_utc: string | null;
    optimistic_depleted_at_utc: string | null;
  };
  communication: { windows: { start_utc: string; end_utc: string }[]; first_contact_utc: string | null; longest_gap_h: number };
  findings: OutlookFinding[];
};
export type MissionBaseline = {
  landing_utc: string;
  propellant_margin_kg: number | null;
  touchdown_offset_m: number;
  min_energy_wh: number;
  depleted_at_utc: string | null;
  first_contact_utc: string | null;
  delta: {
    propellant_margin_kg: number | null;
    touchdown_offset_m: number | null;
    landing_delay_h: number | null;
    min_energy_wh: number | null;
    first_contact_shift_h: number | null;
  };
};
export type MissionContext = {
  surface_window_h: number | null;
  earth_visible_h: number;
  sun_visible_h: number;
  verdict_utc: string;
  limitations: { code: string; detail: string; fault?: string }[];
};

/** Up to two translation keys (with values) naming how this run differs from the fault-free one. */
export function runHighlights(run: MissionRunResult): { key: string; params: Record<string, string> }[] {
  const out: { key: string; params: Record<string, string> }[] = [];
  const delta = run.baseline?.delta;
  const outlook = run.surface_outlook;
  const depleted = outlook?.power.depleted_at_utc;
  const baseDepleted = run.baseline?.depleted_at_utc;
  // Report the battery only when it runs out earlier than in the fault-free run.
  if (outlook && depleted && (!baseDepleted || Date.parse(baseDepleted) - Date.parse(depleted) > 360000)) {
    const hours = (Date.parse(depleted) - Date.parse(outlook.landing_utc)) / 3600000;
    out.push({ key: "missionRun.highlight.battery", params: { h: hours.toFixed(1) } });
  }
  if (delta) {
    if ((delta.touchdown_offset_m ?? 0) > 50)
      out.push({ key: "missionRun.highlight.offset", params: { m: delta.touchdown_offset_m!.toFixed(0) } });
    if ((delta.propellant_margin_kg ?? 0) < -1)
      out.push({ key: "missionRun.highlight.fuel", params: { kg: delta.propellant_margin_kg!.toFixed(0) } });
    if (!depleted && (delta.min_energy_wh ?? 0) < -1)
      out.push({ key: "missionRun.highlight.energy", params: { wh: delta.min_energy_wh!.toFixed(0) } });
    if ((delta.first_contact_shift_h ?? 0) > 0.01)
      out.push({ key: "missionRun.highlight.contact", params: { h: delta.first_contact_shift_h!.toFixed(1) } });
    if ((delta.landing_delay_h ?? 0) > 0.01)
      out.push({ key: "missionRun.highlight.delay", params: { h: delta.landing_delay_h!.toFixed(1) } });
  }
  return out.slice(0, 2);
}

/** How the landing close-up should end for the latest run of the shown analysis. */
export type TouchdownOutcome = "landed" | "crash" | "tip_over";

let current: MissionRunResult | null = null;
const listeners = new Set<() => void>();

export function setMissionRun(result: MissionRunResult | null) {
  current = result;
  listeners.forEach((listener) => listener());
}

export function useMissionRun(analysisId?: string) {
  const result = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
  return result && result.analysis_id === analysisId ? result : null;
}

export function touchdownOutcome(result: MissionRunResult | null): TouchdownOutcome {
  if (result?.failure?.gate === "descent") return "crash";
  if (result?.failure?.code === "tip_over") return "tip_over";
  return "landed";
}
