import { useEffect, useState } from "react";
import type { Result } from "../types";
import HorizonPlot from "./HorizonPlot";
import ExportPanel from "./ExportPanel";
type Lesson = {
  id: string;
  title: string;
  source_url: string;
  limitation: string;
  cards: { title: string; text: string }[];
};
const explanation: Record<string, string> = {
  full: "The whole solar disk clears the modeled terrain horizon at this sample.",
  partial:
    "Only part of the Sun clears the ridge. A point-sized Sun would miss this transition.",
  dark: "Terrain hides the solar disk at this observer. This does not mean the whole region is permanently shadowed.",
  boundary:
    "This sample is at a modeled visibility boundary. Sampling and terrain uncertainty matter.",
  unknown:
    "Coverage is missing here. We cannot conclude that this sample is dark or illuminated.",
};
async function api(path: string, post = false) {
  const response = await fetch(
      "/api" + path,
      post ? { method: "POST" } : undefined,
    ),
    body = await response.json();
  if (!response.ok)
    throw Error(
      typeof body.detail === "string" ? body.detail : "Lesson unavailable",
    );
  return body;
}
export default function Classroom({
  onOpen,
}: {
  onOpen: (id: string, compare: boolean) => Promise<void>;
}) {
  const [lessons, setLessons] = useState<Lesson[]>([]),
    [selected, setSelected] = useState<Lesson | null>(null);
  const [id, setId] = useState<string | null>(null),
    [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState(""),
    [phase, setPhase] = useState(""),
    [starting, setStarting] = useState(false);
  const [index, setIndex] = useState(0),
    [playing, setPlaying] = useState(false),
    [speed, setSpeed] = useState("1000"),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    api("/classroom")
      .then((data) => {
        if (active) setLessons(data.lessons);
      })
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [retry]);
  useEffect(() => {
    if (!id) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const job = await api("/analyses/" + id);
        if (!active) return;
        setPhase(job.status);
        setError("");
        if (job.status === "failed") {
          setError(
            job.error || "Lesson analysis failed; select a lesson to retry.",
          );
          return;
        }
        if (["ready", "insufficient_data"].includes(job.status)) {
          const data = await api("/analyses/" + id + "/result");
          if (active) {
            setResult(data);
            setIndex(0);
          }
        } else timer = setTimeout(poll, 1800);
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "Connection interrupted");
          timer = setTimeout(poll, 5000);
        }
      }
    };
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [id, retry]);
  useEffect(() => {
    if (!playing || !result) return;
    const timer = setInterval(
      () =>
        setIndex((n) => {
          if (n >= result.frames.length - 1) {
            setPlaying(false);
            return n;
          }
          return n + 1;
        }),
      Number(speed),
    );
    return () => clearInterval(timer);
  }, [playing, result, speed]);
  useEffect(() => {
    const m = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const stop = () => setPlaying(false);
    m?.addEventListener("change", stop);
    return () => m?.removeEventListener("change", stop);
  }, []);
  const start = async (lesson: Lesson) => {
    setStarting(true);
    setError("");
    setPlaying(false);
    setResult(null);
    setSelected(lesson);
    setPhase("pending");
    try {
      const job = await api("/classroom/" + lesson.id, true);
      setId(job.id);
      setRetry((n) => n + 1);
    } catch (e) {
      setPhase("failed");
      setError(e instanceof Error ? e.message : "Unable to start lesson");
    } finally {
      setStarting(false);
    }
  };
  const frame = result?.frames[index];
  return (
    <section id="classroom" className="classroom-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">LEARN / THE SAME PLACE, A DIFFERENT DAY</p>
          <h2>
            Follow the light<span>.</span>
          </h2>
          <p className="muted">
            Two short lessons. Real observations. No technical setup.
          </p>
        </div>
        <span className="outline-tag">CLASSROOM</span>
      </div>
      <div className="lesson-options">
        {lessons.map((lesson) => (
          <article className="panel lesson-card" key={lesson.id}>
            <h3>{lesson.title}</h3>
            <p>
              Malapert Massif · {lesson.id === "later-start" ? "6" : "4"}{" "}
              November 2026 · 24 hours
            </p>
            <button
              disabled={starting}
              className="primary"
              onClick={() => start(lesson)}
            >
              Start lesson:{" "}
              {lesson.id === "later-start"
                ? "48 hours later"
                : "changing light"}{" "}
              ↗
            </button>
          </article>
        ))}
      </div>
      {error && (
        <div role="alert" className="alert">
          {error}{" "}
          <button onClick={() => setRetry((n) => n + 1)}>
            Reconnect lessons
          </button>
        </div>
      )}
      {selected && !result && (
        <p role="status">
          {phase.replaceAll("_", " ")} · The first run may take minutes to
          acquire NASA data. Saved lessons open again without a new calculation.
        </p>
      )}
      {selected && result && frame && (
        <div className="lesson-stage">
          <article className="panel lesson-sky">
            <div className="panel-heading">
              <span>{selected.title}</span>
              <span>UTC</span>
            </div>
            <HorizonPlot result={result} frame={frame} />
            <div className="lesson-player">
              <strong>{frame.utc}</strong>
              <button
                aria-label={playing ? "Pause lesson" : "Play lesson"}
                onClick={() => {
                  if (index === result.frames.length - 1) setIndex(0);
                  setPlaying(!playing);
                }}
              >
                {playing ? "Pause Ⅱ" : "Play ▷"}
              </button>
              <label>
                Playback speed
                <select
                  aria-label="Lesson playback speed"
                  value={speed}
                  onChange={(e) => setSpeed(e.target.value)}
                >
                  <option value="2000">Slow · 2 s / sample</option>
                  <option value="1000">Normal · 1 s / sample</option>
                  <option value="250">Fast · 4 samples / s</option>
                </select>
              </label>
              <input
                type="range"
                aria-label="Lesson time"
                aria-valuetext={frame.utc}
                min="0"
                max={result.frames.length - 1}
                value={index}
                onChange={(e) => {
                  setPlaying(false);
                  setIndex(Number(e.target.value));
                }}
              />
              <p className="field-help">
                Playback advances through computed samples; unequal UTC gaps are
                shown by the timestamp.
              </p>
            </div>
          </article>
          <aside
            className="panel lesson-explanation"
            aria-label="Current observation explanation"
          >
            <p className="eyebrow">AT THIS MOMENT</p>
            <h3>Sun: {frame.sun.state}</h3>
            <p>{explanation[frame.sun.state] || explanation.unknown}</p>
            <h3>Earth: {frame.earth.state}</h3>
            <p>
              {frame.earth.state === "visible"
                ? "Earth's centre clears this horizon. A ground station or working radio link is not established by this alone."
                : frame.earth.state === "unknown"
                  ? "Earth visibility is unknown at this sample."
                  : "Earth's centre is blocked or at the horizon boundary at this sample."}
            </p>
            <p>
              Across this window:{" "}
              {result.analysis.sun.metrics.visible_percent_of_interval.toFixed(
                1,
              )}
              % sunlight;{" "}
              {(result.analysis.sun.metrics.longest_blocked_s / 3600).toFixed(
                2,
              )}{" "}
              h longest darkness.
            </p>
          </aside>
          <div className="lesson-cards">
            {selected.cards.map((card) => (
              <article className="panel" key={card.title}>
                <h3>{card.title}</h3>
                <p>{card.text}</p>
              </article>
            ))}
          </div>
          <p className="search-limitation">{selected.limitation}</p>
          <details className="panel lesson-details">
            <summary>Technical details and sources</summary>
            <p>
              Observer height {result.horizon.observer_height_above_terrain_m}{" "}
              m. Terrain search radius{" "}
              {result.horizon.settings.search_distance_m} m. Analysis{" "}
              {result.analysis.analysis_id}.
            </p>
            <a href={selected.source_url} target="_blank" rel="noreferrer">
              NASA coordinate source ↗
            </a>
            <p>
              LOLA terrain and JPL Horizons ephemerides. Open mission control
              for coverage and provenance.
            </p>
            <button
              onClick={() => {
                setPlaying(false);
                onOpen(id!, false).catch((e) => setError(e.message));
              }}
            >
              Open full analysis ↗
            </button>
          </details>
          <ExportPanel jobId={id!} result={result} frameIndex={index} />
        </div>
      )}
    </section>
  );
}
