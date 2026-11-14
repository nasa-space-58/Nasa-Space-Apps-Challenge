import { useEffect, useState } from "react";
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
        : "Scenario request rejected",
    );
  return data;
}
export default function ScenarioTransfer({
  onOpen,
}: {
  onOpen: (id: string, compare: boolean, bId?: string) => Promise<void>;
}) {
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
          setError(
            "A calculation failed. Review the saved jobs, then run the scenario again.",
          );
          setProgress("Calculation failed");
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
            setProgress("Calculation complete");
          }
        } else {
          setBusy(true);
          setProgress(
            jobs
              .map((job, i) => `${i === 0 ? "A" : "B"}: ${job.status}`)
              .join(" · "),
          );
          timer = setTimeout(poll, 1800);
        }
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "Connection interrupted");
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
        throw Error("Scenario file must be at most 1 MB");
      const doc = await api(
        "/scenarios/validate",
        JSON.parse(await file.text()),
      );
      setIds([]);
      setManifest(doc);
      setProgress("Inputs validated. Nothing has been queued.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid scenario file");
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
      setProgress("Calculations queued");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to queue scenario");
      setProgress(
        `${next.length} calculation(s) queued; existing jobs remain saved.`,
      );
      setBusy(false);
    }
  };
  return (
    <details className="panel scenario-transfer">
      <summary>Import a shared scenario file</summary>
      <p>
        Restore one analysis or an A/B pair from JSON. This uses the current
        data and model; the original fingerprints are checked after calculation.
      </p>
      <label>
        Scenario JSON
        <input
          aria-label="Import scenario JSON"
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
                <h3>Scenario {i === 0 ? "A" : "B"}</h3>
                <p>
                  {String(scenario.submission.site_id)} ·{" "}
                  {String(scenario.submission.start_utc)} —{" "}
                  {String(scenario.submission.end_utc)}
                </p>
                <p>
                  Expected model: {scenario.expected.model_version} /{" "}
                  {scenario.expected.horizon_model_version}
                </p>
              </div>
            ))}
          </div>
          <button className="primary" disabled={busy} onClick={run}>
            {busy ? "Calculating…" : "Calculate imported scenario"} ↗
          </button>
          <p className="field-help">
            Validation alone does not download data or start a job. Fingerprints
            check exact result contents, not the authenticity of an imported
            file.
          </p>
        </>
      )}
      {progress && <p role="status">{progress}</p>}
      {error && (
        <p role="alert" className="alert">
          {error}{" "}
          <button onClick={() => setRetry((n) => n + 1)}>
            Reconnect import
          </button>
        </p>
      )}
      {checked && (
        <div className="import-verdict" role="status">
          <strong>
            {checked.matches
              ? "Exact result fingerprint match"
              : "Results differ from the shared snapshot"}
          </strong>
          <p>
            {checked.matches
              ? "The calculated contents match the exported result."
              : "Inputs, acquisition metadata, source data or model versions may differ. These are new results; do not present them as the original snapshot."}
          </p>
          <button
            onClick={() =>
              onOpen(ids[0], ids.length === 2, ids[1]).catch((e) =>
                setError(e.message),
              )
            }
          >
            Open imported {ids.length === 2 ? "comparison" : "analysis"} ↗
          </button>
        </div>
      )}
    </details>
  );
}
