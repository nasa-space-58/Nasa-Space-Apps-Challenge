import { useEffect, useRef, useState } from "react";
import type { MissionCandidate } from "../missionCandidates";
import type { Preset } from "../types";
import { errorText, useTranslate, type Translate } from "../i18n";

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

function RankingTable({
  ranking,
  names,
  caption,
}: {
  ranking: Ranking;
  names: Record<string, string>;
  caption: string;
}) {
  const { t } = useTranslate();
  const CRITERION_LABEL = (c: Criterion) => criterionLabel(t, c);
  const active = CRITERIA.filter((c) => c in ranking.weights);
  return (
    <table className="ranking-table">
      <caption>
        {caption}
        <span className="contribution-legend" aria-hidden="true">
          {active.map((c) => (
            <span key={c}>
              <i className={`contribution ${c}`} /> {CRITERION_LABEL(c)}
            </span>
          ))}
        </span>
      </caption>
      <thead>
        <tr>
          <th scope="col">{t("recommendations.col.rank")}</th>
          <th scope="col">{t("recommendations.col.site")}</th>
          <th scope="col">{t("recommendations.col.score")}</th>
          <th scope="col">{t("recommendations.col.contribution")}</th>
        </tr>
      </thead>
      <tbody>
        {ranking.ranked.map((row) => (
          <tr key={row.candidate_id}>
            <td>#{row.rank}</td>
            <th scope="row">{names[row.candidate_id] ?? row.candidate_id}</th>
            <td>{row.total_score.toFixed(2)}</td>
            <td>
              <div
                className="contribution-bar"
                role="img"
                aria-label={active
                  .map((c) => `${CRITERION_LABEL(c)} ${(row.contributions[c] ?? 0).toFixed(2)}`)
                  .join(", ")}
              >
                {active.map((c) => (
                  <span
                    key={c}
                    className={`contribution ${c}`}
                    style={{ width: `${(row.contributions[c] ?? 0) * 100}%` }}
                    title={t("recommendations.contributionTitle", {
                      criterion: CRITERION_LABEL(c),
                      score: row.criteria[c]?.score?.toFixed(2) ?? "—",
                      weight: (ranking.weights[c] ?? 0).toFixed(2),
                    })}
                  />
                ))}
              </div>
              <small>{row.explanation}</small>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
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
    names[c.candidate_id] =
      sites.find((s) => s.site_id === c.site.site_id)?.display_name ?? c.site.site_id;
  }
  const running = rec ? ACTIVE.includes(rec.status) : false;
  const finished = rec && ["ready", "partial"].includes(rec.status) && rec.ranking;
  const count = picked.length + custom.length;

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
        <span className="outline-tag">{t("recommendations.tag")}</span>
      </div>
      <div className="search-layout">
        <form
          className="panel search-form"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <fieldset disabled={busy || running}>
            <h3>{t("recommendations.candidates")}</h3>
            {sites.map((s) => (
              <label className="checkbox-label" key={s.site_id}>
                <input
                  type="checkbox"
                  checked={picked.includes(s.site_id)}
                  onChange={(e) =>
                    setPicked(
                      e.target.checked
                        ? [...picked, s.site_id]
                        : picked.filter((id) => id !== s.site_id),
                    )
                  }
                />
                {s.display_name}
              </label>
            ))}
            {custom.map((c, i) => (
              <div className="field-pair" key={i}>
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
            {custom.length > 0 && (
              <button
                type="button"
                className="text-button"
                onClick={() => setCustom(custom.slice(0, -1))}
              >
                − {t("recommendations.removeCustom")}
              </button>
            )}
            <h3>{t("recommendations.window")}</h3>
            <label>
              {t("recommendations.start")} · UTC
              <input
                aria-label={t("recommendations.startLabel")}
                type="datetime-local"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label>
              {t("recommendations.duration")}
              <select value={hours} onChange={(e) => setHours(e.target.value)}>
                <option value="24">{t("app.form.hours", { n: 24 })}</option>
                <option value="72">{t("app.form.days", { n: 3 })}</option>
                <option value="168">{t("app.form.days", { n: 7 })}</option>
              </select>
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={refine}
                onChange={(e) => setRefine(e.target.checked)}
              />
              {t("recommendations.refine")}
            </label>
            <h3>{t("recommendations.hard")}</h3>
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
              <select
                value={dsnLimit}
                onChange={(e) => setDsnLimit(Number(e.target.value))}
              >
                <option value={6}>{t("recommendations.limit6")}</option>
                <option value={10}>{t("recommendations.limit10")}</option>
              </select>
            </label>
          </fieldset>
          <h3>{t("recommendations.priorities")}</h3>
          <label className="checkbox-label">
            <input
              type="checkbox"
              aria-label={t("recommendations.includeDescentLabel")}
              checked={includeDescent}
              disabled={busy || running}
              onChange={(e) => {
                setIncludeDescent(e.target.checked);
                if (e.target.checked && weights.fuel === 0) setWeights({ ...weights, fuel: 1 });
              }}
            />
            {t("recommendations.includeDescent")}
          </label>
          {CRITERIA.filter((c) => c !== "fuel" || includeDescent).map((c) => (
            <label key={c}>
              {CRITERION_LABEL(c)} · {weights[c]}
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
          <button
            type="submit"
            className="primary"
            disabled={busy || running || count < 2 || count > 6}
          >
            {t("recommendations.rank")} ↗
          </button>
          {finished && (
            <button type="button" className="text-button" onClick={applyWeights}>
              {t("recommendations.apply")}
            </button>
          )}
          <p className="field-help">{t("recommendations.slopeNote")}</p>
        </form>
        <div className="search-results">
          {error && (
            <div className="alert" role="alert">
              {error}
            </div>
          )}
          {!rec && (
            <div className="panel search-empty">
              {t("recommendations.empty")}
            </div>
          )}
          {rec && (
            <div className="panel search-progress" role="status">
              <div>
                <strong>
                  {STATUSES.includes(rec.status)
                    ? t(`recommendations.status.${rec.status}`)
                    : rec.status}
                </strong>
                <span>
                  {utc(rec.request.start_utc)} → {utc(rec.request.end_utc)}
                </span>
              </div>
              {rec.error && <p className="alert">{rec.error}</p>}
            </div>
          )}
          {rec && (
            <ul className="candidate-status">
              {rec.candidates.map((c) => (
                <li key={c.candidate_id}>
                  <b>{names[c.candidate_id]}</b> ·{" "}
                  {STATES.includes(c.state) ? t(`recommendations.state.${c.state}`) : c.state}
                  {c.safety?.covered && (
                    <>
                      {" "}
                      ·{" "}
                      {t("recommendations.safety", {
                        slope: c.safety.site_slope_deg?.toFixed(1) ?? "—",
                        max: c.safety.max_landing_slope_deg?.toFixed(1) ?? "—",
                      })}{" "}
                      {c.safety.hazard_distance_m == null
                        ? t("recommendations.noHazard")
                        : t("recommendations.hazardAway", {
                            m: c.safety.hazard_distance_m.toFixed(0),
                          })}
                    </>
                  )}
                  {c.descent && (
                    <>
                      {" "}
                      · {t("recommendations.descent")}{" "}
                      {c.descent.feasible
                        ? t("recommendations.reachable", {
                            kg: c.descent.propellant_margin_kg?.toFixed(0) ?? "—",
                          })
                        : t("recommendations.notReachable")}
                    </>
                  )}
                  {c.error && <span className="alert"> {c.error}</span>}
                </li>
              ))}
            </ul>
          )}
          {finished && rec.ranking && (
            <>
              {rec.ranking.ranked.length ? (
                <RankingTable
                  ranking={rec.ranking}
                  names={names}
                  caption={t("recommendations.ranking")}
                />
              ) : (
                <div className="panel search-empty">{t("recommendations.nonePassed")}</div>
              )}
              {[...rec.ranking.rejected, ...rec.ranking.insufficient_data].length > 0 && (
                <ul className="rejections" aria-label={t("recommendations.rejected")}>
                  {[...rec.ranking.rejected, ...rec.ranking.insufficient_data].map((r) => (
                    <li key={r.candidate_id}>
                      <b>{names[r.candidate_id]}</b> — {r.explanation}
                    </li>
                  ))}
                  {(rec.ranking.failed ?? []).map((r) => (
                    <li key={r.candidate_id}>
                      <b>{names[r.candidate_id]}</b> — {r.error}
                    </li>
                  ))}
                </ul>
              )}
              <ul className="not-modeled">
                {Object.entries(rec.ranking.not_modeled).map(([k, v]) => (
                  <li key={k}>
                    <b>{tText(k)}:</b> {tText(v)}
                  </li>
                ))}
              </ul>
            </>
          )}
          {rescored && (
            <div className="rescore-compare">
              <RankingTable
                ranking={rescored.previous}
                names={names}
                caption={t("recommendations.previous")}
              />
              <RankingTable
                ranking={rescored.current}
                names={names}
                caption={t("recommendations.new")}
              />
              <ul aria-label={t("recommendations.changes")}>
                {rescored.changes
                  .filter((c) => c.previous_rank !== c.new_rank)
                  .map((c) => (
                    <li key={c.candidate_id}>
                      <b>{names[c.candidate_id]}</b>{" "}
                      {t("recommendations.moved", {
                        from: c.previous_rank ?? "—",
                        to: c.new_rank ?? "—",
                        reason: c.reason
                          ? CRITERION_LABEL(c.reason)
                          : t("recommendations.newWeights"),
                      })}
                    </li>
                  ))}
                {rescored.changes.every((c) => c.previous_rank === c.new_rank) && (
                  <li>{t("recommendations.noChange")}</li>
                )}
              </ul>
            </div>
          )}
          {finished && (
            <div className="panel reschedule">
              <label>
                {t("recommendations.slip")}
                <input
                  aria-label={t("recommendations.slipLabel")}
                  type="number"
                  min="1"
                  max="720"
                  value={delay}
                  onChange={(e) => setDelay(e.target.value)}
                />
              </label>
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
          )}
          {rec?.schedule_change && (
            <div className="panel schedule-change" role="status">
              <strong>{rec.schedule_change.suggestion}</strong>
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
        </div>
      </div>
    </section>
  );
}
