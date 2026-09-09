import { useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Bot } from "../api/bots";
import { getBotStrategy } from "../api/bots";
import {
  deleteBacktest,
  listBacktests,
  uploadBacktest,
  type Backtest,
} from "../api/backtests";
import { get, useAction, useApi } from "../api/client";
import { cumRCurve } from "../api/trades";
import type { Page, Trade } from "../api/types";
import { tooltipStyle, useChartColors } from "../chartTheme";
import { DASH, date as fmtDate, num, r as fmtR, toNumber } from "../fmt";
import { Card } from "./Card";
import { DataTable } from "./DataTable";
import { DrillChecklist } from "./DrillChecklist";
import { EmptyState } from "./EmptyState";
import { ReadinessBar } from "./ReadinessBar";
import { StageGatePanel } from "./StageGatePanel";
import { StageResults } from "./StageResults";

interface OverlayRow {
  date: string;
  backtest?: number;
  journal?: number;
}

/** Both curves on one date axis. Gaps stay gaps — the lines are drawn with
 * `connectNulls`, so neither series has to be resampled onto the other's
 * dates.
 */
function overlayRows(
  equity: (string | number)[][] | null | undefined,
  journal: { date: string; cum_r: number }[],
): OverlayRow[] {
  const rows = new Map<string, OverlayRow>();
  const at = (day: string) => rows.get(day) ?? { date: day };
  for (const point of equity ?? []) {
    const day = String(point[0] ?? "").slice(0, 10);
    const value = toNumber(point[1] as string | number);
    if (day === "" || value === null) continue;
    rows.set(day, { ...at(day), backtest: value });
  }
  for (const point of journal) {
    rows.set(point.date, { ...at(point.date), journal: point.cum_r });
  }
  return [...rows.values()].sort((a, b) => a.date.localeCompare(b.date));
}

