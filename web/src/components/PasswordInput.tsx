// A new-password box with a simple strength hint. The server has the final
// word (length, common passwords, your email name); this only helps early.
import { useTranslation } from "react-i18next";

export function strength(password: string): "weak" | "ok" | "strong" {
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^\w]/, /\s/].filter((re) => re.test(password)).length;
  if (password.length < 10 || new Set(password.toLowerCase()).size < 4) return "weak";
  if (password.length >= 16 || (password.length >= 12 && kinds >= 3)) return "strong";
  return "ok";
}

export function PasswordInput({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const level = strength(value);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="password"
        autoComplete="new-password"
        required
        minLength={10}
        value={value}
        aria-describedby={`${id}-help`}
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="help" id={`${id}-help`}>
        {t("password.help")}{" "}
        {value && (
          <strong className={level === "weak" ? "error" : undefined} aria-live="polite">
            {t("password.strength", { label: t(`password.${level}`) })}
          </strong>
        )}
      </div>
    </div>
  );
}
