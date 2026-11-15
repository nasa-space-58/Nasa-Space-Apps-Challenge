import { useEffect, useMemo, useState } from "react";
import { useTranslate, type Translate } from "../i18n";
import {
  setMissionRun,
  useMissionRun,
  type MissionGate,
  type MissionRunResult,
} from "../missionRunStore";
import { previewFor, useAnalysisView } from "../analysisView";
import MissionRoutes, { type MissionRouteSet } from "./MissionRoutes";
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
  verdict,
}: {
  points: [number, number][];
  ceiling?: number;
  label: string;
  unit: string;
  marker?: number | null;
  verdict?: number | null;
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
        {verdict !== null && verdict !== undefined && (
          <line x1={sx(verdict)} x2={sx(verdict)} y1={0} y2={h} className="run-chart-verdict" />
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
  const [routes, setRoutes] = useState<MissionRouteSet | null>(null);
  const [routesError, setRoutesError] = useState("");
  const { preview, setPreview } = useAnalysisView();
  const routeSet = routes && routes.analysis_id === analysisId ? routes : null;
  const routesShown = preview?.kind === "routes";
  const selectedRoute = routesShown ? (preview?.selected ?? null) : null;
  const showRoutes = (set: MissionRouteSet, result: MissionRunResult | null, selected?: string) =>
    setPreview({
      ...previewFor("routes", { ...set, flown: result?.descent?.samples ?? [] }),
      selected,
    });

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

  // The route set ignores faults, so one request per analysis is enough.
  const loadRoutes = (id: string): Promise<MissionRouteSet> =>
    routeSet
      ? Promise.resolve(routeSet)
      : fetch(`/api/analyses/${encodeURIComponent(id)}/mission-routes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        }).then(async (r) => {
          const body = await r.json().catch(() => ({}));
          if (!r.ok)
            throw new Error(
              typeof body.detail === "string" ? body.detail : t("missionRun.routes.error"),
            );
          return body as MissionRouteSet;
        });

  const start = (scenario: string | null, faults: Fault[]) => {
    if (!analysisId) return;
    setBusy(true);
    setError("");
    setRoutesError("");
    const routeCall = loadRoutes(analysisId);
    routeCall.catch(() => undefined); // handled after the run; avoids an unhandled rejection
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
      .then(async (result) => {
        setMissionRun(result);
        try {
          const set = await routeCall;
          setRoutes(set);
          showRoutes(set, result);
        } catch (e) {
          // Without routes the run is still valid; fall back to the flight.
          setRoutesError((e as Error).message);
          onWatch();
        }
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
          {!!run.context?.limitations.length && (
            <div className="run-limits" role="note">
              <ul>
                {[...new Set(run.context.limitations.map((l) => l.code))].map((code) => {
                  const kinds = run.context!.limitations.filter((l) => l.code === code && l.fault);
                  return (
                    <li key={code}>
                      {t(`missionRun.limits.${code}`, {
                        faults: kinds.map((l) => faultLabel(l.fault!)).join(", "),
                      })}
                    </li>
                  );
                })}
              </ul>
              <small>{t("missionRun.limits.hint")}</small>
            </div>
          )}
          <div className="run-outcome">
            <strong>{t(`missionRun.outcome.${run.outcome}`)}</strong>
            <span>
              {run.scenario_id ? scenarioName(run.scenario_id) : t("missionRun.customScenario")}
              {run.failure && ` · ${fmt(run.failure.at_utc)}`}
            </span>
            <button
              onClick={() => {
                setPreview(null);
                onWatch();
              }}
            >
              {t("missionRun.watch")}
            </button>
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
          {run.baseline && (
            <div className="run-delta">
              <h4>{t("missionRun.delta.heading")}</h4>
              <dl>
                {(
                  [
                    ["propellant", run.descent?.propellant_margin_kg, run.baseline.delta.propellant_margin_kg, "kg", 0],
                    ["offset", run.touchdown_site?.offset_m, run.baseline.delta.touchdown_offset_m, "m", 0],
                    ["delay", null, run.baseline.delta.landing_delay_h, "h", 1],
                    ["energy", run.surface_outlook?.power.min_energy_wh, run.baseline.delta.min_energy_wh, "Wh", 0],
                    ["contact", null, run.baseline.delta.first_contact_shift_h, "h", 1],
                  ] as [string, number | null | undefined, number | null, string, number][]
                ).map(([key, value, delta, unit, digits]) => (
                  <div key={key} className={delta !== null && Math.abs(delta) >= 10 ** -digits / 2 ? "changed" : undefined}>
                    <dt>{t(`missionRun.delta.${key}`)}</dt>
                    <dd>
                      {value !== null && value !== undefined && `${value.toFixed(digits)} ${unit} · `}
                      {delta === null
                        ? t("missionRun.delta.none")
                        : `${delta > 0 ? "+" : delta < 0 ? "−" : "±"}${Math.abs(delta).toFixed(digits)} ${unit}`}
                    </dd>
                  </div>
                ))}
              </dl>
              {run.surface_outlook?.power.depleted_at_utc && (
                <p className="small">
                  {run.baseline.depleted_at_utc
                    ? t("missionRun.delta.batteryDiedBoth", {
                        at: fmt(run.surface_outlook.power.depleted_at_utc),
                        base: fmt(run.baseline.depleted_at_utc),
                      })
                    : t("missionRun.delta.batteryDied", { at: fmt(run.surface_outlook.power.depleted_at_utc) })}
                </p>
              )}
            </div>
          )}
          {routeSet && (
            <MissionRoutes
              set={routeSet}
              selected={selectedRoute}
              shown={routesShown}
              onShow={() => showRoutes(routeSet, run)}
              onSelect={(id) =>
                routesShown
                  ? setPreview((p) => (p ? { ...p, selected: p.selected === id ? undefined : id } : p))
                  : showRoutes(routeSet, run, id)
              }
            />
          )}
          {routesError && (
            <p className="muted small" role="status">
              {t("missionRun.routes.unavailable", { detail: routesError })}
            </p>
          )}
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
          {(run.surface_outlook ?? run.power) && (() => {
            const power = run.surface_outlook?.power ?? run.power!;
            const verdict = run.failure && run.surface_outlook ? Date.parse(run.failure.at_utc) : null;
            return (
              <>
                <Chart
                  label={t("missionRun.batteryChart")}
                  unit="Wh · UTC"
                  ceiling={power.trace[0]?.capacity_wh}
                  points={power.trace.map((r) => [Date.parse(r.utc), r.energy_wh])}
                  marker={power.depleted_at_utc ? Date.parse(power.depleted_at_utc) : null}
                  verdict={verdict}
                />
                {verdict !== null && <p className="muted small">{t("missionRun.outlook.verdictMarker")}</p>}
              </>
            );
          })()}
          <h4>{t("missionRun.timeline")}</h4>
          <ol className="run-events">
            {run.events.map((e, i) => (
              <li key={i} className={`event-${e.gate}`}>
                <time>{fmt(e.utc)}</time>
                <span>{t(`missionRun.events.${e.kind}`, { fault: e.fault ? faultLabel(e.fault) : "" })}</span>
              </li>
            ))}
          </ol>
          {run.failure && run.surface_outlook && (
            <>
              <h4>{t("missionRun.outlook.heading")}</h4>
              <p className="muted small">{t("missionRun.outlook.intro")}</p>
              <ol className="run-events run-outlook">
                {run.surface_outlook.findings.map((e, i) => (
                  <li key={i} className={`event-${e.gate}`}>
                    <time>{fmt(e.utc)}</time>
                    <span>{t(`missionRun.events.${e.kind}`, { fault: e.fault ? faultLabel(e.fault) : "" })}</span>
                  </li>
                ))}
                {!run.surface_outlook.power.depleted_at_utc && (
                  <li className="event-power">
                    <time>{fmt(run.surface_outlook.evaluated_until_utc)}</time>
                    <span>{t("missionRun.outlook.survived")}</span>
                  </li>
                )}
              </ol>
            </>
          )}
          <p className="muted small">{t("missionRun.disclaimer")}</p>
        </div>
      )}
    </section>
  );
}