function EquityOverlay({ rows }: { rows: OverlayRow[] }) {
  const colors = useChartColors();
  if (rows.length === 0) {
    return (
      <EmptyState>
        Nothing to draw yet — the passed backtest carries no equity curve and the bot has no closed
        trades.
      </EmptyState>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
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
          tickFormatter={(value) => num(Number(value), 1)}
        />
        <Tooltip
          {...tooltipStyle(colors)}
          labelFormatter={(label) => fmtDate(String(label))}
          formatter={(value, name) => [fmtR(Number(value)), String(name)]}
        />
        <Legend wrapperStyle={{ fontSize: 11, color: colors["--muted"] }} />
        <Line
          type="stepAfter"
          dataKey="backtest"
          name="Backtest"
          stroke={colors["--muted"]}
          strokeWidth={1.5}
          strokeDasharray="4 3"
          dot={false}
          connectNulls
        />
        <Line
          type="stepAfter"
          dataKey="journal"
          name="Journal"
          stroke={colors["--accent"]}
          strokeWidth={1.5}
          dot={false}
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

function Verdict({ result }: { result: Backtest }) {
  if (result.passed) return <span className="text-pos">passed</span>;
  return (
    <span className="text-neg">
      failed
      {result.fail_reasons.length > 0 && (
        <span className="block text-[11px] text-muted">{result.fail_reasons.join(" · ")}</span>
      )}
    </span>
  );
}

/** File input and a drop target, both ending in the same upload. The JSON
 * carries every field itself — the endpoint takes nothing else.
 */
function UploadBox({ bot, onUploaded }: { bot: Bot; onUploaded: () => void }) {
  const [over, setOver] = useState(false);
  const [last, setLast] = useState<Backtest | null>(null);
  const action = useAction();

  const submit = (file: File | null | undefined) => {
    if (!file) return;
    void action.run(async () => {
      setLast(null);
      setLast(await uploadBacktest(file));
      onUploaded();
    });
  };

  return (
    <>
      <div
        className={`rounded border border-dashed p-4 text-center ${
          over ? "border-accent text-accent" : "border-line text-muted"
        }`}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          submit(event.dataTransfer.files?.[0]);
        }}
      >
        <p>{action.busy ? "Uploading…" : "Drop a backtest JSON here"}</p>
        <input
          className="field mt-2"
          aria-label="Backtest JSON file"
          type="file"
          accept="application/json,.json"
          disabled={action.busy}
          onChange={(event) => {
            submit(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
        <p className="mt-2 text-[11px]">
          The file must carry <code>"strategy": "{bot.strategy}"</code> — the app judges the numbers
          against the criteria in Settings and stores the verdict with the row.
        </p>
      </div>
      {action.error !== null && <p className="mt-2 text-neg">{action.error}</p>}
      {last !== null && (
        <p className="mt-2">
          {last.label || `#${last.id}`}: <Verdict result={last} />
        </p>
      )}
    </>
  );
}

/** The strategy tab of a bot: how far it is, what each stage produced, and
 * the backtests behind it.
 */
export function StrategyTab({ bot }: { bot: Bot }) {
  const strategy = useApi(() => getBotStrategy(bot.slug), [bot.slug]);
  const backtests = useApi(() => listBacktests({ strategy: bot.strategy }), [bot.strategy]);
  // ponytail: `/analytics/equity` has no bot filter, so the journal curve is
  // summed from the bot's own closed trades instead.
  const journal = useApi(
    async () =>
      cumRCurve(
        (
          await get<Page<Trade>>(
            `/trades?bot_id=${bot.id}&status=closed&mode=all&page_size=500`,
          )
        ).items,
      ),
    [bot.id],
  );
  const remove = useAction();

  if (strategy.data === null) {
    return (
      <EmptyState>{strategy.loading ? "Loading…" : (strategy.error ?? "No strategy view.")}</EmptyState>
    );
  }

  const { results, readiness, drills } = strategy.data;
  // The bot has one account, so one journal column is empty: whichever has
  // trades is the stage it is in, incubation until the first live one.
  const stage = results.live.count > 0 ? "live" : "incubation";
  const current = stage === "live" ? results.live : results.incubation;

  const reloadAll = () => {
    strategy.reload();
    backtests.reload();
    journal.reload();
  };

  return (
    <div className="grid gap-4">
      <Card title="Readiness">
        <ReadinessBar readiness={readiness} />
      </Card>

      <Card title="Results per stage">
        <StageResults
          backtest={results.backtest}
          incubation={results.incubation}
          live={results.live}
        />
      </Card>

      <Card title="Equity — backtest against journal" right={<span className="text-muted">R</span>}>
        {journal.error !== null && <p className="mb-2 text-neg">{journal.error}</p>}
        <EquityOverlay rows={overlayRows(results.backtest?.equity_r, journal.data ?? [])} />
      </Card>

      <Card
        title={`Stage gate — ${stage === "live" ? "real money" : "incubation"}`}
        right={<span className="text-muted">{num(current.count, 0)} closed trades</span>}
      >
        <StageGatePanel gate={current.gate} />
      </Card>

      <Card title="Drills">
        <DrillChecklist slug={bot.slug} drills={drills} onChanged={strategy.reload} />
      </Card>

      <Card title="Backtest results" right={<span className="text-muted">{bot.strategy}</span>}>
        <UploadBox bot={bot} onUploaded={reloadAll} />
        {remove.error !== null && <p className="mt-2 text-neg">{remove.error}</p>}
        {backtests.error !== null && <p className="mt-2 text-neg">{backtests.error}</p>}
        <div className="mt-4">
          <DataTable
            rows={backtests.data ?? []}
            rowKey={(row) => row.id}
            empty={backtests.loading ? "Loading…" : "No result uploaded for this strategy yet."}
            columns={[
              {
                key: "label",
                header: "Result",
                render: (row) => (
                  <>
                    {row.label || `#${row.id}`}
                    <span className="block text-[11px] text-muted">
                      {fmtDate(row.created)} · {row.timeframe} ·{" "}
                      {row.pairs.length === 0 ? DASH : row.pairs.join(", ")}
                      {row.bot_id === bot.id && " · this bot"}
                    </span>
                  </>
                ),
              },
              {
                key: "period",
                header: "Period",
                render: (row) => `${fmtDate(row.period_start)} – ${fmtDate(row.period_end)}`,
              },
              { key: "trades", header: "Trades", align: "right", render: (row) => num(row.trades, 0) },
              {
                key: "expectancy",
                header: "Expectancy",
                align: "right",
                render: (row) => fmtR(row.expectancy_r),
              },
              {
                key: "pf",
                header: "PF",
                align: "right",
                render: (row) => num(row.profit_factor, 2),
              },
              {
                key: "dd",
                header: "Max DD",
                align: "right",
                render: (row) => `${num(row.max_drawdown_pct, 1)} %`,
              },
              { key: "verdict", header: "Verdict", render: (row) => <Verdict result={row} /> },
              {
                key: "actions",
                header: "",
                align: "right",
                render: (row) => (
                  <button
                    type="button"
                    className="btn"
                    disabled={remove.busy}
                    onClick={() => {
                      if (!window.confirm(`Delete backtest "${row.label || row.id}"?`)) return;
                      void remove.run(async () => {
                        await deleteBacktest(row.id);
                        reloadAll();
                      });
                    }}
                  >
                    Delete
                  </button>
                ),
              },
            ]}
          />
        </div>
      </Card>
    </div>
  );
}
