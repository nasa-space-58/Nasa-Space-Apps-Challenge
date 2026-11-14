import { useEffect, useRef, useState } from "react";
import { useTranslate } from "../i18n";
import SolverArena, { type Run } from "./SolverArena";
import "./research-lab.css";

type Point = { x: number; y: number; value: number; missions: number; options: number; kind: string };
type Plot = { metric: string; minimum_log10: number; maximum_log10: number; series: { name: string; options: number; points: Point[] }[] };
type Scaling = { measured_at_utc: string; environment: Record<string, string>; budget: Record<string, number>; plots: Plot[];
  points: { fingerprint: string; missions: number; options: number; combinations: string; logical_qubits: number; exact_median_s: number; milp_median_s: number;
    exact_runs: { status: string }[]; milp_runs: { status: string }[] }[] };
type SiteResult = { site_id: string; covered_s: number; coverage_fraction: number; maximum_gap_s: number; unknown_s: number };
type Validation = { status: string; orbit_ids: string[]; sites: SiteResult[]; issues: string[] };
type Relays = { source_url: string; orientation_assumption: string; interval_rule: string; geometry: { source_digest: string; direct_earth_geometry: Record<string, { state: string; elevation_deg: number; horizon_deg: number | null }> }; rejected_orbits: { orbit_id: string; reason: string; sampled_minimum_radius_km: number }[];
  orbit_audits: { orbit_id: string; period_days: number; closure_error_nd: number; jacobi_drift: number; minimum_moon_radius_km: number; svg_points: string }[];
  single_relay: Validation[]; convergence: { changed_intervals: number; interval_count: number; coarse_step_s: number; fine_step_s: number } };
type RelayResult = { comparison: Run; classical_validation: Validation | null; quantum_validation: Validation | null; request: Settings };
type Settings = { relay_count: number; minimum_coverage: number; maximum_gap_s: number; minimum_contact_s: number; seed: number };
const colors = ["#67e8f9", "#c4b5fd", "#fcd34d", "#fb7185", "#6ee7b7", "#94a3b8"];

