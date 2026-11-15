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
};

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
