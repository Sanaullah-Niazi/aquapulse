// R-04 / R-05 / R-06: my bills, one bill with its lines and payments, the PDF,
// and "Pay online" in Stripe TEST mode (FR-50, FR-39). Paid amounts update
// live when the owner records a payment or Stripe confirms one.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router";
import { api, download } from "../../api";
import { BillStatusBadge, ErrorBox, Loading } from "../../components/ui";
import * as fmt from "../../format";
import { useLiveEvents } from "../../live";
import type { Bill, BillSummary } from "../../types";

function useBillEvents() {
  const queries = useQueryClient();
  useLiveEvents((event) => {
    if (event.type === "bill") void queries.invalidateQueries({ queryKey: ["bills"] });
  });
}

export function MyBillsPage() {
  const { t } = useTranslation();
  useBillEvents();
  const bills = useQuery({ queryKey: ["bills", "mine"], queryFn: () => api<BillSummary[]>("/me/bills") });
  return (
    <>
      <h1>{t("bills.title")}</h1>
      <div className="card">
        {bills.isPending ? (
          <Loading />
        ) : bills.error ? (
          <ErrorBox error={bills.error} retry={() => bills.refetch()} />
        ) : bills.data.length === 0 ? (
          <p className="muted">{t("bills.none")}</p>
        ) : (
          <table className="responsive">
            <thead>
              <tr>
                <th>{t("bills.month")}</th>
                <th>{t("bills.number")}</th>
                <th className="num">{t("bills.total")}</th>
                <th>{t("bills.due")}</th>
                <th>{t("bills.status")}</th>
                <th>
                  <span className="sr-only">{t("bills.open")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {bills.data.map((bill) => (
                <tr key={bill.id}>
                  <td data-label={t("bills.month")}>{fmt.month(bill.month)}</td>
                  <td data-label={t("bills.number")}>{bill.bill_no ?? "-"}</td>
                  <td data-label={t("bills.total")} className="num">{bill.total_text}</td>
                  <td data-label={t("bills.due")}>{fmt.date(bill.due_date)}</td>
                  <td data-label={t("bills.status")}><BillStatusBadge status={bill.status} /></td>
                  <td>
                    <Link to={`/bills/${bill.id}`} aria-label={`${t("bills.open")} ${bill.bill_no ?? ""}`}>
                      {t("bills.open")}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

function money(amount: string, total: Bill): string {
  // The API sends exact decimals as text; show them with the bill's own decimals.
  const decimals = total.total.split(".")[1]?.length ?? 2;
  return `${Number(amount).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`;
}

export function BillPage() {
  const { t } = useTranslation();
  const { billId } = useParams();
  const [params] = useSearchParams();
  useBillEvents();
  const bill = useQuery({ queryKey: ["bills", billId], queryFn: () => api<Bill>(`/bills/${billId}`) });
  const pay = useMutation({
    mutationFn: () => api<{ url: string }>(`/bills/${billId}/checkout`, { method: "POST" }),
    onSuccess: ({ url }) => window.location.assign(url), // Stripe's own page
  });
  const pdf = useMutation({ mutationFn: () => download(`/bills/${billId}/pdf`, `bill-${billId}.pdf`) });

  if (bill.isPending) return <Loading />;
  if (bill.error) return <ErrorBox error={bill.error} retry={() => bill.refetch()} />;
  const b = bill.data;
  const outstanding = Number(b.outstanding) > 0;

  return (
    <>
      <p>
        <Link to="/bills">← {t("bills.title")}</Link>
      </p>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("bills.detail", { number: b.bill_no ?? "-" })}</h1>
        <BillStatusBadge status={b.status} />
      </div>
      {params.get("payment") === "done" && (
        <p className="notice ok" role="status">{t("bills.paymentDone")}</p>
      )}
      {params.get("payment") === "cancelled" && (
        <p className="notice warn" role="status">{t("bills.paymentCancelled")}</p>
      )}
      {b.status === "void" && <p className="notice warn">{t("bills.void", { reason: b.void_reason })}</p>}

      <div className="grid-2">
        <section className="card">
          <p className="stat">
            <span className="value">{b.total_text}</span>
          </p>
          {outstanding && b.status !== "void" && (
            <p><strong>{t("bills.toPay", { amount: `${b.currency_code} ${money(b.outstanding, b)}` })}</strong></p>
          )}
          <p className="muted small" style={{ margin: 0 }}>
            {fmt.month(b.month)} · {t("bills.period", { from: fmt.date(b.period_start), to: fmt.date(b.period_end) })}
            <br />
            {t("bills.used", { litres: fmt.litres(b.litres) })}
            {b.reading_start && b.reading_end && (
              <>
                <br />
                {t("bills.meter", { start: fmt.litres(b.reading_start), end: fmt.litres(b.reading_end) })}
              </>
            )}
            <br />
            {t("bills.due")}: {fmt.date(b.due_date)}
          </p>
          <div className="row" style={{ marginTop: "1rem" }}>
            <button onClick={() => pdf.mutate()} disabled={pdf.isPending}>
              {t("bills.pdf")}
            </button>
            {b.can_pay_online && outstanding && (
              <button className="primary" onClick={() => pay.mutate()} disabled={pay.isPending}>
                {t("bills.payOnline")}
              </button>
            )}
          </div>
          {b.can_pay_online && outstanding && <p className="muted small">{t("bills.payHelp")}</p>}
          {(pdf.error || pay.error) && <ErrorBox error={pdf.error ?? pay.error} />}
        </section>

        <section className="card">
          <h2>{t("bills.lines")}</h2>
          <table>
            <tbody>
              {b.lines.map((line, n) => (
                <tr key={n}>
                  <td>{line.description}</td>
                  <td className="num">{money(line.amount, b)}</td>
                </tr>
              ))}
              <tr>
                <th>{t("bills.subtotal")}</th>
                <td className="num">{money(b.subtotal, b)}</td>
              </tr>
              {Number(b.tax_total) > 0 && (
                <tr>
                  <th>{t("bills.tax")}</th>
                  <td className="num">{money(b.tax_total, b)}</td>
                </tr>
              )}
              <tr>
                <th>{t("bills.total")}</th>
                <td className="num"><strong>{b.total_text}</strong></td>
              </tr>
            </tbody>
          </table>
        </section>

        <section className="card">
          <h2>{t("bills.payments")}</h2>
          {b.payments.length === 0 ? (
            <p className="muted">{t("bills.noPayments")}</p>
          ) : (
            <ul className="list">
              {b.payments.map((p) => (
                <li key={p.id} style={p.reversed_at ? { textDecoration: "line-through" } : undefined}>
                  {fmt.date(p.received_on)} · {t(`bills.methods.${p.method}`)} ·{" "}
                  <strong>{b.currency_code} {money(p.amount, b)}</strong>
                  {p.reference && <span className="muted"> · {p.reference}</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  );
}
