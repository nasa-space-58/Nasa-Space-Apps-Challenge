import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { errorText, useTranslate } from "../i18n";
import "./assistant.css";

// Mission-control Q&A drawer. It only relays questions to the backend
// assistant and renders its answers and sources; no analysis logic lives here.
type Status = {
  enabled: boolean;
  model: string;
  reason: string | null;
  doc_sections: number;
};
type Source = { kind: "doc" | "api"; ref: string };
type Answer = {
  answer: string;
  sources: Source[];
  model: string;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
};
type Message =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; sources: Source[]; tokens: number }
  | { role: "error"; text: string };

const RECOMMENDATION_KEY = "lln.recommendation.id";
const MAX_HISTORY_TURNS = 10;

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : "";
    throw new Error(detail || "request-failed");
  }
  return body as T;
}

function sourceLabel(source: Source) {
  if (source.kind === "api") return source.ref;
  const [path, heading] = source.ref.split("#");
  return `${path.replace(/^docs\//, "")}${heading ? ` · ${heading}` : ""}`;
}

/** Completed user/assistant pairs only, so the backend's alternation rule holds. */
function historyFrom(messages: Message[]) {
  const turns: { role: "user" | "assistant"; text: string }[] = [];
  for (let i = 0; i < messages.length - 1; i += 1) {
    const current = messages[i];
    const next = messages[i + 1];
    if (current.role === "user" && next.role === "assistant") {
      turns.push({ role: "user", text: current.text });
      turns.push({ role: "assistant", text: next.text });
      i += 1;
    }
  }
  return turns.slice(-MAX_HISTORY_TURNS);
}

