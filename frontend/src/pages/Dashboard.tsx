import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { get, soft, useApi } from "../api/client";
import type { Account, Page, Stats, SyncRun, Trade, ValueSeries, YearSummary } from "../api/types";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import { PageHeader } from "../components/Layout";
import { ModeBadge } from "../components/ModeBadge";
import { MoneyCell } from "../components/MoneyCell";
import { DASH, date, dateTime, eur, isoDate, num, pct, r, toNumber } from "../fmt";

const VALUE_WINDOW_DAYS = 30;

interface DashboardData {
  accounts: Account[];
  lastSync: Record<number, SyncRun | null>;
  paper: Stats | null;
  live: Stats | null;
  openTrades: number;
  tax: YearSummary | null;
  value: ValueSeries | null;
}

async function load(year: number): Promise<DashboardData> {
  const accounts = await get<Account[]>("/accounts");
  const to = new Date();
  const from = new Date(to.getTime() - VALUE_WINDOW_DAYS * 86_400_000);
  const [lastSync, paper, live, openPage, tax, value] = await Promise.all([
    Promise.all(
      accounts.map((account) =>
        get<SyncRun[]>(`/accounts/${account.id}/sync-runs`).then(
          (runs) => [account.id, runs[0] ?? null] as const,
        ),
      ),
    ).then((pairs) => Object.fromEntries(pairs) as Record<number, SyncRun | null>),
    // `soft` swallows a failing card so one empty section never blanks the
    // whole dashboard — the card just shows "—".
    soft(get<Stats>("/analytics/stats?mode=paper")),
    soft(get<Stats>("/analytics/stats?mode=live")),
    get<Page<Trade>>("/trades?status=open&mode=all&page_size=1"),
    soft(get<YearSummary>(`/tax/${year}/summary?mode=live`)),
    soft(
      get<ValueSeries>(
        `/portfolio/value-series?mode=live&from=${isoDate(from)}&to=${isoDate(to)}`,
      ),
    ),
  ]);
  return { accounts, lastSync, paper, live, openTrades: openPage.total, tax, value };
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="label">{label}</p>
      <p className="text-sm">{value}</p>
    </div>
  );
}

function StatsCard({ title, mode, stats }: { title: string; mode: "paper" | "live"; stats: Stats | null }) {
  return (
    <Card title={title} right={<ModeBadge mode={mode} />}>
      {stats === null ? (
        <p className="text-muted">No statistics yet.</p>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          <Stat label="Trades" value={num(stats.count, 0)} />
          <Stat label="Expectancy" value={r(stats.expectancy_r)} />
          <Stat label="Win rate" value={pct(stats.win_rate)} />
          <Stat label="Profit factor" value={num(stats.profit_factor)} />
          <Stat label="Max drawdown" value={r(stats.max_dd_r)} />
          <Stat label="Adherence" value={pct(stats.adherence)} />
        </div>
      )}
    </Card>
  );
}

/** Thin bar: how much of a yearly allowance is used up. */
function Meter({ label, value, limit }: { label: string; value: string | null; limit: string | null }) {
  const used = toNumber(value) ?? 0;
  const cap = toNumber(limit);
  const share = cap !== null && cap > 0 ? Math.min(1, Math.max(0, used / cap)) : 0;
  const over = cap !== null && cap > 0 && used >= cap;
  return (
    <div className="mb-3 last:mb-0">
      <div className="flex justify-between">
        <span className="label">{label}</span>
        <span>
          {eur(value)} / {eur(limit)}
        </span>
      </div>
      <div
        className="mt-1 h-1 w-full rounded bg-surface2"
        role="progressbar"
        aria-label={label}
        aria-valuenow={Number((share * 100).toFixed(1))}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`h-1 rounded ${over ? "bg-neg" : "bg-accent"}`}
          style={{ width: `${(share * 100).toFixed(1)}%` }}
        />
      </div>
    </div>
  );
}

export function Dashboard() {
  const year = new Date().getFullYear();
  const state = useApi(() => load(year), [year]);

  if (state.error !== null) return <p className="text-neg">{state.error}</p>;
  if (state.data === null) return <p className="text-muted">{state.loading ? "Loading…" : DASH}</p>;

  const { accounts, lastSync, paper, live, openTrades, tax, value } = state.data;
  const lastPoint = value?.points.at(-1) ?? null;
  const p20Used =
    tax === null ? null : String((toNumber(tax.p20.dividends) ?? 0) + (toNumber(tax.p20.interest) ?? 0));

  return (
    <>
      <PageHeader title="Dashboard" subtitle={`Steuerjahr ${year}`} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <StatsCard title="Paper" mode="paper" stats={paper} />
        <StatsCard title="Live" mode="live" stats={live} />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="Open trades">
          <p className="text-2xl">{num(openTrades, 0)}</p>
          <p className="text-muted">across all modes</p>
        </Card>

        <Card title="Portfolio value" right={<ModeBadge mode="live" />}>
          <p className="text-2xl">
            <MoneyCell value={lastPoint?.value_eur ?? null} />
          </p>
          <p className="text-muted">
            {lastPoint === null
              ? `no valuation in the last ${VALUE_WINDOW_DAYS} days`
              : `as of ${date(lastPoint.date)}`}
          </p>
        </Card>

        <Card title={`Steuer ${year}`}>
          {tax === null ? (
            <p className="text-muted">No tax summary yet.</p>
          ) : (
            <>
              <Meter label="§ 23 Gewinn / Freigrenze" value={tax.p23.net} limit={tax.p23.freigrenze} />
              <Meter
                label="§ 20 Erträge / Sparerpauschbetrag"
                value={p20Used}
                limit={tax.p20.sparerpauschbetrag}
              />
              <Meter label="§ 22 Einnahmen / Freigrenze" value={tax.p22.income} limit={tax.p22.freigrenze} />
              <p className="mt-3 text-[11px] text-muted">
                {tax.disclaimer} · {tax.form_status}
              </p>
            </>
          )}
        </Card>
      </div>

      <Card title="Accounts" className="mt-4">
        <DataTable
          rows={accounts}
          rowKey={(account) => account.id}
          empty="No accounts yet — add one in Settings."
          columns={[
            {
              key: "name",
              header: "Account",
              render: (account) => (
                <Link className="text-accent hover:underline" to={`/accounts/${account.id}`}>
                  {account.name}
                </Link>
              ),
            },
            { key: "venue", header: "Venue", render: (account) => account.venue },
            { key: "mode", header: "Mode", render: (account) => <ModeBadge mode={account.mode} /> },
            {
              key: "last_sync",
              header: "Last sync",
              render: (account) => {
                const run = lastSync[account.id] ?? null;
                return run === null ? DASH : dateTime(run.started);
              },
            },
            {
              key: "result",
              header: "Result",
              align: "right",
              render: (account) => {
                const run = lastSync[account.id] ?? null;
                if (run === null) return DASH;
                if (run.status === "error") return <span className="text-neg">{run.error ?? "error"}</span>;
                return `${run.status} · +${num(run.added, 0)} / ${num(run.skipped, 0)} skipped`;
              },
            },
          ]}
        />
      </Card>
    </>
  );
}
