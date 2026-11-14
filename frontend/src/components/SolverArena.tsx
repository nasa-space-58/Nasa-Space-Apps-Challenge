import { useEffect, useRef, useState } from "react";
import { useTranslate } from "../i18n";
import "./solver-arena.css";

type Controls = { preset_id: string; depth: number; evaluations: number; shots: number; seed: number };
type Plan = { selected_option_ids: string[] };
type Validation = { status: string; score: number; issues: { message: string }[] };
type ClassicalStep = { evaluated: number; feasible_count: number; best_option_ids: string[]; best_exact_score: string | null; elapsed_s: number };
type QuantumStep = { iteration: number; probabilities: number[]; best_probabilities: number[]; energy: number; best_energy: number; elapsed_s: number };
type Sample = { basis_index: number; probability: number; count: number; selected_option_ids: string[]; feasible: boolean; optimal: boolean };
type Run = {
  input_fingerprint: string; request: Controls; preparation_s: number; total_elapsed_s: number;
  classical: { elapsed_s: number; score: number | null; optimum_gap: number | null; trace: ClassicalStep[];
    result: { status: string; evaluated_combinations: number; total_combinations: number; feasible_count: number; plan: Plan | null; issues: {message: string}[] } };
  quantum: { status: string; reason: string | null; elapsed_s: number; trace: QuantumStep[]; score: number | null; optimum_gap: number | null;
    validation: Validation | null; result: null | { logical_qubits: number; samples: Sample[]; sampled_feasible_rate: number; sampled_optimal_rate: number; uniform_optimal_probability: number; best_sampled_plan: Plan | null } };
};
type Hardware = { kind: string; job_id: string; backend_code: string; input_fingerprint: string; shots: number; counts: Record<string, number>;
  sampled_feasible_rate: number; sampled_optimal_rate: number; optimum_gap: number | null; credits_spent: number; observed_elapsed_s: number;
  submitted_utc: string; depth: number; training_seed: number; training_evaluations: number; attribution_url: string; };

const number = (value: number | null | undefined, digits = 3) => value == null ? "—" : value.toFixed(digits);
const percent = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;

