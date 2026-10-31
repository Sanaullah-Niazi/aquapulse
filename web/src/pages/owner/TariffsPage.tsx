// O-06: tariffs (FR-33, FR-45). Every saved change is a new VERSION with its
// own start date; issued bills keep the version they were made with.
// The editor cannot make gaps or overlaps: each slab starts where the one
// before it ends, and the last slab has no upper limit. A live preview shows
// what a home using N litres would pay (the API does the real maths).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { api } from "../../api";
import { ErrorBox, Field, Loading } from "../../components/ui";
import * as fmt from "../../format";
import type { Charge, Preview, Slab, Tariff } from "../../types";
import { NoBuilding, useBuilding } from "./building";
import { today } from "./dates";

interface Draft {
  name: string;
  effective_from: string;
  ends: string[]; // where each slab ends ("" for the last one)
  rates: string[];
  charges: Charge[];
}

const STARTER: Draft = {
  name: "",
  effective_from: today(),
  ends: ["5000", "15000", ""],
  rates: ["60", "120", "200"],
  charges: [],
};

function fromTariff(tariff: Tariff): Draft {
  return {
    name: tariff.name,
    effective_from: today(),
    ends: tariff.slabs.map((s) => (s.to_l == null ? "" : String(s.to_l))),
    rates: tariff.slabs.map((s) => s.rate_per_kl),
    charges: tariff.charges.map((c) => ({ ...c })),
  };
}

/** The slabs as the API wants them, or the first problem in words. */
function slabsOf(draft: Draft, t: TFunction): Slab[] | string {
  const slabs: Slab[] = [];
  let from = 0;
  for (let n = 0; n < draft.ends.length; n++) {
    const last = n === draft.ends.length - 1;
    const rate = draft.rates[n].trim();
    if (rate === "" || !(Number(rate) >= 0)) return t("tariffs.badRate", { n: n + 1 });
    if (last) {
      slabs.push({ from_l: from, to_l: null, rate_per_kl: rate });
      break;
    }
    const to = Number(draft.ends[n]);
    if (!Number.isInteger(to) || to <= from) return t("tariffs.badEnd", { n: n + 1, from: fmt.litres(from) });
    slabs.push({ from_l: from, to_l: to, rate_per_kl: rate });
    from = to;
  }
  for (const c of draft.charges) {
    if (!c.name.trim()) return t("tariffs.chargeName");
    if (c.value.trim() === "" || !(Number(c.value) >= 0)) return t("tariffs.badCharge", { name: c.name });
    if (c.kind !== "extra_fixed" && Number(c.value) > 100) return t("tariffs.badPercent", { name: c.name });
  }
  return slabs;
}

export function TariffsPage() {
  const { t } = useTranslation();
  const { buildingId, building, picker } = useBuilding();
  const queries = useQueryClient();
  const tariffs = useQuery({
    queryKey: ["tariffs", buildingId],
    queryFn: () => api<Tariff[]>(`/buildings/${buildingId}/tariffs`),
    enabled: buildingId !== null,
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const save = useMutation({
    mutationFn: (body: object) =>
      api<Tariff>(`/buildings/${buildingId}/tariffs`, { method: "POST", body }),
    onSuccess: (tariff) => {
      setSaved(tariff.version);
      setDraft(null);
      void queries.invalidateQueries({ queryKey: ["tariffs", buildingId] });
    },
  });

  if (buildingId === null) return <NoBuilding />;
  if (tariffs.isPending) return <Loading />;
  if (tariffs.error) return <ErrorBox error={tariffs.error} retry={() => tariffs.refetch()} />;
  const newest = tariffs.data[0];
  const currency = building?.currency_code ?? newest?.currency_code ?? "";

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("tariffs.title")}</h1>
        {picker}
      </div>
      <p className="muted">{t("tariffs.intro")}</p>
      {saved !== null && (
        <p className="notice ok" role="status">{t("tariffs.saved", { version: saved })}</p>
      )}
      {draft ? (
        <Editor
          draft={draft}
          setDraft={setDraft}
          currency={currency}
          error={save.error}
          saving={save.isPending}
          onCancel={() => {
            save.reset();
            setDraft(null);
          }}
          onSave={(body) => save.mutate(body)}
        />
      ) : (
        <p>
          <button className="primary" onClick={() => {
            setSaved(null);
            setDraft(newest ? fromTariff(newest) : { ...STARTER });
          }}>
            {newest ? t("tariffs.newVersion") : t("tariffs.first")}
          </button>
        </p>
      )}
      {tariffs.data.length === 0 ? (
        <p className="notice info">{t("tariffs.none")}</p>
      ) : (
        tariffs.data.map((tariff) => <TariffCard key={tariff.id} tariff={tariff} current={tariff === newest} />)
      )}
    </>
  );
}

