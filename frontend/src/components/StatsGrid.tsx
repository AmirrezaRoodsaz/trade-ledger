import type { Stats } from "../api/types";
import { eur, num, pct, r, signClass } from "../fmt";

type Format = "count" | "r" | "eur" | "pct" | "ratio" | "hours";

interface FieldSpec {
  key: keyof Stats;
  label: string;
  format: Format;
  /** One line, shown as the browser tooltip on the tile. */
  hint: string;
  /** Green/red is only ever the sign of a P&L figure. */
  signed?: boolean;
}

const FIELDS: FieldSpec[] = [
  { key: "count", label: "Trades", format: "count", hint: "Closed trades in the current filter." },
  { key: "wins", label: "Wins", format: "count", hint: "Closed trades with a positive result." },
  { key: "losses", label: "Losses", format: "count", hint: "Closed trades with a negative result." },
  { key: "flat", label: "Flat", format: "count", hint: "Closed trades that ended at exactly zero." },
  { key: "win_rate", label: "Win rate", format: "pct", hint: "Wins divided by closed trades." },
  {
    key: "expectancy_r",
    label: "Expectancy",
    format: "r",
    signed: true,
    hint: "Average R per trade — what one trade is worth in units of risk.",
  },
  { key: "total_r", label: "Total R", format: "r", signed: true, hint: "Sum of all R multiples." },
  {
    key: "total_eur",
    label: "Total P&L",
    format: "eur",
    signed: true,
    hint: "Sum of all trade results in euro, fees included.",
  },
  { key: "avg_win_r", label: "Avg win", format: "r", hint: "Mean R of the winning trades." },
  { key: "avg_loss_r", label: "Avg loss", format: "r", hint: "Mean R of the losing trades." },
  {
    key: "payoff",
    label: "Payoff",
    format: "ratio",
    hint: "Average win divided by average loss. Above 1 means winners are bigger.",
  },
  {
    key: "profit_factor",
    label: "Profit factor",
    format: "ratio",
    hint: "Gross profit divided by gross loss. Above 1 is a profitable system.",
  },
  {
    key: "sqn",
    label: "SQN",
    format: "ratio",
    hint: "System Quality Number: expectancy / standard deviation × √trades.",
  },
  {
    key: "std_r",
    label: "Std dev R",
    format: "ratio",
    hint: "Standard deviation of the R multiples — how spread out results are.",
  },
  {
    key: "max_dd_r",
    label: "Max DD (R)",
    format: "r",
    hint: "Largest fall of the R equity curve below its running peak.",
  },
  {
    key: "max_dd_eur",
    label: "Max DD (€)",
    format: "eur",
    hint: "Largest fall of the euro equity curve below its running peak.",
  },
  {
    key: "recovery_factor",
    label: "Recovery factor",
    format: "ratio",
    hint: "Total R divided by the maximum drawdown in R.",
  },
  {
    key: "max_win_streak",
    label: "Win streak",
    format: "count",
    hint: "Longest run of consecutive winning trades.",
  },
  {
    key: "max_loss_streak",
    label: "Loss streak",
    format: "count",
    hint: "Longest run of consecutive losing trades.",
  },
  {
    key: "avg_hold_hours_win",
    label: "Hold win",
    format: "hours",
    hint: "Average hours a winning trade stayed open.",
  },
  {
    key: "avg_hold_hours_loss",
    label: "Hold loss",
    format: "hours",
    hint: "Average hours a losing trade stayed open.",
  },
  {
    key: "adherence",
    label: "Adherence",
    format: "pct",
    hint: "Share of reviewed trades that followed the playbook.",
  },
  {
    key: "cost_of_mistakes_r",
    label: "Mistakes (R)",
    format: "r",
    hint: "R given up on trades marked as a mistake.",
  },
  {
    key: "cost_of_mistakes_eur",
    label: "Mistakes (€)",
    format: "eur",
    hint: "Euro given up on trades marked as a mistake.",
  },
  {
    key: "avg_mae_r",
    label: "Avg MAE",
    format: "r",
    hint: "Maximum adverse excursion: average worst drawdown inside a trade, in R.",
  },
  {
    key: "avg_mfe_r",
    label: "Avg MFE",
    format: "r",
    hint: "Maximum favourable excursion: average best unrealised gain inside a trade, in R.",
  },
  {
    key: "capture",
    label: "Capture",
    format: "pct",
    hint: "Share of the favourable excursion actually taken home.",
  },
];

function render(value: Stats[keyof Stats], format: Format): string {
  const scalar = value as string | number | null;
  switch (format) {
    case "count":
      return num(scalar, 0);
    case "r":
      return r(scalar);
    case "eur":
      return eur(scalar);
    case "pct":
      return pct(scalar);
    case "hours":
      return scalar === null ? "—" : `${num(scalar, 1)} h`;
    default:
      return num(scalar, 2);
  }
}

export function StatsGrid({ stats }: { stats: Stats }) {
  const mistakes = Object.entries(stats.mistakes).sort((a, b) => b[1] - a[1]);
  return (
    <>
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
        {FIELDS.map((field) => (
          <div key={field.key} title={field.hint}>
            <p className="label">{field.label}</p>
            <p className={`text-sm ${field.signed ? signClass(stats[field.key] as string) : ""}`}>
              {render(stats[field.key], field.format)}
            </p>
          </div>
        ))}
      </div>
      <div className="mt-4 border-t border-line pt-3" title="How often each mistake was tagged.">
        <p className="label">Mistakes</p>
        {mistakes.length === 0 ? (
          <p className="text-muted">none recorded</p>
        ) : (
          <p className="text-sm">
            {mistakes.map(([name, count]) => `${name} × ${count}`).join(" · ")}
          </p>
        )}
      </div>
    </>
  );
}
