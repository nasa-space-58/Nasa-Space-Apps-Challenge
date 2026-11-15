import { useState } from "react";
import { useTranslate } from "../i18n";

/** Keep unrecognised server diagnostics available without presenting English as Turkish UI. */
export default function ServerExplanation({text,reason}: {text?:string|null;reason?:string|null}) {
  const {lang,t,tOr,tText}=useTranslate();
  const [open,setOpen]=useState(false);
  if(!text)return null;
  const translated=tText(text);
  if(lang!=="tr"||translated!==text)return <>{translated}</>;
  return <span className="server-explanation"><span>{reason ? tOr(`serverReasons.${reason}`, t("workspace.technical")) : t("workspace.technical")}</span>{" "}<button type="button" aria-expanded={open} onClick={()=>setOpen(!open)}>{t("workspace.source")} {open?"−":"+"}</button>{open&&<span className="source-text" lang="en">{text}</span>}</span>;
}
