// A-03: the whole audit log (FR-28) with filters, and "Check the chain now"
// (FR-73): the API re-calculates every hash, which proves that no entry was
// changed or removed. The worker also checks it every day.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { ErrorBox, Field, Loading } from "../../components/ui";
import * as fmt from "../../format";
import type { AuditCheck, AuditEntry } from "../../types";

const PAGE = 50;

export function AuditPage() {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [draft, setDraft] = useState({ action: "", since: "", until: "" });
  const [filters, setFilters] = useState(draft);
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (filters.action) params.set("action", filters.action);
  // Whole days in the browser's time: from the start of "since" to the end of "until".
  if (filters.since) params.set("since", new Date(`${filters.since}T00:00`).toISOString());
  if (filters.until) params.set("until", new Date(`${filters.until}T23:59:59`).toISOString());
  const entries = useQuery({
    queryKey: ["admin-audit", params.toString()],
    queryFn: () => api<AuditEntry[]>(`/admin/audit?${params}`),
    placeholderData: keepPreviousData,
  });
  const checks = useQuery({
    queryKey: ["admin-audit-checks"],
    queryFn: () => api<AuditCheck[]>("/admin/audit/checks?limit=10"),
  });
  const verify = useMutation({
    mutationFn: () => api<AuditCheck>("/admin/audit/verify", { method: "POST" }),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: ["admin-audit-checks"] });
      void queries.invalidateQueries({ queryKey: ["admin-health"] });
    },
  });
  const search = (e: FormEvent) => {
    e.preventDefault();
    setOffset(0);
    setFilters(draft);
  };

  return (
    <>
      <h1>{t("admin.audit.title")}</h1>

      <section className="card" aria-labelledby="chain-title" style={{ marginBottom: "1rem" }}>
        <h2 id="chain-title">{t("admin.audit.chain")}</h2>
        <p className="muted small">{t("admin.audit.chainHelp")}</p>
        <button className="primary" onClick={() => verify.mutate()} disabled={verify.isPending}>
          {verify.isPending ? t("admin.audit.checking") : t("admin.audit.verify")}
        </button>
        {verify.error && <ErrorBox error={verify.error} />}
        {verify.data && (
          <p className={`notice ${verify.data.ok ? "ok" : "bad"}`} role="status" style={{ marginTop: "1rem" }}>
            {verify.data.ok
              ? t("admin.audit.chainOk", { count: verify.data.entries, when: fmt.dateTime(verify.data.at) })
              : t("admin.audit.chainBroken", { id: verify.data.first_broken_id, when: fmt.dateTime(verify.data.at) })}{" "}
            ({t("admin.audit.took", { seconds: verify.data.seconds })})
          </p>
        )}
        {checks.data && checks.data.length > 0 && (
          <details style={{ marginTop: "1rem" }}>
            <summary>{t("admin.audit.history")}</summary>
            <ul className="list">
              {checks.data.map((c) => (
                <li key={c.at}>
                  {c.ok ? "✓" : "✕"} {fmt.dateTime(c.at)} · {t("admin.audit.entries", { count: c.entries })} ·{" "}
                  {c.requested_by ? t("admin.audit.byAdmin") : t("admin.audit.daily")}
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <form className="card row" onSubmit={search} style={{ alignItems: "flex-end", marginBottom: "1rem" }}
            aria-label={t("admin.audit.filters")}>
        <Field id="f-action" label={t("admin.audit.action")}>
          <input id="f-action" placeholder="auth.login" maxLength={64} value={draft.action}
                 onChange={(e) => setDraft({ ...draft, action: e.target.value })} />
        </Field>
        <Field id="f-since" label={t("admin.audit.since")}>
          <input id="f-since" type="date" value={draft.since} onChange={(e) => setDraft({ ...draft, since: e.target.value })} />
        </Field>
        <Field id="f-until" label={t("admin.audit.until")}>
          <input id="f-until" type="date" value={draft.until} onChange={(e) => setDraft({ ...draft, until: e.target.value })} />
        </Field>
        <button className="primary" type="submit" style={{ marginBottom: "1rem" }}>{t("admin.audit.search")}</button>
      </form>

      <section className="card">
        {entries.isPending ? (
          <Loading />
        ) : entries.error ? (
          <ErrorBox error={entries.error} retry={() => entries.refetch()} />
        ) : entries.data.length === 0 ? (
          <p className="muted">{t("admin.audit.none")}</p>
        ) : (
          <table className="responsive">
            <thead>
              <tr>
                <th>#</th>
                <th>{t("admin.audit.when")}</th>
                <th>{t("admin.audit.action")}</th>
                <th>{t("admin.audit.who")}</th>
                <th>{t("admin.audit.target")}</th>
                <th>{t("admin.audit.details")}</th>
              </tr>
            </thead>
            <tbody>
              {entries.data.map((a) => (
                <tr key={a.id}>
                  <td data-label="#">{a.id}</td>
                  <td data-label={t("admin.audit.when")}>{fmt.dateTime(a.at)}</td>
                  <td data-label={t("admin.audit.action")}><code>{a.action}</code></td>
                  <td data-label={t("admin.audit.who")}>
                    {a.actor_role ? t(`roles.${a.actor_role}`, { defaultValue: a.actor_role }) : t("admin.audit.system")}
                    {a.ip && <div className="muted small">{a.ip}</div>}
                  </td>
                  <td data-label={t("admin.audit.target")} style={{ overflowWrap: "anywhere" }}>
                    {a.target_type ? `${a.target_type} ${a.target_id ?? ""}` : "-"}
                  </td>
                  <td data-label={t("admin.audit.details")} style={{ overflowWrap: "anywhere" }}>
                    {Object.keys(a.details).length > 0 ? (
                      <details>
                        <summary>{t("admin.audit.show")}</summary>
                        <pre className="small" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                          {JSON.stringify(a.details, null, 2)}
                        </pre>
                      </details>
                    ) : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="row" style={{ justifyContent: "space-between", marginTop: "1rem" }}>
          <button onClick={() => setOffset(Math.max(0, offset - PAGE))} disabled={offset === 0}>
            ← {t("admin.audit.newer")}
          </button>
          <span className="muted small">{t("admin.audit.page", { from: offset + 1, to: offset + (entries.data?.length ?? 0) })}</span>
          <button onClick={() => setOffset(offset + PAGE)} disabled={(entries.data?.length ?? 0) < PAGE}>
            {t("admin.audit.older")} →
          </button>
        </div>
      </section>
    </>
  );
}
