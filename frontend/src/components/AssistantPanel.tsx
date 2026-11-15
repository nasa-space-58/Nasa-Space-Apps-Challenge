import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { errorText, useTranslate } from "../i18n";
import "./assistant.css";

// Q&A drawer. It only relays questions to the backend assistant and renders
// its answers and sources; no analysis logic lives here.
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
  return `${path.replace(/^docs\//, "").replace(/\.md$/, "")}${heading ? ` · ${heading}` : ""}`;
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

/** Inline **bold** and `code` as React nodes; never injects HTML. */
function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4)
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2)
      return <code key={index}>{part.slice(1, -1)}</code>;
    return part;
  });
}

/** Minimal Markdown subset: paragraphs, headings, bullet and numbered lists. */
function RichText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let paragraph: string[] = [];
  const flushParagraph = () => {
    if (paragraph.length)
      blocks.push(<p key={blocks.length}>{inline(paragraph.join(" "))}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    const items = list.items.map((item, index) => <li key={index}>{inline(item)}</li>);
    blocks.push(
      list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>,
    );
    list = null;
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const ordered = !!numbered;
      if (list && list.ordered !== ordered) flushList();
      list ??= { ordered, items: [] };
      list.items.push((bullet ?? numbered)![1]);
    } else if (!line) {
      flushParagraph();
      flushList();
    } else if (heading) {
      flushParagraph();
      flushList();
      blocks.push(<h4 key={blocks.length}>{inline(heading[1])}</h4>);
    } else if (list && raw.startsWith(" ")) {
      list.items[list.items.length - 1] += ` ${line}`;
    } else {
      flushList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  return <div className="assistant-rich">{blocks}</div>;
}

function Spark() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 2c.4 4.9 2.6 8.1 7.5 9.5L22 12l-2.5.5C14.6 13.9 12.4 17.1 12 22c-.4-4.9-2.6-8.1-7.5-9.5L2 12l2.5-.5C9.4 10.1 11.6 6.9 12 2Z"
      />
    </svg>
  );
}

function Sources({ sources, tokens }: { sources: Source[]; tokens: number }) {
  const { t } = useTranslate();
  const [open, setOpen] = useState(false);
  if (!sources.length) return null;
  return (
    <div className="assistant-sources">
      <button
        type="button"
        className="assistant-sources-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {t("assistant.sourcesToggle", { count: sources.length })}
        <span className={`assistant-chevron${open ? " up" : ""}`} aria-hidden="true" />
      </button>
      {open && (
        <ul>
          {sources.map((source) => (
            <li key={`${source.kind}:${source.ref}`}>
              <span className={`assistant-kind ${source.kind}`}>
                {source.kind === "doc" ? t("assistant.kindDoc") : t("assistant.kindApi")}
              </span>
              {sourceLabel(source)}
            </li>
          ))}
          <li className="assistant-cost">{t("assistant.tokens", { count: tokens })}</li>
        </ul>
      )}
    </div>
  );
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
  const offline = !!status && !status.enabled;
  const linked = analysisIds.length + (recommendationId ? 1 : 0);

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
        <Spark />
        {t("assistant.launcher")}
      </button>
      {/* The launcher lives in the blurred header, which would become the
          containing block for fixed children, so the drawer is portaled. */}
      {createPortal(
        <>
      <aside
        id="assistant-panel"
        className={`assistant-panel${open ? " is-open" : ""}`}
        role="dialog"
        aria-modal="false"
        aria-labelledby="assistant-title"
        hidden={!open}
      >
        <header className="assistant-head">
          <span className="assistant-logo">
            <Spark />
          </span>
          <div className="assistant-heading">
            <h2 id="assistant-title">{t("assistant.title")}</h2>
            <p aria-live="polite">
              <span className={`assistant-dot${ready ? " on" : offline ? " off" : ""}`} />
              {statusError
                ? statusError
                : !status
                  ? t("assistant.connecting")
                  : ready
                    ? linked
                      ? t("assistant.onlineLinked", { count: linked })
                      : t("assistant.onlineDocs")
                    : t("assistant.offline")}
            </p>
          </div>
          <button
            type="button"
            className="assistant-close"
            onClick={close}
            aria-label={t("assistant.close")}
          >
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
              <path
                fill="currentColor"
                d="M18.3 5.7 12 12l6.3 6.3-1.4 1.4L10.6 13.4 4.3 19.7 2.9 18.3 9.2 12 2.9 5.7l1.4-1.4 6.3 6.3 6.3-6.3z"
              />
            </svg>
          </button>
        </header>

        <div className="assistant-log" ref={logRef}>
          {offline && (
            <div className="assistant-notice">
              <strong>{t("assistant.offlineTitle")}</strong>
              <p>{t("assistant.offlineBody")}</p>
            </div>
          )}
          {ready && messages.length === 0 && (
            <div className="assistant-welcome">
              <h3>{t("assistant.greeting")}</h3>
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
          {messages.map((message, index) =>
            message.role === "user" ? (
              <div key={index} className="assistant-msg user">
                <p>{message.text}</p>
              </div>
            ) : (
              <div key={index} className={`assistant-msg ${message.role}`}>
                <span className="assistant-avatar">
                  <Spark />
                </span>
                <div className="assistant-body">
                  {message.role === "assistant" ? (
                    <RichText text={message.text} />
                  ) : (
                    <p>{message.text}</p>
                  )}
                  {message.role === "assistant" && (
                    <Sources sources={message.sources} tokens={message.tokens} />
                  )}
                </div>
              </div>
            ),
          )}
          {sending && (
            <div className="assistant-msg assistant" aria-live="polite">
              <span className="assistant-avatar thinking">
                <Spark />
              </span>
              <div className="assistant-body">
                <p className="assistant-pending">{t("assistant.thinking")}</p>
              </div>
            </div>
          )}
        </div>

        <form className="assistant-form" onSubmit={submit}>
          <div className="assistant-input">
            <textarea
              ref={inputRef}
              value={draft}
              maxLength={2000}
              rows={1}
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
            <button
              type="submit"
              disabled={!ready || sending || !draft.trim()}
              aria-label={t("assistant.send")}
            >
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path fill="currentColor" d="M3 20.5v-6.3L11 12 3 9.8V3.5L22 12z" />
              </svg>
            </button>
          </div>
          <p className="assistant-disclaimer">{t("assistant.disclaimer")}</p>
        </form>
      </aside>
        </>,
        document.body,
      )}
    </>
  );
}
