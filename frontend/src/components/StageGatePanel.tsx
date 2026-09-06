import type { StageGate } from "../api/analytics";
import { eur, num } from "../fmt";
import { EmptyState } from "./EmptyState";

/** A check's `actual`/`required` arrive as plain strings — a trade count or a
 * ratio. Give the ratios two decimals and the counts none.
 */
const value = (raw: string): string => num(raw, raw.includes(".") ? 2 : 0);

function Badge({ passed }: { passed: boolean }) {
  return (
    <span
      className={`rounded border px-1 text-[10px] uppercase leading-4 tracking-wide ${
        passed ? "border-pos text-pos" : "border-neg text-neg"
      }`}
    >
      {passed ? "pass" : "fail"}
    </span>
  );
}

/** The same four checks the money side's `trade_stats.py` applies before a
 * stage advances. `mode=all` has no gate — a mixed sample is not a stage.
 */
export function StageGatePanel({ gate }: { gate: StageGate | null }) {
  if (gate === null) {
    return (
      <EmptyState>
        The stage gate needs a single mode. Pick <b>paper</b> or <b>live</b> in the filter bar —
        a mixed sample cannot decide whether a stage is passed.
      </EmptyState>
    );
  }
  return (
    <>
      <p className="mb-3 text-muted">
        {gate.mode} · capital {eur(gate.capital)}
      </p>
      <ul>
        {gate.checks.map((check) => (
          <li
            key={check.name}
            className="flex items-baseline justify-between gap-4 border-b border-line py-1.5 last:border-0"
          >
            <span className="flex items-baseline gap-2">
              <Badge passed={check.passed} />
              {check.name}
            </span>
            <span className={check.passed ? "" : "text-neg"}>
              {value(check.actual)}{" "}
              <span className="text-muted">/ required {value(check.required)}</span>
            </span>
          </li>
        ))}
      </ul>
      <p className={`mt-3 ${gate.passed ? "text-pos" : "text-muted"}`}>
        {gate.passed ? "Gate passed. " : "Gate not passed. "}
        {gate.next}
      </p>
    </>
  );
}
