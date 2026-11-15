import { useSyncExternalStore } from "react";
import type { Result } from "./types";

/** One shared-cursor sample of POST /comparisons, rendered as delivered. */
export type ComparisonCursor = {
  elapsed_s: number;
  a_index: number | null;
  b_index: number | null;
  a_sample_age_s: number | null;
  b_sample_age_s: number | null;
};

/** A ready A/B pair and its shared playback position, shared by the panel and the split scene. */
export type ComparisonPlayback = {
  aId: string;
  bId: string;
  a: Result;
  b: Result;
  alignment: string;
  cursor: ComparisonCursor[];
  index: number;
  playing: boolean;
  /** Cursor samples advanced per playback tick. */
  speed: PlaybackRate;
};

/** Display-only multiplier applied to every elevation angle in the split sky view. */
export const SKY_ELEVATION_DISPLAY_FACTOR = 6;

export const PLAYBACK_RATES = [1, 2, 4] as const;
export type PlaybackRate = (typeof PLAYBACK_RATES)[number];

let current: ComparisonPlayback | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());

/** Publish (or clear) the ready pair; keeps the position when the same pair is republished. */
export function setComparisonPair(
  pair: Omit<ComparisonPlayback, "index" | "playing" | "speed"> | null,
  index = 0,
) {
  if (!pair) {
    if (current) {
      current = null;
      emit();
    }
    return;
  }
  const last = Math.max(0, pair.cursor.length - 1);
  current = {
    ...pair,
    index: Math.max(0, Math.min(index, last)),
    playing: false,
    speed: current?.speed ?? 1,
  };
  emit();
}

export function setComparisonIndex(next: number | ((index: number) => number)) {
  if (!current) return;
  const raw = typeof next === "function" ? next(current.index) : next;
  const index = Math.max(0, Math.min(Math.round(raw), current.cursor.length - 1));
  if (index === current.index) return;
  current = { ...current, index };
  emit();
}

export function setComparisonPlaying(playing: boolean) {
  if (!current || current.playing === playing) return;
  current = { ...current, playing };
  emit();
}

export function setComparisonSpeed(speed: PlaybackRate) {
  if (!current || current.speed === speed) return;
  current = { ...current, speed };
  emit();
}

/** Advance one playback tick; stops at the last shared sample. */
export function stepComparison() {
  if (!current) return;
  const last = current.cursor.length - 1;
  if (current.index >= last) {
    setComparisonPlaying(false);
    return;
  }
  setComparisonIndex(current.index + current.speed);
  if (current.index >= last) setComparisonPlaying(false);
}

export function useComparisonPlayback() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}
