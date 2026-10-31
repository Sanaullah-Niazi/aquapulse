// Which page shows for which address, and for which role (FR-53). Someone
// not logged in only reaches the login and password pages.
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, Route, Routes, useLocation } from "react-router";
import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { setLive } from "./live";
import { AlertRulesPage } from "./pages/owner/AlertRulesPage";
import { OwnerBillsPage } from "./pages/owner/BillsPage";
import { DevicesPage } from "./pages/owner/DevicesPage";
import { HomeDetailPage } from "./pages/owner/HomeDetailPage";
import { HomesPage } from "./pages/owner/HomesPage";
import { OverviewPage } from "./pages/owner/OverviewPage";
import { SettingsPage } from "./pages/owner/SettingsPage";
import { TankPage } from "./pages/owner/TankPage";
import { TariffsPage } from "./pages/owner/TariffsPage";
import { BillPage, MyBillsPage } from "./pages/resident/BillsPages";
import { MyHomePage } from "./pages/resident/MyHomePage";
import { QualityPage } from "./pages/resident/QualityPage";
import { UsagePage } from "./pages/resident/UsagePage";
import { AlertsPage } from "./pages/shared/AlertsPage";
import {
  AcceptInvitePage,
  ForgotPasswordPage,
  LoginPage,
  ResetPasswordPage,
} from "./pages/shared/AuthPages";
import { NotificationsPage } from "./pages/shared/NotificationsPage";
import { ProfilePage } from "./pages/shared/ProfilePage";
import { StaffHomePage } from "./pages/shared/StaffHomePage";

function NotFound() {
  const { t } = useTranslation();
  return (
    <>
      <h1>{t("app.notFound")}</h1>
      <Link to="/">{t("app.home")}</Link>
    </>
  );
}

export function App() {
  const { me, loading } = useAuth();
  const location = useLocation();
  useEffect(() => setLive(me !== null), [me]);

  if (loading) return <Loading />;

  if (!me)
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/accept-invite" element={<AcceptInvitePage />} />
        <Route path="*" element={<Navigate to="/login" replace state={{ from: location.pathname }} />} />
      </Routes>
    );

  // Logged in: the login and password pages lead home (or where you were going).
  const from = (location.state as { from?: string } | null)?.from;
  return (
    <Layout>
      <Routes>
        {["/login", "/forgot-password", "/reset-password", "/accept-invite"].map((path) => (
          <Route key={path} path={path} element={<Navigate to={from ?? "/"} replace />} />
        ))}
        {me.role === "resident" ? (
          <>
            <Route path="/" element={<MyHomePage />} />
            <Route path="/usage" element={<UsagePage />} />
            <Route path="/quality" element={<QualityPage />} />
            <Route path="/bills" element={<MyBillsPage />} />
            <Route path="/bills/:billId" element={<BillPage />} />
          </>
        ) : me.role === "owner" ? (
          <>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/homes" element={<HomesPage />} />
            <Route path="/homes/:homeId" element={<HomeDetailPage />} />
            <Route path="/tank" element={<TankPage />} />
            <Route path="/devices" element={<DevicesPage />} />
            <Route path="/tariffs" element={<TariffsPage />} />
            <Route path="/bills" element={<OwnerBillsPage />} />
            <Route path="/bills/:billId" element={<BillPage />} />
            <Route path="/alert-rules" element={<AlertRulesPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </>
        ) : (
          // The System Admin console comes in the next part of M8.
          <Route path="/" element={<StaffHomePage />} />
        )}
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Layout>
  );
}
