import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ValueSeries } from "../api/types";
import { tooltipStyle, useChartColors } from "../chartTheme";
import { EmptyState } from "./EmptyState";
import { date as fmtDate, eur, num, toNumber } from "../fmt";

/** Portfolio value over the chosen range. Days the valuation could not price
 * are left out of the line and named underneath rather than drawn as zero.
 */
export function ValueChart({ series }: { series: ValueSeries }) {
  const colors = useChartColors();
  const rows = series.points.map((point) => ({
    date: point.date,
    value: toNumber(point.value_eur) ?? 0,
  }));

  if (rows.length === 0) {
    return <EmptyState>No valuation in this range — no prices for the holdings.</EmptyState>;
  }

  return (
    <>
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
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
            width={72}
            tickFormatter={(value) => num(Number(value), 0)}
          />
          <Tooltip
            {...tooltipStyle(colors)}
            labelFormatter={(label) => fmtDate(String(label))}
            formatter={(value) => [eur(Number(value)), "Value"]}
          />
          <Area
            type="monotone"
            dataKey="value"
            name="Value"
            stroke={colors["--accent"]}
            strokeWidth={1.5}
            fill={colors["--accent"]}
            fillOpacity={0.12}
          />
        </AreaChart>
      </ResponsiveContainer>
      {series.missing_dates.length > 0 && (
        <p className="mt-2 text-muted">
          {series.missing_dates.length} day(s) without a price and therefore not valued
          {series.missing_dates.length <= 5
            ? `: ${series.missing_dates.map(fmtDate).join(", ")}`
            : ` (first ${fmtDate(series.missing_dates[0])}, last ${fmtDate(
                series.missing_dates[series.missing_dates.length - 1],
              )})`}
          .
        </p>
      )}
    </>
  );
}
