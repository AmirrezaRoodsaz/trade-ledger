import type { ReactNode } from "react";
import type { StageResult } from "../api/bots";
import type { Backtest } from "../api/backtests";
import { DASH, date, eur, num, pct, r } from "../fmt";

/** One row of the comparison: the same measure in all three columns. */
interface Row {
  label: string;
  backtest: (result: Backtest) => ReactNode;
  stage: (result: StageResult) => ReactNode;
}

/** `max_drawdown_pct` is a percentage already; the journal reports its
 * drawdown in R and euro instead, so the row shows what each side has.
 */
const ROWS: Row[] = [
  {
    label: "Trades",
    backtest: (result) => num(result.trades, 0),
    stage: (result) => num(result.count, 0),
  },
  {
    label: "Expectancy",
    backtest: (result) => r(result.expectancy_r),
    stage: (result) => r(result.expectancy_r),
  },
  {
    label: "Profit factor",
    backtest: (result) => num(result.profit_factor, 2),
    stage: (result) => num(result.profit_factor, 2),
  },
  {
    label: "Win rate",
    backtest: (result) => pct(result.win_rate),
    stage: (result) => pct(result.win_rate),
  },
  {
    label: "Max drawdown",
    backtest: (result) => `${num(result.max_drawdown_pct, 1)} %`,
    stage: (result) => `${r(result.max_dd_r)} · ${eur(result.max_dd_eur)}`,
  },
  {
    label: "Period",
    backtest: (result) => `${date(result.period_start)} – ${date(result.period_end)}`,
    // ponytail: the journal columns are "everything closed so far" — the API
    // reports no window for them, and inventing one from the trades would
    // only repeat what the equity curve already shows.
    stage: () => DASH,
  },
];

function Head({ title, hint }: { title: string; hint: string }) {
  return (
    <th className="px-2 py-1 text-right font-medium">
      <div>{title}</div>
      <div className="text-[11px] font-normal text-muted">{hint}</div>
    </th>
  );
}

/** Backtest, incubation and real money side by side — the same measures, so
 * a drop between the columns is visible without arithmetic.
 */
export function StageResults({
  backtest,
  incubation,
  live,
}: {
  backtest: Backtest | null;
  incubation: StageResult;
  live: StageResult;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line text-[11px] uppercase tracking-wide text-muted">
            <th className="px-2 py-1 font-medium">Measure</th>
            <Head
              title="Backtest"
              hint={backtest === null ? "no passed result" : backtest.label || backtest.strategy}
            />
            <Head title="Incubation" hint="demo and paper trades" />
            <Head title="Real money" hint="live trades" />
          </tr>
        </thead>
        <tbody>
          {ROWS.map((row) => (
            <tr key={row.label} className="border-b border-line last:border-0">
              <td className="px-2 py-1.5 text-muted">{row.label}</td>
              <td className="px-2 py-1.5 text-right">
                {backtest === null ? DASH : row.backtest(backtest)}
              </td>
              <td className="px-2 py-1.5 text-right">
                {incubation.count === 0 && row.label !== "Trades" ? DASH : row.stage(incubation)}
              </td>
              <td className="px-2 py-1.5 text-right">
                {live.count === 0 && row.label !== "Trades" ? DASH : row.stage(live)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
