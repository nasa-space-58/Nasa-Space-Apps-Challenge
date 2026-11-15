import { useEffect, useRef, useState } from "react";
import type { MissionCandidate } from "../missionCandidates";
import type { Preset } from "../types";
import "./recommendations.css";
import { errorText, siteLabel, useTranslate, type Translate } from "../i18n";

// R3 (S8/S9): renders backend rankings; scores and rescoring come from the API.
// "fuel" is the opt-in R5 descent criterion; it appears only when the API returns it.
const CRITERIA = ["energy", "darkness", "communication", "terrain_safety", "fuel"] as const;
type Criterion = (typeof CRITERIA)[number];
const criterionLabel = (t: Translate, c: Criterion) =>
  t(`recommendations.criterion.${c}`);
type Weights = Record<Criterion, number>;
type Score = { score: number | null; value: number | null; unit: string; rule: string };
type Ranked = {
  candidate_id: string;
  rank: number;
  total_score: number;
  contributions: Partial<Weights>;
  criteria: Partial<Record<Criterion, Score>>;
  explanation: string;
};
type Ranking = {
  weights: Weights;
  ranked: Ranked[];
  rejected: { candidate_id: string; explanation: string }[];
  insufficient_data: { candidate_id: string; explanation: string }[];
  failed?: { candidate_id: string; error: string }[];
  not_modeled: Record<string, string>;
};
type Safety = {
  covered: boolean;
  site_slope_deg?: number | null;
  max_landing_slope_deg?: number | null;
  hazard_distance_m?: number | null;
  error?: string;
};
type CandidateState = {
  candidate_id: string;
  site: { site_id: string; latitude_deg: number; longitude_deg_east: number };
  state: string;
  error: string | null;
  safety: Safety | null;
  descent?: { feasible: boolean; propellant_margin_kg: number | null; detail?: string } | null;
};
type Recommendation = {
  id: string;
  status: string;
  previous_id: string | null;
  request: {
    start_utc: string;
    end_utc: string;
    weights: Weights;
    include_descent?: boolean;
    constraints: { slope_limit_deg: number; landing_radius_m: number };
  };
  candidates: CandidateState[];
  ranking: Ranking | null;
  schedule_change: null | {
    suggestion: string;
    changes: {
      candidate_id: string;
      site_id: string;
      previous_rank: number | null;
      new_rank: number | null;
      previous_status: string;
      new_status: string;
    }[];
  };
  error?: string;
};
type Rescore = {
  previous: Ranking;
  current: Ranking;
  changes: {
    candidate_id: string;
    previous_rank: number | null;
    new_rank: number | null;
    reason: Criterion | null;
  }[];
};
type Custom = { lat: string; lon: string };

const ACTIVE = ["pending", "running"];
const STATUSES = ["pending", "running", "ready", "partial", "failed"];
const STATES = ["waiting", "analyzing", "communicating", "evaluated", "failed"];
const KEY = "lln.recommendation.id";
const defaults: Weights = { energy: 1, darkness: 1, communication: 1, terrain_safety: 1, fuel: 0 };

async function call(path: string, init?: RequestInit) {
  const response = await fetch(`/api${path}`, init);
  const body = await response.json();
  if (!response.ok) {
    const detail = body?.detail;
    throw Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg: string }) => d.msg).join("; ")
          : "",
    );
  }
  return body;
}
function remembered() {
  try {
    return localStorage.getItem(KEY) || undefined;
  } catch {
    return undefined;
  }
}
function remember(id: string) {
  try {
    localStorage.setItem(KEY, id);
  } catch {
    // Convenience only; the recommendation is saved on the server.
  }
}
const utc = (v: string) => `${v.replace("T", " ").slice(0, 16)} UTC`;
const DURATIONS = ["24", "72", "168"] as const;

