import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useMe } from "../auth";
import { currentTheme, saveTheme, type Theme } from "../theme";
import { IconLogout, IconMoon, IconSun } from "./icons";
import Logo from "./Logo";
import { NAV, titleFor, type NavItem } from "./nav";

function Item({ item }: { item: NavItem }) {
  const { Icon } = item;
  return (
    <NavLink to={item.path} end className={({ isActive }) => "nav-item" + (isActive ? " active" : "")}>
      <Icon />
      <span className="nav-label">{item.label}</span>
      <span className="nav-short">{item.short}</span>
    </NavLink>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button type="button" className="icon-btn" onClick={() => { saveTheme(next); setTheme(next); }}
      aria-label={next === "light" ? "Mudar para modo claro" : "Mudar para modo escuro"}
      title={next === "light" ? "Modo claro" : "Modo escuro"}>
      {theme === "dark" ? <IconSun /> : <IconMoon />}
    </button>
  );
}

export default function Shell() {
  const { me, logout } = useMe();
  const { pathname } = useLocation();
  const admin = me?.role === "admin";
  const title = titleFor(pathname);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <Logo />
          <span className="sidebar-sub">Apoio ao cliente</span>
        </div>
        <nav className="nav" aria-label="Menu">
          <div className="nav-group">
            <span className="nav-heading">Apoio</span>
            {NAV.filter((n) => !n.admin).map((n) => <Item key={n.path} item={n} />)}
          </div>
          {admin && (
            <div className="nav-group">
              <span className="nav-heading">Administração</span>
              {NAV.filter((n) => n.admin).map((n) => <Item key={n.path} item={n} />)}
            </div>
          )}
        </nav>
      </aside>

      <div className="main">
        <header className="header">
          <Logo className="header-logo" />
          <h1 className="page-title">{title}</h1>
          <div className="header-actions">
            <span className="account">
              <span className="avatar" aria-hidden="true">{me?.user.slice(0, 1).toUpperCase()}</span>
              <span className="account-text">
                <span className="account-name">{me?.user}</span>
                <span className="account-role">{admin ? "Administrador" : "Apoio"}</span>
              </span>
            </span>
            <ThemeToggle />
            <button type="button" className="btn btn-ghost" onClick={() => void logout()}>
              <IconLogout /><span className="btn-text">Sair</span>
            </button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
