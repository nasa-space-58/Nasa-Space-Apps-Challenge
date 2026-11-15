import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useTranslate } from "../i18n";
import "./unified-mission.css";

type Mode = "classical" | "local_experiment";
type Mission = {
  mission_id: string;
  status: string;
  site_id: string | null;
  landing_utc: string | null;
  required_bits: number;
  delivered_bits: number;
};
type Allocation = {
  mission_id: string;
  station_id: string;
  start_utc: string;
  end_utc: string;
  bits: number;
};
type Accepted = {
  job_id: string;
  revision: number;
  plan_id: string;
  snapshot_sha256: string;
  target_id: string;
  source: string;
  score_exact: string;
  simulation_utc: string;
  physical: {
    fine: { state: { propellant_kg: number }; downrange_m: number };
    uncertainty: { pass_count: number; sample_count: number };
  };
  timeline: { missions: Mission[]; allocations: Allocation[] };
};
type Method = {
  proposal_s: number;
  method: string;
  raw_counts: Record<string, number>;
  raw_local_feasible_count: number;
  skipped_by_completion_cap: string[];
  attempts: {
    option_ids: string[];
    status: string;
    reason: string | null;
    sample_count: number;
    completion_s: number;
    admission_s: number;
  }[];
};
type Job = {
  id: string;
  revision: number;
  mode: Mode;
  status: string;
  deadline_at: number;
  queue_s: number | null;
  compute_s: number | null;
  validation_s: number | null;
  error: string | null;
  comparison: {
    preparation_s: number;
    baseline_s: number;
    selection_reason: string;
    unsupported_reason: string | null;
    logical_qubits: number | null;
    hardware_jobs: number;
    methods: Method[];
    candidates: { target_id: string; status: string; reasons: string[] }[];
  } | null;
};
type Session = {
  id: string;
  revision: number;
  simulation_utc: string;
  snapshot_sha256: string;
  accepted: Accepted | null;
  history: Accepted[];
  active_job_id: string | null;
  jobs: Job[];
  events: {
    event_id: string;
    elapsed_s: number;
    start_downrange_m: number;
    end_downrange_m: number;
  }[];
};
type Recent = Pick<Session, "id" | "revision" | "simulation_utc">;
type State = {
  session: Session | null;
  recent: Recent[];
  busy: boolean;
  error: string;
  select: (id: string) => void;
  create: () => Promise<void>;
  submit: (
    mode: Mode,
    event?: {
      elapsed_s: number;
      start_downrange_m: number;
      end_downrange_m: number;
    },
  ) => Promise<void>;
};
const Context = createContext<State | null>(null);
const KEY = "lunar-unified-session-v1";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/unified/sessions${path}`, init);
  if (!response.ok) throw new Error(String(response.status));
  return response.json() as Promise<T>;
}
function remembered(): string {
  try {
    return localStorage.getItem(KEY) || "";
  } catch {
    return "";
  }
}

export function UnifiedPlanProvider({ children }: { children: ReactNode }) {
  const [id, setId] = useState(remembered);
  const [session, setSession] = useState<Session | null>(null);
  const [recent, setRecent] = useState<Recent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const mutation = useRef(false);
  const reads = useRef(0);
  const select = useCallback((next: string) => {
    generation.current++;
    reads.current++;
    setId(next);
    setSession(null);
    setError("");
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* Persistence is optional. */
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const epoch = generation.current;
    const poll = async () => {
      try {
        if (document.visibilityState !== "hidden") {
          const items = await api<Recent[]>("", { signal: controller.signal });
          if (!stopped && epoch === generation.current) setRecent(items);
          if (id && !mutation.current) {
            const read = ++reads.current;
            const view = await api<Session>(`/${id}`, {
              signal: controller.signal,
            });
            if (
              !stopped &&
              epoch === generation.current &&
              read === reads.current
            ) {
              setSession(view);
              setError("");
            }
          }
        }
      } catch (err) {
        if (!stopped && epoch === generation.current)
          setError(err instanceof Error ? err.message : "network");
      } finally {
        if (!stopped) timer = setTimeout(poll, 2000);
      }
    };
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [id]);

  const create = async () => {
    if (mutation.current) return;
    mutation.current = true;
    reads.current++;
    setBusy(true);
    setError("");
    try {
      const next = await api<Session>("", { method: "POST" });
      select(next.id);
      setSession(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "network");
    } finally {
      mutation.current = false;
      setBusy(false);
    }
  };
  const submit: State["submit"] = async (mode, event) => {
    if (!session || mutation.current) return;
    mutation.current = true;
    reads.current++;
    setBusy(true);
    setError("");
    const epoch = generation.current;
    const requestId = crypto.randomUUID();
    try {
      await api(`/${session.id}/${event ? "events" : "plans"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_id: requestId,
          expected_revision: session.revision,
          mode,
          ttl_s: 30,
          ...(event ? { ...event, event_id: requestId } : {}),
        }),
      });
      if (event && epoch === generation.current) setSession(null);
      const view = await api<Session>(`/${session.id}`);
      if (epoch === generation.current) setSession(view);
    } catch (err) {
      if (epoch === generation.current)
        setError(err instanceof Error ? err.message : "network");
    } finally {
      mutation.current = false;
      setBusy(false);
    }
  };
  return (
    <Context.Provider
      value={{ session, recent, busy, error, select, create, submit }}
    >
      {children}
    </Context.Provider>
  );
}

