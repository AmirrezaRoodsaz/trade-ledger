import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { Allocation } from "../api/portfolio";
import { sliceColor, tooltipStyle, useChartColors } from "../chartTheme";
import { EmptyState } from "./EmptyState";
import { eur, pct, toNumber } from "../fmt";

/** Current value split by asset class, venue or instrument. Slice colours are
 * categorical on purpose — no green, no red, those mean a sign here.
 */
export function AllocationPie({ slices }: { slices: Allocation[] }) {
  const colors = useChartColors();
  if (slices.length === 0) return <EmptyState>Nothing to allocate yet.</EmptyState>;

  const rows = slices.map((slice) => ({
    key: slice.key,
    value: toNumber(slice.value_eur) ?? 0,
    weight: slice.weight,
  }));

  return (
    <>
      <ResponsiveContainer width="100%" height={180}>
        <PieChart>
          <Pie
            data={rows}
            dataKey="value"
            nameKey="key"
            innerRadius={40}
            outerRadius={70}
            isAnimationActive={false}
            stroke={colors["--surface"]}
          >
            {rows.map((row, index) => (
              <Cell key={row.key} fill={sliceColor(index)} />
            ))}
          </Pie>
          <Tooltip {...tooltipStyle(colors)} formatter={(value) => eur(Number(value))} />
        </PieChart>
      </ResponsiveContainer>
      <ul className="mt-2">
        {rows.map((row, index) => (
          <li key={row.key} className="flex items-center justify-between gap-2 py-0.5">
            <span className="flex items-center gap-2">
              <span
                className="inline-block h-2 w-2 rounded-sm"
                style={{ background: sliceColor(index) }}
              />
              {row.key}
            </span>
            <span className="text-muted">
              {eur(row.value)} · {pct(row.weight)}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}
