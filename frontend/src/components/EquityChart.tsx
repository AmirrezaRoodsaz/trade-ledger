import { useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EquityPoint } from "../api/analytics";
import { tooltipStyle, useChartColors } from "../chartTheme";
import { EmptyState } from "./EmptyState";
import { date as fmtDate, eur, num, r as fmtR, toNumber } from "../fmt";

type Unit = "r" | "eur";

/** Cumulative result per closed trade, with the distance below the running
 * peak drawn as an area hanging under the zero line.
 */
export function EquityChart({ points }: { points: EquityPoint[] }) {
  const [unit, setUnit] = useState<Unit>("r");
  const colors = useChartColors();
  const format = (value: number) => (unit === "r" ? fmtR(value) : eur(value));

  if (points.length === 0) return <EmptyState>No closed trades in this selection.</EmptyState>;

  const rows = points.map((point) => ({
    date: point.date,
    cum: toNumber(unit === "r" ? point.cum_r : point.cum_eur) ?? 0,
    // The API reports drawdown as a positive distance; negate it so the area
    // reads as "below the peak" on the same axis as the curve.
    dd: -(toNumber(unit === "r" ? point.dd_r : point.dd_eur) ?? 0),
  }));

  return (
    <>
      <div className="mb-2 flex gap-2">
        {(["r", "eur"] as Unit[]).map((option) => (
          <button
            key={option}
            type="button"
            className={option === unit ? "btn-accent" : "btn"}
            onClick={() => setUnit(option)}
          >
            {option === "r" ? "R" : "€"}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid stroke={colors["--border"]} vertical={false} />
          <XAxis
            dataKey="date"
            stroke={colors["--muted"]}
            tick={{ fontSize: 11 }}
            tickFormatter={(value) => fmtDate(String(value))}
            minTickGap={24}
          />
          <YAxis
            stroke={colors["--muted"]}
            tick={{ fontSize: 11 }}
            width={64}
            tickFormatter={(value) => num(Number(value), unit === "r" ? 1 : 0)}
          />
          <Tooltip
            {...tooltipStyle(colors)}
            labelFormatter={(label) => fmtDate(String(label))}
            formatter={(value, name) => [
              format(name === "Drawdown" ? -Number(value) : Number(value)),
              String(name),
            ]}
          />
          <Area
            type="stepAfter"
            dataKey="dd"
            name="Drawdown"
            stroke="none"
            fill={colors["--neg"]}
            fillOpacity={0.18}
          />
          <Line
            type="stepAfter"
            dataKey="cum"
            name="Equity"
            stroke={colors["--accent"]}
            strokeWidth={1.5}
            dot={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </>
  );
}
