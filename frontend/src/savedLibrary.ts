import { useSyncExternalStore } from "react";
import type { Result } from "./types";

/**
 * Browser-local library of saved analyses and A/B comparisons.
 *
 * Analyses themselves are persisted by the backend; this list only keeps
 * their ids with a user-chosen name and a copy of the backend summary
 * metrics, so the list can be read without refetching every result.
 */
export type SavedResult = {
  id: string;
  name: string;
  site_id: string;
  latitude_deg: number;
  longitude_deg_east: number;
  start_utc: string;
  end_utc: string;
  saved_at_utc: string;
  summary: {
    sun_visible_percent: number;
    sun_visible_s: number;
    earth_visible_percent: number;
    earth_visible_s: number;
    longest_dark_s: number;
    unknown_s: number;
  };
};
export type SavedComparison = {
  a_id: string;
  b_id: string;
  name: string;
  saved_at_utc: string;
};
type Library = { version: 1; results: SavedResult[]; comparisons: SavedComparison[] };

const STORAGE_KEY = "lunar-saved-library";
const EMPTY: Library = { version: 1, results: [], comparisons: [] };
let current: Library = read();
const listeners = new Set<() => void>();

function read(): Library {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (parsed && parsed.version === 1 && Array.isArray(parsed.results) && Array.isArray(parsed.comparisons))
      return parsed;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }
  return EMPTY;
}
function write(next: Library) {
  current = next;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  listeners.forEach((listener) => listener());
}
window.addEventListener("storage", (event) => {
  if (event.key !== STORAGE_KEY) return;
  current = read();
  listeners.forEach((listener) => listener());
});

export function useLibrary(): Library {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}

export const defaultName = (result: Result) =>
  `${result.analysis.request.site.site_id.replaceAll("-", " ")} · ${result.analysis.request.start_utc.slice(0, 16).replace("T", " ")}`;

export function saveResult(id: string, result: Result, name?: string) {
  const sun = result.analysis.sun.metrics,
    earth = result.analysis.earth.metrics,
    site = result.analysis.request.site;
  const entry: SavedResult = {
    id,
    name: name?.trim() || defaultName(result),
    site_id: site.site_id,
    latitude_deg: site.latitude_deg,
    longitude_deg_east: site.longitude_deg_east,
    start_utc: result.analysis.request.start_utc,
    end_utc: result.analysis.request.end_utc,
    saved_at_utc: new Date().toISOString(),
    summary: {
      sun_visible_percent: sun.visible_percent_of_interval,
      sun_visible_s: sun.visible_duration_s,
      earth_visible_percent: earth.visible_percent_of_interval,
      earth_visible_s: earth.visible_duration_s,
      longest_dark_s: sun.longest_blocked_s,
      unknown_s: sun.unknown_duration_s + earth.unknown_duration_s,
    },
  };
  write({ ...current, results: [entry, ...current.results.filter((r) => r.id !== id)] });
}

export function renameResult(id: string, name: string) {
  if (!name.trim()) return;
  write({
    ...current,
    results: current.results.map((r) => (r.id === id ? { ...r, name: name.trim() } : r)),
  });
}

export function removeResult(id: string) {
  // Saved comparisons keep working: they only reference backend analysis ids.
  write({ ...current, results: current.results.filter((r) => r.id !== id) });
}

export function saveComparison(a_id: string, b_id: string, name: string) {
  const entry: SavedComparison = { a_id, b_id, name: name.trim(), saved_at_utc: new Date().toISOString() };
  write({
    ...current,
    comparisons: [entry, ...current.comparisons.filter((c) => !(c.a_id === a_id && c.b_id === b_id))],
  });
}

export function removeComparison(a_id: string, b_id: string) {
  write({
    ...current,
    comparisons: current.comparisons.filter((c) => !(c.a_id === a_id && c.b_id === b_id)),
  });
}

export const savedName = (library: Library, id: string) =>
  library.results.find((r) => r.id === id)?.name;
