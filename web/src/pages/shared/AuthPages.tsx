// Pages for people who are not logged in (S-02 to S-05): log in (with the
// two-factor step), forgot password, set a new password, accept an invitation.
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router";
import { api, type Tokens } from "../../api";
import { useAuth } from "../../auth";
import { BASE_PATH } from "../../config";
import { ErrorBox, Field } from "../../components/ui";
import { PasswordInput } from "../../components/PasswordInput";

function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <main className="auth-page" id="main">
      <div className="card">
        <div className="brand">
          <img src={`${BASE_PATH}favicon.svg`} alt="" width={32} height={32} />
          {t("app.name")}
        </div>
        <h1>{title}</h1>
        {children}
      </div>
    </main>
  );
}

/** Runs a form action, with "busy" and "error" for the screen. */
function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = (action: () => Promise<void>) => async (event: FormEvent) => {
    event.preventDefault();
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
  return { busy, error, run };
}

export function LoginPage() {
  const { t } = useTranslation();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"password" | "code">("password");
  const { busy, error, run } = useAction();

  const submit = run(async () => {
    const result = await login(email, password, step === "code" ? code : undefined);
    if (result === "need-code") setStep("code");
  });

  if (step === "code")
    return (
      <AuthCard title={t("login.codeTitle")}>
        <form onSubmit={submit}>
          <p className="muted">{t("login.codeHelp")}</p>
          <Field id="code" label={t("login.code")}>
            <input
              id="code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoComplete="one-time-code"
              inputMode="text"
              autoFocus
              required
            />
          </Field>
          {error != null && <ErrorBox error={error} />}
          <div className="row">
            <button className="primary" disabled={busy}>
              {t("login.verify")}
            </button>
            <button type="button" onClick={() => setStep("password")}>
              {t("login.back")}
            </button>
          </div>
        </form>
      </AuthCard>
    );

  return (
    <AuthCard title={t("login.title")}>
      <form onSubmit={submit}>
        <Field id="email" label={t("login.email")}>
          <input
            id="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </Field>
        <Field id="password" label={t("login.password")}>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </Field>
        {error != null && <ErrorBox error={error} />}
        <p>
          <button className="primary" disabled={busy} style={{ width: "100%" }}>
            {t("login.submit")}
          </button>
        </p>
        <Link to="/forgot-password">{t("login.forgot")}</Link>
      </form>
    </AuthCard>
  );
}

export function ForgotPasswordPage() {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const { busy, error, run } = useAction();
  const submit = run(async () => {
    await api("/auth/password/forgot", { method: "POST", auth: false, body: { email } });
    setSent(true);
  });
  return (
    <AuthCard title={t("forgot.title")}>
      {sent ? (
        <p className="notice ok" role="status">
          {t("forgot.sent")}
        </p>
      ) : (
        <form onSubmit={submit}>
          <p className="muted">{t("forgot.help")}</p>
          <Field id="email" label={t("login.email")}>
            <input id="email" type="email" value={email} required
                   onChange={(e) => setEmail(e.target.value)} />
          </Field>
          {error != null && <ErrorBox error={error} />}
          <button className="primary" disabled={busy}>
            {t("forgot.submit")}
          </button>
        </form>
      )}
      <p style={{ marginTop: "1rem" }}>
        <Link to="/reset-password">{t("forgot.haveCode")}</Link> ·{" "}
        <Link to="/login">{t("login.title")}</Link>
      </p>
    </AuthCard>
  );
}

export function ResetPasswordPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const [token, setToken] = useState(params.get("token") ?? "");
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const { busy, error, run } = useAction();
  const submit = run(async () => {
    await api("/auth/password/reset", {
      method: "POST",
      auth: false,
      body: { token: token.trim(), new_password: password },
    });
    setDone(true);
  });
  return (
    <AuthCard title={t("reset.title")}>
      {done ? (
        <>
          <p className="notice ok" role="status">
            {t("reset.done")}
          </p>
          <Link className="button primary" to="/login">
            {t("login.title")}
          </Link>
        </>
      ) : (
        <form onSubmit={submit}>
          <Field id="token" label={t("reset.code")}>
            <input id="token" value={token} required onChange={(e) => setToken(e.target.value)} />
          </Field>
          <PasswordInput id="password" label={t("reset.password")} value={password}
                         onChange={setPassword} />
          {error != null && <ErrorBox error={error} />}
          <button className="primary" disabled={busy}>
            {t("reset.submit")}
          </button>
        </form>
      )}
    </AuthCard>
  );
}

export function AcceptInvitePage() {
  const { t } = useTranslation();
  const { start } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [token, setToken] = useState(params.get("token") ?? "");
  const [password, setPassword] = useState("");
  const { busy, error, run } = useAction();
  const submit = run(async () => {
    const tokens = await api<Tokens>("/auth/invitations/accept", {
      method: "POST",
      auth: false,
      body: { token: token.trim(), password },
    });
    await start(tokens); // logged in straight away
    navigate("/");
  });
  return (
    <AuthCard title={t("invite.title")}>
      <form onSubmit={submit}>
        <p className="muted">{t("invite.help")}</p>
        <Field id="token" label={t("invite.code")}>
          <input id="token" value={token} required onChange={(e) => setToken(e.target.value)} />
        </Field>
        <PasswordInput id="password" label={t("invite.password")} value={password}
                       onChange={setPassword} />
        {error != null && <ErrorBox error={error} />}
        <button className="primary" disabled={busy}>
          {t("invite.submit")}
        </button>
      </form>
    </AuthCard>
  );
}
