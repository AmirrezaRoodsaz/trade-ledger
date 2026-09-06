import type { ReactElement, ReactNode } from "react";
import { useState } from "react";
import { get, useAction, useApi } from "../api/client";
import type { AllocationBy, PortfolioFilters, RefreshResult } from "../api/portfolio";
import {
  daysToTaxFree,
  fetchAllocation,
  fetchCashflows,
  fetchDividends,
  fetchFees,
  fetchHoldings,
  fetchReturns,
  fetchTaxLots,
  fetchValueSeries,
  portfolioQuery,
  refreshPrices,
} from "../api/portfolio";
import type { Account, Holding, ModeFilter } from "../api/types";
import { MODES } from "../api/types";
import { AllocationPie } from "../components/AllocationPie";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import { HoldingsTable } from "../components/HoldingsTable";
import { PageHeader } from "../components/Layout";
import { ValueChart } from "../components/ValueChart";
import { date as fmtDate, eur, isoDate, num, pct, signClass } from "../fmt";

const MODE_OPTIONS: ModeFilter[] = [...MODES, "all"];
const RANGE_DAYS = 90;
const ALLOCATIONS: { by: AllocationBy; title: string }[] = [
  { by: "asset_class", title: "By asset class" },
  { by: "venue", title: "By venue" },
  { by: "instrument", title: "By instrument" },
];

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

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div title={hint}>
      <p className="label">{label}</p>
      <p className="text-sm">{value}</p>
    </div>
  );
}

function KeyValueList({ entries }: { entries: [string, string][] }) {
  if (entries.length === 0) return <p className="text-muted">nothing</p>;
  return (
    <ul>
      {entries.map(([key, value]) => (
        <li key={key} className="flex justify-between gap-4 py-0.5">
          <span>{key}</span>
          <span>{eur(value)}</span>
        </li>
      ))}
    </ul>
  );
}

