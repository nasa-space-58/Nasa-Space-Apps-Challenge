import type { Interval, Result } from "./types";

/**
 * Display-only replay of backend-classified visibility intervals.
 *
 * Nothing here decides visibility: the backend already labelled every
 * interval (full/partial/visible, dark/blocked, unknown/boundary). These
 * helpers only clip those labelled intervals at the playback cursor and add
 * up their durations, using the same state groups as the backend metrics
 * (backend/app/visibility.py `_metrics`). Final, whole-window figures are
 * always shown from the backend metrics themselves.
 */
export type Visibility = "visible" | "blocked" | "unknown";

const VISIBLE_STATES = new Set(["full", "partial", "visible"]);
const BLOCKED_STATES = new Set(["dark", "blocked"]);

export const visibilityOf = (state: string | undefined): Visibility =>
  state && VISIBLE_STATES.has(state)
    ? "visible"
    : state && BLOCKED_STATES.has(state)
      ? "blocked"
      : "unknown";

export type Tally = {
  elapsed_s: number;
  visible_s: number;
  blocked_s: number;
  unknown_s: number;
  longest_visible_s: number;
  longest_blocked_s: number;
  /** Visibility of the interval that contains the cursor. */
  current: Visibility;
  /** How long the current visibility class has lasted at the cursor. */
  current_since_s: number;
  /** How long the current visibility class continues after the cursor. */
  current_remaining_s: number;
  /** Number of visible → blocked changes before the cursor. */
  losses: number;
};

type Span = { from: number; to: number; kind: Visibility };

/** Intervals merged into contiguous runs of one visibility class (ms). */
function runs(intervals: Interval[]): Span[] {
  const out: Span[] = [];
  for (const row of intervals) {
    const from = Date.parse(row.start_utc),
      to = Date.parse(row.end_utc),
      kind = visibilityOf(row.state);
    if (!(to > from)) continue;
    const last = out[out.length - 1];
    if (last && last.kind === kind && Math.abs(last.to - from) < 1) last.to = to;
    else out.push({ from, to, kind });
  }
  return out;
}

/** Totals of one target from the window start up to `cursorMs`. */
export function tallyUntil(intervals: Interval[], startMs: number, cursorMs: number): Tally {
  const tally: Tally = {
    elapsed_s: Math.max(0, (cursorMs - startMs) / 1000),
    visible_s: 0,
    blocked_s: 0,
    unknown_s: 0,
    longest_visible_s: 0,
    longest_blocked_s: 0,
    current: "unknown",
    current_since_s: 0,
    current_remaining_s: 0,
    losses: 0,
  };
  const spans = runs(intervals);
  let previous: Visibility | null = null;
  for (const span of spans) {
    const from = Math.max(span.from, startMs);
    if (span.to <= from) continue;
    const clippedTo = Math.min(span.to, cursorMs);
    if (clippedTo > from) {
      const seconds = (clippedTo - from) / 1000;
      if (span.kind === "visible") {
        tally.visible_s += seconds;
        tally.longest_visible_s = Math.max(tally.longest_visible_s, seconds);
      } else if (span.kind === "blocked") {
        tally.blocked_s += seconds;
        tally.longest_blocked_s = Math.max(tally.longest_blocked_s, seconds);
      } else tally.unknown_s += seconds;
      if (previous === "visible" && span.kind === "blocked") tally.losses += 1;
      previous = span.kind;
    }
    const contains =
      span.from <= cursorMs &&
      (cursorMs < span.to || (span === spans[spans.length - 1] && cursorMs <= span.to));
    if (contains) {
      tally.current = span.kind;
      tally.current_since_s = (cursorMs - from) / 1000;
      tally.current_remaining_s = (span.to - cursorMs) / 1000;
    }
  }
  return tally;
}

export type Joint = {
  both_s: number;
  sun_only_s: number;
  earth_only_s: number;
  neither_s: number;
  unknown_s: number;
};

/** Time split by the joint Sun/Earth state over [startMs, endMs]. */
export function jointBreakdown(
  sun: Interval[],
  earth: Interval[],
  startMs: number,
  endMs: number,
): Joint {
  const out: Joint = { both_s: 0, sun_only_s: 0, earth_only_s: 0, neither_s: 0, unknown_s: 0 };
  const a = runs(sun),
    b = runs(earth);
  const edges = Array.from(
    new Set([startMs, endMs, ...a.flatMap((s) => [s.from, s.to]), ...b.flatMap((s) => [s.from, s.to])]),
  )
    .filter((x) => x >= startMs && x <= endMs)
    .sort((x, y) => x - y);
  const kindAt = (spans: Span[], t: number): Visibility =>
    spans.find((s) => s.from <= t && t < s.to)?.kind ?? "unknown";
  for (let i = 0; i < edges.length - 1; i++) {
    const seconds = (edges[i + 1] - edges[i]) / 1000;
    if (seconds <= 0) continue;
    const mid = (edges[i] + edges[i + 1]) / 2;
    const s = kindAt(a, mid),
      e = kindAt(b, mid);
    if (s === "unknown" || e === "unknown") out.unknown_s += seconds;
    else if (s === "visible" && e === "visible") out.both_s += seconds;
    else if (s === "visible") out.sun_only_s += seconds;
    else if (e === "visible") out.earth_only_s += seconds;
    else out.neither_s += seconds;
  }
  return out;
}

export const windowOf = (result: Result) => ({
  startMs: Date.parse(result.analysis.request.start_utc),
  endMs: Date.parse(result.analysis.request.end_utc),
});

/** The combined situation of the site at one moment, for plain-language text. */
export type Situation = "both" | "sun_only" | "earth_only" | "neither" | "unknown";
export const situationOf = (sun: Visibility, earth: Visibility): Situation =>
  sun === "unknown" || earth === "unknown"
    ? "unknown"
    : sun === "visible" && earth === "visible"
      ? "both"
      : sun === "visible"
        ? "sun_only"
        : earth === "visible"
          ? "earth_only"
          : "neither";
