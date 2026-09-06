import { useState } from "react";
import { useParams } from "react-router-dom";
import { get, post, useAction, useApi } from "../api/client";
import type { AccountSummary } from "../api/transactions";
import type {
  Account as AccountRow,
  Holding,
  Instrument,
  Page,
  SyncRun,
  Trade,
} from "../api/types";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/Layout";
import { ModeBadge } from "../components/ModeBadge";
import { MoneyCell } from "../components/MoneyCell";
import { TradeCard } from "../components/TradeCard";
import { TransactionsTable } from "../components/TransactionsTable";
import { DASH, dateTime, num, pct } from "../fmt";

const TABS = ["Overview", "Transactions", "Trades", "Holdings"] as const;
type Tab = (typeof TABS)[number];

/** Cash, open positions, and the sync history of one venue account. */
function Overview({ accountId }: { accountId: number }) {
  const summary = useApi(
    () => get<AccountSummary>(`/accounts/${accountId}/summary`),
    [accountId],
  );
  const runs = useApi(() => get<SyncRun[]>(`/accounts/${accountId}/sync-runs`), [accountId]);
  const [latest, setLatest] = useState<SyncRun | null>(null);
  const sync = useAction();

  return (
    <div className="grid gap-4">
      <Card
        title="Position"
        right={
          <button
            className="btn"
            disabled={sync.busy}
            onClick={() =>
              void sync.run(async () => {
                setLatest(await post<SyncRun>(`/accounts/${accountId}/sync`));
                summary.reload();
                runs.reload();
              })
            }
          >
            {sync.busy ? "Syncing…" : "Sync now"}
          </button>
        }
      >
        {summary.error !== null && <p className="mb-2 text-neg">{summary.error}</p>}
        {sync.error !== null && <p className="mb-2 text-neg">{sync.error}</p>}
        {latest !== null && (
          <p className={`mb-3 ${latest.status === "error" ? "text-neg" : "text-muted"}`}>
            {latest.status === "error"
              ? (latest.error ?? "sync failed")
              : `${latest.status} · ${num(latest.added, 0)} added, ${num(
                  latest.skipped,
                  0,
                )} skipped`}
          </p>
        )}
        <div className="grid grid-cols-3 gap-4">
          <div>
            <p className="label">Cash</p>
            <p className="text-sm">
              <MoneyCell value={summary.data?.cash_eur ?? null} />
            </p>
          </div>
          <div>
            <p className="label">Transactions</p>
            <p className="text-sm">{num(summary.data?.tx_count ?? null, 0)}</p>
          </div>
          <div>
            <p className="label">Last sync</p>
            <p className="text-sm">{dateTime(summary.data?.last_sync ?? null)}</p>
          </div>
        </div>
      </Card>

      <Card title="Open positions">
        <DataTable
          rows={summary.data?.positions ?? []}
          rowKey={(position) => position.instrument}
          empty={summary.loading ? "Loading…" : "No open positions."}
          columns={[
            { key: "instrument", header: "Instrument", render: (row) => row.instrument },
            {
              key: "quantity",
              header: "Quantity",
              align: "right",
              render: (row) => num(row.quantity, 8),
            },
          ]}
        />
      </Card>

      <Card title="Sync runs">
        {runs.error !== null && <p className="mb-2 text-neg">{runs.error}</p>}
        <DataTable
          rows={runs.data ?? []}
          rowKey={(run) => run.id}
          empty={runs.loading ? "Loading…" : "Never synced."}
          columns={[
            { key: "started", header: "Started", render: (run) => dateTime(run.started) },
            { key: "finished", header: "Finished", render: (run) => dateTime(run.finished) },
            {
              key: "status",
              header: "Status",
              render: (run) => (
                <span className={run.status === "error" ? "text-neg" : undefined}>
                  {run.status}
                </span>
              ),
            },
            { key: "added", header: "Added", align: "right", render: (run) => num(run.added, 0) },
            {
              key: "skipped",
              header: "Skipped",
              align: "right",
              render: (run) => num(run.skipped, 0),
            },
            { key: "error", header: "Error", render: (run) => run.error ?? DASH },
          ]}
        />
      </Card>
    </div>
  );
}