function ContributionBar({ row, ranking }: { row: Ranked; ranking: Ranking }) {
  const { t } = useTranslate();
  const active = CRITERIA.filter((c) => c in ranking.weights);
  return (
    <div
      className="contribution-bar"
      role="img"
      aria-label={active
        .map((c) => `${criterionLabel(t, c)} ${(row.contributions[c] ?? 0).toFixed(2)}`)
        .join(", ")}
    >
      {active.map((c) => (
        <span
          key={c}
          className={`contribution ${c}`}
          style={{ width: `${(row.contributions[c] ?? 0) * 100}%` }}
          title={t("recommendations.contributionTitle", {
            criterion: criterionLabel(t, c),
            score: row.criteria[c]?.score?.toFixed(2) ?? "—",
            weight: (ranking.weights[c] ?? 0).toFixed(2),
          })}
        />
      ))}
    </div>
  );
}

function Legend({ ranking }: { ranking: Ranking }) {
  const { t } = useTranslate();
  return (
    <span className="contribution-legend" aria-hidden="true">
      {CRITERIA.filter((c) => c in ranking.weights).map((c) => (
        <span key={c}>
          <i className={`contribution ${c}`} /> {criterionLabel(t, c)}
        </span>
      ))}
    </span>
  );
}

/** Slope / descent facts the backend attached to a candidate, as short chips. */
function CandidateFacts({ candidate }: { candidate?: CandidateState }) {
  const { t } = useTranslate();
  if (!candidate) return null;
  const { safety, descent } = candidate;
  if (!safety?.covered && !descent) return null;
  return (
    <ul className="rec-facts">
      {safety?.covered && (
        <li>
          {t("recommendations.safety", {
            slope: safety.site_slope_deg?.toFixed(1) ?? "—",
            max: safety.max_landing_slope_deg?.toFixed(1) ?? "—",
          })}{" "}
          {safety.hazard_distance_m == null
            ? t("recommendations.noHazard")
            : t("recommendations.hazardAway", { m: safety.hazard_distance_m.toFixed(0) })}
        </li>
      )}
      {descent && (
        <li className={descent.feasible ? "good" : "bad"}>
          {t("recommendations.descent")}{" "}
          {descent.feasible
            ? t("recommendations.reachable", {
                kg: descent.propellant_margin_kg?.toFixed(0) ?? "—",
              })
            : t("recommendations.notReachable")}
        </li>
      )}
    </ul>
  );
}

