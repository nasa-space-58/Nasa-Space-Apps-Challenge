import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import en from "./en.json";
import tr from "./tr.json";

export type Language = "en" | "tr";
type Dictionary = { [key: string]: string | Dictionary };
type Params = Record<string, string | number>;

const dictionaries: Record<Language, Dictionary> = { en, tr };
/** BCP 47 locale used for date and number formatting in each language. */
export const LOCALES: Record<Language, string> = { en: "en-GB", tr: "tr-TR" };
const STORAGE_KEY = "lunar-language";

function lookup(dictionary: Dictionary, key: string): string | undefined {
  let node: string | Dictionary | undefined = dictionary;
  for (const part of key.split(".")) {
    if (typeof node !== "object" || node === null) return undefined;
    node = node[part];
  }
  return typeof node === "string" ? node : undefined;
}

function interpolate(text: string, params?: Params) {
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

export type Translate = (key: string, params?: Params) => string;

/** Translates fixed English text sent by the backend; unknown text is returned unchanged. */
export type TranslateText = (text: string) => string;

type LanguageContext = {
  lang: Language;
  locale: string;
  setLang: (lang: Language) => void;
  t: Translate;
  tText: TranslateText;
};

function initialLanguage(): Language {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "en" || stored === "tr") return stored;
  return navigator.language?.toLowerCase().startsWith("tr") ? "tr" : "en";
}

function makeTranslate(lang: Language): Translate {
  return (key, params) => {
    const text =
      lookup(dictionaries[lang], key) ?? lookup(dictionaries.en, key) ?? key;
    return interpolate(text, params);
  };
}

function makeTranslateText(lang: Language): TranslateText {
  const table = dictionaries[lang].serverText;
  return (text) => {
    const value = typeof table === "object" ? table[text] : undefined;
    if (typeof value === "string") return value;
    if (lang === "tr") {
      const patterns: [RegExp, (...values: string[]) => string][] = [
        [/^Landed ([\d.]+) m from the target\.$/, (_,m)=>`Hedeften ${m} m uzakta iniş yapıldı.`],
        [/^Vertical speed ([\d.]+) m\/s at touchdown\.$/, (_,v)=>`Temas anındaki düşey hız: ${v} m/s.`],
        [/^Horizontal speed ([\d.]+) m\/s at touchdown\.$/, (_,v)=>`Temas anındaki yatay hız: ${v} m/s.`],
        [/^([\d.-]+) kg left; the reserve needs ([\d.-]+) kg\.$/, (_,left,need)=>`Kalan yakıt ${left} kg; gereken rezerv ${need} kg.`],
        [/^Needs ([\d.]+) % of maximum thrust for a ([\d.]+) m sideways move\.$/, (_,percent,m)=>`${m} m yanal manevra için azami itkinin %${percent} kadarı gerekiyor.`],
        [/^Leaves ([\d.-]+) kg; the reserve \(with a ([\d.]+) m correction allowance\) needs ([\d.]+) kg\.$/, (_,left,m,need)=>`Kalan yakıt ${left} kg. ${m} m düzeltme payıyla gereken rezerv ${need} kg.`],
      ];
      for (const [pattern,translate] of patterns) {
        const match = text.match(pattern);
        if (match) return translate(...match);
      }
    }
    return text;
  };
}

const Context = createContext<LanguageContext>({
  lang: "en",
  locale: LOCALES.en,
  setLang: () => undefined,
  t: makeTranslate("en"),
  tText: makeTranslateText("en"),
});

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Language>(initialLanguage);
  const setLang = useCallback((next: Language) => {
    localStorage.setItem(STORAGE_KEY, next);
    setLangState(next);
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = makeTranslate(lang)("app.documentTitle");
  }, [lang]);
  const value = useMemo(
    () => ({
      lang,
      locale: LOCALES[lang],
      setLang,
      t: makeTranslate(lang),
      tText: makeTranslateText(lang),
    }),
    [lang, setLang],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** Returns the active translator; every embedded UI string comes from en.json / tr.json. */
export function useTranslate() {
  return useContext(Context);
}

export function LanguageToggle() {
  const { lang, setLang, t } = useTranslate();
  return (
    <div className="language-toggle" role="group" aria-label={t("language.label")}>
      {(["tr", "en"] as const).map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={lang === option}
          onClick={() => setLang(option)}
          title={t(`language.${option}`)}
        >
          {option.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

/** Server-provided error text when present, otherwise the translated fallback. */
export function errorText(error: unknown, t: Translate, fallbackKey: string) {
  return error instanceof Error && error.message ? error.message : t(fallbackKey);
}

/** Localized preset label; falls back to the backend display name for unknown sites. */
export function siteLabel(
  t: Translate,
  site: { site_id: string; display_name: string },
  part: "name" | "short" = "name",
) {
  const key = `siteNames.${site.site_id}.${part}`;
  const text = t(key);
  if (text !== key) return text;
  return part === "short" ? site.site_id.toUpperCase() : site.display_name;
}
