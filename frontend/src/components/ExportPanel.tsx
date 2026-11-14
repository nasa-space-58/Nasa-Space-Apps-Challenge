import { useRef, useState } from "react";
import type { Result } from "../types";
import HorizonPlot from "./HorizonPlot";
import { errorText, useTranslate } from "../i18n";

export default function ExportPanel({
  jobId,
  result,
  frameIndex = 0,
  bId,
  b,
  bFrameIndex = 0,
  cursor = 0,
}: {
  jobId: string;
  result: Result;
  frameIndex?: number;
  bId?: string;
  b?: Result;
  bFrameIndex?: number;
  cursor?: number;
}) {
  const { t } = useTranslate();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const svg = useRef<SVGSVGElement>(null);
  const params = new URLSearchParams({
    analysis: jobId,
    frame: String(frameIndex),
  });
  if (bId) {
    params.set("compare", bId);
    params.set("cursor", String(cursor));
  }
  const link = `${location.origin}${location.pathname}?${params}`;
  const endpoint = `/api/analyses/${encodeURIComponent(jobId)}/export/`;
  const suffix = bId ? `?b_id=${encodeURIComponent(bId)}` : "";
  const download = async (kind: string) => {
    setError("");
    setBusy(true);
    try {
      const response = await fetch(endpoint + kind + suffix);
      if (!response.ok) {
        const body = await response.json();
        throw Error(
          typeof body.detail === "string" ? body.detail : t("export.unavailable"),
        );
      }
      const blob = await response.blob();
      save(
        blob,
        `lunar-${bId ? "comparison" : "analysis"}.${kind === "scenario" ? "json" : kind === "report" ? "html" : "csv"}`,
      );
      setMessage(
        kind === "report"
          ? t("export.reportReady")
          : t("export.downloadReady"),
      );
    } catch (e) {
      setError(errorText(e, t, "export.failed"));
    } finally {
      setBusy(false);
    }
  };
  const png = async () => {
    setError("");
    setBusy(true);
    let url = "";
    try {
      if (!svg.current) throw Error(t("export.chartNotReady"));
      const original = svg.current;
      const clone = original.cloneNode(true) as SVGSVGElement;
      const nodes = [original, ...Array.from(original.querySelectorAll("*"))];
      const copies = [clone, ...Array.from(clone.querySelectorAll("*"))];
      nodes.forEach((node, i) => {
        const style = getComputedStyle(node);
        for (const key of [
          "fill",
          "stroke",
          "stroke-width",
          "font-family",
          "font-size",
          "font-weight",
          "opacity",
          "stroke-dasharray",
        ])
          copies[i].setAttribute(key, style.getPropertyValue(key));
      });
      clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      url = URL.createObjectURL(
        new Blob([new XMLSerializer().serializeToString(clone)], {
          type: "image/svg+xml",
        }),
      );
      const img = new Image();
      img.src = url;
      await img.decode();
      const canvas = document.createElement("canvas");
      canvas.width = 2400;
      canvas.height = (b ? 1060 : 560) * 2;
      const context = canvas.getContext("2d");
      if (!context) throw Error(t("export.needsCanvas"));
      context.drawImage(img, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob>((resolve, reject) =>
        canvas.toBlob(
          (value) =>
            value ? resolve(value) : reject(Error(t("export.encodeFailed"))),
          "image/png",
        ),
      );
      save(blob, "lunar-charts.png");
      setMessage(t("export.pngReady"));
    } catch (e) {
      setError(errorText(e, t, "export.pngFailed"));
    } finally {
      if (url) URL.revokeObjectURL(url);
      setBusy(false);
    }
  };
  const views = [
    { label: "A", view: result, index: frameIndex },
    ...(b ? [{ label: "B", view: b, index: bFrameIndex }] : []),
  ];
  return (
    <div className="export-panel">
      <button
        className="text-button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {t("export.toggle")} ↗
      </button>
      {open && (
        <div className="panel export-options">
          <h3>{t("export.title")}</h3>
          <div className="export-actions">
            <button disabled={busy} onClick={() => download("csv")}>
              {t("export.csv")}
            </button>
            <button disabled={busy} onClick={png}>
              {t("export.png")}
            </button>
            <button disabled={busy} onClick={() => download("report")}>
              {t("export.report")}
            </button>
            <button disabled={busy} onClick={() => download("scenario")}>
              {t("export.scenario")}
            </button>
          </div>
          <label>
            {t("export.link")}
            <input
              aria-label={t("export.link")}
              readOnly
              value={link}
              onFocus={(e) => e.target.select()}
            />
          </label>
          <button
            className="text-button"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(link);
                setMessage(t("export.copied"));
              } catch {
                setMessage(t("export.copyManually"));
              }
            }}
          >
            {t("export.copy")}
          </button>
          <p className="field-help">{t("export.linkNote")}</p>
          {message && <p role="status">{message}</p>}
          {error && (
            <p role="alert" className="alert">
              {error}
            </p>
          )}
        </div>
      )}
      <div className="chart-export-source" aria-hidden="true">
        <svg
          ref={svg}
          width="1200"
          height={b ? 1060 : 560}
          viewBox={`0 0 1200 ${b ? 1060 : 560}`}
        >
          <rect width="100%" height="100%" fill="#0b1117" />
          {views.map(({ label, view, index }, i) => {
            const frame = view.frames[index] || view.frames[0];
            const from = Date.parse(view.analysis.request.start_utc),
              span = Date.parse(view.analysis.request.end_utc) - from;
            const colors: Record<string, string> = {
              full: "#edbb66",
              partial: "#9c7841",
              dark: "#37434c",
              visible: "#9ac9e5",
              blocked: "#37434c",
              unknown: "#ba86bd",
              boundary: "#e4ae8a",
            };
            return (
              <g
                key={label}
                transform={`translate(0 ${i * 500})`}
                fill="#e7edf2"
                fontFamily="monospace"
                fontSize="12"
              >
                <text x="40" y="30">
                  {label} · {view.analysis.request.site.site_id} ·{" "}
                  {t("export.chart.sample", { utc: frame.utc })}
                </text>
                <text x="40" y="52">
                  {t("export.chart.window", {
                    start: view.analysis.request.start_utc,
                    end: view.analysis.request.end_utc,
                  })}
                </text>
                <text x="40" y="73" fontSize="10">
                  {t("app.trace.analysis")}: {view.analysis.analysis_id}
                </text>
                <svg x="40" y="90" width="1100" height="280">
                  <HorizonPlot
                    result={view}
                    frame={frame}
                    scaleResults={views.map((x) => x.view)}
                  />
                </svg>
                {(["sun", "earth"] as const).map((target, j) => (
                  <g key={target} transform={`translate(0 ${380 + j * 36})`}>
                    <text x="40" y="14">
                      {target === "sun" ? t("common.sun") : t("common.earth")}
                    </text>
                    {view.analysis[target].intervals.map((row, k) => (
                      <rect
                        key={k}
                        x={
                          110 +
                          ((Date.parse(row.start_utc) - from) / span) * 1030
                        }
                        y="0"
                        width={
                          ((Date.parse(row.end_utc) -
                            Date.parse(row.start_utc)) /
                            span) *
                          1030
                        }
                        height="20"
                        fill={colors[row.state] || colors.unknown}
                      />
                    ))}
                  </g>
                ))}
                <text x="40" y="460" fontSize="10">
                  {t("export.chart.legend")}
                </text>
                <text x="40" y="480" fontSize="10">
                  {t("export.chart.timeline")}
                </text>
              </g>
            );
          })}
          <text x="40" y={b ? 1030 : 530} fill="#abb9c5" fontSize="11">
            {t("export.chart.disclaimer")}
          </text>
        </svg>
      </div>
    </div>
  );
}
function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
