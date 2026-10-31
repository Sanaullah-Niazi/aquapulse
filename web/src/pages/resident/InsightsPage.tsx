// R-06: AI for my home: the forecast with the month-end bill (FR-65) and
// insights such as leaks or unusual days, each with its reason (FR-63 to FR-66).
import { useTranslation } from "react-i18next";
import { ForecastCard, InsightsCard } from "../../components/Ai";
import { useHomePicker } from "./MyHomePage";

export function InsightsPage() {
  const { t } = useTranslation();
  const { homeId, picker } = useHomePicker();
  if (homeId === null) return <p className="notice info">{t("home.noHome")}</p>;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("ai.title")}</h1>
        {picker}
      </div>
      <p className="muted">{t("ai.intro")}</p>
      <div className="grid-2">
        <ForecastCard path={`/homes/${homeId}/forecast`} title={t("ai.forecastHome")} />
        <InsightsCard homeId={homeId} />
      </div>
    </>
  );
}
