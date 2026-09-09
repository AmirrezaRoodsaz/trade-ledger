import { DASH } from "../fmt";
import type { KillRule, KillStatus } from "../api/bots";

/** The fleet list carries statuses only (`kill_summary`); `/health` carries
 * the full rule. Both render here — the extra fields only fill the tooltip.
 */
export type KillRuleLike = Pick<KillRule, "rule" | "status"> & Partial<KillRule>;

// ok stays quiet on purpose: five green chips per card would drown the one
// chip that matters.
const TONE: Record<KillStatus, string> = {
  ok: "border-line text-muted",
  warning: "border-live text-live",
  triggered: "border-neg text-neg",
};

function tooltip(rule: KillRuleLike): string {
  const parts = [`${rule.rule} · ${rule.status}`];
  if (rule.value || rule.threshold) parts.push(`${rule.value ?? DASH} / ${rule.threshold ?? DASH}`);
  if (rule.action && rule.action !== "none") parts.push(`action: ${rule.action}`);
  if (rule.detail) parts.push(rule.detail);
  return parts.join(" — ");
}

export function KillRulePanel({ rules }: { rules: KillRuleLike[] }) {
  if (rules.length === 0) return <span className="text-muted">{DASH}</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {rules.map((rule) => (
        <span
          key={rule.rule}
          title={tooltip(rule)}
          className={`rounded border px-1 text-[10px] uppercase leading-4 tracking-wide ${TONE[rule.status]}`}
        >
          {rule.rule}
        </span>
      ))}
    </div>
  );
}
