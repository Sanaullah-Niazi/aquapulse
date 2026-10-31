// S-06: my details, my password, two-factor login (FR-70) and "log out everywhere".
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { useAuth, useMe } from "../../auth";
import { PasswordInput } from "../../components/PasswordInput";
import { ErrorBox, Field } from "../../components/ui";

export function ProfilePage() {
  const { t } = useTranslation();
  return (
    <>
      <h1>{t("profile.title")}</h1>
      <div className="grid-2">
        <Details />
        <ChangePassword />
        <TwoFactor />
        <Sessions />
      </div>
    </>
  );
}

function Details() {
  const { t } = useTranslation();
  const me = useMe();
  const { reload } = useAuth();
  const [name, setName] = useState(me.full_name);
  const [phone, setPhone] = useState(me.phone ?? "");
  const save = useMutation({
    mutationFn: () =>
      api("/me", { method: "PATCH", body: { full_name: name, ...(phone ? { phone } : {}) } }),
    onSuccess: reload,
  });
  return (
    <form
      className="card"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <h2>{t("profile.details")}</h2>
      <p className="muted">
        {me.email} · {t(`roles.${me.role}`)}
      </p>
      <Field id="name" label={t("profile.name")}>
        <input id="name" value={name} required maxLength={120} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field id="phone" label={t("profile.phone")}>
        <input id="phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
      </Field>
      {save.error && <ErrorBox error={save.error} />}
      {save.isSuccess && <p className="notice ok" role="status">{t("app.saved")}</p>}
      <button className="primary" disabled={save.isPending}>
        {t("app.save")}
      </button>
    </form>
  );
}

function ChangePassword() {
  const { t } = useTranslation();
  const { logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = useMutation({
    mutationFn: () =>
      api("/me/password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      }),
    // The server ends every session after a password change: log in again.
    onSuccess: () => setTimeout(() => void logout(), 1500),
  });
  return (
    <form
      className="card"
      onSubmit={(e) => {
        e.preventDefault();
        change.mutate();
      }}
    >
      <h2>{t("profile.password")}</h2>
      <Field id="current" label={t("profile.current")}>
        <input id="current" type="password" autoComplete="current-password" required
               value={current} onChange={(e) => setCurrent(e.target.value)} />
      </Field>
      <PasswordInput id="new" label={t("profile.new")} value={next} onChange={setNext} />
      {change.error && <ErrorBox error={change.error} />}
      {change.isSuccess && <p className="notice ok" role="status">{t("profile.changed")}</p>}
      <button className="primary" disabled={change.isPending}>
        {t("profile.change")}
      </button>
    </form>
  );
}

interface Setup {
  secret: string;
  otpauth_url: string;
  qr_svg: string;
}

/** Turn two-factor login on or off, and make new recovery codes (FR-70). */
export function TwoFactor() {
  const { t } = useTranslation();
  const me = useMe();
  const { reload } = useAuth();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<Setup | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const step = (action: () => Promise<void>) => async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const begin = step(async () => {
    setSetup(await api<Setup>("/me/2fa/setup", { method: "POST", body: { password } }));
    setPassword("");
  });
  const enable = step(async () => {
    const r = await api<{ recovery_codes: string[] }>("/me/2fa/enable", {
      method: "POST",
      body: { code },
    });
    setCodes(r.recovery_codes);
    setSetup(null);
    setCode("");
    // /me is read again only after "I saved them": a System Admin's screens
    // change as soon as 2FA is on, and the codes must be seen first.
  });
  const disable = step(async () => {
    await api("/me/2fa/disable", { method: "POST", body: { password, code } });
    setPassword("");
    setCode("");
    await reload();
  });
  const renew = step(async () => {
    const r = await api<{ recovery_codes: string[] }>("/me/2fa/recovery-codes", {
      method: "POST",
      body: { password, code },
    });
    setCodes(r.recovery_codes);
    setPassword("");
    setCode("");
  });

  const passwordField = (
    <Field id="tf-password" label={t("profile.confirmPassword")}>
      <input id="tf-password" type="password" autoComplete="current-password" required
             value={password} onChange={(e) => setPassword(e.target.value)} />
    </Field>
  );
  const codeField = (
    <Field id="tf-code" label={t("profile.enterCode")}>
      <input id="tf-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} required
             autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} />
    </Field>
  );

  return (
    <section className="card" aria-labelledby="tf-title">
      <h2 id="tf-title">{t("profile.twoFactor")}</h2>
      {codes ? (
        <>
          <h3>{t("profile.recoveryTitle")}</h3>
          <p className="notice warn">{t("profile.recoveryHelp")}</p>
          <div className="codes" aria-label={t("profile.recoveryTitle")}>
            {codes.map((c) => (
              <span key={c}>{c}</span>
            ))}
          </div>
          <p style={{ marginTop: "1rem" }}>
            <button className="primary" onClick={() => {
              setCodes(null);
              void reload();
            }}>
              {t("profile.recoveryDone")}
            </button>
          </p>
        </>
      ) : me.two_factor ? (
        <form onSubmit={me.role === "sysadmin" ? renew : disable}>
          <p className="notice ok">{t("profile.twoFactorOn")}</p>
          {passwordField}
          {codeField}
          {error != null && <ErrorBox error={error} />}
          {me.role === "sysadmin" && <p className="muted small">{t("profile.adminKeepsIt")}</p>}
          <div className="row">
            {me.role !== "sysadmin" && (
              <button className="danger" disabled={busy}>
                {t("profile.turnOff")}
              </button>
            )}
            <button type="button" disabled={busy} onClick={(e) => void renew(e)}>
              {t("profile.newCodes")}
            </button>
          </div>
        </form>
      ) : setup ? (
        <form onSubmit={enable}>
          <p>{t("profile.scan")}</p>
          {/* The QR code is an SVG drawn by our own API (no outside service). */}
          <div className="qr" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
          <p>
            <code style={{ overflowWrap: "anywhere" }}>{setup.secret}</code>
          </p>
          {codeField}
          {error != null && <ErrorBox error={error} />}
          <button className="primary" disabled={busy}>
            {t("profile.enable")}
          </button>
        </form>
      ) : (
        <form onSubmit={begin}>
          <p className="muted">{t("profile.twoFactorOff")}</p>
          {passwordField}
          {error != null && <ErrorBox error={error} />}
          <button className="primary" disabled={busy}>
            {t("profile.start")}
          </button>
        </form>
      )}
    </section>
  );
}

function Sessions() {
  const { t } = useTranslation();
  const { logout } = useAuth();
  return (
    <section className="card">
      <h2>{t("profile.sessions")}</h2>
      <p className="muted">{t("profile.logoutAllHelp")}</p>
      <button onClick={() => void logout(true)}>{t("profile.logoutAll")}</button>
    </section>
  );
}
