import { useEffect, useMemo, useState } from "react";
import { useTranslate, type Translate } from "../i18n";
import {
  setMissionRun,
  useMissionRun,
  type MissionGate,
  type MissionRunResult,
} from "../missionRunStore";
import "./mission-run.css";

type Fault = { kind: string } & Record<string, number | string>;
type Property = {
  type?: string;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
};
type FaultKind = {
  kind: string;
  phase: "descent" | "landing" | "surface";
  schema: { properties: Record<string, Property>; required?: string[] };
};
type Scenario = { scenario_id: string; summary: string; faults: Fault[] };
type Catalog = {
  scenarios: Scenario[];
  fault_kinds: FaultKind[];
  defaults: {
    power: Record<string, number>;
    communication: Record<string, number>;
  };
};
type Saved = { id: string; name: string; faults: Fault[] };

const SAVED_KEY = "lunar-custom-fault-scenarios";
const POWER_FIELDS = ["battery_capacity_wh", "solar_array_power_w", "base_load_w"];
const COMM_FIELDS = ["first_contact_deadline_h", "max_contact_gap_h"];

const loadSaved = (): Saved[] => {
  try {
    const value = JSON.parse(localStorage.getItem(SAVED_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
};

/** Unit shown next to a backend field, taken from its unit-suffixed name. */
const unitOf = (name: string) =>
  name.endsWith("_kg_s")
    ? "kg/s"
    : name.endsWith("_s")
      ? "s"
      : name.endsWith("_h")
        ? "h"
        : name.endsWith("_wh")
          ? "Wh"
          : name.endsWith("_w")
            ? "W"
            : name.endsWith("_m")
              ? "m"
              : name.endsWith("_deg")
                ? "°"
                : "0–1";

/** A starting value inside the backend bounds for a new custom fault field. */
const startValue = (p: Property) => {
  const low = p.minimum ?? p.exclusiveMinimum ?? 0;
  const high = p.maximum ?? p.exclusiveMaximum ?? low + 1;
  return Number(((low + high) / 2).toPrecision(3));
};

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

function gateText(t: Translate, gate: MissionGate, run: MissionRunResult) {
  const m = gate.metrics ?? {};
  const contact = run.descent?.contact;
  const key = `missionRun.reason.${gate.gate}.${gate.code ?? gate.status}`;
  const params: Record<string, string | number> = {
    v: contact ? Math.abs(contact.vertical_speed_mps).toFixed(2) : "–",
    h: contact ? contact.horizontal_speed_mps.toFixed(2) : "–",
    margin: run.descent ? run.descent.propellant_margin_kg.toFixed(0) : "–",
    nominal: run.descent ? run.descent.nominal_propellant_margin_kg.toFixed(0) : "–",
    altitude: run.descent?.exhausted_at ? run.descent.exhausted_at.altitude_m.toFixed(0) : "–",
    offset: run.touchdown_site ? run.touchdown_site.offset_m.toFixed(0) : "–",
    slope: num(m.site_slope_deg)?.toFixed(1) ?? "–",
    area: num(m.max_landing_slope_deg)?.toFixed(1) ?? "–",
    limit: run.settings.terrain.slope_limit_deg,
    energy: num(m.min_energy_wh)?.toFixed(0) ?? "–",
    gap: num(m.longest_gap_h)?.toFixed(1) ?? "–",
    deadline: run.settings.communication.first_contact_deadline_h,
    maxgap: run.settings.communication.max_contact_gap_h,
  };
  const text = t(key, params);
  return text === key ? gate.detail : text;
}

/** Small SVG line chart; values come straight from the backend result. */
function Chart({
  points,
  ceiling,
  label,
  unit,
  marker,
}: {
  points: [number, number][];
  ceiling?: number;
  label: string;
  unit: string;
  marker?: number | null;
}) {
  if (points.length < 2) return null;
  const w = 320, h = 96;
  const xs = points.map((p) => p[0]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || 1;
  const y1 = Math.max(ceiling ?? 0, ...points.map((p) => p[1])) || 1;
  const sx = (x: number) => ((x - x0) / (x1 - x0 || 1)) * w;
  const sy = (y: number) => h - (Math.max(0, y) / y1) * (h - 6);
  return (
    <figure className="run-chart">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={label}>
        {ceiling !== undefined && (
          <line x1={0} x2={w} y1={sy(ceiling)} y2={sy(ceiling)} className="run-chart-cap" />
        )}
        <polyline points={points.map(([x, y]) => `${sx(x)},${sy(y)}`).join(" ")} />
        {marker !== null && marker !== undefined && (
          <line x1={sx(marker)} x2={sx(marker)} y1={0} y2={h} className="run-chart-marker" />
        )}
      </svg>
      <figcaption>
        {label} · {unit}
      </figcaption>
    </figure>
  );
}

export default function MissionRun({
  analysisId,
  onWatch,
}: {
  analysisId?: string;
  onWatch: () => void;
}) {
  const { t, locale } = useTranslate();
  const run = useMissionRun(analysisId);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [scenarioId, setScenarioId] = useState("nominal");
  const [custom, setCustom] = useState<Fault[]>([]);
  const [saved, setSaved] = useState<Saved[]>(loadSaved);
  const [name, setName] = useState("");
  const [addKind, setAddKind] = useState("");
  const [power, setPower] = useState<Record<string, number>>({});
  const [comm, setComm] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/mission-faults")
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((body: Catalog) => {
        setCatalog(body);
        setPower(Object.fromEntries(POWER_FIELDS.map((f) => [f, body.defaults.power[f]])));
        setComm(Object.fromEntries(COMM_FIELDS.map((f) => [f, body.defaults.communication[f]])));
        setAddKind(body.fault_kinds[0]?.kind ?? "");
      })
      .catch(() => setError(t("missionRun.catalogError")));
  }, [t]);
  useEffect(() => {
    localStorage.setItem(SAVED_KEY, JSON.stringify(saved));
  }, [saved]);

  const kinds = useMemo(
    () => Object.fromEntries((catalog?.fault_kinds ?? []).map((k) => [k.kind, k])),
    [catalog],
  );
  const fmt = (utc: string) =>
    new Date(utc).toLocaleString(locale, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
    }) + " UTC";
  const scenarioName = (id: string) => {
    const key = `missionRun.scenarios.${id}`;
    const text = t(key);
    return text === key ? id : text;
  };
  const faultLabel = (kind: string) => t(`missionRun.faults.${kind}`);

  const addFault = () => {
    const kind = kinds[addKind];
    if (!kind) return;
    const fields = Object.fromEntries(
      Object.entries(kind.schema.properties).map(([field, p]) => [field, startValue(p)]),
    );
    setCustom((list) => [...list, { kind: addKind, ...fields }]);
  };
  const editFault = (index: number, field: string, value: number) =>
    setCustom((list) => list.map((f, i) => (i === index ? { ...f, [field]: value } : f)));

  const start = (scenario: string | null, faults: Fault[]) => {
    if (!analysisId) return;
    setBusy(true);
    setError("");
    fetch(`/api/analyses/${encodeURIComponent(analysisId)}/mission-run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario_id: scenario,
        faults,
        power: { ...catalog?.defaults.power, ...power },
        communication: { ...catalog?.defaults.communication, ...comm },
      }),
    })
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (!r.ok)
          throw new Error(
            typeof body.detail === "string" ? body.detail : t("missionRun.invalidInput"),
          );
        return body as MissionRunResult;
      })
      .then((result) => {
        setMissionRun(result);
        onWatch();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  if (!analysisId)
    return (
      <section className="mission-run">
        <h3>{t("missionRun.title")}</h3>
        <p className="muted">{t("missionRun.needsAnalysis")}</p>
      </section>
    );

  const field = (
    values: Record<string, number>,
    set: (v: Record<string, number>) => void,
    key: string,
  ) => (
    <label key={key}>
      {t(`missionRun.fields.${key}`)} ({unitOf(key)})
      <input
        type="number"
        value={values[key] ?? ""}
        onChange={(e) => set({ ...values, [key]: Number(e.target.value) })}
      />
    </label>
  );

  return (
    <section className="mission-run">
      <h3>{t("missionRun.title")}</h3>
      <p className="muted">{t("missionRun.intro")}</p>

      <h4>{t("missionRun.scenarioHeading")}</h4>
      <div className="run-scenarios" role="radiogroup" aria-label={t("missionRun.scenarioHeading")}>
        {(catalog?.scenarios ?? []).map((s) => (
          <button
            key={s.scenario_id}
            role="radio"
            aria-checked={scenarioId === s.scenario_id}
            className="run-scenario"
            title={s.summary}
            onClick={() => setScenarioId(s.scenario_id)}
          >
            <strong>{scenarioName(s.scenario_id)}</strong>
            <small>{s.faults.map((f) => faultLabel(f.kind)).join(" + ") || t("missionRun.noFault")}</small>
          </button>
        ))}
      </div>
      <button
        className="primary"
        disabled={busy || !catalog}
        onClick={() => start(scenarioId, [])}
      >
        {busy ? t("missionRun.running") : t("missionRun.run")}
      </button>

      <details className="run-builder">
        <summary>{t("missionRun.customHeading")}</summary>
        <p className="muted">{t("missionRun.customIntro")}</p>
        {custom.map((fault, index) => (
          <fieldset key={index} className="run-fault">
            <legend>
              {faultLabel(fault.kind)}
              <button
                className="text-button"
                aria-label={t("missionRun.removeFault")}
                onClick={() => setCustom((list) => list.filter((_, i) => i !== index))}
              >
                ✕
              </button>
            </legend>
            {Object.entries(kinds[fault.kind]?.schema.properties ?? {}).map(([key, p]) => (
              <label key={key}>
                {t(`missionRun.fields.${key}`)} ({unitOf(key)})
                <input
                  type="number"
                  step="any"
                  min={p.minimum ?? p.exclusiveMinimum}
                  max={p.maximum ?? p.exclusiveMaximum}
                  value={fault[key] as number}
                  onChange={(e) => editFault(index, key, Number(e.target.value))}
                />
              </label>
            ))}
          </fieldset>
        ))}
        <div className="run-add">
          <select value={addKind} onChange={(e) => setAddKind(e.target.value)} aria-label={t("missionRun.faultKind")}>
            {(catalog?.fault_kinds ?? []).map((k) => (
              <option key={k.kind} value={k.kind}>
                {t(`missionRun.phase.${k.phase}`)} · {faultLabel(k.kind)}
              </option>
            ))}
          </select>
          <button onClick={addFault}>{t("missionRun.addFault")}</button>
        </div>
        <div className="run-add">
          <input
            value={name}
            placeholder={t("missionRun.namePlaceholder")}
            onChange={(e) => setName(e.target.value)}
            aria-label={t("missionRun.namePlaceholder")}
          />
          <button
            disabled={!custom.length || !name.trim()}
            onClick={() => {
              setSaved((list) => [...list, { id: crypto.randomUUID(), name: name.trim(), faults: custom }]);
              setName("");
            }}
          >
            {t("missionRun.save")}
          </button>
          <button className="primary" disabled={busy || !custom.length} onClick={() => start(null, custom)}>
            {t("missionRun.runCustom")}
          </button>
        </div>
        {saved.length > 0 && (
          <ul className="run-saved">
            {saved.map((s) => (
              <li key={s.id}>
                <span>
                  <strong>{s.name}</strong>
                  <small>{s.faults.map((f) => faultLabel(f.kind)).join(" + ")}</small>
                </span>
                <button onClick={() => setCustom(s.faults)}>{t("missionRun.edit")}</button>
                <button disabled={busy} onClick={() => start(null, s.faults)}>{t("missionRun.run")}</button>
                <button className="text-button" aria-label={t("missionRun.delete")} onClick={() => setSaved((list) => list.filter((x) => x.id !== s.id))}>
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}
      </details>

      <details className="run-builder">
        <summary>{t("missionRun.vehicleHeading")}</summary>
        <div className="run-fields">
          {POWER_FIELDS.map((k) => field(power, setPower, k))}
          {COMM_FIELDS.map((k) => field(comm, setComm, k))}
        </div>
      </details>

      {error && <p className="error" role="alert">{error}</p>}

      {run && (
        <div className={`run-result run-${run.outcome}`} aria-live="polite">
          <div className="run-outcome">
            <strong>{t(`missionRun.outcome.${run.outcome}`)}</strong>
            <span>
              {run.scenario_id ? scenarioName(run.scenario_id) : t("missionRun.customScenario")}
              {run.failure && ` · ${fmt(run.failure.at_utc)}`}
            </span>
            <button onClick={onWatch}>{t("missionRun.watch")}</button>
          </div>
          <ol className="run-gates">
            {run.gates.map((g) => (
              <li key={g.gate} className={`gate-${g.status}`}>
                <span className="gate-icon" aria-hidden="true">
                  {{ pass: "✓", warn: "!", fail: "✕", unknown: "?", not_reached: "–" }[g.status]}
                </span>
                <span>
                  <strong>{t(`missionRun.gates.${g.gate}`)}</strong>
                  <small>{t(`missionRun.status.${g.status}`)}</small>
                  {g.status !== "not_reached" && <p>{gateText(t, g, run)}</p>}
                </span>
              </li>
            ))}
          </ol>
          {run.descent && (
            <Chart
              label={t("missionRun.altitudeChart")}
              unit="m · s"
              points={run.descent.samples
                .filter((s) => s.altitude_m < 3000)
                .map((s) => [s.t_s, s.altitude_m])}
              marker={run.descent.exhausted_at?.t_s ?? null}
            />
          )}
          {run.power && (
            <Chart
              label={t("missionRun.batteryChart")}
              unit="Wh · UTC"
              ceiling={run.power.trace[0]?.capacity_wh}
              points={run.power.trace.map((r) => [Date.parse(r.utc), r.energy_wh])}
              marker={run.power.depleted_at_utc ? Date.parse(run.power.depleted_at_utc) : null}
            />
          )}
          <h4>{t("missionRun.timeline")}</h4>
          <ol className="run-events">
            {run.events.map((e, i) => (
              <li key={i} className={`event-${e.gate}`}>
                <time>{fmt(e.utc)}</time>
                <span>{t(`missionRun.events.${e.kind}`, { fault: e.fault ? faultLabel(e.fault) : "" })}</span>
              </li>
            ))}
          </ol>
          <p className="muted small">{t("missionRun.disclaimer")}</p>
        </div>
      )}
    </section>
  );
}