export function Portfolio() {
  const today = new Date();
  const [mode, setMode] = useState<ModeFilter>("live");
  const [accountIds, setAccountIds] = useState<number[]>([]);
  const [from, setFrom] = useState(isoDate(new Date(today.getTime() - RANGE_DAYS * 86_400_000)));
  const [to, setTo] = useState(isoDate(today));
  const [year, setYear] = useState(today.getFullYear());
  const [refreshed, setRefreshed] = useState<RefreshResult | null>(null);

  const filters: PortfolioFilters = { accountIds, mode };
  const key = portfolioQuery(filters);
  const refresh = useAction();

  const accounts = useApi(() => get<Account[]>("/accounts"), []);
  const visible = (accounts.data ?? []).filter(
    (account) =>
      (mode === "all" || account.mode === mode) &&
      (accountIds.length === 0 || accountIds.includes(account.id)),
  );

  const holdings = useApi(
    async (): Promise<{ combined: Holding[]; perAccount: [Account, Holding[]][] }> => {
      const combined = await fetchHoldings(filters);
      const perAccount =
        visible.length > 1
          ? await Promise.all(
              visible.map(
                async (account) =>
                  [account, await fetchHoldings({ accountIds: [account.id], mode })] as [
                    Account,
                    Holding[],
                  ],
              ),
            )
          : [];
      return { combined, perAccount };
    },
    [key, visible.map((account) => account.id).join(",")],
  );

  // Crypto § 23 clock, joined into the holdings table by instrument symbol.
  const lots = useApi(() => fetchTaxLots(year, mode), [year, mode]);
  const taxFree = daysToTaxFree(lots.data?.lots ?? [], today);

  const value = useApi(() => fetchValueSeries(filters, from, to), [key, from, to]);
  const cashflows = useApi(() => fetchCashflows(filters, from, to), [key, from, to]);
  const returns = useApi(() => fetchReturns(filters, from, to), [key, from, to]);
  const dividends = useApi(() => fetchDividends(filters, year), [key, year]);
  const fees = useApi(() => fetchFees(filters, year), [key, year]);
  const allocation = useApi(
    () => Promise.all(ALLOCATIONS.map((entry) => fetchAllocation(filters, entry.by))),
    [key],
  );

  const years = Array.from({ length: 6 }, (_, index) => today.getFullYear() - index);

  return (
    <>
      <PageHeader title="Portfolio" subtitle="Positions, valuation and income. Modes never mix." />

      <div className="mb-4 rounded border border-line bg-surface p-4">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
          <label className="block">
            <span className="label">Mode</span>
            <select
              className="field"
              value={mode}
              onChange={(event) => setMode(event.target.value as ModeFilter)}
            >
              {MODE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="label">Accounts</span>
            <select
              multiple
              size={4}
              className="field h-[76px]"
              value={accountIds.map(String)}
              onChange={(event) =>
                setAccountIds(
                  Array.from(event.target.selectedOptions, (option) => Number(option.value)),
                )
              }
            >
              {(accounts.data ?? []).map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name} ({account.mode})
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="label">From</span>
            <input
              type="date"
              className="field"
              value={from}
              onChange={(event) => setFrom(event.target.value)}
            />
          </label>

          <label className="block">
            <span className="label">To</span>
            <input
              type="date"
              className="field"
              value={to}
              onChange={(event) => setTo(event.target.value)}
            />
          </label>

          <label className="block">
            <span className="label">Year</span>
            <select
              className="field"
              value={year}
              onChange={(event) => setYear(Number(event.target.value))}
            >
              {years.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="mt-3 flex items-center gap-4">
          <button
            type="button"
            className="btn"
            disabled={refresh.busy}
            onClick={() =>
              refresh.run(async () => {
                setRefreshed(await refreshPrices(from, to));
                holdings.reload();
                value.reload();
                allocation.reload();
                returns.reload();
              })
            }
          >
            {refresh.busy ? "Refreshing…" : "Refresh prices"}
          </button>
          {refresh.error !== null && <span className="text-neg">{refresh.error}</span>}
          {refreshed !== null && refresh.error === null && (
            <span className="text-muted">
              {num(refreshed.prices_written, 0)} price(s) and {num(refreshed.fx_written, 0)} FX
              rate(s) written for {fmtDate(from)} – {fmtDate(to)}.
            </span>
          )}
        </div>
      </div>

      <Card title="Holdings — combined">
        <Panel state={holdings}>
          {(data) => <HoldingsTable holdings={data.combined} daysToTaxFree={taxFree} />}
        </Panel>
      </Card>

      {/* Only when more than one account is in scope — otherwise the combined
          table above already is that account. */}
      {(holdings.data?.perAccount ?? []).map(([account, rows]) => (
        <Card key={account.id} title={`Holdings — ${account.name}`} className="mt-4">
          <HoldingsTable holdings={rows} daysToTaxFree={taxFree} />
        </Card>
      ))}

      <Card title="Value" className="mt-4">
        <Panel state={value}>{(data) => <ValueChart series={data} />}</Panel>
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {ALLOCATIONS.map((entry, index) => (
          <Card key={entry.by} title={entry.title}>
            <Panel state={allocation}>{(data) => <AllocationPie slices={data[index] ?? []} />}</Panel>
          </Card>
        ))}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title={`Returns ${fmtDate(from)} – ${fmtDate(to)}`}>
          <Panel state={returns}>
            {(data) => (
              <div className="grid grid-cols-2 gap-4">
                <Stat
                  label="TTWROR"
                  value={<span className={signClass(data.ttwror)}>{pct(data.ttwror, 1)}</span>}
                  hint="True time-weighted return — the return of the strategy, deposits removed."
                />
                <Stat
                  label="XIRR"
                  value={<span className={signClass(data.xirr)}>{pct(data.xirr, 1)}</span>}
                  hint="Money-weighted annual return — what your capital actually earned."
                />
                <Stat label="Start value" value={eur(data.start_value)} />
                <Stat label="End value" value={eur(data.end_value)} />
                <Stat
                  label="Net flows"
                  value={eur(data.net_flows)}
                  hint="Deposits minus withdrawals inside the period."
                />
              </div>
            )}
          </Panel>
        </Card>

        <Card title={`Dividends ${year}`}>
          <Panel state={dividends}>
            {(data) => (
              <>
                <div className="grid grid-cols-2 gap-4">
                  <Stat label="Total (net)" value={eur(data.total)} />
                  <Stat
                    label="Withholding"
                    value={eur(data.withholding)}
                    hint="Foreign tax already deducted at the source."
                  />
                  <Stat
                    label="Projected next 12 m"
                    value={eur(data.projected_next_12m)}
                    hint="What the last twelve months would repeat — an estimate, not a promise."
                  />
                </div>
                <p className="label mt-3">By instrument</p>
                <KeyValueList entries={Object.entries(data.by_instrument)} />
                <p className="label mt-3">By month</p>
                <KeyValueList entries={Object.entries(data.by_month).sort()} />
              </>
            )}
          </Panel>
        </Card>

        <Card title={`Fees ${year}`}>
          <Panel state={fees}>
            {(data) => (
              <>
                <Stat label="Total" value={eur(data.total)} />
                <p className="label mt-3">Per account</p>
                <KeyValueList entries={Object.entries(data.by_account)} />
              </>
            )}
          </Panel>
        </Card>
      </div>

      <Card title={`Cash flows ${fmtDate(from)} – ${fmtDate(to)}`} className="mt-4">
        <Panel state={cashflows}>
          {(data) => (
            <DataTable
              rows={data}
              rowKey={(row, index) => `${row.date}-${index}`}
              empty="No cash flow in this range."
              columns={[
                { key: "date", header: "Date", render: (row) => fmtDate(row.date) },
                { key: "type", header: "Type", render: (row) => row.type },
                { key: "account", header: "Account", render: (row) => row.account },
                {
                  key: "amount",
                  header: "Amount",
                  align: "right",
                  render: (row) => (
                    <span className={signClass(row.amount_eur)}>{eur(row.amount_eur)}</span>
                  ),
                },
              ]}
            />
          )}
        </Panel>
      </Card>

      {lots.error !== null && <p className="mt-4 text-neg">Tax lots: {lots.error}</p>}
    </>
  );
}