function CriterionDetails({ row, ranking }: { row: Ranked; ranking: Ranking }) {
  const { t, tText } = useTranslate();
  const active = CRITERIA.filter((c) => c in ranking.weights);
  return (
    <dl className="rec-criteria">
      {active.map((c) => {
        const score = row.criteria[c];
        return (
          <div key={c}>
            <dt>
              <i className={`contribution ${c}`} aria-hidden="true" /> {criterionLabel(t, c)}
            </dt>
            <dd>
              <b>{score?.score == null ? "—" : score.score.toFixed(2)}</b>
              {score?.value != null && (
                <span>
                  {" "}
                  · {Number(score.value.toFixed(2))} {score.unit}
                </span>
              )}
              {score?.rule && <small>{tText(score.rule)}</small>}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function RankedCard({
  row,
  ranking,
  name,
  candidate,
  move,
}: {
  row: Ranked;
  ranking: Ranking;
  name: string;
  candidate?: CandidateState;
  move?: { from: number | null; reason: string };
}) {
  const { t, tText } = useTranslate();
  const top = row.rank === 1;
  return (
    <li className={`rec-card${top ? " top" : ""}`}>
      <div className="rec-card-head">
        <span className="rec-rank" aria-label={t("recommendations.col.rank")}>
          #{row.rank}
        </span>
        <div>
          {top && <p className="rec-kicker">{t("recommendations.recommended")}</p>}
          <h4>{name}</h4>
        </div>
        <span className="rec-score">
          <b>{row.total_score.toFixed(2)}</b>
          <small>{t("recommendations.col.score")}</small>
        </span>
      </div>
      <ContributionBar row={row} ranking={ranking} />
      <p className="rec-explanation">{tText(row.explanation)}</p>
      {move && move.from !== row.rank && (
        <p className="rec-move">
          {move.from == null || move.from > row.rank ? "▲" : "▼"}{" "}
          {t("recommendations.moved", { from: move.from ?? "—", to: row.rank, reason: move.reason })}
        </p>
      )}
      <CandidateFacts candidate={candidate} />
      <details className="rec-more">
        <summary>{t("recommendations.details")}</summary>
        <CriterionDetails row={row} ranking={ranking} />
      </details>
    </li>
  );
}

export default function Recommendations({ sites, onCandidates }: { sites: Preset[]; onCandidates?: (items: MissionCandidate[]) => void }) {
  const { t, tText } = useTranslate();
  const CRITERION_LABEL = (c: Criterion) => criterionLabel(t, c);
  const [picked, setPicked] = useState<string[]>([]);
  const [custom, setCustom] = useState<Custom[]>([]);
  const [start, setStart] = useState("2026-11-04T00:00");
  const [hours, setHours] = useState("24");
  const [refine, setRefine] = useState(true);
  const [slope, setSlope] = useState("10");
  const [minSun, setMinSun] = useState("");
  const [minComm, setMinComm] = useState("");
  const [dsnLimit, setDsnLimit] = useState(6);
  const [includeDescent, setIncludeDescent] = useState(false);
  const [weights, setWeights] = useState<Weights>(defaults);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [rescored, setRescored] = useState<Rescore | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  useEffect(() => {
    if (!rec) return;
    const ranking = rescored?.current ?? rec.ranking;
    onCandidates?.(rec.candidates.map((c) => {
      const row = ranking?.ranked.find((r) => r.candidate_id === c.candidate_id);
      const rejected = ranking?.rejected.find((r) => r.candidate_id === c.candidate_id);
      const insufficient = ranking?.insufficient_data.find((r) => r.candidate_id === c.candidate_id);
      const failed = ranking?.failed?.find((r) => r.candidate_id === c.candidate_id);
      return {
        id: `site:${c.candidate_id}`, label: c.site.site_id,
        lat: c.site.latitude_deg, lon: c.site.longitude_deg_east,
        status: failed || c.error ? "failed" : rejected ? "rejected" : row ? "accepted" : "unknown",
        score: row?.total_score ?? null,
        explanation: failed?.error ?? c.error ?? rejected?.explanation ?? insufficient?.explanation ?? row?.explanation ?? c.state,
        source: "Landing recommendation / API",
        metrics: Object.entries(row?.criteria ?? {}).map(([key, value]) => ({
          label: key, value: `${value?.value ?? "—"} ${value?.unit ?? ""} · score ${value?.score ?? "—"} · ${value?.rule ?? ""}`,
        })),
      };
    }));
  }, [rec, rescored, onCandidates]);
  const [delay, setDelay] = useState("48");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!picked.length && sites.length) setPicked(sites.map((s) => s.site_id));
  }, [sites, picked.length]);

  const poll = (id: string) => {
    window.clearTimeout(timer.current);
    call(`/recommendations/${encodeURIComponent(id)}`)
      .then((body: Recommendation) => {
        setRec(body);
        if (ACTIVE.includes(body.status)) {
          timer.current = window.setTimeout(() => poll(id), 2000);
        } else {
          setWeights({ ...defaults, ...body.request.weights });
          setIncludeDescent(Boolean(body.request.include_descent));
        }
      })
      .catch((e: Error) => setError(errorText(e, t, "communication.requestFailed")));
  };
  useEffect(() => {
    const saved = remembered();
    if (saved) poll(saved);
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async (path: string, body: unknown) => {
    setError("");
    setBusy(true);
    setRescored(null);
    try {
      const created: Recommendation = await call(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      remember(created.id);
      setSetupOpen(false);
      setRec(created);
      poll(created.id);
    } catch (e) {
      setError(errorText(e, t, "communication.requestFailed"));
    } finally {
      setBusy(false);
    }
  };

  const submit = () => {
    const begin = new Date(`${start}:00Z`);
    const candidates = [
      ...sites
        .filter((s) => picked.includes(s.site_id))
        .map((s) => ({
          site_id: s.site_id,
          latitude_deg: s.latitude_deg,
          longitude_deg_east: s.longitude_deg_east,
          observer_height_m: s.proposed_observer_height_above_terrain_m,
        })),
      ...custom.map((c, i) => ({
        site_id: `custom-${i + 1}`,
        latitude_deg: Number(c.lat),
        longitude_deg_east: Number(c.lon),
      })),
    ];
    run("/recommendations", {
      candidates,
      start_utc: begin.toISOString().replace(".000Z", "Z"),
      end_utc: new Date(begin.getTime() + Number(hours) * 3600000)
        .toISOString()
        .replace(".000Z", "Z"),
      refine,
      min_elevation_deg: dsnLimit,
      constraints: {
        slope_limit_deg: Number(slope),
        min_sunlight_percent: minSun === "" ? null : Number(minSun),
        min_communication_h: minComm === "" ? null : Number(minComm),
      },
      weights: { ...weights, fuel: includeDescent ? weights.fuel : 0 },
      include_descent: includeDescent,
    });
  };

  const applyWeights = async () => {
    if (!rec) return;
    setError("");
    try {
      setRescored(
        await call(`/recommendations/${encodeURIComponent(rec.id)}/rescore`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(weights),
        }),
      );
    } catch (e) {
      setError(errorText(e, t, "recommendations.rescoreFailed"));
    }
  };

  const names: Record<string, string> = {};
  for (const c of rec?.candidates ?? []) {
    const preset = sites.find((s) => s.site_id === c.site.site_id);
    names[c.candidate_id] = preset ? siteLabel(t, preset) : c.site.site_id;
  }
  const running = rec ? ACTIVE.includes(rec.status) : false;
  const finished = rec && ["ready", "partial"].includes(rec.status) && rec.ranking;
  const count = picked.length + custom.length;
  const ranking = rescored?.current ?? rec?.ranking ?? null;
  const candidateOf = (id: string) => rec?.candidates.find((c) => c.candidate_id === id);
  const evaluated = rec?.candidates.filter((c) => ["evaluated", "failed"].includes(c.state)).length ?? 0;
  const unranked = ranking
    ? [
        ...ranking.rejected.map((r) => ({ id: r.candidate_id, text: r.explanation })),
        ...ranking.insufficient_data.map((r) => ({ id: r.candidate_id, text: r.explanation })),
        ...(ranking.failed ?? []).map((r) => ({ id: r.candidate_id, text: r.error })),
      ]
    : [];
  const moves = new Map(
    (rescored?.changes ?? []).map((c) => [
      c.candidate_id,
      {
        from: c.previous_rank,
        reason: c.reason ? CRITERION_LABEL(c.reason) : t("recommendations.newWeights"),
      },
    ]),
  );
  const shownCriteria = CRITERIA.filter((c) => c !== "fuel" || includeDescent);
  const weightTotal = shownCriteria.reduce((sum, c) => sum + weights[c], 0);
  const locked = busy || running;
  const setupVisible = !rec || setupOpen;

  const priorities = (
    <div className="rec-priorities">
      <div className="priority-split" role="img" aria-label={t("recommendations.prioritySplit")}>
        {shownCriteria.map((c) => (
          <span
            key={c}
            className={`contribution ${c}`}
            style={{ flexGrow: weights[c] }}
            title={`${CRITERION_LABEL(c)} ${weightTotal ? Math.round((weights[c] / weightTotal) * 100) : 0}%`}
          />
        ))}
      </div>
      {shownCriteria.map((c) => (
        <label key={c} className="weight-row">
          <span>
            <i className={`contribution ${c}`} aria-hidden="true" /> {CRITERION_LABEL(c)}
          </span>
          <output>
            {weightTotal ? Math.round((weights[c] / weightTotal) * 100) : 0}%
          </output>
          <input
            aria-label={t("recommendations.weightLabel", { criterion: CRITERION_LABEL(c) })}
            type="range"
            min="0"
            max="10"
            step="0.5"
            value={weights[c]}
            onChange={(e) => setWeights({ ...weights, [c]: Number(e.target.value) })}
          />
        </label>
      ))}
    </div>
  );

  return (
    <section
      id="recommendations"
      className="window-search-section recommendation-section"
      aria-label={t("recommendations.label")}
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t("recommendations.eyebrow")}</p>
          <h2>
            {t("recommendations.title")}<span>?</span>
          </h2>
          <p className="muted">{t("recommendations.intro")}</p>
        </div>
      </div>

      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}

      {rec && (
        <div className={`panel rec-status ${rec.status}`} role="status">
          <div>
            <strong>
              {STATUSES.includes(rec.status) ? t(`recommendations.status.${rec.status}`) : rec.status}
            </strong>
            <span>
              {utc(rec.request.start_utc)} → {utc(rec.request.end_utc)}
            </span>
          </div>
          {running && (
            <>
              <progress max={rec.candidates.length} value={evaluated} />
              <ul className="rec-pipeline">
                {rec.candidates.map((c) => (
                  <li key={c.candidate_id} data-state={c.state}>
                    <b>{names[c.candidate_id]}</b>
                    <span>
                      {STATES.includes(c.state) ? t(`recommendations.state.${c.state}`) : c.state}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {rec.error && <p className="alert">{rec.error}</p>}
          {!running && (
            <button
              type="button"
              className="text-button"
              aria-expanded={setupVisible}
              onClick={() => setSetupOpen(!setupOpen)}
            >
              {setupOpen ? t("recommendations.hideSetup") : t("recommendations.editSetup")}
            </button>
          )}
        </div>
      )}

      {setupVisible && (
        <form
          className="panel rec-setup"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <fieldset disabled={locked}>
            <legend>
              <span className="rec-step">1</span> {t("recommendations.candidates")}
              <small>{t("recommendations.selected", { n: count })}</small>
            </legend>
            <div className="rec-chips">
              {sites.map((s) => (
                <label className="rec-chip" key={s.site_id}>
                  <input
                    type="checkbox"
                    checked={picked.includes(s.site_id)}
                    disabled={!picked.includes(s.site_id) && count >= 6}
                    onChange={(e) =>
                      setPicked(
                        e.target.checked
                          ? [...picked, s.site_id]
                          : picked.filter((id) => id !== s.site_id),
                      )
                    }
                  />
                  <span>{siteLabel(t, s)}</span>
                </label>
              ))}
            </div>
            {custom.map((c, i) => (
              <div className="rec-custom" key={i}>
                <label>
                  {t("recommendations.customLat", { n: i + 1 })} · °
                  <input
                    type="number"
                    step="any"
                    min="-90"
                    max="-80"
                    value={c.lat}
                    onChange={(e) =>
                      setCustom(custom.map((x, j) => (j === i ? { ...x, lat: e.target.value } : x)))
                    }
                  />
                </label>
                <label>
                  {t("app.form.longitude")} · °E
                  <input
                    type="number"
                    step="any"
                    min="0"
                    max="359.999"
                    value={c.lon}
                    onChange={(e) =>
                      setCustom(custom.map((x, j) => (j === i ? { ...x, lon: e.target.value } : x)))
                    }
                  />
                </label>
                <button
                  type="button"
                  className="rec-remove"
                  aria-label={t("recommendations.removeCustomN", { n: i + 1 })}
                  onClick={() => setCustom(custom.filter((_, j) => j !== i))}
                >
                  ×
                </button>
              </div>
            ))}
            <button
              type="button"
              className="text-button"
              disabled={count >= 6}
              onClick={() => setCustom([...custom, { lat: "-85.5", lon: "0" }])}
            >
              + {t("recommendations.addCustom")}
            </button>
          </fieldset>

          <fieldset disabled={locked}>
            <legend>
              <span className="rec-step">2</span> {t("recommendations.window")}
            </legend>
            <div className="rec-window">
              <label>
                {t("recommendations.start")} · UTC
                <input
                  aria-label={t("recommendations.startLabel")}
                  type="datetime-local"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </label>
              <div className="rec-segment" role="radiogroup" aria-label={t("recommendations.duration")}>
                <span>{t("recommendations.duration")}</span>
                <div>
                  {DURATIONS.map((h) => (
                    <button
                      type="button"
                      key={h}
                      role="radio"
                      aria-checked={hours === h}
                      onClick={() => setHours(h)}
                    >
                      {h === "24" ? t("app.form.hours", { n: 24 }) : t("app.form.days", { n: Number(h) / 24 })}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <label className="checkbox-label">
              <input type="checkbox" checked={refine} onChange={(e) => setRefine(e.target.checked)} />
              {t("recommendations.refine")}
            </label>
          </fieldset>

          <fieldset disabled={locked}>
            <legend>
              <span className="rec-step">3</span> {t("recommendations.priorities")}
            </legend>
            <label className="checkbox-label">
              <input
                type="checkbox"
                aria-label={t("recommendations.includeDescentLabel")}
                checked={includeDescent}
                onChange={(e) => {
                  setIncludeDescent(e.target.checked);
                  if (e.target.checked && weights.fuel === 0) setWeights({ ...weights, fuel: 1 });
                }}
              />
              {t("recommendations.includeDescent")}
            </label>
            {!finished && priorities}
            {finished && <p className="field-help">{t("recommendations.prioritiesBelow")}</p>}
          </fieldset>

          <details className="rec-constraints">
            <summary>
              {t("recommendations.hard")}
              <small>{t("recommendations.constraintsSummary", { slope, dsn: dsnLimit })}</small>
            </summary>
            <fieldset disabled={locked}>
              <label>
                {t("recommendations.slope")} · °
                <input
                  aria-label={t("recommendations.slopeLabel")}
                  type="number"
                  min="1"
                  max="45"
                  step="0.5"
                  value={slope}
                  onChange={(e) => setSlope(e.target.value)}
                />
              </label>
              <label>
                {t("recommendations.minSun")}
                <input
                  type="number"
                  min="0"
                  max="100"
                  value={minSun}
                  onChange={(e) => setMinSun(e.target.value)}
                />
              </label>
              <label>
                {t("recommendations.minComm")}
                <input
                  type="number"
                  min="0"
                  step="0.5"
                  value={minComm}
                  onChange={(e) => setMinComm(e.target.value)}
                />
              </label>
              <label>
                {t("recommendations.dsnLimit")}
                <select value={dsnLimit} onChange={(e) => setDsnLimit(Number(e.target.value))}>
                  <option value={6}>{t("recommendations.limit6")}</option>
                  <option value={10}>{t("recommendations.limit10")}</option>
                </select>
              </label>
              <p className="field-help">{t("recommendations.slopeNote")}</p>
            </fieldset>
          </details>

          <button type="submit" className="primary" disabled={locked || count < 2 || count > 6}>
            {t("recommendations.rank")} ↗
          </button>
          {count < 2 && <p className="field-help">{t("recommendations.empty")}</p>}
        </form>
      )}

      {!rec && <p className="rec-empty muted">{t("recommendations.emptyResult")}</p>}

      {finished && ranking && (
        <div className="rec-results">
          <h3 className="rec-section-title">
            {rescored ? t("recommendations.new") : t("recommendations.ranking")}
          </h3>
          {ranking.ranked.length ? (
            <>
              <Legend ranking={ranking} />
              <ol className="rec-list">
                {ranking.ranked.map((row) => (
                  <RankedCard
                    key={row.candidate_id}
                    row={row}
                    ranking={ranking}
                    name={names[row.candidate_id] ?? row.candidate_id}
                    candidate={candidateOf(row.candidate_id)}
                    move={moves.get(row.candidate_id)}
                  />
                ))}
              </ol>
            </>
          ) : (
            <div className="panel search-empty">{t("recommendations.nonePassed")}</div>
          )}
          {rescored && rescored.changes.every((c) => c.previous_rank === c.new_rank) && (
            <p className="rec-move">{t("recommendations.noChange")}</p>
          )}

          {unranked.length > 0 && (
            <>
              <h3 className="rec-section-title">{t("recommendations.rejected")}</h3>
              <ul className="rec-unranked" aria-label={t("recommendations.rejected")}>
                {unranked.map((r) => (
                  <li key={r.id}>
                    <b>{names[r.id] ?? r.id}</b>
                    <span>{tText(r.text)}</span>
                    <CandidateFacts candidate={candidateOf(r.id)} />
                  </li>
                ))}
              </ul>
            </>
          )}

          <div className="panel rec-whatif">
            <h3>{t("recommendations.whatIf")}</h3>
            <p className="muted">{t("recommendations.whatIfIntro")}</p>
            <h4>{t("recommendations.priorities")}</h4>
            {priorities}
            <button type="button" className="primary" disabled={busy} onClick={applyWeights}>
              {t("recommendations.apply")}
            </button>
            <h4>{t("recommendations.slip")}</h4>
            <div className="rec-slip">
              <input
                aria-label={t("recommendations.slipLabel")}
                type="number"
                min="1"
                max="720"
                value={delay}
                onChange={(e) => setDelay(e.target.value)}
              />
              <button
                type="button"
                className="text-button"
                disabled={busy}
                onClick={() =>
                  run(`/recommendations/${encodeURIComponent(rec.id)}/reschedule`, {
                    delay_hours: Number(delay),
                  })
                }
              >
                {t("recommendations.rerank")} ↗
              </button>
            </div>
          </div>

          {rescored && rec.ranking && (
            <details className="rec-more">
              <summary>{t("recommendations.previous")}</summary>
              <ol className="rec-list compact">
                {rescored.previous.ranked.map((row) => (
                  <li key={row.candidate_id} className="rec-card">
                    <div className="rec-card-head">
                      <span className="rec-rank">#{row.rank}</span>
                      <h4>{names[row.candidate_id] ?? row.candidate_id}</h4>
                      <span className="rec-score">
                        <b>{row.total_score.toFixed(2)}</b>
                      </span>
                    </div>
                    <ContributionBar row={row} ranking={rescored.previous} />
                  </li>
                ))}
              </ol>
            </details>
          )}

          <details className="rec-more">
            <summary>{t("recommendations.notModeled")}</summary>
            <ul className="not-modeled">
              {Object.entries(ranking.not_modeled).map(([k, v]) => (
                <li key={k}>
                  <b>{tText(k)}:</b> {tText(v)}
                </li>
              ))}
            </ul>
          </details>
        </div>
      )}

      {rec?.schedule_change && (
        <div className="panel schedule-change" role="status">
          <strong>{tText(rec.schedule_change.suggestion)}</strong>
          <ul>
            {rec.schedule_change.changes.map((c) => (
              <li key={c.candidate_id}>
                {names[c.candidate_id] ?? c.site_id}: {c.previous_status}
                {c.previous_rank ? ` #${c.previous_rank}` : ""} → {c.new_status}
                {c.new_rank ? ` #${c.new_rank}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