export default function UnifiedMissionWorkspace({
  view,
  children,
}: {
  view: "plan" | "recovery" | "comparison";
  children: ReactNode;
}) {
  const state = useContext(Context);
  const { t, locale } = useTranslate();
  const [independentOpen, setIndependentOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("classical");
  const [elapsed, setElapsed] = useState(8);
  const [start, setStart] = useState(-5);
  const [end, setEnd] = useState(5);
  if (!state) throw new Error("UnifiedPlanProvider is required");
  const { session, busy, error } = state;
  const plan = session?.accepted;
  const job = session?.jobs[0];
  const status = (value: string) => t(`unified.state.${value}`);
  const date = (value: string | null) =>
    value
      ? new Date(value).toLocaleTimeString(locale, {
          timeZone: "UTC",
          hour12: false,
        })
      : "—";
  const number = (value: unknown) =>
    typeof value === "number"
      ? value.toLocaleString(locale, { maximumFractionDigits: 3 })
      : "—";
  const lastEvent = session?.events[session.events.length - 1]?.elapsed_s ?? 0;
  const invalidEvent =
    !Number.isFinite(elapsed) ||
    elapsed <= lastEvent ||
    elapsed >= 10 ||
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    start >= end ||
    start < -50000 ||
    end > 50000;
  return (
    <section className="unified-workspace" aria-label={t("unified.title")}>
      <header className="unified-header">
        <div>
          <p className="unified-eyebrow">{t("unified.eyebrow")}</p>
          <h2>{t("unified.title")}</h2>
          <p>{t("unified.description")}</p>
        </div>
        <span className="unified-badge">{t("unified.synthetic")}</span>
      </header>
      <div className="unified-toolbar">
        <label>
          {t("unified.session")}
          <select
            value={session?.id || ""}
            disabled={busy}
            onChange={(e) => state.select(e.target.value)}
          >
            <option value="">{t("unified.choose")}</option>
            {session && !state.recent.some((s) => s.id === session.id) && (
              <option value={session.id}>{session.id.slice(0, 8)}</option>
            )}
            {state.recent.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id.slice(0, 8)} · r{s.revision} · {date(s.simulation_utc)}{" "}
                UTC
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={busy}
          onClick={() => void state.create()}
        >
          {t("unified.new")}
        </button>
        <label>
          {t("unified.mode")}
          <select
            value={mode}
            disabled={busy}
            onChange={(e) => setMode(e.target.value as Mode)}
          >
            <option value="classical">{t("unified.classical")}</option>
            <option value="local_experiment">{t("unified.local")}</option>
          </select>
        </label>
        <button
          className="unified-primary"
          type="button"
          disabled={!session || busy || !!error || !!session.active_job_id}
          onClick={() => void state.submit(mode)}
        >
          {t("unified.calculate")}
        </button>
      </div>
      {error && (
        <p role="alert" className="unified-error">
          {t(
            error === "404"
              ? "unified.missing"
              : error === "409"
                ? "unified.conflict"
                : error === "422"
                  ? "unified.invalid"
                  : "unified.error",
          )}
        </p>
      )}
      {!session ? (
        <p className="unified-empty">{t("unified.empty")}</p>
      ) : (
        <>
          <dl className="unified-metrics">
            <div>
              <dt>{t("unified.revision")}</dt>
              <dd>r{session.revision}</dd>
            </div>
            <div>
              <dt>{t("unified.clock")}</dt>
              <dd>{date(session.simulation_utc)} UTC</dd>
            </div>
            <div>
              <dt>{t("unified.target")}</dt>
              <dd>{plan?.target_id || "—"}</dd>
            </div>
            <div>
              <dt>{t("unified.current")}</dt>
              <dd className={plan ? "unified-good" : ""}>
                {plan ? t("unified.validated") : t("unified.noPlan")}
              </dd>
            </div>
            <div>
              <dt>{t("unified.source")}</dt>
              <dd>
                {plan
                  ? t(
                      plan.source === "classical"
                        ? "unified.classical"
                        : "unified.quantumCompleted",
                    )
                  : "—"}
              </dd>
            </div>
          </dl>
          <div className="unified-identity">
            <span>
              {t("unified.session")}: <code>{session.id}</code>
            </span>
            <span>
              {t("unified.plan")}:{" "}
              <code>{plan?.plan_id.slice(0, 16) || "—"}</code>
            </span>
            <span>
              {t("unified.input")}:{" "}
              <code>{session.snapshot_sha256.slice(0, 16)}</code>
            </span>
          </div>
          <p className="unified-note">{t("unified.clockNote")}</p>
          {job && (
            <div className="unified-job" aria-live="polite">
              <strong>{status(job.status)}</strong>
              <span>
                {t("unified.revision")}: r{job.revision}
              </span>
              <span>
                {t("unified.queue")}: {number(job.queue_s)} s
              </span>
              <span>
                {t("unified.compute")}: {number(job.compute_s)} s
              </span>
              <span>
                {t("unified.validation")}: {number(job.validation_s)} s
              </span>
              {session.active_job_id && (
                <span>
                  {t("unified.deadline")}:{" "}
                  {new Date(job.deadline_at * 1000).toLocaleTimeString(locale)}
                </span>
              )}
            </div>
          )}
          {view === "recovery" && (
            <form
              className="unified-event"
              onSubmit={(e) => {
                e.preventDefault();
                void state.submit(mode, {
                  elapsed_s: elapsed,
                  start_downrange_m: start,
                  end_downrange_m: end,
                });
              }}
            >
              <h3>{t("unified.event")}</h3>
              <p>{t("unified.eventNote")}</p>
              <div className="unified-toolbar">
                <label>
                  {t("unified.elapsed")}
                  <input
                    type="number"
                    step="0.1"
                    min={lastEvent + 0.1}
                    max="9.9"
                    value={Number.isFinite(elapsed) ? elapsed : ""}
                    onChange={(e) => setElapsed(e.target.valueAsNumber)}
                    required
                  />
                </label>
                <label>
                  {t("unified.start")}
                  <input
                    type="number"
                    min="-50000"
                    max="50000"
                    value={Number.isFinite(start) ? start : ""}
                    onChange={(e) => setStart(e.target.valueAsNumber)}
                    required
                  />
                </label>
                <label>
                  {t("unified.end")}
                  <input
                    type="number"
                    min="-50000"
                    max="50000"
                    value={Number.isFinite(end) ? end : ""}
                    onChange={(e) => setEnd(e.target.valueAsNumber)}
                    required
                  />
                </label>
                <button
                  type="submit"
                  disabled={busy || !!error || invalidEvent}
                >
                  {t("unified.inject")}
                </button>
              </div>
              {session.events.length > 0 && (
                <ul>
                  {session.events.map((e) => (
                    <li key={e.event_id}>
                      {e.elapsed_s} s · [{e.start_downrange_m},{" "}
                      {e.end_downrange_m}] m ·{" "}
                      <code>{e.event_id.slice(0, 8)}</code>
                    </li>
                  ))}
                </ul>
              )}
            </form>
          )}
          {plan && view !== "comparison" && (
            <>
              <h3>{t("unified.timeline")}</h3>
              <div className="unified-table">
                <table>
                  <thead>
                    <tr>
                      <th>{t("unified.mission")}</th>
                      <th>{t("unified.status")}</th>
                      <th>{t("unified.site")}</th>
                      <th>{t("unified.landing")}</th>
                      <th>{t("unified.data")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.timeline.missions.map((m) => (
                      <tr key={m.mission_id}>
                        <th scope="row">{m.mission_id}</th>
                        <td>{status(m.status)}</td>
                        <td>{m.site_id || "—"}</td>
                        <td>{date(m.landing_utc)} UTC</td>
                        <td>
                          {number(m.delivered_bits)} / {number(m.required_bits)}{" "}
                          bit
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <h3>{t("unified.links")}</h3>
              <div className="unified-table">
                <table>
                  <thead>
                    <tr>
                      <th>{t("unified.mission")}</th>
                      <th>{t("unified.station")}</th>
                      <th>{t("unified.interval")}</th>
                      <th>bit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.timeline.allocations.map((a, i) => (
                      <tr key={i}>
                        <th scope="row">{a.mission_id}</th>
                        <td>{a.station_id}</td>
                        <td>
                          {date(a.start_utc)} – {date(a.end_utc)} UTC
                        </td>
                        <td>{number(a.bits)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {view === "recovery" && (
                <dl className="unified-metrics">
                  <div>
                    <dt>{t("unified.fuel")}</dt>
                    <dd>{number(plan.physical.fine.state.propellant_kg)} kg</dd>
                  </div>
                  <div>
                    <dt>{t("unified.samples")}</dt>
                    <dd>
                      {number(plan.physical.uncertainty.pass_count)} /{" "}
                      {number(plan.physical.uncertainty.sample_count)}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("unified.position")}</dt>
                    <dd>{number(plan.physical.fine.downrange_m)} m</dd>
                  </div>
                </dl>
              )}
            </>
          )}
          {view === "comparison" && (
            <>
              <h3>{t("unified.comparison")}</h3>
              <p>{t("unified.comparisonNote")}</p>
              {job?.comparison ? (
                <>
                  <p>
                    {t(`unified.reason.${job.comparison.selection_reason}`)}
                  </p>
                  {job.comparison.unsupported_reason && (
                    <p className="unified-note">
                      {job.comparison.unsupported_reason}
                    </p>
                  )}
                  <p>
                    {t("unified.preparation")}:{" "}
                    {number(job.comparison.preparation_s)} s ·{" "}
                    {t("unified.baseline")}: {number(job.comparison.baseline_s)}{" "}
                    s
                  </p>
                  <p>
                    {t("unified.qubits")}:{" "}
                    {job.comparison.logical_qubits ?? "—"} ·{" "}
                    {t("unified.hardware")}: {job.comparison.hardware_jobs}
                  </p>
                  <div className="unified-methods">
                    {job.comparison.methods.map((m) => (
                      <article key={m.method}>
                        <h4>{m.method.toUpperCase()}</h4>
                        <p>
                          {t("unified.proposal")}: {number(m.proposal_s)} s
                        </p>
                        <p>
                          {t("unified.rawFeasible")}:{" "}
                          {m.raw_local_feasible_count}
                        </p>
                        <p>
                          {t("unified.rawCounts")}:{" "}
                          {Object.entries(m.raw_counts)
                            .map(([key, n]) => `${key}: ${n}`)
                            .join(" · ")}
                        </p>
                        <ul>
                          {m.attempts.map((a, i) => (
                            <li key={i}>
                              {a.option_ids.join(", ")} · {a.sample_count} ·{" "}
                              {status(a.status)}
                              <small>
                                {t("unified.completion")}:{" "}
                                {number(a.completion_s)} s ·{" "}
                                {t("unified.validation")}:{" "}
                                {number(a.admission_s)} s
                              </small>
                              {a.reason && <small>{a.reason}</small>}
                            </li>
                          ))}
                        </ul>
                        <p>
                          {t("unified.skipped")}:{" "}
                          {m.skipped_by_completion_cap.length}
                        </p>
                      </article>
                    ))}
                  </div>
                </>
              ) : (
                <p>{t("unified.noComparison")}</p>
              )}
            </>
          )}
          {job?.comparison && (
            <details>
              <summary>{t("unified.candidates")}</summary>
              <ul>
                {job.comparison.candidates.map((c) => (
                  <li key={c.target_id}>
                    {c.target_id} · {status(c.status)}
                    {c.reasons.length > 0 && (
                      <small>{c.reasons.join("; ")}</small>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {session.history.length > 0 && (
            <details className="unified-history">
              <summary>
                {t("unified.history")} ({session.history.length})
              </summary>
              <p>{t("unified.historyNote")}</p>
              <ul>
                {session.history.map((p, i) => (
                  <li key={i}>
                    r{p.revision} · {p.target_id} ·{" "}
                    <code>{p.plan_id.slice(0, 16)}</code>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
      <details
        className="unified-independent"
        onToggle={(e) => setIndependentOpen(e.currentTarget.open)}
      >
        <summary>{t("unified.independent")}</summary>
        <p>{t("unified.independentNote")}</p>
        {independentOpen && children}
      </details>
    </section>
  );
}