export default function AssistantPanel({ analysisIds }: { analysisIds: string[] }) {
  const { t } = useTranslate();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [statusError, setStatusError] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const launcherRef = useRef<HTMLButtonElement>(null);

  const recommendationId = (() => {
    try {
      return localStorage.getItem(RECOMMENDATION_KEY) || undefined;
    } catch {
      return undefined;
    }
  })();

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    call<Status>("/assistant/status")
      .then((value) => {
        if (!cancelled) {
          setStatus(value);
          setStatusError("");
        }
      })
      .catch((error) => {
        if (!cancelled) setStatusError(errorText(error, t, "assistant.statusFailed"));
      });
    const focus = setTimeout(() => inputRef.current?.focus(), 50);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      cancelled = true;
      clearTimeout(focus);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, t]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  const close = useCallback(() => {
    setOpen(false);
    launcherRef.current?.focus();
  }, []);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || sending || !status?.enabled) return;
    const history = historyFrom(messages);
    setMessages((current) => [...current, { role: "user", text: question }]);
    setDraft("");
    setSending(true);
    try {
      const answer = await call<Answer>("/assistant/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          analysis_ids: analysisIds.slice(0, 2),
          recommendation_id: recommendationId ?? null,
          history,
        }),
      });
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          text: answer.answer,
          sources: answer.sources,
          tokens: answer.input_tokens + answer.output_tokens,
        },
      ]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        { role: "error", text: errorText(error, t, "assistant.askFailed") },
      ]);
    } finally {
      setSending(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void send(draft);
  };

  const suggestions = analysisIds.length
    ? [
        t("assistant.suggest.explainSite"),
        analysisIds.length > 1
          ? t("assistant.suggest.compare")
          : t("assistant.suggest.darkness"),
        t("assistant.suggest.limits"),
      ]
    : [
        t("assistant.suggest.horizon"),
        t("assistant.suggest.sites"),
        t("assistant.suggest.data"),
      ];
  const ready = !!status?.enabled;
  const offline = status && !status.enabled;

  return (
    <>
      <button
        ref={launcherRef}
        type="button"
        className={`assistant-launcher${open ? " is-open" : ""}`}
        aria-expanded={open}
        aria-controls="assistant-panel"
        onClick={() => (open ? close() : setOpen(true))}
      >
        <span className="assistant-launcher-pulse" aria-hidden="true" />
        <span className="assistant-launcher-label">
          <small>{t("assistant.launcherEyebrow")}</small>
          {t("assistant.launcher")}
        </span>
      </button>
      {open && (
        <div className="assistant-scrim" onClick={close} aria-hidden="true" />
      )}
      <aside
        id="assistant-panel"
        className={`assistant-panel${open ? " is-open" : ""}`}
        role="dialog"
        aria-modal="false"
        aria-labelledby="assistant-title"
        hidden={!open}
      >
        <header className="assistant-head">
          <div>
            <p className="assistant-eyebrow">
              <i aria-hidden="true" /> {t("assistant.eyebrow")}
            </p>
            <h2 id="assistant-title">{t("assistant.title")}</h2>
          </div>
          <button
            type="button"
            className="assistant-close"
            onClick={close}
            aria-label={t("assistant.close")}
          >
            ×
          </button>
        </header>
        <div className="assistant-telemetry" aria-live="polite">
          <span className={`assistant-dot ${ready ? "on" : offline ? "off" : ""}`} />
          {statusError
            ? statusError
            : !status
              ? t("assistant.connecting")
              : ready
                ? t("assistant.online", { model: status.model, sections: status.doc_sections })
                : t("assistant.offline")}
        </div>
        <div className="assistant-context">
          <span>{t("assistant.contextLabel")}</span>
          {analysisIds.length ? (
            analysisIds.slice(0, 2).map((id, index) => (
              <code key={id}>
                {index === 0 ? "A" : "B"} · {id.slice(0, 8)}
              </code>
            ))
          ) : (
            <em>{t("assistant.noContext")}</em>
          )}
          {recommendationId && <code>REC · {recommendationId.slice(0, 8)}</code>}
        </div>

        <div className="assistant-log" ref={logRef}>
          {offline && (
            <div className="assistant-offline">
              <strong>{t("assistant.offlineTitle")}</strong>
              <p>{t("assistant.offlineBody")}</p>
              {status?.reason && <code>{status.reason}</code>}
            </div>
          )}
          {ready && messages.length === 0 && (
            <div className="assistant-intro">
              <p>{t("assistant.intro")}</p>
              <div className="assistant-suggestions">
                {suggestions.map((text) => (
                  <button key={text} type="button" onClick={() => void send(text)}>
                    {text}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((message, index) => (
            <article key={index} className={`assistant-msg ${message.role}`}>
              <span className="assistant-who">
                {message.role === "user"
                  ? t("assistant.you")
                  : message.role === "assistant"
                    ? t("assistant.navigator")
                    : t("assistant.signalLost")}
              </span>
              <p>{message.text}</p>
              {message.role === "assistant" && message.sources.length > 0 && (
                <ul className="assistant-sources" aria-label={t("assistant.sources")}>
                  {message.sources.map((source) => (
                    <li key={`${source.kind}:${source.ref}`} className={source.kind}>
                      <b>{source.kind === "doc" ? "DOC" : "API"}</b> {sourceLabel(source)}
                    </li>
                  ))}
                </ul>
              )}
              {message.role === "assistant" && (
                <small className="assistant-cost">
                  {t("assistant.tokens", { count: message.tokens })}
                </small>
              )}
            </article>
          ))}
          {sending && (
            <article className="assistant-msg assistant pending" aria-live="polite">
              <span className="assistant-who">{t("assistant.navigator")}</span>
              <p className="assistant-typing">
                <span />
                <span />
                <span />
                {t("assistant.thinking")}
              </p>
            </article>
          )}
        </div>

        <form className="assistant-form" onSubmit={submit}>
          <textarea
            ref={inputRef}
            value={draft}
            maxLength={2000}
            rows={2}
            disabled={!ready || sending}
            placeholder={ready ? t("assistant.placeholder") : t("assistant.placeholderOff")}
            aria-label={t("assistant.placeholder")}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send(draft);
              }
            }}
          />
          <button type="submit" disabled={!ready || sending || !draft.trim()}>
            {t("assistant.send")}
          </button>
        </form>
        <p className="assistant-disclaimer">{t("assistant.disclaimer")}</p>
      </aside>
    </>
  );
}
