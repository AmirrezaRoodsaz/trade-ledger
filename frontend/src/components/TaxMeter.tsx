import type { ReactNode } from "react";
import { eur, toNumber } from "../fmt";

/** How much of a yearly allowance is used up, plus the note that applies when
 * it is blown. The Dashboard keeps its own smaller copy of the bar.
 */
export function TaxMeter({
  label,
  value,
  limit,
  warning,
}: {
  label: string;
  value: string | number | null | undefined;
  limit: string | number | null | undefined;
  warning?: ReactNode;
}) {
  const used = toNumber(value) ?? 0;
  const cap = toNumber(limit);
  const share = cap !== null && cap > 0 ? Math.min(1, Math.max(0, used / cap)) : 0;
  const over = warning !== undefined && warning !== null;
  return (
    <div>
      <div className="flex justify-between gap-2">
        <span className="label">{label}</span>
        <span className={over ? "text-neg" : undefined}>
          {eur(value)} / {eur(limit)}
        </span>
      </div>
      <div className="mt-1 h-1 w-full rounded bg-surface2">
        <div
          className={`h-1 rounded ${over ? "bg-neg" : "bg-accent"}`}
          style={{ width: `${(share * 100).toFixed(1)}%` }}
        />
      </div>
      {over && <p className="mt-1 text-[11px] text-neg">{warning}</p>}
    </div>
  );
}
