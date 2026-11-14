import { useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * Horizontal UTC date strip under the mission-control scene. Dragging,
 * scrolling or the arrow keys move the start time in whole hours; a click
 * opens a full date-time field. Values use the `YYYY-MM-DDTHH:MM` form of
 * `<input type="datetime-local">`, always interpreted as UTC.
 */
const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;
const PX_PER_HOUR = 4;
const CLICK_SLOP_PX = 4;
const MIN_UTC = "2026-01-01T00:00";
const MAX_UTC = "2027-12-31T23:00";
const MONTHS = [
  "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
];
const WEEKDAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

const toMs = (value: string) => Date.parse(value + "Z");
const toValue = (ms: number) => new Date(ms).toISOString().slice(0, 16);
const clamp = (ms: number) =>
  Math.min(toMs(MAX_UTC), Math.max(toMs(MIN_UTC), ms));
const snap = (ms: number) => clamp(Math.round(ms / HOUR_MS) * HOUR_MS);
const fullLabel = (ms: number) => {
  const d = new Date(ms);
  return `${WEEKDAYS[d.getUTCDay()]} ${String(d.getUTCDate()).padStart(2, "0")} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()} · ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")} UTC`;
};

export default function DateScrubber({
  value,
  durationHours,
  disabled,
  onChange,
}: {
  value: string;
  durationHours: number;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const track = useRef<HTMLDivElement>(null);
  const editor = useRef<HTMLInputElement>(null);
  const drag = useRef<{ x: number; ms: number; moved: boolean } | null>(null);
  const [width, setWidth] = useState(0);
  const [editing, setEditing] = useState(false);
  const parsed = toMs(value);
  const ms = Number.isFinite(parsed) ? parsed : toMs(MIN_UTC);
  const latest = useRef({ ms, disabled, onChange });
  latest.current = { ms, disabled, onChange };

  useLayoutEffect(() => {
    const el = track.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, [editing]);

  // React registers wheel listeners as passive; a native one can stop the
  // page from scrolling while the strip moves.
  useEffect(() => {
    const el = track.current;
    if (!el) return;
    const wheel = (event: WheelEvent) => {
      const { ms: now, disabled: off, onChange: emit } = latest.current;
      if (off) return;
      event.preventDefault();
      const delta =
        Math.abs(event.deltaX) > Math.abs(event.deltaY)
          ? event.deltaX
          : event.deltaY;
      const hours = Math.sign(delta) * Math.max(1, Math.round(Math.abs(delta) / 12));
      emit(toValue(snap(now + hours * HOUR_MS)));
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, [editing]);

  useEffect(() => {
    if (editing) editor.current?.focus();
  }, [editing]);

  const move = (deltaMs: number) => {
    if (!disabled) onChange(toValue(snap(ms + deltaMs)));
  };

  if (editing)
    return (
      <div className="date-scrubber editing">
        <label>
          <span className="eyebrow">START · UTC</span>
          <input
            ref={editor}
            type="datetime-local"
            aria-label="Start date and time UTC"
            min={MIN_UTC}
            max={MAX_UTC}
            value={value}
            onChange={(e) => {
              const next = toMs(e.target.value);
              if (Number.isFinite(next)) onChange(toValue(clamp(next)));
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === "Escape") setEditing(false);
            }}
            onBlur={() => setEditing(false)}
          />
        </label>
        <button
          type="button"
          className="text-button"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => setEditing(false)}
        >
          Done
        </button>
      </div>
    );

  const halfDays = Math.ceil(width / 2 / (24 * PX_PER_HOUR)) + 1;
  const firstDay = Math.floor(ms / DAY_MS) - halfDays;
  const days = Array.from({ length: halfDays * 2 + 1 }, (_, i) => firstDay + i);
  const x = (t: number) => width / 2 + ((t - ms) / HOUR_MS) * PX_PER_HOUR;
  const spanEnd = ms + Math.max(0, durationHours) * HOUR_MS;

  return (
    <div
      className={`date-scrubber${disabled ? " disabled" : ""}`}
      ref={track}
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label="Observation start, UTC. Drag or scroll to change; press Enter to type a date."
      aria-valuemin={toMs(MIN_UTC)}
      aria-valuemax={toMs(MAX_UTC)}
      aria-valuenow={ms}
      aria-valuetext={fullLabel(ms)}
      aria-disabled={disabled}
      onPointerDown={(e) => {
        if (disabled || e.button !== 0) return;
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, ms, moved: false };
      }}
      onPointerMove={(e) => {
        const start = drag.current;
        if (!start) return;
        const dx = e.clientX - start.x;
        if (Math.abs(dx) > CLICK_SLOP_PX) start.moved = true;
        if (start.moved)
          onChange(toValue(snap(start.ms - (dx / PX_PER_HOUR) * HOUR_MS)));
      }}
      onPointerUp={() => {
        const start = drag.current;
        drag.current = null;
        if (start && !start.moved) setEditing(true);
      }}
      onPointerCancel={() => {
        drag.current = null;
      }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? DAY_MS : HOUR_MS;
        const actions: Record<string, () => void> = {
          ArrowLeft: () => move(-step),
          ArrowRight: () => move(step),
          PageDown: () => move(-7 * DAY_MS),
          PageUp: () => move(7 * DAY_MS),
          Enter: () => !disabled && setEditing(true),
          " ": () => !disabled && setEditing(true),
        };
        const action = actions[e.key];
        if (action) {
          e.preventDefault();
          action();
        }
      }}
    >
      <div
        className="scrubber-span"
        style={{ left: x(ms), width: x(spanEnd) - x(ms) }}
        aria-hidden="true"
      />
      <div className="scrubber-ruler" aria-hidden="true">
        {days.map((day) => {
          const dayMs = day * DAY_MS;
          const d = new Date(dayMs);
          const monthStart = d.getUTCDate() === 1;
          return (
            <div
              key={day}
              className={`scrubber-day${monthStart ? " month-start" : ""}`}
              style={{ left: x(dayMs), width: 24 * PX_PER_HOUR }}
            >
              <span>
                {monthStart
                  ? `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
                  : `${String(d.getUTCDate()).padStart(2, "0")} ${MONTHS[d.getUTCMonth()]}`}
              </span>
            </div>
          );
        })}
      </div>
      <div className="scrubber-needle" aria-hidden="true" />
      <div className="scrubber-readout">
        <strong>{fullLabel(ms)}</strong>
        <small>
          {disabled ? "Locked while analysis runs" : "Drag or scroll · click to type a date"}
        </small>
      </div>
    </div>
  );
}
