import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { HistogramBin } from "../api/analytics";
import { tooltipStyle, useChartColors } from "../chartTheme";
import { EmptyState } from "./EmptyState";
import { num, r as fmtR, toNumber } from "../fmt";

export const BIN_SIZES = ["0.25", "0.5", "1"];

/** How the R multiples are distributed. Losing bins are red, winning bins
 * green — the sign of a P&L figure is the only place colour carries meaning.
 */
export function RHistogram({
  bins,
  binSize,
  onBinSize,
}: {
  bins: HistogramBin[];
  binSize: string;
  onBinSize: (next: string) => void;
}) {
  const colors = useChartColors();
  const rows = bins.map((bin) => ({
    label: bin.lo,
    lo: toNumber(bin.lo) ?? 0,
    hi: toNumber(bin.hi) ?? 0,
    count: bin.count,
  }));

  return (
    <>
      <div className="mb-2 flex items-center gap-2">
        <span className="label">Bin size</span>
        {BIN_SIZES.map((size) => (
          <button
            key={size}
            type="button"
            className={size === binSize ? "btn-accent" : "btn"}
            onClick={() => onBinSize(size)}
          >
            {num(size, 2)} R
          </button>
        ))}
      </div>
      {rows.length === 0 ? (
        <EmptyState>No closed trades in this selection.</EmptyState>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
            <CartesianGrid stroke={colors["--border"]} vertical={false} />
            <XAxis
              dataKey="label"
              stroke={colors["--muted"]}
              tick={{ fontSize: 11 }}
              tickFormatter={(value) => num(String(value), 2)}
            />
            <YAxis
              stroke={colors["--muted"]}
              tick={{ fontSize: 11 }}
              width={40}
              allowDecimals={false}
            />
            <Tooltip
              {...tooltipStyle(colors)}
              labelFormatter={(label, payload) => {
                const row = payload?.[0]?.payload as { lo: number; hi: number } | undefined;
                return row === undefined
                  ? String(label)
                  : `${fmtR(row.lo)} … ${fmtR(row.hi)}`;
              }}
              formatter={(value) => [num(Number(value), 0), "Trades"]}
            />
            <Bar dataKey="count" name="Trades" isAnimationActive={false}>
              {rows.map((row) => (
                <Cell
                  key={row.label}
                  fill={row.lo < 0 ? colors["--neg"] : colors["--pos"]}
                  fillOpacity={0.8}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </>
  );
}
