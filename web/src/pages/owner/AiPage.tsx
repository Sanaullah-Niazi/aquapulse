// O-10: AI for the building: the building forecast with every home's bill
// estimate added up (FR-65), and the model registry (FR-67): every trained
// version with its scores, and which one is live.
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { ForecastCard } from "../../components/Ai";
import { ErrorBox, Loading } from "../../components/ui";
import * as fmt from "../../format";
import type { AiModel } from "../../types";
import { NoBuilding, useBuilding } from "./building";

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "-" : `${Math.round(v * 100)}%`;
}

export function scores(m: AiModel): string {
  if (m.kind === "forecast")
    return `MAPE ${pct(m.metrics.method_mape)} · naive ${pct(m.metrics.naive_mape)} · in range ${pct(m.metrics.coverage)}`;
  return `doubled days caught ${pct(m.metrics.doubled_recall)} · real days flagged ${pct(m.metrics.false_rate)}`;
}

export function AiPage() {
  const { t } = useTranslation();
  const { buildingId, picker } = useBuilding();
  const models = useQuery({
    queryKey: ["ai-models", buildingId],
    queryFn: () => api<AiModel[]>(`/buildings/${buildingId}/ai/models`),
    enabled: buildingId !== null,
  });
  if (buildingId === null) return <NoBuilding />;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("ai.title")}</h1>
        {picker}
      </div>
      <p className="muted">{t("ai.ownerIntro")}</p>
      <ForecastCard path={`/buildings/${buildingId}/forecast`} title={t("ai.forecastBuilding")} />
      <section className="card" aria-labelledby="models-title" style={{ marginTop: "1rem" }}>
        <h2 id="models-title">{t("ai.models")}</h2>
        <p className="small muted">{t("ai.modelsIntro")}</p>
        {models.isPending ? (
          <Loading />
        ) : models.error ? (
          <ErrorBox error={models.error} retry={() => models.refetch()} />
        ) : models.data.length === 0 ? (
          <p className="muted">{t("ai.noModels")}</p>
        ) : (
          <table className="responsive">
              <thead>
                <tr>
                  <th>{t("ai.model")}</th>
                  <th>{t("ai.version")}</th>
                  <th>{t("ai.trained")}</th>
                  <th>{t("ai.data")}</th>
                  <th>{t("ai.scores")}</th>
                  <th>{t("ai.status")}</th>
                </tr>
              </thead>
              <tbody>
                {models.data.map((m) => (
                  <tr key={m.id}>
                    <td data-label={t("ai.model")}>{t(`ai.kind.${m.kind}`)}</td>
                    <td data-label={t("ai.version")}>v{m.version}</td>
                    <td data-label={t("ai.trained")}>{fmt.dateTime(m.trained_at)}</td>
                    <td data-label={t("ai.data")}>
                      {fmt.date(m.data_from)} – {fmt.date(m.data_to)} ({m.rows})
                    </td>
                    <td data-label={t("ai.scores")} className="small">{scores(m)}</td>
                    <td data-label={t("ai.status")}>
                      {m.live ? <span className="badge ok">{t("ai.live")}</span> : <span className="muted">{t("ai.keptOut")}</span>}
                      <div className="small muted">{m.note}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
        )}
      </section>
    </>
  );
}
