import { useEffect, useRef, useState } from "react";
import { useTranslate } from "../i18n";
import SolverArena, { type Run } from "./SolverArena";
import "./shared-antenna.css";

type Window = { start_utc: string; end_utc: string };
type Site = { site_id: string; latitude_deg: number; longitude_deg_east: number };
type Option = { index: number; site: Site; window: Window; sunlight_s: number | null; contact_state: string; source: { analysis_id: string; sha256: string } };
type Settings = { mode: string; crew_option: number; depth: number; evaluations: number; shots: number; seed: number };
type Issue = { code: string; message: string; option_ids: string[]; window: Window | null; required: string | null; capacity: string | null };
type Timeline = { rows: { mission_id: string; option_id: string; site: Site; window: Window; left_percent: number; width_percent: number }[];
  occupancy: { window: Window; required_channels: number; capacity_channels: number | null; conflict: boolean; unknown: boolean; left_percent: number; width_percent: number }[];
  validation: { status: string; score: number | null; issues: Issue[] } | null };
type Campaign = { campaign_request: Settings; comparison: Run; window: Window; timelines: Record<string, Timeline> };
type Hardware = { status: string; input_fingerprint: string; timeline?: Timeline; job_id?: string; shots?: number; credits_spent?: number;
  sampled_feasible_rate?: number; sampled_optimal_rate?: number; optimum_gap?: number | null; counts?: Record<string, number>; submitted_utc?: string; training_depth?: number; training_seed?: number; training_evaluations?: number };
const utc = (value: string) => value.replace("T", " ").replace(/(?:\.\d+)?(?:Z|\+00:00)$/, " UTC");

