import { useEffect, useRef, useState } from "react";
import { useTranslate } from "../i18n";
import "./mission-resilience.css";

type Candidate = { id: string; kind: string; feasible: boolean; reasons: string[]; duration_s: number; fuel_kg: number; power_w: number; physics?: { numerically_consistent: boolean; uncertainty: { pass_count: number; sample_count: number } } };
type Decision = { met_s: number; based_on_met_s: number; revision: number; status: string; selected: Candidate | null; candidates: Candidate[]; conditions: { valid_until_met_s: number; fuel_reserve_kg: number; energy_reserve_wh: number; temperature_limits_c: number[] } };
type State = { fuel_kg: number; energy_wh: number; temperature_c: number; available_thrust_n: number; link: boolean; hardware: string[]; blocked_targets: string[]; task_progress_s: Record<string, number>; navigation_bound_m?: number; sensor_conflict?: boolean };
type Frame = { met_s: number; phase: string; estimate: State; truth: State; executing: string | null; planning_pending: boolean; control_validated: boolean; control_reasons: string[]; navigation_channels_m: number[] };
type Observation = { event_id: string; met_s: number; kind: string; subject: string; value: number; status: string };
type Report = { status: string; scenario_id: string; reasons: string[]; frames: Frame[]; decisions: Decision[]; observations: Observation[]; completed_tasks: string[]; unfinished_tasks: { task_id: string; reason: string; progress_s: number }[]; limitations: string[]; source: { kind: string; description: string }; transitions: { phase: string; met_s: number }[]; input_sha256: string };
type Run = { id: string; status: string; scenario_id: string; result: Report | null; compute_s: number | null; error: string | null };
const pending = (status: string) => status === "queued" || status === "running";
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/resilience${path}`, init);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
  return body as T;
}

export default function MissionResilience({ active }: { active: boolean }) {
  const { t } = useTranslate();
  const [examples, setExamples] = useState<Record<string, unknown>>({});
  const [choice, setChoice] = useState("");
  const [editor, setEditor] = useState("");
  const [recent, setRecent] = useState<Run[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [index, setIndex] = useState(0);
  const [truth, setTruth] = useState(false);
  const generation = useRef(0);
  const sending = useRef(false);
  const retryCommand = useRef<{ input: string; request_id: string } | null>(null);
  useEffect(() => {
    if (!active) return;
    let alive = true;
    Promise.all([api<Record<string, unknown>>("/examples"), api<Run[]>("/runs")]).then(([cases, rows]) => {
      if (!alive) return;
      setExamples(cases); setRecent(rows);
      setChoice(previous => previous || Object.keys(cases)[0]);
      setEditor(previous => previous || JSON.stringify(Object.values(cases)[0], null, 2));
    }).catch(e => { if (alive) setError(String(e)); });
    return () => { alive = false; };
  }, [active]);
  useEffect(() => {
    if (!active || !run || !pending(run.status)) return;
    let alive = true;
    const current = generation.current;
    const poll = async () => {
      try {
        const next = await api<Run>(`/runs/${run.id}`);
        if (!alive || current !== generation.current) return;
        setRun(next);
        if (next.result) setIndex(next.result.frames.length - 1);
        if (!pending(next.status)) setRecent(await api<Run[]>("/runs"));
      } catch (e) { if (alive) setError(String(e)); }
    };
    const timer = window.setInterval(() => void poll(), 750);
    return () => { alive = false; clearInterval(timer); };
  }, [active, run?.id, run?.status]);
  async function submit() {
    if (sending.current) return;
    sending.current = true; setBusy(true); setError("");
    const current = ++generation.current;
    try {
      const scenario = JSON.parse(editor);
      if (retryCommand.current?.input !== editor) retryCommand.current = { input: editor, request_id: crypto.randomUUID() };
      const job = await api<Run>("/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_id: retryCommand.current.request_id, scenario, ttl_s: 120 }) });
      if (current === generation.current) { setRun(job); setIndex(0); }
      retryCommand.current = null;
    } catch (e) { setError(String(e)); }
    finally { sending.current = false; setBusy(false); }
  }
  async function open(id: string) {
    const current = ++generation.current;
    setError("");
    try { const job = await api<Run>(`/runs/${id}`); if (current === generation.current) { setRun(job); setIndex(job.result ? job.result.frames.length - 1 : 0); } }
    catch (e) { if (current === generation.current) setError(String(e)); }
  }
  async function cancel() {
    if (!run) return;
    const id = run.id, current = ++generation.current;
    setError("");
    try { const job = await api<Run>(`/runs/${id}/cancel`, { method: "POST" }); if (current === generation.current) setRun(job); }
    catch (e) { if (current === generation.current) setError(String(e)); }
  }
  const report = run?.result;
  const frame = report?.frames[Math.min(index, report.frames.length - 1)];
  const state = frame && (truth ? frame.truth : frame.estimate);
  const decisions = report?.decisions.filter(d => frame && d.met_s <= frame.met_s) || [];
  const decision = decisions[decisions.length - 1];
  const observations = report?.observations.filter(o => frame && o.met_s <= frame.met_s) || [];
  const label = (code: string) => { const translated = t(`resilience.codes.${code}`); return translated.startsWith("resilience.codes.") ? code.replaceAll("_", " ") : translated; };
  function download() {
    if (!run) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(run, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `mission-replay-${run.id}.json`; link.click(); URL.revokeObjectURL(url);
  }
  return <section className="resilience" aria-label={t("resilience.title")} aria-busy={busy}>
    <header className="resilience-heading"><div><p className="resilience-eyebrow">{t("resilience.eyebrow")}</p><h2>{t("resilience.title")}</h2><p>{t("resilience.intro")}</p></div><span className="resilience-badge">{t("resilience.classical")}</span></header>
    <div className="resilience-notice">{t("resilience.scope")}</div>
    <div className="resilience-controls">
      <label>{t("resilience.scenario")}<select value={choice} onChange={e => { setChoice(e.target.value); setEditor(JSON.stringify(examples[e.target.value], null, 2)); }}>
        {Object.keys(examples).map(name => <option key={name}>{name}</option>)}
      </select></label>
      <button className="resilience-primary" onClick={() => void submit()} disabled={busy || !editor || !!run && pending(run.status)}>{t("resilience.run")}</button>
      {run && pending(run.status) && <button onClick={() => void cancel()}>{t("resilience.cancel")}</button>}
      {report && <button onClick={download}>{t("resilience.export")}</button>}
    </div>
    <details className="resilience-input"><summary>{t("resilience.edit")}</summary><p>{t("resilience.editNote")}</p><textarea aria-label={t("resilience.input")} rows={14} spellCheck={false} value={editor} onChange={e => setEditor(e.target.value)} /></details>
    {error && <p role="alert" className="resilience-error">{error}</p>}
    {run && <p role="status" className="resilience-run-state">{t("resilience.runState")}: <strong>{label(run.status)}</strong>{run.compute_s !== null && ` · ${run.compute_s.toFixed(2)} s`}{run.error && ` · ${run.error}`}<button onClick={() => void open(run.id)}>{t("resilience.refresh")}</button></p>}
    {report && frame && state && <>
      <div className={`resilience-outcome ${report.status === "completed" ? "success" : "caution"}`}><strong>{label(report.status)}</strong><span>{report.reasons.map(label).join(" · ") || t("resilience.tasksComplete")}</span></div>
      <ol className="resilience-phases">{["launch", "cruise", "descent", "surface"].map(phase => <li key={phase} aria-current={frame.phase === phase ? "step" : undefined}><span>{t(`resilience.phase.${phase}`)}</span><small>{phase === "launch" || phase === "cruise" ? t("resilience.accounting") : phase === "descent" ? t("resilience.planar") : t("resilience.resources")}</small></li>)}</ol>
      <div className="resilience-playback"><label htmlFor="resilience-time">{t("resilience.time")} <strong>{frame.met_s.toFixed(2)} s</strong></label><input id="resilience-time" type="range" min={0} max={report.frames.length-1} value={index} onChange={e => setIndex(Number(e.target.value))} /><div><button onClick={() => setIndex(Math.max(0, index-1))} disabled={index === 0}>{t("resilience.previous")}</button><button onClick={() => setIndex(Math.min(report.frames.length-1, index+1))} disabled={index === report.frames.length-1}>{t("resilience.next")}</button><label><input type="checkbox" checked={truth} onChange={e => setTruth(e.target.checked)} />{t("resilience.truth")}</label></div></div>
      <p className="resilience-caption">{truth ? t("resilience.truthNote") : t("resilience.estimateNote")}</p>
      <div className="resilience-stats">
        <article><span>{t("resilience.fuel")}</span><strong>{state.fuel_kg.toFixed(3)} <small>kg</small></strong></article>
        <article><span>{t("resilience.energy")}</span><strong>{state.energy_wh.toFixed(3)} <small>Wh</small></strong></article>
        <article><span>{t("resilience.temperature")}</span><strong>{state.temperature_c.toFixed(2)} <small>°C</small></strong></article>
        <article><span>{t("resilience.thrust")}</span><strong>{state.available_thrust_n.toFixed(0)} <small>N</small></strong></article>
      </div>
      <div className="resilience-columns">
        <article className="resilience-card"><h3>{t("resilience.current")}</h3><p className="resilience-plan">{frame.executing ? label(frame.executing) : "—"}</p><p>{frame.control_validated ? t("resilience.checked") : t("resilience.unvalidated")}</p><p>{frame.control_reasons.map(label).join(" · ")}</p><p>{frame.planning_pending ? t("resilience.calculating") : t("resilience.noPending")}</p><dl><dt>{t("resilience.link")}</dt><dd>{state.link ? t("resilience.up") : t("resilience.down")}</dd><dt>{t("resilience.hardware")}</dt><dd>{state.hardware.join(", ") || "—"}</dd><dt>{t("resilience.uncertainty")}</dt><dd>±{frame.estimate.navigation_bound_m?.toFixed(2)} m{frame.estimate.sensor_conflict && ` · ${t("resilience.conflict")}`}</dd></dl></article>
        <article className="resilience-card"><h3>{t("resilience.observations")}</h3>{!observations.length && <p>{t("resilience.noEvents")}</p>}<ol className="resilience-events">{observations.map(o => <li key={o.event_id}><time>+{o.met_s.toFixed(1)} s</time><strong>{o.event_id}</strong><span>{label(o.kind)} · {o.subject}: {o.value}</span><small>{label(o.status)}</small></li>)}</ol></article>
      </div>
      {decision && <article className="resilience-card"><h3>{t("resilience.candidates")} · +{decision.met_s.toFixed(2)} s</h3><p>{label(decision.status)} · {t("resilience.snapshot")} +{decision.based_on_met_s.toFixed(2)} s · {t("resilience.revision")} {decision.revision}</p><div className="resilience-table"><table><thead><tr><th>{t("resilience.option")}</th><th>{t("resilience.decision")}</th><th>{t("resilience.duration")}</th><th>{t("resilience.constraints")}</th></tr></thead><tbody>{decision.candidates.map(c => <tr key={c.id}><th>{label(c.id)}{decision.selected?.id === c.id && <span className="resilience-chosen">{t("resilience.selected")}</span>}</th><td>{c.feasible ? t("resilience.allowed") : t("resilience.eliminated")}</td><td>{c.duration_s.toFixed(1)} s</td><td>{c.reasons.map(label).join(" · ") || t("resilience.checked")}{c.physics && <small>{t("resilience.probes")}: {c.physics.uncertainty.pass_count}/{c.physics.uncertainty.sample_count}</small>}</td></tr>)}</tbody></table></div><p className="resilience-caption">{t("resilience.conditions")}: {decision.conditions.fuel_reserve_kg} kg · {decision.conditions.energy_reserve_wh} Wh · {decision.conditions.temperature_limits_c.join(" … ")} °C. {t("resilience.recheck")}</p></article>}
      <article className="resilience-card"><h3>{t("resilience.history")}</h3><ol className="resilience-history">{decisions.map((d, n) => <li key={n}><time>+{d.met_s.toFixed(2)} s</time><strong>{label(d.status)}</strong><span>{d.selected ? label(d.selected.id) : "—"}</span></li>)}</ol></article>
      <article className="resilience-card"><h3>{t("resilience.taskOutcome")}</h3><p>{t("resilience.completed")}: {report.completed_tasks.join(", ") || "—"}</p>{report.unfinished_tasks.map(task => <p key={task.task_id}>{task.task_id} · {task.progress_s.toFixed(1)} s · {label(task.reason)}</p>)}</article>
      <details className="resilience-input"><summary>{t("resilience.assumptions")}</summary><p>{report.source.kind}: {report.source.description}</p><ul>{report.limitations.map(note => <li key={note}>{note}</li>)}</ul><code>{report.input_sha256}</code></details>
    </>}
    {!!recent.length && <details className="resilience-input"><summary>{t("resilience.archive")}</summary><ul>{recent.map(job => <li key={job.id}><button onClick={() => void open(job.id)}>{job.scenario_id} · {label(job.status)} · {job.id.slice(0,8)}</button></li>)}</ul></details>}
  </section>;
}
