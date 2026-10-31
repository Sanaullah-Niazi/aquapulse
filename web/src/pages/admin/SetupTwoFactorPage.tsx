// FR-70: "System Admins must" use two-factor login. Until they turn it on,
// this is the only screen they get (the API refuses everything else too).
import { useTranslation } from "react-i18next";
import { TwoFactor } from "../shared/ProfilePage";

export function SetupTwoFactorPage() {
  const { t } = useTranslation();
  return (
    <>
      <h1>{t("admin.setup.title")}</h1>
      <p className="notice warn" style={{ maxWidth: 640 }}>{t("admin.setup.why")}</p>
      <ol className="muted" style={{ maxWidth: 640 }}>
        <li>{t("admin.setup.step1")}</li>
        <li>{t("admin.setup.step2")}</li>
        <li>{t("admin.setup.step3")}</li>
      </ol>
      <div style={{ maxWidth: 640 }}>
        <TwoFactor />
      </div>
    </>
  );
}
