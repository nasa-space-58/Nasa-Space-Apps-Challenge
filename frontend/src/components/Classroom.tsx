import { useEffect, useState } from "react";
import type { Result } from "../types";
import HorizonPlot from "./HorizonPlot";
import ExportPanel from "./ExportPanel";
import { errorText, useTranslate } from "../i18n";
type Lesson = {
  id: string;
  title: string;
  source_url: string;
  limitation: string;
  cards: { title: string; text: string }[];
};
const EXPLAINED_STATES = ["full", "partial", "dark", "boundary", "unknown"];
async function api(path: string, post = false) {
  const response = await fetch(
      "/api" + path,
      post ? { method: "POST" } : undefined,
    ),
    body = await response.json();
  if (!response.ok)
    throw Error(
      typeof body.detail === "string" ? body.detail : "",
    );
  return body;
}
export default function Classroom({
  onOpen,
}: {
  onOpen: (id: string, compare: boolean) => Promise<void>;
}) {
  const { t, tText } = useTranslate();
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
      .catch((e) => active && setError(errorText(e, t, "classroom.unavailable")));
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
          setError(job.error || t("classroom.failed"));
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
          setError(errorText(e, t, "scenario.connection"));
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
      setError(errorText(e, t, "classroom.unableToStart"));
    } finally {
      setStarting(false);
    }
  };
  const frame = result?.frames[index];
  return (
    <section id="classroom" className="classroom-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t("classroom.eyebrow")}</p>
          <h2>
            {t("classroom.title")}<span>.</span>
          </h2>
          <p className="muted">{t("classroom.intro")}</p>
        </div>
        <span className="outline-tag">{t("classroom.tag")}</span>
      </div>
      <div className="lesson-options">
        {lessons.map((lesson) => (
          <article className="panel lesson-card" key={lesson.id}>
            <h3>{tText(lesson.title)}</h3>
            <p>
              {t("classroom.lessonMeta", {
                day: lesson.id === "later-start" ? "6" : "4",
              })}
            </p>
            <button
              disabled={starting}
              className="primary"
              onClick={() => start(lesson)}
            >
              {t("classroom.start")}:{" "}
              {lesson.id === "later-start"
                ? t("classroom.later")
                : t("classroom.changing")}{" "}
              ↗
            </button>
          </article>
        ))}
      </div>
      {error && (
        <div role="alert" className="alert">
          {error}{" "}
          <button onClick={() => setRetry((n) => n + 1)}>
            {t("classroom.reconnect")}
          </button>
        </div>
      )}
      {selected && !result && (
        <p role="status">
          {t(`jobState.${phase}`)} · {t("classroom.firstRun")}
        </p>
      )}
      {selected && result && frame && (
        <div className="lesson-stage">
          <article className="panel lesson-sky">
            <div className="panel-heading">
              <span>{tText(selected.title)}</span>
              <span>UTC</span>
            </div>
            <HorizonPlot result={result} frame={frame} />
            <div className="lesson-player">
              <strong>{frame.utc}</strong>
              <button
                aria-label={playing ? t("classroom.pause") : t("classroom.play")}
                onClick={() => {
                  if (index === result.frames.length - 1) setIndex(0);
                  setPlaying(!playing);
                }}
              >
                {playing ? `${t("classroom.pauseShort")} Ⅱ` : `${t("classroom.playShort")} ▷`}
              </button>
              <label>
                {t("classroom.speed")}
                <select
                  aria-label={t("classroom.speedLabel")}
                  value={speed}
                  onChange={(e) => setSpeed(e.target.value)}
                >
                  <option value="2000">{t("classroom.slow")}</option>
                  <option value="1000">{t("classroom.normal")}</option>
                  <option value="250">{t("classroom.fast")}</option>
                </select>
              </label>
              <input
                type="range"
                aria-label={t("classroom.time")}
                aria-valuetext={frame.utc}
                min="0"
                max={result.frames.length - 1}
                value={index}
                onChange={(e) => {
                  setPlaying(false);
                  setIndex(Number(e.target.value));
                }}
              />
              <p className="field-help">{t("classroom.playbackNote")}</p>
            </div>
          </article>
          <aside
            className="panel lesson-explanation"
            aria-label={t("classroom.explanationLabel")}
          >
            <p className="eyebrow">{t("classroom.atMoment")}</p>
            <h3>
              {t("common.sun")}: {t(`displayState.${frame.sun.state}`)}
            </h3>
            <p>
              {t(
                `classroom.explanation.${EXPLAINED_STATES.includes(frame.sun.state) ? frame.sun.state : "unknown"}`,
              )}
            </p>
            <h3>
              {t("common.earth")}: {t(`displayState.${frame.earth.state}`)}
            </h3>
            <p>
              {frame.earth.state === "visible"
                ? t("classroom.earthVisible")
                : frame.earth.state === "unknown"
                  ? t("classroom.earthUnknown")
                  : t("classroom.earthBlocked")}
            </p>
            <p>
              {t("classroom.across", {
                percent:
                  result.analysis.sun.metrics.visible_percent_of_interval.toFixed(1),
                hours: (
                  result.analysis.sun.metrics.longest_blocked_s / 3600
                ).toFixed(2),
              })}
            </p>
          </aside>
          <div className="lesson-cards">
            {selected.cards.map((card) => (
              <article className="panel" key={card.title}>
                <h3>{tText(card.title)}</h3>
                <p>{tText(card.text)}</p>
              </article>
            ))}
          </div>
          <p className="search-limitation">{tText(selected.limitation)}</p>
          <details className="panel lesson-details">
            <summary>{t("classroom.technical")}</summary>
            <p>
              {t("classroom.technicalDetail", {
                height: result.horizon.observer_height_above_terrain_m,
                radius: result.horizon.settings.search_distance_m,
                id: result.analysis.analysis_id,
              })}
            </p>
            <a href={selected.source_url} target="_blank" rel="noreferrer">
              {t("classroom.coordinateSource")} ↗
            </a>
            <p>{t("classroom.sourcesNote")}</p>
            <button
              onClick={() => {
                setPlaying(false);
                onOpen(id!, false).catch((e) => setError(e.message));
              }}
            >
              {t("classroom.openFull")} ↗
            </button>
          </details>
          <ExportPanel jobId={id!} result={result} frameIndex={index} />
        </div>
      )}
    </section>
  );
}