export default function SharedAntenna({ active }: { active: boolean }) {
  const { t } = useTranslate();
  const [options, setOptions] = useState<Option[]>([]);
  const [settings, setSettings] = useState<Settings>({ mode: "standard", crew_option: 0, depth: 1, evaluations: 80, shots: 256, seed: 17 });
  const [result, setResult] = useState<Campaign | null>(null);
  const [hardware, setHardware] = useState<Hardware | null>(null);
  const [selected, setSelected] = useState("independent");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    if (!active) { controller.current?.abort(); generation.current++; setBusy(false); return; }
    setBusy(false);
    const ac = new AbortController();
    fetch("/api/campaign/catalog", { signal: ac.signal }).then(async response => {
      if (!response.ok) throw Error(t("arena.connectionError"));
      return response.json();
    }).then(data => { if (!ac.signal.aborted) setOptions(data.options); })
      .catch(e => { if (!ac.signal.aborted) setError(e.message); });
    return () => { ac.abort(); controller.current?.abort(); generation.current++; };
  }, [active, t]);
  async function calculate() {
    if (busy) return;
    controller.current?.abort();
    const ac = new AbortController(); controller.current = ac;
    const id = ++generation.current;
    setBusy(true); setError(""); setResult(null); setHardware(null); setSelected("independent");
    try {
      const response = await fetch("/api/campaign/compare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings), signal: ac.signal });
      const body = await response.json();
      if (!response.ok) throw Error(typeof body.detail === "string" ? body.detail : t("arena.invalidRequest"));
      if (id === generation.current) setResult(body);
    } catch (e) { if (id === generation.current && !ac.signal.aborted) setError(e instanceof Error ? e.message : t("arena.connectionError")); }
    finally { if (id === generation.current) setBusy(false); }
  }
  async function loadHardware() {
    if (busy) return;
    const ac = new AbortController(); controller.current?.abort(); controller.current = ac;
    const id = ++generation.current; setBusy(true); setError("");
    try {
      const response = await fetch("/api/campaign/hardware", { signal: ac.signal });
      const body = await response.json();
      if (!response.ok) throw Error(typeof body.detail === "string" ? body.detail : t("arena.invalidRequest"));
      if (id === generation.current) setHardware(body);
    } catch (e) { if (id === generation.current && !ac.signal.aborted) setError(e instanceof Error ? e.message : t("arena.connectionError")); }
    finally { if (id === generation.current) setBusy(false); }
  }
  function download() {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify({ ...result, recorded_hardware: hardware }, null, 2)], { type: "application/json" }));
    const a = document.createElement("a"); a.href = url; a.download = "h9-shared-antenna.json"; a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const timeline = selected === "hardware" ? hardware?.timeline : result?.timelines[selected];
  const matches = hardware?.status === "completed" && hardware.input_fingerprint === result?.comparison.input_fingerprint;
  const changed = result && JSON.stringify(settings) !== JSON.stringify(result.campaign_request);
  return <section className="shared-antenna solver-arena" aria-labelledby="antenna-title" aria-busy={busy}>
    <header className="arena-header"><div><p className="eyebrow">H9 / DSS-43 · CANBERRA</p><h2 id="antenna-title">{t("sharedAntenna.title")}</h2></div><span className="antenna-badge">3 {t("sharedAntenna.missions")}</span></header>
    <p className="arena-intro">{t("sharedAntenna.intro")}</p>
    <div className="arena-tags"><span>{t("sharedAntenna.illustrative")}</span><span>{t("sharedAntenna.capacity")}</span><span>{t("arena.noCredits")}</span></div>
    <div className="antenna-missions">{["crew", "robot-a", "robot-b"].map(id => <article key={id}><strong>{t(`sharedAntenna.${id}`)}</strong><small>{t(`sharedAntenna.${id}Detail`)}</small></article>)}</div>
    <details className="arena-details"><summary>{t("sharedAntenna.evidence")}</summary>
      <p>{t("sharedAntenna.evidenceNote")}</p><div className="arena-table-scroll"><table><thead><tr><th>{t("sharedAntenna.option")}</th><th>{t("sharedAntenna.siteTime")}</th><th>{t("sharedAntenna.contact")}</th><th>{t("sharedAntenna.sun")}</th></tr></thead><tbody>
        {options.map(o => <tr key={o.index}><td>{o.index}</td><td>{o.site.site_id}<br />{o.site.latitude_deg}°, {o.site.longitude_deg_east}° E (MOON_ME)<br />{utc(o.window.start_utc)} — {utc(o.window.end_utc)}<details><summary>{t("arena.identity")}</summary><code className="arena-id">{o.source.analysis_id}<br />{o.source.sha256}</code></details></td><td>{t(`sharedAntenna.${o.contact_state}`)}</td><td>{o.sunlight_s ?? "—"} s</td></tr>)}
      </tbody></table></div>
    </details>
    <form className="arena-controls" onSubmit={e => { e.preventDefault(); void calculate(); }}><fieldset disabled={busy}>
      <label className="arena-preset">{t("sharedAntenna.mode")}<select value={settings.mode} onChange={e => setSettings({ ...settings, mode: e.target.value })}>{["standard", "deadline", "closed", "unknown"].map(m => <option key={m} value={m}>{t(`sharedAntenna.mode_${m}`)}</option>)}</select></label>
      <label className="arena-preset">{t("sharedAntenna.pin")}<select value={settings.crew_option} onChange={e => setSettings({ ...settings, crew_option: Number(e.target.value) })}><option value="0">{t("sharedAntenna.auto")}</option>{options.map(o => <option value={o.index} key={o.index}>{o.index} · {utc(o.window.start_utc)}</option>)}</select></label>
      {([["depth", 1, 3], ["evaluations", 1, 256], ["shots", 1, 8192], ["seed", 0, 4294967295]] as const).map(([key, min, max]) => <label key={key}>{t(`arena.${key}`)}<input type="number" required step="1" min={min} max={max} value={Number.isNaN(settings[key]) ? "" : settings[key]} onChange={e => setSettings({ ...settings, [key]: e.target.valueAsNumber })} /></label>)}
      <button className="primary" type="submit">{t(busy ? "arena.running" : "sharedAntenna.run")}</button>
    </fieldset></form>
    <p className="arena-note">{t("sharedAntenna.pinNote")}</p>
    {error && <p role="alert" className="alert">{error}</p>}
    <p role="status">{busy ? t("arena.working") : result ? t("arena.completed") : t("sharedAntenna.ready")}</p>
    {changed && <p className="arena-caution">{t("arena.changedInputs")}</p>}
    {result && <>
      <p className="arena-note">{t(`sharedAntenna.mode_${result.campaign_request.mode}`)} · {t("sharedAntenna.pin")}: {result.campaign_request.crew_option || t("sharedAntenna.auto")}</p>
      <nav className="antenna-tabs" aria-label={t("sharedAntenna.planView")}>{["independent", "classical", "quantum", ...(matches ? ["hardware"] : [])].map(key => <button type="button" key={key} aria-pressed={selected === key} onClick={() => setSelected(key)}>{t(`sharedAntenna.${key}`)}</button>)}</nav>
      <p className="arena-note">{t(`sharedAntenna.explain_${selected}`)}</p>
      <div className="antenna-timeline"><div className="antenna-axis"><time>{utc(result.window.start_utc)}</time><time>{utc(result.window.end_utc)}</time></div>
        {["crew", "robot-a", "robot-b"].map(mid => <div className="antenna-lane" key={mid}><strong>{t(`sharedAntenna.${mid}`)}</strong><div className="antenna-track">{timeline?.rows.filter(row => row.mission_id === mid).map(row => <span className={`antenna-slot ${mid}`} key={row.option_id} title={`${row.option_id}: ${utc(row.window.start_utc)} — ${utc(row.window.end_utc)}`} style={{ left: `${row.left_percent}%`, width: `${row.width_percent}%` }}>{row.option_id}</span>)}</div></div>)}
        <div className="antenna-lane"><strong>DSS-43</strong><div className="antenna-track">{timeline?.occupancy.filter(row => row.required_channels > 0).map(row => <span key={row.window.start_utc} className={`antenna-load ${row.conflict ? "conflict" : row.unknown ? "unknown" : "clear"}`} title={`${utc(row.window.start_utc)}: ${row.required_channels} / ${row.capacity_channels ?? "?"}`} style={{ left: `${row.left_percent}%`, width: `${row.width_percent}%` }}>{row.required_channels}</span>)}</div></div>
      </div>
      {!timeline?.validation && <p className="arena-caution">{t("sharedAntenna.noPlan")} {t(`arena.status.${result.comparison.classical.result.status}`)}</p>}
      {timeline?.validation && <div className={`antenna-verdict ${timeline.validation.status}`}><strong>{t(`sharedAntenna.${timeline.validation.status}`)}</strong><span>{t("arena.score")}: {timeline.validation.score?.toFixed(3) ?? "—"}</span><p>{t("sharedAntenna.scoreNote")}</p>
        {timeline.validation.issues.map((issue, i) => <div className="antenna-issue" key={i}><strong>{t(`sharedAntenna.issue_${issue.code}`)}</strong><p>{issue.message}</p><small>{issue.option_ids.join(" + ")}{issue.window && ` · ${utc(issue.window.start_utc)} — ${utc(issue.window.end_utc)}`}{issue.required != null && ` · ${issue.required} / ${issue.capacity ?? "?"}`}</small></div>)}
      </div>}
      {timeline && timeline.rows.length > 0 && <div className="arena-table-scroll"><table><thead><tr><th>{t("sharedAntenna.missions")}</th><th>{t("sharedAntenna.option")}</th><th>{t("sharedAntenna.contact")}</th></tr></thead><tbody>{timeline.rows.map(row => <tr key={row.option_id}><td>{t(`sharedAntenna.${row.mission_id}`)}</td><td>{row.option_id}<br />{row.site.site_id}</td><td>{utc(row.window.start_utc)}<br />{utc(row.window.end_utc)}</td></tr>)}</tbody></table></div>}
      <p className="arena-caution">{t("sharedAntenna.boundary")}</p>
      <div className="arena-actions"><button type="button" onClick={download}>{t("arena.export")}</button><button type="button" disabled={busy} onClick={() => void loadHardware()}>{t("sharedAntenna.hardwareLoad")}</button></div>
      {hardware && <div className="arena-hardware"><h3>{t("sharedAntenna.hardware")}</h3>{hardware.status === "completed" ? <><p>{t(matches ? "sharedAntenna.hardwareMatch" : "sharedAntenna.hardwareMismatch")}</p><p>{hardware.job_id} · {hardware.shots} {t("arena.shots")} · {hardware.credits_spent} Spark</p><p>{t("arena.optimalShots")}: {((hardware.sampled_optimal_rate ?? 0) * 100).toFixed(1)}% · {t("arena.feasibleShots")}: {((hardware.sampled_feasible_rate ?? 0) * 100).toFixed(1)}%</p><p>{t("arena.gap")}: {hardware.optimum_gap ?? "—"}</p><p>{t("arena.runInfo", { seed: hardware.training_seed ?? "—", depth: hardware.training_depth ?? "—", evaluations: hardware.training_evaluations ?? "—", shots: hardware.shots ?? "—" })}</p><p>{hardware.submitted_utc}</p><p>{t("sharedAntenna.archiveNote")}</p><a href="https://www.openquantum.com/citation" target="_blank" rel="noreferrer">Open Quantum · {t("arena.attribution")}</a></> : <p>{t("sharedAntenna.hardwareNone")}</p>}</div>}
      <details className="arena-details"><summary>{t("sharedAntenna.arena")}</summary><SolverArena key={result.comparison.input_fingerprint + JSON.stringify(result.campaign_request)} active={active} comparison={result.comparison} /></details>
    </>}
  </section>;
}