export default function ResearchLab({ active, kind }: { active: boolean; kind: "scaling" | "relays" }) {
  const { t } = useTranslate();
  const [scaling, setScaling] = useState<Scaling | null>(null);
  const [relays, setRelays] = useState<Relays | null>(null);
  const [result, setResult] = useState<RelayResult | null>(null);
  const [settings, setSettings] = useState<Settings>({ relay_count: 2, minimum_coverage: 0.8, maximum_gap_s: 86400, minimum_contact_s: 3600, seed: 17 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    controller.current?.abort(); generation.current++; setBusy(false);
    if (!active) return;
    const ac = new AbortController(); controller.current = ac;
    setError("");
    fetch(`/api/research/${kind}`, { signal: ac.signal }).then(async r => {
      const body = await r.json(); if (!r.ok) throw Error(body.detail || t("arena.connectionError")); return body;
    }).then(body => { if (!ac.signal.aborted) { if (kind === "scaling") setScaling(body); else setRelays(body); } })
      .catch(e => { if (!ac.signal.aborted) setError(e.message); });
    return () => { ac.abort(); generation.current++; };
  }, [active, kind, t]);
  async function run() {
    controller.current?.abort(); const ac = new AbortController(); controller.current = ac;
    const id = ++generation.current; setBusy(true); setError(""); setResult(null);
    try {
      const r = await fetch("/api/research/relays/compare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings), signal: ac.signal });
      const body = await r.json(); if (!r.ok) throw Error(typeof body.detail === "string" ? body.detail : t("arena.invalidRequest"));
      if (id === generation.current) setResult(body);
    } catch (e) { if (!ac.signal.aborted && id === generation.current) setError(e instanceof Error ? e.message : t("arena.connectionError")); }
    finally { if (id === generation.current) setBusy(false); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ scaling, relays, result }, null, 2)], { type: "application/json" }));
    const a = document.createElement("a"); a.href = url; a.download = `h10-h11-${kind}.json`; a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function validation(v: Validation, label: string) {
    return <article className="research-validation"><h4>{label} · {t(`research.${v.status}`)}</h4><p>{v.orbit_ids.join(" + ")}</p>
      {v.sites.map(s => <p key={s.site_id}><strong>{s.site_id}</strong> · {t("research.coverage")}: {s.coverage_fraction.toFixed(4)} · {t("research.contact")}: {s.covered_s} s · {t("research.gap")}: {s.maximum_gap_s} s · {t("research.unknown")}: {s.unknown_s} s</p>)}
      {v.issues.length > 0 && <ul>{v.issues.map((i, n) => <li key={n}>{i}</li>)}</ul>}</article>;
  }
  return <section className="research-lab solver-arena" aria-busy={busy}>
    <header className="arena-header"><div><p className="eyebrow">{kind === "scaling" ? "H10 / SCALING" : "H11 / RELAY ORBITS"}</p><h2>{t(`research.${kind}`)}</h2></div><button onClick={download} disabled={!scaling && !relays}>{t("research.export")}</button></header>
    <p className="arena-intro">{t(`research.${kind}Intro`)}</p><div className="arena-tags"><span>{t("arena.noCredits")}</span><span>{t("research.noAdvantage")}</span></div>
    {error && <p role="alert" className="arena-error">{error}</p>}
    {kind === "scaling" && scaling && <>
      <p>{t("research.measuredAt")}: {scaling.measured_at_utc}</p>
      <div className="research-charts">{scaling.plots.map(plot => <figure key={plot.metric}><figcaption>{t(`research.${plot.metric}`)} · {t("research.logAxis")}</figcaption>
        <svg viewBox="-8 -8 120 120" role="img" aria-label={t(`research.${plot.metric}`)}><path d="M0 0V100H100" stroke="#64748b" fill="none" strokeWidth="0.4" />
          {plot.series.map((series, i) => <g key={`${series.name}-${series.options}`} fill={colors[i % colors.length]}>
            <polyline points={series.points.map(p => `${p.x},${p.y}`).join(" ")} fill="none" stroke={colors[i % colors.length]} strokeWidth="0.5" strokeDasharray={series.name === "projection" ? "2 2" : undefined} />
            {series.points.map((p, j) => <circle key={j} cx={p.x} cy={p.y} r={p.kind === "budget_limited" ? 1.6 : 1} stroke={p.kind === "budget_limited" ? "#fff" : undefined} strokeWidth="0.4"><title>{series.name} · {p.missions} / {p.options} · {p.value} · {p.kind}</title></circle>)}
          </g>)}
        </svg><p>{t("research.xAxis")}; log10(y): {plot.minimum_log10.toFixed(2)} → {plot.maximum_log10.toFixed(2)}</p>
        <ul className="research-legend">{plot.series.map((s,i) => <li key={`${s.name}-${s.options}`} style={{ color: colors[i % colors.length] }}>{t(`research.${s.name}`)} · {s.options} {t("research.options")}</li>)}</ul>
      </figure>)}</div>
      <p className="research-note">{t("research.censoring")}</p>
      <div className="arena-table-scroll"><table><thead><tr>{["missions", "options", "combinations", "logical_qubits", "exact", "milp"].map(k => <th key={k}>{t(`research.${k}`)}</th>)}</tr></thead><tbody>{scaling.points.map(p => <tr key={p.fingerprint}><td>{p.missions}</td><td>{p.options}</td><td>{p.combinations}</td><td>{p.logical_qubits}</td><td>{p.exact_median_s.toFixed(6)} s<br />{p.exact_runs.map(r => r.status).join(" / ")}</td><td>{p.milp_median_s.toFixed(6)} s<br />{p.milp_runs.map(r => r.status).join(" / ")}</td></tr>)}</tbody></table></div>
      <details><summary>{t("research.environment")}</summary><pre>{JSON.stringify({ environment: scaling.environment, budget: scaling.budget }, null, 2)}</pre></details>
    </>}
    {kind === "relays" && relays && <>
      <p className="research-note">{t("research.geometryLimit")}</p>
      <div className="research-validation"><h3>{t("research.directEarth")}</h3>{Object.entries(relays.geometry.direct_earth_geometry).map(([id,g]) => <p key={id}><strong>{id}</strong> · {t(`campaign.${g.state}`)} · {t("research.elevation")}: {g.elevation_deg.toFixed(2)}° · {t("research.horizon")}: {g.horizon_deg?.toFixed(2) ?? "—"}°</p>)}</div>
      <div className="research-charts"><figure><figcaption>{t("research.orbitView")}</figcaption><svg viewBox="0 0 100 100" role="img" aria-label={t("research.orbitView")}>{relays.orbit_audits.map((o,i) => <polyline key={o.orbit_id} points={o.svg_points} fill="none" stroke={colors[i]} strokeWidth="0.6" />)}</svg><p>{t("research.orbitViewNote")}</p></figure>
      <div className="arena-table-scroll"><table><thead><tr>{["orbit", "period", "closure", "jacobi"].map(k => <th key={k}>{t(`research.${k}`)}</th>)}</tr></thead><tbody>{relays.orbit_audits.map((o,i) => <tr key={o.orbit_id}><td style={{color: colors[i]}}>{o.orbit_id}</td><td>{o.period_days.toFixed(3)} d</td><td>{o.closure_error_nd.toExponential(2)}</td><td>{o.jacobi_drift.toExponential(2)}</td></tr>)}</tbody></table></div></div>
      <details><summary>{t("research.sources")}</summary><a href={relays.source_url} target="_blank" rel="noreferrer">NASA/JPL</a><p>{relays.orientation_assumption}</p><p>{relays.interval_rule}</p>{relays.rejected_orbits.map(o => <p key={o.orbit_id}>{t("research.rejected")}: {o.orbit_id} — {o.reason} ({o.sampled_minimum_radius_km.toFixed(2)} km)</p>)}<pre>{JSON.stringify(relays.convergence,null,2)}</pre><code>{relays.geometry.source_digest}</code></details>
      <form className="arena-controls" onSubmit={e => {e.preventDefault(); void run();}}><fieldset disabled={busy}>
        <label>{t("research.relayCount")}<select value={settings.relay_count} onChange={e => setSettings({...settings, relay_count: Number(e.target.value)})}><option value="1">1</option><option value="2">2</option></select></label>
        {([["minimum_coverage",0,1,0.05],["maximum_gap_s",0,691200,1],["minimum_contact_s",0,691200,1],["seed",0,4294967295,1]] as const).map(([key,min,max,step]) => <label key={key}>{t(`research.${key}`)}<input required type="number" min={min} max={max} step={step} value={Number.isNaN(settings[key]) ? "" : settings[key]} onChange={e => setSettings({...settings,[key]:e.target.valueAsNumber})}/></label>)}
        <button type="submit" className="primary">{t(busy ? "arena.running" : "research.compare")}</button>
      </fieldset></form>
      {result && <>{JSON.stringify(result.request)!==JSON.stringify(settings) && <p role="status">{t("research.changed")}</p>}
        {result.classical_validation ? validation(result.classical_validation,t("research.classical")) : <p>{t("research.noPlan")} ({result.comparison.classical.result.status})</p>}
        {result.quantum_validation && validation(result.quantum_validation,t("research.quantum"))}
        <details><summary>{t("research.solverDetails")}</summary><SolverArena active={active} comparison={result.comparison}/></details></>}
      <details><summary>{t("research.singleRelay")}</summary>{relays.single_relay.map((v,i) => <div key={i}>{validation(v,v.orbit_ids.join(" / "))}</div>)}</details>
    </>}
  </section>;
}
