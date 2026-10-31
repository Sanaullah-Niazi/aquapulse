// The frame around every logged-in page: top bar, menu for this role, and
// the page itself. On phones the menu folds away behind a button (FR-53).
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, useLocation } from "react-router";
import { useMe, useAuth } from "../auth";
import { BASE_PATH } from "../config";
import { useLiveStatus } from "../live";
import { useTheme, type ThemeChoice } from "../theme";
import type { Role } from "../types";

const MENUS: Record<Role, { to: string; key: string }[]> = {
  resident: [
    { to: "/", key: "nav.myHome" },
    { to: "/usage", key: "nav.usage" },
    { to: "/quality", key: "nav.quality" },
    { to: "/bills", key: "nav.bills" },
    { to: "/alerts", key: "nav.alerts" },
  ],
  owner: [
    { to: "/", key: "nav.overview" },
    { to: "/homes", key: "nav.homes" },
    { to: "/tank", key: "nav.tank" },
    { to: "/devices", key: "nav.devices" },
    { to: "/bills", key: "nav.ownerBills" },
    { to: "/tariffs", key: "nav.tariffs" },
    { to: "/alerts", key: "nav.alerts" },
    { to: "/alert-rules", key: "nav.alertRules" },
    { to: "/settings", key: "nav.settings" },
  ],
  sysadmin: [
    { to: "/", key: "nav.overview" },
    { to: "/alerts", key: "nav.alerts" },
  ],
};

function useOnline(): boolean {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  return online;
}

export function Layout({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const me = useMe();
  const { logout } = useAuth();
  const live = useLiveStatus();
  const online = useOnline();
  const [theme, setTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]); // close the phone menu after a click

  return (
    <>
      <a className="skip-link" href="#main">
        {t("app.skip")}
      </a>
      <header className="topbar">
        <button
          className="menu-button"
          aria-expanded={open}
          aria-controls="sidenav"
          onClick={() => setOpen(!open)}
        >
          {open ? t("app.close") : t("app.menu")}
        </button>
        <NavLink to="/" className="brand">
          <img src={`${BASE_PATH}favicon.svg`} alt="" />
          {t("app.name")}
        </NavLink>
        <span className="spacer" />
        <span className="row small muted" role="status" aria-live="polite">
          <span className={`dot ${live}`} aria-hidden="true" />
          {t(`app.live.${live}`)}
        </span>
      </header>
      <div className="layout">
        <nav id="sidenav" className={`sidenav${open ? " open" : ""}`} aria-label={t("app.menu")}>
          <div className="who">
            <strong>{me.full_name}</strong>
            <br />
            {t(`roles.${me.role}`)}
          </div>
          {MENUS[me.role].map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"}>
              {t(item.key)}
            </NavLink>
          ))}
          <NavLink to="/profile">{t("nav.profile")}</NavLink>
          <NavLink to="/notifications">{t("nav.notifications")}</NavLink>
          <a
            href="#logout"
            onClick={(e) => {
              e.preventDefault();
              void logout();
            }}
          >
            {t("nav.logout")}
          </a>
          {/* Light, dark or like the device (kept in this browser). */}
          <div className="theme-choice">
            <label htmlFor="theme">{t("app.theme.label")}</label>
            <select
              id="theme"
              value={theme}
              onChange={(e) => setTheme(e.target.value as ThemeChoice)}
            >
              <option value="system">{t("app.theme.system")}</option>
              <option value="light">{t("app.theme.light")}</option>
              <option value="dark">{t("app.theme.dark")}</option>
            </select>
          </div>
        </nav>
        <main id="main" tabIndex={-1}>
          {!online && (
            <p className="notice warn" role="status">
              {t("app.offline")}
            </p>
          )}
          {children}
        </main>
      </div>
    </>
  );
}
