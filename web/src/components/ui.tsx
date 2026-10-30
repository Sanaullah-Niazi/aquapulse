// Small pieces used on many screens.
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { BillStatus, Severity } from "../types";

export function Loading() {
  const { t } = useTranslation();
  return (
    <p className="muted" role="status">
      {t("app.loading")}
    </p>
  );
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useTranslation();
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="notice bad" role="alert">
      <p>{t("app.error", { message })}</p>
      {retry && <button onClick={retry}>{t("app.retry")}</button>}
    </div>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

const SEVERITY_CLASS: Record<Severity, string> = { info: "info", warning: "warn", critical: "bad" };
const SEVERITY_ICON: Record<Severity, string> = { info: "ℹ", warning: "⚠", critical: "⛔" };

/** Colour AND an icon and a word, never colour alone (accessibility, FR-43). */
export function SeverityBadge({ severity }: { severity: Severity }) {
  const { t } = useTranslation();
  return (
    <span className={`badge ${SEVERITY_CLASS[severity]}`}>
      <span aria-hidden="true">{SEVERITY_ICON[severity]}</span>
      {t(`alerts.severities.${severity}`)}
    </span>
  );
}

const BILL_CLASS: Record<BillStatus, string> = {
  draft: "",
  issued: "info",
  partially_paid: "warn",
  paid: "ok",
  overdue: "bad",
  void: "",
};

export function BillStatusBadge({ status }: { status: BillStatus }) {
  const { t } = useTranslation();
  return <span className={`badge ${BILL_CLASS[status]}`}>{t(`bills.statuses.${status}`)}</span>;
}

export function QualityBadge({ label }: { label: "safe" | "check" | "unsafe" | "unknown" }) {
  const { t } = useTranslation();
  const style = { safe: "ok", check: "warn", unsafe: "bad", unknown: "" }[label];
  const icon = { safe: "✓", check: "!", unsafe: "✕", unknown: "?" }[label];
  return (
    <span className={`badge ${style}`}>
      <span aria-hidden="true">{icon}</span>
      {t(`quality.${label}`)}
    </span>
  );
}

export function Segmented<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Field({
  id,
  label,
  help,
  children,
}: {
  id: string;
  label: string;
  help?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {help && (
        <div className="help" id={`${id}-help`}>
          {help}
        </div>
      )}
    </div>
  );
}
