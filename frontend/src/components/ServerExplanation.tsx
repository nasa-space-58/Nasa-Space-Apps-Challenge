import { useState } from "react";
import { useTranslate } from "../i18n";

const reasons: Record<string,string> = {
  thrust:"Motorun itki sınırı aşılıyor.", propellant:"İnişe kadar yakıt yeterli değil.", reserve:"İniş sonunda gereken yakıt rezervi kalmıyor.",
  touchdown_tolerance:"İniş konumu veya hızı izin verilen sınırların dışında.", obstacle:"Hedef algılanan engelin içinde.", uncertainty_margin:"Yön bulma belirsizliği için yeterli güvenlik mesafesi yok.",
  position:"Hedef konumundan sapma sınırı aşıldı.", vertical_speed:"Düşey iniş hızı sınırı aşıldı.", horizontal_speed:"Yatay iniş hızı sınırı aşıldı.", energy:"Enerji sınırı aşıldı.", terrain:"Arazi koşulları uygun değil.",
};
/** Keep unrecognised server diagnostics available without presenting English as Turkish UI. */
export default function ServerExplanation({text,reason}: {text?:string|null;reason?:string|null}) {
  const {lang,t,tText}=useTranslate();
  const [open,setOpen]=useState(false);
  if(!text)return null;
  const translated=tText(text);
  if(lang!=="tr"||translated!==text)return <>{translated}</>;
  return <span className="server-explanation"><span>{reason&&reasons[reason] ? reasons[reason] : t("workspace.technical")}</span>{" "}<button type="button" aria-expanded={open} onClick={()=>setOpen(!open)}>{t("workspace.source")} {open?"−":"+"}</button>{open&&<span className="source-text" lang="en">{text}</span>}</span>;
}
