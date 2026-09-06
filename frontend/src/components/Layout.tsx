import type { ReactNode } from "react";
import { Outlet } from "react-router-dom";
import { Nav } from "./Nav";

export function Layout() {
  return (
    <div className="flex h-full">
      <Nav />
      <main className="min-w-0 flex-1 overflow-y-auto px-8 py-6">
        <Outlet />
      </main>
    </div>
  );
}

export function PageHeader({ title, subtitle }: { title: string; subtitle?: ReactNode }) {
  return (
    <header className="mb-6">
      <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
      {subtitle !== undefined && <p className="text-muted">{subtitle}</p>}
    </header>
  );
}
