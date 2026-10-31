// O-07: the owner's bills (FR-35 to FR-38, FR-45). Pick a finished month,
// make its draft bills from the meter readings, check them, then issue them
// all in one go. "Unpaid" lists everything still owed, oldest due date first.
// CSV exports for a spreadsheet (FR-30). One bill's actions (issue, void,
// record or reverse a payment) live on the bill's own page: OwnerBillActions.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import { api, download } from "../../api";
import { BillStatusBadge, ErrorBox, Field, Loading, Segmented } from "../../components/ui";
import * as fmt from "../../format";
import { useLiveEvents } from "../../live";
import type { Bill, BillSummary } from "../../types";
import { NoBuilding, useBuilding } from "./building";
import { lastMonth, today } from "./dates";

interface GenerateResult {
  created: number;
  updated: number;
  kept: number;
  issued: number;
  problems: string[];
}

interface Unpaid {
  bills: BillSummary[];
  totals: { currency_code: string; bills: number; amount_text: string; overdue_amount: string }[];
}

/** First and last day of "2026-09". */
function monthRange(month: string): [string, string] {
  const [year, m] = month.split("-").map(Number);
  const lastDay = new Date(year, m, 0).getDate();
  return [`${month}-01`, `${month}-${String(lastDay).padStart(2, "0")}`];
}

export function OwnerBillsPage() {
  const { t } = useTranslation();
  const { buildingId, picker } = useBuilding();
  const [params, setParams] = useSearchParams();
  const show = params.get("show") === "unpaid" ? "unpaid" : "month";
  const queries = useQueryClient();
  useLiveEvents((event) => {
    if (event.type === "bill") void queries.invalidateQueries({ queryKey: ["bills"] });
  });

  if (buildingId === null) return <NoBuilding />;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("ownerBills.title")}</h1>
        {picker}
      </div>
      <Segmented
        label={t("ownerBills.show")}
        value={show}
        onChange={(value) => setParams(value === "unpaid" ? { show: "unpaid" } : {})}
        options={[
          { value: "month", label: t("ownerBills.byMonth") },
          { value: "unpaid", label: t("ownerBills.unpaid") },
        ]}
      />
      {show === "unpaid" ? <UnpaidList buildingId={buildingId} /> : <MonthBills buildingId={buildingId} />}
    </>
  );
}

function MonthBills({ buildingId }: { buildingId: string }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [month, setMonth] = useState(lastMonth());
  const bills = useQuery({
    queryKey: ["bills", buildingId, month],
    queryFn: () => api<BillSummary[]>(`/buildings/${buildingId}/bills?month=${month}`),
    enabled: /^\d{4}-\d{2}$/.test(month),
  });
  const refresh = () => void queries.invalidateQueries({ queryKey: ["bills"] });
  const generate = useMutation({
    mutationFn: () =>
      api<GenerateResult>(`/buildings/${buildingId}/bills/generate`, { method: "POST", body: { month } }),
    onSuccess: refresh,
  });
  const issueAll = useMutation({
    mutationFn: () =>
      api<{ issued: number }>(`/buildings/${buildingId}/bills/issue-all`, { method: "POST", body: { month } }),
    onSuccess: refresh,
  });
  const csv = useMutation({
    mutationFn: (kind: "consumption" | "bills" | "payments") => {
      const [from, to] = monthRange(month);
      return download(`/buildings/${buildingId}/export/${kind}.csv?from=${from}&to=${to}`, `${kind}-${month}.csv`);
    },
  });
  const finished = month < today().slice(0, 7);
  const drafts = bills.data?.filter((b) => b.status === "draft").length ?? 0;

  return (
    <>
      <section className="card" style={{ margin: "1rem 0" }}>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <Field id="bill-month" label={t("ownerBills.month")}>
            <input id="bill-month" type="month" value={month} max={lastMonth()}
                   onChange={(e) => {
                     generate.reset();
                     issueAll.reset();
                     setMonth(e.target.value);
                   }} />
          </Field>
          <button className="primary" onClick={() => generate.mutate()} disabled={!finished || generate.isPending}>
            {t("ownerBills.generate")}
          </button>
          <button onClick={() => issueAll.mutate()} disabled={drafts === 0 || issueAll.isPending}>
            {t("ownerBills.issueAll", { count: drafts })}
          </button>
        </div>
        <p className="muted small">{t("ownerBills.help")}</p>
        {generate.data && (
          <div className="notice ok" role="status">
            {t("ownerBills.generated", { ...generate.data })}
            {generate.data.problems.length > 0 && (
              <ul>
                {generate.data.problems.map((p) => <li key={p}>{p}</li>)}
              </ul>
            )}
          </div>
        )}
        {issueAll.data && (
          <p className="notice ok" role="status">{t("ownerBills.issued", { count: issueAll.data.issued })}</p>
        )}
        {(generate.error || issueAll.error) && <ErrorBox error={generate.error ?? issueAll.error} />}
      </section>

      <section className="card" style={{ marginBottom: "1rem" }}>
        {bills.isPending ? (
          <Loading />
        ) : bills.error ? (
          <ErrorBox error={bills.error} retry={() => bills.refetch()} />
        ) : bills.data.length === 0 ? (
          <p className="muted">{t("ownerBills.none")}</p>
        ) : (
          <BillTable bills={bills.data} />
        )}
      </section>

      <section className="card">
        <h2>{t("ownerBills.export")}</h2>
        <p className="muted small">{t("ownerBills.exportHelp", { month: fmt.month(`${month}-01`) })}</p>
        <div className="row">
          {(["consumption", "bills", "payments"] as const).map((kind) => (
            <button key={kind} onClick={() => csv.mutate(kind)} disabled={csv.isPending}>
              {t(`ownerBills.csv.${kind}`)}
            </button>
          ))}
        </div>
        {csv.error && <ErrorBox error={csv.error} />}
      </section>
    </>
  );
}

