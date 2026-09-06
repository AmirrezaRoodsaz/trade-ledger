import type { ReactNode } from "react";

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-muted">{children}</p>;
}