function TariffCard({ tariff, current }: { tariff: Tariff; current: boolean }) {
  const { t } = useTranslation();
  return (
    <section className="card" style={{ marginBottom: "1rem" }}>
      <h2>
        {t("tariffs.version", { version: tariff.version, name: tariff.name })}{" "}
        {current && <span className="badge ok">{t("tariffs.newest")}</span>}
      </h2>
      <p className="muted small">{t("tariffs.from", { date: fmt.date(tariff.effective_from) })}</p>
      <table className="responsive">
        <thead>
          <tr>
            <th>{t("tariffs.slab")}</th>
            <th className="num">{t("tariffs.rate", { currency: tariff.currency_code })}</th>
          </tr>
        </thead>
        <tbody>
          {tariff.slabs.map((s, n) => (
            <tr key={n}>
              <td data-label={t("tariffs.slab")}>
                {s.to_l == null
                  ? t("tariffs.above", { from: fmt.litres(s.from_l) })
                  : t("tariffs.range", { from: fmt.litres(s.from_l), to: fmt.litres(s.to_l) })}
              </td>
              <td data-label={t("tariffs.rate", { currency: tariff.currency_code })} className="num">{s.rate_per_kl}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {tariff.charges.length > 0 && (
        <ul className="list">
          {tariff.charges.map((c, n) => (
            <li key={n}>
              {c.name}: {c.kind === "extra_fixed" ? `${tariff.currency_code} ${c.value}` : `${c.value}%`}{" "}
              <span className="muted small">({t(`tariffs.kinds.${c.kind}`)})</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Editor({
  draft,
  setDraft,
  currency,
  error,
  saving,
  onCancel,
  onSave,
}: {
  draft: Draft;
  setDraft: (draft: Draft) => void;
  currency: string;
  error: unknown;
  saving: boolean;
  onCancel: () => void;
  onSave: (body: object) => void;
}) {
  const { t } = useTranslation();
  const [litres, setLitres] = useState("12000");
  const slabs = slabsOf(draft, t);
  const problem = typeof slabs === "string" ? slabs : null;
  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });
  const starts = draft.ends.map((_, n) => (n === 0 ? "0" : draft.ends[n - 1] || "?"));

  const addSlab = () => {
    // Split the open-ended last slab: the new one starts where the old one now ends.
    const ends = [...draft.ends];
    const lastStart = Number(starts[starts.length - 1]) || 0;
    ends.splice(ends.length - 1, 0, String(lastStart + 5000));
    const rates = [...draft.rates, draft.rates[draft.rates.length - 1]];
    set({ ends, rates });
  };
  const removeSlab = (n: number) => {
    const ends = draft.ends.filter((_, i) => i !== n);
    ends[ends.length - 1] = ""; // the last one is always open
    set({ ends, rates: draft.rates.filter((_, i) => i !== n) });
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (typeof slabs === "string") return;
    onSave({ name: draft.name, effective_from: draft.effective_from, slabs, charges: draft.charges });
  };

  return (
    <form className="card" onSubmit={submit} style={{ marginBottom: "1rem" }} aria-label={t("tariffs.editor")}>
      <h2>{t("tariffs.editor")}</h2>
      <div className="grid-2">
        <Field id="tariff-name" label={t("tariffs.name")}>
          <input id="tariff-name" required maxLength={80} value={draft.name}
                 onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <Field id="tariff-from" label={t("tariffs.effectiveFrom")} help={t("tariffs.effectiveHelp")}>
          <input id="tariff-from" type="date" required value={draft.effective_from}
                 aria-describedby="tariff-from-help" onChange={(e) => set({ effective_from: e.target.value })} />
        </Field>
      </div>

      <h3>{t("tariffs.slabs")}</h3>
      <table className="responsive">
        <thead>
          <tr>
            <th>{t("tariffs.fromL")}</th>
            <th>{t("tariffs.toL")}</th>
            <th>{t("tariffs.rate", { currency })}</th>
            <th><span className="sr-only">{t("tariffs.remove")}</span></th>
          </tr>
        </thead>
        <tbody>
          {draft.ends.map((end, n) => {
            const last = n === draft.ends.length - 1;
            return (
              <tr key={n}>
                <td data-label={t("tariffs.fromL")}>{starts[n] === "?" ? "?" : fmt.litres(starts[n])}</td>
                <td data-label={t("tariffs.toL")}>
                  {last ? (
                    <span className="muted">{t("tariffs.noLimit")}</span>
                  ) : (
                    <input type="number" min={1} step={1} inputMode="numeric" required value={end}
                           aria-label={t("tariffs.endOf", { n: n + 1 })}
                           onChange={(e) => set({ ends: draft.ends.map((v, i) => (i === n ? e.target.value : v)) })} />
                  )}
                </td>
                <td data-label={t("tariffs.rate", { currency })}>
                  <input type="number" min={0} step="0.001" inputMode="decimal" required value={draft.rates[n]}
                         aria-label={t("tariffs.rateOf", { n: n + 1 })}
                         onChange={(e) => set({ rates: draft.rates.map((v, i) => (i === n ? e.target.value : v)) })} />
                </td>
                <td>
                  {draft.ends.length > 1 && (
                    <button type="button" onClick={() => removeSlab(n)} aria-label={t("tariffs.removeSlab", { n: n + 1 })}>
                      {t("tariffs.remove")}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {draft.ends.length < 10 && (
        <button type="button" onClick={addSlab}>{t("tariffs.addSlab")}</button>
      )}

      <h3>{t("tariffs.charges")}</h3>
      <p className="muted small">{t("tariffs.chargesHelp")}</p>
      {draft.charges.map((c, n) => {
        const change = (patch: Partial<Charge>) =>
          set({ charges: draft.charges.map((old, i) => (i === n ? { ...old, ...patch } : old)) });
        return (
          <div key={n} className="row" style={{ alignItems: "flex-end", marginBottom: "0.5rem" }}>
            <Field id={`charge-name-${n}`} label={t("tariffs.chargeLabel")}>
              <input id={`charge-name-${n}`} required maxLength={60} value={c.name}
                     onChange={(e) => change({ name: e.target.value })} />
            </Field>
            <Field id={`charge-kind-${n}`} label={t("tariffs.kind")}>
              <select id={`charge-kind-${n}`} value={c.kind} onChange={(e) => change({ kind: e.target.value as Charge["kind"] })}>
                {(["tax_percent", "extra_percent", "extra_fixed"] as const).map((k) => (
                  <option key={k} value={k}>{t(`tariffs.kinds.${k}`)}</option>
                ))}
              </select>
            </Field>
            <Field id={`charge-value-${n}`} label={c.kind === "extra_fixed" ? currency : "%"}>
              <input id={`charge-value-${n}`} type="number" min={0} step="0.001" inputMode="decimal" required
                     value={c.value} onChange={(e) => change({ value: e.target.value })} />
            </Field>
            <button type="button" onClick={() => set({ charges: draft.charges.filter((_, i) => i !== n) })}>
              {t("tariffs.remove")}
            </button>
          </div>
        );
      })}
      {draft.charges.length < 10 && (
        <button type="button" onClick={() =>
          set({ charges: [...draft.charges, { name: "", kind: "tax_percent", value: "0" }] })}>
          {t("tariffs.addCharge")}
        </button>
      )}

      {problem && <p className="notice warn" role="alert">{problem}</p>}
      <PreviewCard slabs={typeof slabs === "string" ? null : slabs} charges={draft.charges}
                   currency={currency} litres={litres} setLitres={setLitres} />
      {error != null && <ErrorBox error={error} />}
      <div className="row" style={{ marginTop: "1rem" }}>
        <button className="primary" type="submit" disabled={saving || problem !== null}>{t("tariffs.save")}</button>
        <button type="button" onClick={onCancel}>{t("app.cancel")}</button>
      </div>
    </form>
  );
}

/** "A home using N litres this month pays...", from the API's own bill maths. */
function PreviewCard({
  slabs,
  charges,
  currency,
  litres,
  setLitres,
}: {
  slabs: Slab[] | null;
  charges: Charge[];
  currency: string;
  litres: string;
  setLitres: (value: string) => void;
}) {
  const { t } = useTranslation();
  // Wait until typing stops for a moment, so each key press is not a request.
  const body = JSON.stringify({ slabs, charges, currency_code: currency, litres: litres || "0" });
  const [settled, setSettled] = useState(body);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(body), 400);
    return () => clearTimeout(timer);
  }, [body]);
  const ready = slabs !== null && currency !== "" && Number(litres) >= 0;
  const preview = useQuery({
    queryKey: ["tariff-preview", settled],
    queryFn: () => api<Preview>("/tariffs/preview", { method: "POST", body: JSON.parse(settled) }),
    enabled: ready && settled === body,
    retry: false,
    placeholderData: (previous) => previous,
  });
  return (
    <section className="card" style={{ marginTop: "1rem" }} aria-live="polite">
      <h3>{t("tariffs.preview")}</h3>
      <Field id="preview-litres" label={t("tariffs.previewLitres")}>
        <input id="preview-litres" type="number" min={0} step={100} inputMode="numeric" value={litres}
               onChange={(e) => setLitres(e.target.value)} style={{ maxWidth: "12rem" }} />
      </Field>
      {!ready ? (
        <p className="muted">{t("tariffs.previewWaiting")}</p>
      ) : preview.error ? (
        <ErrorBox error={preview.error} />
      ) : preview.data ? (
        <table>
          <tbody>
            {preview.data.lines.map((line, n) => (
              <tr key={n}>
                <td>{line.description}</td>
                <td className="num">{line.amount}</td>
              </tr>
            ))}
            <tr>
              <th>{t("bills.total")}</th>
              <td className="num"><strong data-testid="preview-total">{preview.data.total_text}</strong></td>
            </tr>
          </tbody>
        </table>
      ) : (
        <Loading />
      )}
    </section>
  );
}