/** Every trade booked on this account, whatever its mode. */
function Trades({ accountId, instruments }: { accountId: number; instruments: Instrument[] }) {
  const trades = useApi(
    () => get<Page<Trade>>(`/trades?account_id=${accountId}&mode=all&page_size=200`),
    [accountId],
  );
  const symbols = new Map(instruments.map((one) => [one.id, one.symbol]));
  const rows = trades.data?.items ?? [];

  return (
    <Card title={`Trades (${num(trades.data?.total ?? 0, 0)})`}>
      {trades.error !== null && <p className="mb-2 text-neg">{trades.error}</p>}
      {rows.length === 0 ? (
        <EmptyState>{trades.loading ? "Loading…" : "No trades on this account."}</EmptyState>
      ) : (
        <div className="grid gap-2">
          {rows.map((trade) => (
            <TradeCard
              key={trade.id}
              trade={trade}
              symbol={symbols.get(trade.instrument_id) ?? `#${trade.instrument_id}`}
              accountName=""
              onChanged={trades.reload}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

function Holdings({ accountId }: { accountId: number }) {
  const holdings = useApi(
    () => get<Holding[]>(`/portfolio/holdings?account_id=${accountId}&mode=all`),
    [accountId],
  );

  return (
    <Card title="Holdings">
      {holdings.error !== null && <p className="mb-2 text-neg">{holdings.error}</p>}
      <DataTable
        rows={holdings.data ?? []}
        rowKey={(holding) => holding.instrument.id}
        empty={holdings.loading ? "Loading…" : "Nothing held."}
        columns={[
          {
            key: "symbol",
            header: "Instrument",
            render: (holding) => holding.instrument.symbol,
          },
          {
            key: "class",
            header: "Class",
            render: (holding) => holding.instrument.asset_class,
          },
          {
            key: "quantity",
            header: "Quantity",
            align: "right",
            render: (holding) => num(holding.quantity, 8),
          },
          {
            key: "avg_cost",
            header: "Avg cost",
            align: "right",
            render: (holding) => <MoneyCell value={holding.avg_cost_eur} />,
          },
          {
            key: "cost",
            header: "Cost",
            align: "right",
            render: (holding) => <MoneyCell value={holding.cost_eur} />,
          },
          {
            key: "price",
            header: "Price",
            align: "right",
            render: (holding) => <MoneyCell value={holding.price_eur} />,
          },
          {
            key: "value",
            header: "Value",
            align: "right",
            render: (holding) => <MoneyCell value={holding.value_eur} />,
          },
          {
            key: "unrealised",
            header: "Unrealised",
            align: "right",
            render: (holding) => <MoneyCell value={holding.unrealised_eur} sign />,
          },
          {
            key: "weight",
            header: "Weight",
            align: "right",
            render: (holding) => pct(holding.weight),
          },
        ]}
      />
    </Card>
  );
}

export function Account() {
  const { id } = useParams();
  const accountId = Number(id);
  const [tab, setTab] = useState<Tab>("Overview");
  const account = useApi(() => get<AccountRow>(`/accounts/${accountId}`), [accountId]);
  const instruments = useApi(() => get<Instrument[]>("/instruments"));

  if (!Number.isInteger(accountId)) return <p className="text-neg">Unknown account.</p>;

  return (
    <>
      <PageHeader
        title={account.data?.name ?? "Account"}
        subtitle={
          account.data === null ? (
            account.error
          ) : (
            <span className="inline-flex items-center gap-2">
              {account.data.venue} · {account.data.kind} · Steuer-Wallet{" "}
              {account.data.tax_wallet} <ModeBadge mode={account.data.mode} />
            </span>
          )
        }
      />

      <div className="mb-4 flex gap-2">
        {TABS.map((name) => (
          <button
            key={name}
            className={`btn ${tab === name ? "border-accent text-accent" : ""}`}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </div>

      {instruments.error !== null && <p className="mb-2 text-neg">{instruments.error}</p>}

      {tab === "Overview" && <Overview accountId={accountId} />}
      {tab === "Transactions" && (
        <Card title="Transactions">
          <TransactionsTable accountId={accountId} instruments={instruments.data ?? []} />
        </Card>
      )}
      {tab === "Trades" && <Trades accountId={accountId} instruments={instruments.data ?? []} />}
      {tab === "Holdings" && <Holdings accountId={accountId} />}
    </>
  );
}