function BillTable({ bills }: { bills: BillSummary[] }) {
  const { t } = useTranslation();
  return (
    <table className="responsive">
      <thead>
        <tr>
          <th>{t("owner.cols.unit")}</th>
          <th>{t("ownerBills.billTo")}</th>
          <th>{t("bills.number")}</th>
          <th className="num">{t("bills.total")}</th>
          <th className="num">{t("ownerBills.outstanding")}</th>
          <th>{t("bills.due")}</th>
          <th>{t("bills.status")}</th>
          <th><span className="sr-only">{t("bills.open")}</span></th>
        </tr>
      </thead>
      <tbody>
        {bills.map((b) => (
          <tr key={b.id}>
            <td data-label={t("owner.cols.unit")}>{b.unit_no}</td>
            <td data-label={t("ownerBills.billTo")}>{b.bill_to}</td>
            <td data-label={t("bills.number")}>{b.bill_no ?? "-"}</td>
            <td data-label={t("bills.total")} className="num">{b.total_text}</td>
            <td data-label={t("ownerBills.outstanding")} className="num">{b.status === "void" ? "-" : b.outstanding}</td>
            <td data-label={t("bills.due")}>{fmt.date(b.due_date)}</td>
            <td data-label={t("bills.status")}><BillStatusBadge status={b.status} /></td>
            <td>
              <Link to={`/bills/${b.id}`} aria-label={`${t("bills.open")} ${b.unit_no} ${fmt.month(b.month)}`}>
                {t("bills.open")}
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function UnpaidList({ buildingId }: { buildingId: string }) {
  const { t } = useTranslation();
  const unpaid = useQuery({
    queryKey: ["bills", "unpaid", buildingId],
    queryFn: () => api<Unpaid>(`/bills/unpaid?building_id=${buildingId}`),
  });
  if (unpaid.isPending) return <Loading />;
  if (unpaid.error) return <ErrorBox error={unpaid.error} retry={() => unpaid.refetch()} />;
  return (
    <section className="card" style={{ marginTop: "1rem" }}>
      {unpaid.data.totals.map((total) => (
        <p key={total.currency_code}>
          <strong>{t("ownerBills.unpaidTotal", { count: total.bills, amount: total.amount_text })}</strong>
        </p>
      ))}
      {unpaid.data.bills.length === 0 ? (
        <p className="muted">{t("ownerBills.allPaid")}</p>
      ) : (
        <BillTable bills={unpaid.data.bills} />
      )}
    </section>
  );
}

/** The owner's buttons on one bill: issue a draft, void with a reason,
 *  record a cash or bank payment, reverse a payment, warn before shut-off. */
export function OwnerBillActions({ bill }: { bill: Bill }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [voiding, setVoiding] = useState(false);
  const [reason, setReason] = useState("");
  const [reversing, setReversing] = useState<string | null>(null);
  const [payment, setPayment] = useState({ amount: bill.outstanding, method: "cash", received_on: today(), reference: "" });
  const done = () => {
    setVoiding(false);
    setReversing(null);
    setReason("");
    void queries.invalidateQueries({ queryKey: ["bills"] });
  };
  const action = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: object }) => api(path, { method: "POST", body }),
    onSuccess: done,
  });
  const open = ["issued", "partially_paid", "overdue"].includes(bill.status);

  const pay = (e: FormEvent) => {
    e.preventDefault();
    action.mutate({ path: `/bills/${bill.id}/payments`, body: payment });
  };

  return (
    <section className="card" aria-label={t("ownerBills.actions")}>
      <h2>{t("ownerBills.actions")}</h2>
      {action.error && <ErrorBox error={action.error} />}
      <div className="row">
        {bill.status === "draft" && (
          <button className="primary" disabled={action.isPending}
                  onClick={() => action.mutate({ path: `/bills/${bill.id}/issue` })}>
            {t("ownerBills.issue")}
          </button>
        )}
        {bill.status === "overdue" && (
          <button disabled={action.isPending}
                  onClick={() => action.mutate({ path: `/bills/${bill.id}/shutoff-warning` })}>
            {t("ownerBills.warn")}
          </button>
        )}
        {bill.status !== "void" && !voiding && (
          <button onClick={() => setVoiding(true)}>{t("ownerBills.void")}</button>
        )}
      </div>
      {voiding && (
        <form className="stack" style={{ marginTop: "1rem" }} onSubmit={(e) => {
          e.preventDefault();
          action.mutate({ path: `/bills/${bill.id}/void`, body: { reason } });
        }}>
          <Field id="void-reason" label={t("ownerBills.voidReason")} help={t("lock.reasonHelp")}>
            <input id="void-reason" required minLength={10} maxLength={300} value={reason}
                   aria-describedby="void-reason-help" onChange={(e) => setReason(e.target.value)} />
          </Field>
          <div className="row">
            <button className="danger" type="submit" disabled={reason.trim().length < 10 || action.isPending}>
              {t("ownerBills.confirmVoid")}
            </button>
            <button type="button" onClick={() => setVoiding(false)}>{t("app.cancel")}</button>
          </div>
        </form>
      )}

      {open && Number(bill.outstanding) > 0 && (
        <form onSubmit={pay} style={{ marginTop: "1rem" }}>
          <h3>{t("ownerBills.recordPayment")}</h3>
          <div className="grid-2">
            <Field id="pay-amount" label={t("ownerBills.amount", { currency: bill.currency_code })}>
              <input id="pay-amount" type="number" min="0.001" step="0.001" inputMode="decimal" required
                     value={payment.amount} onChange={(e) => setPayment({ ...payment, amount: e.target.value })} />
            </Field>
            <Field id="pay-method" label={t("ownerBills.method")}>
              <select id="pay-method" value={payment.method} onChange={(e) => setPayment({ ...payment, method: e.target.value })}>
                {["cash", "bank", "other"].map((m) => <option key={m} value={m}>{t(`bills.methods.${m}`)}</option>)}
              </select>
            </Field>
            <Field id="pay-date" label={t("ownerBills.receivedOn")}>
              <input id="pay-date" type="date" required max={today()} value={payment.received_on}
                     onChange={(e) => setPayment({ ...payment, received_on: e.target.value })} />
            </Field>
            <Field id="pay-ref" label={t("ownerBills.reference")}>
              <input id="pay-ref" maxLength={100} value={payment.reference}
                     onChange={(e) => setPayment({ ...payment, reference: e.target.value })} />
            </Field>
          </div>
          <button className="primary" type="submit" disabled={action.isPending}>{t("ownerBills.savePayment")}</button>
        </form>
      )}

      {bill.payments.some((p) => !p.reversed_at) && (
        <>
          <h3>{t("ownerBills.reverse")}</h3>
          <ul className="list">
            {bill.payments.filter((p) => !p.reversed_at).map((p) => (
              <li key={p.id}>
                {fmt.date(p.received_on)} · {t(`bills.methods.${p.method}`)} · {bill.currency_code} {p.amount}{" "}
                {reversing === p.id ? (
                  <form className="row" style={{ marginTop: "0.5rem" }} onSubmit={(e) => {
                    e.preventDefault();
                    action.mutate({ path: `/payments/${p.id}/reverse`, body: { reason } });
                  }}>
                    <input aria-label={t("ownerBills.reverseReason")} placeholder={t("ownerBills.reverseReason")}
                           required minLength={10} maxLength={300} value={reason}
                           onChange={(e) => setReason(e.target.value)} style={{ flex: 1, minWidth: "12rem" }} />
                    <button className="danger" type="submit" disabled={reason.trim().length < 10 || action.isPending}>
                      {t("ownerBills.confirmReverse")}
                    </button>
                    <button type="button" onClick={() => setReversing(null)}>{t("app.cancel")}</button>
                  </form>
                ) : (
                  <button className="link" onClick={() => { setReason(""); setReversing(p.id); }}>
                    {t("ownerBills.reverseThis")}
                  </button>
                )}
              </li>
            ))}
          </ul>
          {bill.payments.some((p) => p.method === "stripe_test" && !p.reversed_at) && (
            <p className="muted small">{t("ownerBills.stripeRefund")}</p>
          )}
        </>
      )}
    </section>
  );
}
