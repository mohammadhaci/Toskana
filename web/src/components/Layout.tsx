import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useSystemInfo } from "../api/hooks";
import { persistLanguage } from "../i18n";

const PAGES: Array<{ to: string; key: string; icon: string }> = [
  { to: "/live", key: "nav.live", icon: "📺" },
  { to: "/stats", key: "nav.stats", icon: "📊" },
  { to: "/events", key: "nav.events", icon: "🧾" },
  { to: "/reconcile", key: "nav.reconcile", icon: "🧮" },
];

const ADMIN: Array<{ to: string; key: string; icon: string }> = [
  { to: "/admin/restaurants", key: "nav.restaurants", icon: "🏠" },
  { to: "/admin/cameras", key: "nav.cameras", icon: "🎥" },
  { to: "/admin/exit-groups", key: "nav.exitGroups", icon: "🚪" },
  { to: "/admin/menu", key: "nav.menu", icon: "🍽️" },
  { to: "/admin/mappings", key: "nav.mappings", icon: "🔗" },
  { to: "/admin/models", key: "nav.models", icon: "🧠" },
  { to: "/system", key: "nav.system", icon: "🩺" },
];

function titleKeyFor(pathname: string): string {
  if (pathname.startsWith("/live")) return "nav.live";
  if (pathname.startsWith("/stats")) return "nav.stats";
  if (pathname.startsWith("/events")) return "nav.events";
  if (pathname.startsWith("/reconcile")) return "nav.reconcile";
  if (pathname.startsWith("/system")) return "nav.system";
  if (pathname.startsWith("/admin/restaurants")) return "nav.restaurants";
  if (pathname.startsWith("/admin/cameras")) return "nav.cameras";
  if (pathname.startsWith("/admin/exit-groups")) return "nav.exitGroups";
  if (pathname.startsWith("/admin/menu")) return "nav.menu";
  if (pathname.startsWith("/admin/mappings")) return "nav.mappings";
  if (pathname.startsWith("/admin/models")) return "nav.models";
  return "app.title";
}

export function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const change = (lang: "de" | "en") => {
    void i18n.changeLanguage(lang);
    persistLanguage(lang);
  };
  return (
    <div className="lang-switch" role="group" aria-label={t("lang.label")}>
      {(["de", "en"] as const).map((lang) => (
        <button
          key={lang}
          type="button"
          className={i18n.language === lang ? "active" : ""}
          onClick={() => change(lang)}
          title={t(`lang.${lang}`)}
        >
          {lang.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

export default function Layout() {
  const { t } = useTranslation();
  const location = useLocation();
  const { data: info } = useSystemInfo();

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">T</div>
          <div>
            <div className="brand-name">{t("app.title")}</div>
            <div className="brand-sub">{info?.active_restaurant?.name ?? t("app.subtitle")}</div>
          </div>
        </div>
        <nav className="nav" aria-label={t("app.title")}>
          {PAGES.map((page) => (
            <NavLink key={page.to} to={page.to}>
              <span className="nav-icon" aria-hidden="true">
                {page.icon}
              </span>
              <span className="label">{t(page.key)}</span>
            </NavLink>
          ))}
          <div className="nav-section">{t("nav.admin")}</div>
          {ADMIN.map((page) => (
            <NavLink key={page.to} to={page.to}>
              <span className="nav-icon" aria-hidden="true">
                {page.icon}
              </span>
              <span className="label">{t(page.key)}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <h1>{t(titleKeyFor(location.pathname))}</h1>
          <div className="topbar-right">
            <LanguageSwitcher />
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
