import { useEffect, useState } from "react";
import { errorText, useTranslate } from "../i18n";
type Manifest = {
  schema_version: number;
  kind: string;
  scenarios: {
    submission: Record<string, unknown>;
    expected: {
      model_version: string;
      horizon_model_version: string;
      result_sha256: string;
    };
  }[];
};
async function api(path: string, body?: unknown) {
  const response = await fetch(
    "/api" + path,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  const data = await response.json();
  if (!response.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : "",
    );
  return data;
}
export default function ScenarioTransfer({
  onOpen,
}: {
  onOpen: (id: string, compare: boolean, bId?: string) => Promise<void>;
}) {
  const { t } = useTranslate();
  const [manifest, setManifest] = useState<Manifest | null>(null),
    [ids, setIds] = useState<string[]>([]),
    [error, setError] = useState("");
  const [busy, setBusy] = useState(false),
    [progress, setProgress] = useState(""),
    [checked, setChecked] = useState<{ matches: boolean } | null>(null),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("lunar-import") || "null");
      if (saved) {
        setManifest(saved.manifest);
        setIds(saved.ids);
      }
    } catch {
      localStorage.removeItem("lunar-import");
    }
  }, []);
  useEffect(() => {
    if (manifest)
      localStorage.setItem("lunar-import", JSON.stringify({ manifest, ids }));
  }, [manifest, ids]);
  useEffect(() => {
    if (!manifest || ids.length !== manifest.scenarios.length) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const jobs = await Promise.all(ids.map((id) => api("/analyses/" + id)));
        if (!active) return;
        if (jobs.some((job) => job.status === "failed")) {
          setBusy(false);
          setError(t("scenario.calcFailedDetail"));
          setProgress(t("scenario.calcFailed"));
          return;
        }
        if (
          jobs.every((job) =>
            ["ready", "insufficient_data"].includes(job.status),
          )
        ) {
          const verdict = await api("/scenarios/check", {
            manifest,
            job_ids: ids,
          });
          if (active) {
            setChecked(verdict);
            setBusy(false);
            setError("");
            setProgress(t("scenario.calcComplete"));
          }
        } else {
          setBusy(true);
          setProgress(
            jobs
              .map((job, i) => `${i === 0 ? "A" : "B"}: ${t(`jobState.${job.status}`)}`)
              .join(" · "),
          );
          timer = setTimeout(poll, 1800);
        }
      } catch (e) {
        if (active) {
          setError(errorText(e, t, "scenario.connection"));
          timer = setTimeout(poll, 5000);
        }
      }
    };
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [ids, manifest, retry]);
  const load = async (file: File) => {
    setError("");
    setChecked(null);
    setManifest(null);
    setIds([]);
    setBusy(true);
    localStorage.removeItem("lunar-import");
    try {
      if (file.size > 1_000_000)
        throw Error(t("scenario.tooLarge"));
      const doc = await api(
        "/scenarios/validate",
        JSON.parse(await file.text()),
      );
      setIds([]);
      setManifest(doc);
      setProgress(t("scenario.validated"));
    } catch (e) {
      setError(errorText(e, t, "scenario.invalid"));
    } finally {
      setBusy(false);
    }
  };
  const run = async () => {
    if (!manifest) return;
    setBusy(true);
    setError("");
    setChecked(null);
    setIds([]);
    const next: string[] = [];
    try {
      for (const scenario of manifest.scenarios) {
        const job = await api("/analyses", scenario.submission);
        next.push(job.id);
        setIds([...next]);
      }
      setProgress(t("scenario.queued"));
    } catch (e) {
      setError(errorText(e, t, "scenario.queueFailed"));
      setProgress(t("scenario.partialQueued", { n: next.length }));
      setBusy(false);
    }
  };
  return (
    <details className="panel scenario-transfer">
      <summary>{t("scenario.summary")}</summary>
      <p>{t("scenario.intro")}</p>
      <label>
        {t("scenario.json")}
        <input
          aria-label={t("scenario.importLabel")}
          disabled={busy}
          type="file"
          accept="application/json,.json"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) load(file);
            e.target.value = "";
          }}
        />
      </label>
      {manifest && (
        <>
          <div className="import-scenarios">
            {manifest.scenarios.map((scenario, i) => (
              <div key={i}>
                <h3>{t("scenario.title", { name: i === 0 ? "A" : "B" })}</h3>
                <p>
                  {String(scenario.submission.site_id)} ·{" "}
                  {String(scenario.submission.start_utc)} —{" "}
                  {String(scenario.submission.end_utc)}
                </p>
                <p>
                  {t("scenario.expectedModel")}: {scenario.expected.model_version} /{" "}
                  {scenario.expected.horizon_model_version}
                </p>
              </div>
            ))}
          </div>
          <button className="primary" disabled={busy} onClick={run}>
            {busy ? t("scenario.calculating") : t("scenario.calculate")} ↗
          </button>
          <p className="field-help">{t("scenario.validationNote")}</p>
        </>
      )}
      {progress && <p role="status">{progress}</p>}
      {error && (
        <p role="alert" className="alert">
          {error}{" "}
          <button onClick={() => setRetry((n) => n + 1)}>
            {t("scenario.reconnect")}
          </button>
        </p>
      )}
      {checked && (
        <div className="import-verdict" role="status">
          <strong>
            {checked.matches
              ? t("scenario.match")
              : t("scenario.differ")}
          </strong>
          <p>
            {checked.matches
              ? t("scenario.matchDetail")
              : t("scenario.differDetail")}
          </p>
          <button
            onClick={() =>
              onOpen(ids[0], ids.length === 2, ids[1]).catch((e) =>
                setError(e.message),
              )
            }
          >
            {ids.length === 2 ? t("scenario.openComparison") : t("scenario.openAnalysis")} ↗
          </button>
        </div>
      )}
    </details>
  );
}
