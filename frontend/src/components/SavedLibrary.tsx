import { useState } from "react";
import "./results-live.css";
import type { Result } from "../types";
import { useTranslate } from "../i18n";
import {
  defaultName,
  removeComparison,
  removeResult,
  renameResult,
  saveResult,
  savedName,
  useLibrary,
} from "../savedLibrary";

const shortDate = (iso: string) => iso.slice(0, 16).replace("T", " ");

/** Save the shown analysis and open, compare, rename or delete saved ones. */
export default function SavedLibrary({
  current,
  onOpen,
  onCompare,
}: {
  current: { id: string; result: Result } | null;
  onOpen: (id: string) => Promise<void>;
  /** Open the A/B comparison of two saved or current analyses. */
  onCompare: (aId: string, bId: string) => Promise<void>;
}) {
  const { t } = useTranslate();
  const library = useLibrary();
  const h = (seconds: number) => t("units.hours", { value: (seconds / 3600).toFixed(1) });
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const isSaved = !!current && library.results.some((r) => r.id === current.id);
  const run = async (action: () => Promise<void>) => {
    setError("");
    try {
      await action();
    } catch {
      setError(t("savedLibrary.unavailable"));
    }
  };
  const toggle = (id: string) =>
    setPicked((list) =>
      list.includes(id) ? list.filter((x) => x !== id) : [...list, id].slice(-2),
    );
  const best = (key: "sun_visible_percent" | "earth_visible_percent" | "longest_dark_s") => {
    if (library.results.length < 2) return null;
    const values = library.results.map((r) => r.summary[key]);
    return key === "longest_dark_s" ? Math.min(...values) : Math.max(...values);
  };
  const bestSun = best("sun_visible_percent"),
    bestEarth = best("earth_visible_percent"),
    bestDark = best("longest_dark_s");

  return (
    <section className="panel saved-library" aria-labelledby="saved-library-title">
      <div className="panel-heading">
        <span id="saved-library-title">{t("savedLibrary.title")}</span>
        <small>{t("savedLibrary.count", { n: library.results.length })}</small>
      </div>
      {current && (
        <form
          className="saved-save"
          onSubmit={(e) => {
            e.preventDefault();
            saveResult(current.id, current.result, name);
            setName("");
            setMessage(t("savedLibrary.savedMessage"));
          }}
        >
          <input
            aria-label={t("savedLibrary.nameLabel")}
            placeholder={savedName(library, current.id) ?? defaultName(current.result)}
            value={name}
            maxLength={80}
            onChange={(e) => {
              setName(e.target.value);
              setMessage("");
            }}
          />
          <button className="primary" type="submit">
            {isSaved ? t("savedLibrary.update") : t("savedLibrary.save")}
          </button>
        </form>
      )}
      {message && (
        <p className="field-help" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {library.results.length ? (
        <>
          <p className="field-help">{t("savedLibrary.pickHelp")}</p>
          <ul className="saved-list">
            {library.results.map((entry) => {
              const here = current?.id === entry.id;
              return (
                <li key={entry.id} className={here ? "current" : ""}>
                  <label className="saved-pick">
                    <input
                      type="checkbox"
                      checked={picked.includes(entry.id)}
                      onChange={() => toggle(entry.id)}
                      aria-label={t("savedLibrary.pick", { name: entry.name })}
                    />
                  </label>
                  <div className="saved-main">
                    {editing === entry.id ? (
                      <form
                        className="saved-rename"
                        onSubmit={(e) => {
                          e.preventDefault();
                          renameResult(entry.id, editName);
                          setEditing(null);
                        }}
                      >
                        <input
                          autoFocus
                          aria-label={t("savedLibrary.nameLabel")}
                          value={editName}
                          maxLength={80}
                          onChange={(e) => setEditName(e.target.value)}
                          onKeyDown={(e) => e.key === "Escape" && setEditing(null)}
                        />
                        <button type="submit" className="text-button">{t("savedLibrary.ok")}</button>
                      </form>
                    ) : (
                      <b>
                        {entry.name}
                        {here && <span className="outline-tag">{t("savedLibrary.shown")}</span>}
                      </b>
                    )}
                    <small>
                      {entry.site_id.replaceAll("-", " ")} · {entry.latitude_deg.toFixed(3)}° /{" "}
                      {entry.longitude_deg_east.toFixed(3)}°E · {shortDate(entry.start_utc)} →{" "}
                      {shortDate(entry.end_utc)} UTC
                    </small>
                    <div className="saved-metrics">
                      <span className={entry.summary.sun_visible_percent === bestSun ? "best" : ""}>
                        <i className="sun-dot" /> {entry.summary.sun_visible_percent.toFixed(1)}%
                        <span className="saved-bar sun"><i style={{ width: `${entry.summary.sun_visible_percent}%` }} /></span>
                      </span>
                      <span className={entry.summary.earth_visible_percent === bestEarth ? "best" : ""}>
                        <i className="earth-dot" /> {entry.summary.earth_visible_percent.toFixed(1)}%
                        <span className="saved-bar earth"><i style={{ width: `${entry.summary.earth_visible_percent}%` }} /></span>
                      </span>
                      <span className={entry.summary.longest_dark_s === bestDark ? "best" : ""}>
                        {t("savedLibrary.dark")} {h(entry.summary.longest_dark_s)}
                      </span>
                    </div>
                  </div>
                  <div className="saved-actions">
                    {!here && (
                      <button type="button" className="text-button" onClick={() => run(() => onOpen(entry.id))}>
                        {t("savedLibrary.open")}
                      </button>
                    )}
                    {current && !here && (
                      <button
                        type="button"
                        className="text-button"
                        onClick={() => run(() => onCompare(current.id, entry.id))}
                      >
                        {t("savedLibrary.compareWithShown")}
                      </button>
                    )}
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => {
                        setEditing(entry.id);
                        setEditName(entry.name);
                      }}
                    >
                      {t("savedLibrary.rename")}
                    </button>
                    <button
                      type="button"
                      className="text-button danger"
                      onClick={() => {
                        if (window.confirm(t("savedLibrary.confirmDelete", { name: entry.name }))) {
                          removeResult(entry.id);
                          setPicked((list) => list.filter((x) => x !== entry.id));
                        }
                      }}
                    >
                      {t("savedLibrary.delete")}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
          <button
            type="button"
            className="primary saved-compare"
            disabled={picked.length !== 2}
            onClick={() => run(() => onCompare(picked[0], picked[1]))}
          >
            {t("savedLibrary.comparePicked", { n: picked.length })} ↗
          </button>
          {library.results.length >= 2 && <p className="field-help">{t("savedLibrary.bestNote")}</p>}
        </>
      ) : (
        <p className="field-help">{t("savedLibrary.empty")}</p>
      )}
      {library.comparisons.length > 0 && (
        <div className="saved-comparisons">
          <h4>{t("savedLibrary.comparisons")}</h4>
          <ul>
            {library.comparisons.map((entry) => (
              <li key={`${entry.a_id}:${entry.b_id}`}>
                <div>
                  <b>{entry.name}</b>
                  <small>
                    A: {savedName(library, entry.a_id) ?? entry.a_id.slice(0, 8)} · B:{" "}
                    {savedName(library, entry.b_id) ?? entry.b_id.slice(0, 8)}
                  </small>
                </div>
                <button type="button" className="text-button" onClick={() => run(() => onCompare(entry.a_id, entry.b_id))}>
                  {t("savedLibrary.open")}
                </button>
                <button
                  type="button"
                  className="text-button danger"
                  onClick={() => removeComparison(entry.a_id, entry.b_id)}
                >
                  {t("savedLibrary.delete")}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
