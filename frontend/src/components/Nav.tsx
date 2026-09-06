import { NavLink, useLocation } from "react-router-dom";
import { get, useApi } from "../api/client";
import type { Account } from "../api/types";
import { ModeBadge } from "./ModeBadge";

const LINKS = [
  { to: "/journal", label: "Journal" },
  { to: "/analytics", label: "Analytics" },
  { to: "/portfolio", label: "Portfolio" },
  { to: "/steuer", label: "Steuer" },
  { to: "/reports", label: "Reports" },
  { to: "/settings", label: "Settings" },
];

function itemClass({ isActive }: { isActive: boolean }): string {
  return `flex items-center justify-between gap-2 rounded px-2 py-1 ${
    isActive ? "bg-surface2 text-ink" : "text-muted hover:bg-surface2 hover:text-ink"
  }`;
}

export function Nav() {
  const location = useLocation();
  // Re-read on navigation so an account added in Settings shows up here.
  const accounts = useApi(() => get<Account[]>("/accounts"), [location.pathname]);
  const active = (accounts.data ?? []).filter((account) => account.active);

  return (
    <nav className="flex w-60 shrink-0 flex-col gap-4 border-r border-line bg-surface px-3 py-4">
      <div className="px-2">
        <span className="text-sm font-semibold tracking-tight">trade-ledger</span>
        <p className="text-[11px] text-muted">journal · portfolio · Steuer</p>
      </div>

      <NavLink to="/" end className={itemClass}>
        Dashboard
      </NavLink>

      <div className="flex flex-col gap-0.5">
        <p className="px-2 pb-1 text-[10px] uppercase tracking-wide text-muted">Accounts</p>
        {accounts.error !== null && <p className="px-2 text-[11px] text-neg">{accounts.error}</p>}
        {accounts.error === null && active.length === 0 && (
          <p className="px-2 text-[11px] text-muted">
            {accounts.loading ? "…" : "none yet — add one in Settings"}
          </p>
        )}
        {active.map((account) => (
          <NavLink key={account.id} to={`/accounts/${account.id}`} className={itemClass}>
            <span className="truncate">{account.name}</span>
            <ModeBadge mode={account.mode} />
          </NavLink>
        ))}
      </div>

      <div className="flex flex-col gap-0.5">
        {LINKS.map((link) => (
          <NavLink key={link.to} to={link.to} className={itemClass}>
            {link.label}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
