import type { ReactElement } from "react";
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { get, useApi } from "../api/client";
import type { BreakdownKey, Filters } from "../api/analytics";
import {
  BREAKDOWN_KEYS,
  EMPTY_FILTERS,
  fetchBreakdown,
  fetchEquity,
  fetchHistogram,
  fetchStageGate,
  fetchStats,
  filterKey,
  filterQuery,
} from "../api/analytics";
import type { Page, Trade } from "../api/types";
import { BreakdownTable } from "../components/BreakdownTable";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { EquityChart } from "../components/EquityChart";
import { FilterBar } from "../components/FilterBar";
import { PageHeader } from "../components/Layout";
import { RHistogram } from "../components/RHistogram";
import { StageGatePanel } from "../components/StageGatePanel";
import { StatsGrid } from "../components/StatsGrid";
import { tooltipStyle, useChartColors } from "../chartTheme";
import { num, r as fmtR, toNumber } from "../fmt";

/** One panel's async state rendered the same way everywhere. */
function Panel<T>({
  state,
  children,
}: {
  state: { data: T | null; error: string | null; loading: boolean };
  children: (data: T) => ReactElement;
}) {
  if (state.error !== null) return <p className="text-neg">{state.error}</p>;
  if (state.data === null) return <p className="text-muted">{state.loading ? "Loading…" : "—"}</p>;
  return children(state.data);
}

/** How far a trade went against the plan before it went for it. Points above
 * the diagonal gave back more than they kept.
 */
function ExcursionScatter({ filters }: { filters: Filters }) {
  const key = filterKey(filters);
  const colors = useChartColors();
  const state = useApi(
    () =>
      get<Page<Trade>>(
        `/trades${filterQuery(filters, { status: "closed", page_size: "500" })}`,
      ),
    [key],
  );

  const rows = useMemo(() => {
    const items = state.data?.items ?? [];
    return items
      // `TradeOut` carries the excursions in euro plus the planned risk; R is
      // that ratio, the same one the backend's `Trade.mae_r` property computes.
      .map((trade) => {
        const risk = toNumber(trade.risk_eur);
        const inR = (value: string | null) => {
          const euros = toNumber(value);
          return euros === null || risk === null || risk === 0 ? null : euros / risk;
        };
        return {
          mae: inR(trade.mae_eur),
          mfe: inR(trade.mfe_eur),
          result: toNumber(trade.r_multiple) ?? 0,
          id: trade.id,
        };
      })
      .filter((row): row is { mae: number; mfe: number; result: number; id: number } =>
        row.mae !== null && row.mfe !== null,
      );
  }, [state.data]);

  return (
    <Panel state={state}>
      {() =>
        rows.length === 0 ? (
          <EmptyState>
            No closed trade in this selection has both an excursion and a planned risk.
          </EmptyState>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <ScatterChart margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
              <CartesianGrid stroke={colors["--border"]} />
              <XAxis
                type="number"
                dataKey="mae"
                name="MAE"
                stroke={colors["--muted"]}
                tick={{ fontSize: 11 }}
                tickFormatter={(value) => num(Number(value), 1)}
              />
              <YAxis
                type="number"
                dataKey="mfe"
                name="MFE"
                stroke={colors["--muted"]}
                tick={{ fontSize: 11 }}
                width={48}
                tickFormatter={(value) => num(Number(value), 1)}
              />
              <ZAxis range={[36, 36]} />
              <ReferenceLine x={0} stroke={colors["--border"]} />
              <Tooltip
                {...tooltipStyle(colors)}
                cursor={{ strokeDasharray: "3 3" }}
                formatter={(value, name) => [fmtR(Number(value)), String(name)]}
              />
              <Scatter data={rows} isAnimationActive={false}>
                {rows.map((row) => (
                  <Cell
                    key={row.id}
                    fill={row.result >= 0 ? colors["--pos"] : colors["--neg"]}
                    fillOpacity={0.7}
                  />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        )
      }
    </Panel>
  );
}

export function Analytics() {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [binSize, setBinSize] = useState("0.5");
  const [by, setBy] = useState<BreakdownKey>("playbook");
  const key = filterKey(filters);

  const stats = useApi(() => fetchStats(filters), [key]);
  const equity = useApi(() => fetchEquity(filters), [key]);
  const histogram = useApi(() => fetchHistogram(filters, binSize), [key, binSize]);
  const breakdown = useApi(() => fetchBreakdown(filters, by), [key, by]);
  // The stage gate refuses `mode=all`; don't ask, explain instead.
  const gate = useApi(
    () => (filters.mode === "all" ? Promise.resolve(null) : fetchStageGate(filters)),
    [key],
  );

  return (
    <>
      <PageHeader title="Analytics" subtitle="Closed trades only. Every panel follows the filter." />
      <FilterBar value={filters} onChange={setFilters} />

      <Card title="Statistics">
        <Panel state={stats}>{(data) => <StatsGrid stats={data} />}</Panel>
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Equity curve and drawdown">
          <Panel state={equity}>{(data) => <EquityChart points={data} />}</Panel>
        </Card>

        <Card title="R distribution">
          <Panel state={histogram}>
            {(data) => <RHistogram bins={data} binSize={binSize} onBinSize={setBinSize} />}
          </Panel>
        </Card>

        <Card title="MAE vs MFE">
          <ExcursionScatter filters={filters} />
          <p className="mt-2 text-muted">
            X: worst excursion against the trade, Y: best excursion for it, both in R. Green dots
            closed positive.
          </p>
        </Card>

        <Card title="Stage gate">
          {filters.mode === "all" ? (
            <StageGatePanel gate={null} />
          ) : (
            <Panel state={gate}>{(data) => <StageGatePanel gate={data} />}</Panel>
          )}
        </Card>
      </div>

      <Card
        title="Breakdown"
        className="mt-4"
        right={
          <select
            className="field w-40"
            value={by}
            onChange={(event) => setBy(event.target.value as BreakdownKey)}
          >
            {BREAKDOWN_KEYS.map((option) => (
              <option key={option} value={option}>
                {option.replace("_", " ")}
              </option>
            ))}
          </select>
        }
      >
        <Panel state={breakdown}>{(data) => <BreakdownTable breakdown={data} />}</Panel>
      </Card>
    </>
  );
}
