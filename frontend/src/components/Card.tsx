import type { ReactNode } from "react";

export function Card({
  title,
  right,
  children,
  className,
}: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded border border-line bg-surface ${className ?? ""}`}>
      {(title !== undefined || right !== undefined) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
          <h2 className="text-[11px] font-medium uppercase tracking-wide text-muted">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}