export default function SolverArena({ active }: { active: boolean }) {
  const { t } = useTranslate();
  const [presets, setPresets] = useState<string[]>([]);
  const [controls, setControls] = useState<Controls>({ preset_id: "recovery", depth: 2, evaluations: 80, shots: 512, seed: 17 });
  const [result, setResult] = useState<Run | null>(null);
  const [hardware, setHardware] = useState<Hardware | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [incumbent, setIncumbent] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const wasExpanded = useRef(false);
  const frames = Math.max(result?.quantum.trace.length ?? 0, result?.classical.trace.length ?? 0, 1);

  useEffect(() => {
    if (!active) {
      controller.current?.abort(); generation.current += 1;
      setRunning(false); setPlaying(false); setExpanded(false);
      return;
    }
    setRunning(false);
    const request = new AbortController();
    fetch("/api/arena/presets", { signal: request.signal })
      .then(async response => {
        if (!response.ok) throw Error(t("arena.connectionError"));
        return response.json();
      })
      .then(body => { if (!request.signal.aborted) setPresets(body.presets.map((p: { id: string }) => p.id)); })
      .catch(e => { if (!request.signal.aborted) setError(e.message); });
    return () => { request.abort(); controller.current?.abort(); generation.current += 1; };
  }, [active, t]);

  useEffect(() => {
    if (expanded && dialog.current) { dialog.current.showModal(); wasExpanded.current = true; }
    else if (wasExpanded.current && active) { expandButton.current?.focus(); wasExpanded.current = false; }
  }, [expanded, active]);

  useEffect(() => {
    if (!playing || !active || document.hidden) return;
    const timer = window.setInterval(() => setCursor(value => {
      if (value >= frames - 1) { setPlaying(false); return value; }
      return value + 1;
    }), 120);
    return () => window.clearInterval(timer);
  }, [playing, active, frames]);

  async function request(path: string, body?: Controls) {
    controller.current?.abort();
    const next = new AbortController(); controller.current = next;
    const response = await fetch(`/api/arena/${path}`, {
      signal: next.signal, ...(body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    const payload = await response.json();
    if (!response.ok) throw Error(typeof payload.detail === "string" ? payload.detail : t("arena.invalidRequest"));
    return payload;
  }

  async function run() {
    if (running) return;
    const id = ++generation.current;
    setRunning(true); setError(""); setPlaying(false); setCursor(0); setResult(null); setHardware(null);
    try {
      const value: Run = await request("compare", controls);
      if (id === generation.current) setResult(value);
    } catch (e) {
      if (id === generation.current && !(e instanceof DOMException && e.name === "AbortError")) setError(e instanceof Error ? e.message : t("arena.connectionError"));
    } finally { if (id === generation.current) setRunning(false); }
  }

  async function archive() {
    if (running) return;
    const id = ++generation.current; setRunning(true); setError("");
    try {
      const value: Hardware = await request("hardware");
      if (id === generation.current) setHardware(value);
    } catch (e) {
      if (id === generation.current && !(e instanceof DOMException && e.name === "AbortError")) setError(e instanceof Error ? e.message : t("arena.connectionError"));
    } finally { if (id === generation.current) setRunning(false); }
  }

  function download() {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify({ comparison: result, recorded_hardware: hardware }, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `arena-${result.request.preset_id}-${result.request.seed}.json`; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // Synchronize recorded progress, not execution time: the algorithms use different step counts.
  const at = (length: number) => Math.min(length - 1, Math.floor(cursor / Math.max(1, frames - 1) * Math.max(0, length - 1)));
  const classical = result?.classical.trace[at(result.classical.trace.length)];
  const quantum = result?.quantum.trace[at(result.quantum.trace.length)];
  const probabilities = quantum ? incumbent ? quantum.best_probabilities : quantum.probabilities : [];
  const samples = result?.quantum.result?.samples ?? [];
  const dirty = result && JSON.stringify(result.request) !== JSON.stringify(controls);
  const selected = (ids: string[] | undefined) => ids?.length ? ids.join(" + ") : t("arena.none");

  const content = <section className="solver-arena" aria-labelledby="arena-title" aria-busy={running}>
    <header className="arena-header">
      <div><p className="eyebrow">H8 / {t("arena.eyebrow")}</p><h2 id="arena-title">{t("arena.title")}</h2></div>
      <button type="button" ref={!expanded ? expandButton : undefined} onClick={() => setExpanded(!expanded)}>{t(expanded ? "arena.close" : "arena.expand")}</button>
    </header>
    <p className="arena-intro">{t("arena.intro")}</p>
    <div className="arena-tags"><span>{t("arena.simulation")}</span><span>{t("arena.synthetic")}</span><span>{t("arena.noCredits")}</span></div>
    <form className="arena-controls" onSubmit={e => { e.preventDefault(); void run(); }}>
      <fieldset disabled={running}>
        <label className="arena-preset">{t("arena.scenario")}<select value={controls.preset_id} onChange={e => setControls({ ...controls, preset_id: e.target.value })}>
          {(presets.length ? presets : ["recovery"]).map(id => <option key={id} value={id}>{t(`arena.presets.${id}`)}</option>)}
        </select></label>
        {([ ["depth", 1, 3], ["evaluations", 1, 256], ["shots", 1, 8192], ["seed", 0, 4294967295] ] as const).map(([key, min, max]) =>
          <label key={key}>{t(`arena.${key}`)}<input type="number" min={min} max={max} step="1" required value={Number.isNaN(controls[key]) ? "" : controls[key]} onChange={e => setControls({ ...controls, [key]: e.target.valueAsNumber })} /></label>)}
        <button className="primary" type="submit">{t(running ? "arena.running" : "arena.run")}</button>
      </fieldset>
    </form>
    {error && <p className="alert" role="alert">{error}</p>}
    <p role="status" className="arena-status">{running ? t("arena.working") : result ? t("arena.completed") : t("arena.ready")}</p>
    {!result && !running && <div className="arena-empty"><span aria-hidden="true">01 ↔ 11</span><h3>{t("arena.emptyTitle")}</h3><p>{t("arena.emptyBody")}</p></div>}
    {result && <>
      <div className="arena-run-info"><strong>{t(`arena.presets.${result.request.preset_id}`)}</strong><small>{t("arena.runInfo", { seed: result.request.seed, shots: result.request.shots, depth: result.request.depth, evaluations: result.request.evaluations })}</small></div>
      {dirty && <p className="arena-note">{t("arena.changedInputs")}</p>}
      <div className="arena-playback">
        <div><h3>{t("arena.playback")}</h3><p>{t("arena.playbackNote")}</p></div>
        <button type="button" disabled={frames < 2} onClick={() => { if (cursor === frames - 1) setCursor(0); setPlaying(!playing); }}>{t(playing ? "arena.pause" : "arena.play")}</button>
        <button type="button" disabled={cursor === 0} onClick={() => { setPlaying(false); setCursor(0); }}>{t("arena.reset")}</button>
        <input aria-label={t("arena.step")} type="range" min="0" max={frames - 1} value={cursor} disabled={frames < 2} onChange={e => { setPlaying(false); setCursor(Number(e.target.value)); }} />
        <output>{cursor + 1} / {frames}</output>
      </div>
      <div className="arena-lanes">
        <article className="arena-lane classical"><p className="eyebrow">01 / {t("arena.reference")}</p><h3>{t("arena.classical")}</h3>
          <div className="arena-hero-number">{classical?.evaluated ?? 0}<small> / {result.classical.result.total_combinations}</small></div>
          <p>{t("arena.examined")}</p>
          <progress aria-label={t("arena.examined")} max={Math.max(1, result.classical.result.total_combinations)} value={classical?.evaluated ?? 0} />
          <dl><dt>{t("arena.feasibleExamined")}</dt><dd>{classical?.feasible_count ?? 0}</dd><dt>{t("arena.stepTime")}</dt><dd>{number(classical?.elapsed_s)} s</dd></dl>
          <h4>{t("arena.incumbent")}</h4><p className="arena-plan">{selected(classical?.best_option_ids)}</p>
          <p>{t(`arena.status.${result.classical.result.status}`)}</p>
          {result.classical.result.issues.map((issue, i) => <p className="arena-note" key={i}>{issue.message}</p>)}
        </article>
        <article className="arena-lane quantum"><p className="eyebrow">02 / {t("arena.simulation")}</p><h3>QAOA</h3>
          {result.quantum.result ? <>
            <div className="arena-hero-number">{result.quantum.result.logical_qubits}<small> {t("arena.qubits")}</small></div>
            <p>{t("arena.iteration", { current: quantum?.iteration ?? 0, total: result.request.evaluations })}</p>
            <label className="arena-checkbox"><input type="checkbox" checked={incumbent} onChange={e => setIncumbent(e.target.checked)} />{t("arena.bestDistribution")}</label>
            <div className="arena-bars" role="img" aria-label={t("arena.distributionLabel")}>
              {samples.map((sample, i) => <div key={sample.basis_index} className={`arena-bar ${sample.optimal ? "optimal" : sample.feasible ? "feasible" : "invalid"}`} title={`${sample.basis_index}: ${percent(probabilities[i])} · ${selected(sample.selected_option_ids)}`}>
                <div className="arena-bar-track"><i style={{ height: `${(probabilities[i] ?? 0) * 100}%` }} /></div><small>{sample.basis_index}</small>
              </div>)}
            </div>
            <p className="arena-legend"><span className="optimal">{t("arena.optimal")}</span><span className="feasible">{t("arena.feasible")}</span><span className="invalid">{t("arena.invalid")}</span></p>
            <p className="arena-note">{t("arena.probabilityNote")}</p>
            <dl><dt>{t("arena.normalizedEnergy")}</dt><dd>{number(incumbent ? quantum?.best_energy : quantum?.energy)}</dd><dt>{t("arena.stepTime")}</dt><dd>{number(quantum?.elapsed_s)} s</dd></dl>
          </> : <p className="arena-note">{t("arena.notRun")} {result.classical.result.status !== "optimal" ? t(`arena.status.${result.classical.result.status}`) : result.quantum.reason}</p>}
        </article>
      </div>
      <section className="arena-scoreboard"><h3>{t("arena.scoreboard")}</h3><p>{t("arena.scoreboardNote")}</p>
        <div className="arena-table-scroll"><table><thead><tr><th>{t("arena.metric")}</th><th>{t("arena.classical")}</th><th>QAOA</th></tr></thead><tbody>
          <tr><th>{t("arena.score")}</th><td>{number(result.classical.score)}</td><td>{number(result.quantum.score)}</td></tr>
          <tr><th>{t("arena.gap")}</th><td>{number(result.classical.optimum_gap)}</td><td>{number(result.quantum.optimum_gap)}</td></tr>
          <tr><th>{t("arena.runTime")}</th><td>{number(result.classical.elapsed_s)} s</td><td>{number(result.quantum.elapsed_s)} s</td></tr>
          <tr><th>{t("arena.feasibleShots")}</th><td>{t("arena.notSampling")}</td><td>{percent(result.quantum.result?.sampled_feasible_rate)}</td></tr>
          <tr><th>{t("arena.optimalShots")}</th><td>{t("arena.notSampling")}</td><td>{percent(result.quantum.result?.sampled_optimal_rate)}</td></tr>
        </tbody></table></div>
        <p>{t("arena.totalTime", { preparation: number(result.preparation_s), total: number(result.total_elapsed_s) })}</p>
        <p className="arena-note">{t("arena.timingNote")}</p>
        <div className="arena-final-plans"><p><strong>{t("arena.classical")}</strong> {selected(result.classical.result.plan?.selected_option_ids)}</p><p><strong>QAOA</strong> {selected(result.quantum.result?.best_sampled_plan?.selected_option_ids)}</p></div>
        <p className="arena-caution">{t("arena.noAdvantage")}</p>
      </section>
      {samples.length > 0 && <details className="arena-details"><summary>{t("arena.rawCounts")}</summary><div className="arena-table-scroll"><table><thead><tr><th>{t("arena.plan")}</th><th>{t("arena.probability")}</th><th>{t("arena.count")}</th><th>{t("arena.validation")}</th></tr></thead><tbody>
        {samples.map((sample, i) => <tr key={sample.basis_index}><td>{sample.basis_index}: {selected(sample.selected_option_ids)}</td><td>{percent(probabilities[i])}</td><td>{sample.count}</td><td>{t(sample.optimal ? "arena.optimal" : sample.feasible ? "arena.feasible" : "arena.invalid")}</td></tr>)}
      </tbody></table></div></details>}
      <div className="arena-actions"><button type="button" onClick={download}>{t("arena.export")}</button>{result.request.preset_id === "recovery" && <button type="button" disabled={running || !!hardware} onClick={() => void archive()}>{t("arena.loadHardware")}</button>}</div>
      {hardware && <section className="arena-hardware"><p className="eyebrow">{t("arena.archive")}</p><h3>Rigetti · {t("arena.hardware")}</h3><p>{t("arena.hardwareNote")}</p>
        <dl><dt>{t("arena.optimalShots")}</dt><dd>{percent(hardware.sampled_optimal_rate)}</dd><dt>{t("arena.feasibleShots")}</dt><dd>{percent(hardware.sampled_feasible_rate)}</dd><dt>{t("arena.gap")}</dt><dd>{number(hardware.optimum_gap)}</dd><dt>{t("arena.observedTime")}</dt><dd>{number(hardware.observed_elapsed_s)} s</dd><dt>{t("arena.historicalCost")}</dt><dd>{hardware.credits_spent} Spark</dd></dl>
        <p>{t("arena.runInfo", { seed: hardware.training_seed, shots: hardware.shots, depth: hardware.depth, evaluations: hardware.training_evaluations })}</p>
        <p>{hardware.submitted_utc} · {t("arena.counts")}: {Object.entries(hardware.counts).map(([key, count]) => `${key}: ${count}`).join(" · ")}</p>
        <p className="arena-id">{t("arena.job")}: {hardware.job_id}</p>
        <a href={hardware.attribution_url} target="_blank" rel="noreferrer">Open Quantum · {t("arena.attribution")}</a>
      </section>}
      <details className="arena-details"><summary>{t("arena.identity")}</summary><code className="arena-id">{result.input_fingerprint}</code></details>
    </>}
  </section>;
  return expanded ? <dialog className="arena-dialog" aria-labelledby="arena-title" ref={dialog} onCancel={() => setExpanded(false)}>{content}</dialog> : content;
}
