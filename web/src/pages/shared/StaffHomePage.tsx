// Start page for owners and System Admins in Part 1 of M8. Their own screens
// come in the next parts; this page says so honestly and links to Swagger.
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useMe } from "../../auth";
import { API_BASE } from "../../config";

export function StaffHomePage() {
  const { t } = useTranslation();
  const me = useMe();
  return (
    <>
      <h1>{t("staff.title", { name: me.full_name })}</h1>
      <div className="card" style={{ maxWidth: 640 }}>
        <p>{t(me.role === "owner" ? "staff.comingOwner" : "staff.comingAdmin")}</p>
        <div className="row">
          <Link className="button primary" to="/alerts">{t("nav.alerts")}</Link>
          <Link className="button" to="/profile">{t("nav.profile")}</Link>
          <a className="button" href={`${API_BASE.replace(/\/v1$/, "")}/docs`}>{t("staff.swagger")}</a>
        </div>
      </div>
    </>
  );
}
