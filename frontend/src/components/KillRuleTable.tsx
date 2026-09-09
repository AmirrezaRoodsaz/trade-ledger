import type { KillRule } from "../api/bots";
import { DataTable } from "./DataTable";
import { DASH } from "../fmt";

/** Tone per kill-rule status. `triggered` is the only red in this table. */
const TONE: Record<string, string> = {
  ok: "text-muted",
  warning: "text-live",
  triggered: "text-neg",
};

export function KillRuleStatus({ status }: { status: string }) {
  return (
    <span className={`uppercase tracking-wide ${TONE[status] ?? "text-ink"}`}>{status}</span>
  );
}

/** K1–K5 with the numbers behind them: what was measured, what the limit is,
 * and what happens when it is crossed. */
export function KillRuleTable({ rules }: { rules: KillRule[] }) {
  return (
    <DataTable
      rows={rules}
      rowKey={(rule) => rule.rule}
      empty="No kill rules evaluated yet."
      columns={[
        { key: "rule", header: "Rule", render: (rule) => rule.rule },
        {
          key: "status",
          header: "Status",
          render: (rule) => <KillRuleStatus status={rule.status} />,
        },
        { key: "value", header: "Value", align: "right", render: (rule) => rule.value ?? DASH },
        {
          key: "threshold",
          header: "Threshold",
          align: "right",
          render: (rule) => rule.threshold ?? DASH,
        },
        { key: "action", header: "Action", render: (rule) => rule.action },
        {
          key: "detail",
          header: "Detail",
          render: (rule) => <span className="text-muted">{rule.detail || DASH}</span>,
        },
      ]}
    />
  );
}
