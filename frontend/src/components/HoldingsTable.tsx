import type { Holding } from "../api/types";
import { DataTable } from "./DataTable";
import type { Column } from "./DataTable";
import { DASH, eur, num, pct, signClass, toNumber } from "../fmt";

/** Share of the portfolio as a thin bar behind the percentage. */
function WeightBar({ weight }: { weight: string }) {
  const share = Math.min(1, Math.max(0, toNumber(weight) ?? 0));
  return (
    <div className="flex items-center justify-end gap-2">
      <div className="h-1 w-16 rounded bg-surface2">
        <div className="h-1 rounded bg-accent" style={{ width: `${(share * 100).toFixed(1)}%` }} />
      </div>
      <span>{pct(weight)}</span>
    </div>
  );
}

/** Days until a crypto lot passes the § 23 one-year line; already past it
 * shows as "steuerfrei".
 */
function TaxFreeCell({ days }: { days: number | undefined }) {
  if (days === undefined) return <span className="text-muted">{DASH}</span>;
  if (days <= 0) return <span className="text-pos">steuerfrei</span>;
  return <span>{num(days, 0)} d</span>;
}

export function HoldingsTable({
  holdings,
  daysToTaxFree = {},
}: {
  holdings: Holding[];
  /** By instrument symbol, from `GET /api/tax/{year}/lots`. Crypto only. */
  daysToTaxFree?: Record<string, number>;
}) {
  const columns: Column<Holding>[] = [
    {
      key: "symbol",
      header: "Instrument",
      render: (holding) => (
        <>
          <span>{holding.instrument.symbol}</span>
          {holding.instrument.name !== null && (
            <span className="ml-2 text-muted">{holding.instrument.name}</span>
          )}
        </>
      ),
    },
    {
      key: "asset_class",
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
      render: (holding) => eur(holding.avg_cost_eur),
    },
    { key: "cost", header: "Cost", align: "right", render: (holding) => eur(holding.cost_eur) },
    {
      key: "price",
      header: "Price",
      align: "right",
      render: (holding) => eur(holding.price_eur),
    },
    { key: "value", header: "Value", align: "right", render: (holding) => eur(holding.value_eur) },
    {
      key: "unrealised",
      header: "Unrealised",
      align: "right",
      render: (holding) => (
        <span className={signClass(holding.unrealised_eur)}>{eur(holding.unrealised_eur)}</span>
      ),
    },
    {
      key: "weight",
      header: "Weight",
      align: "right",
      render: (holding) => <WeightBar weight={holding.weight} />,
    },
    {
      key: "taxfree",
      header: <span title="Days until the oldest open crypto lot passes the § 23 one-year line.">§ 23</span>,
      align: "right",
      render: (holding) => (
        <TaxFreeCell
          days={
            holding.instrument.asset_class === "crypto"
              ? daysToTaxFree[holding.instrument.symbol]
              : undefined
          }
        />
      ),
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={holdings}
      rowKey={(holding) => holding.instrument.id}
      empty="No open positions."
    />
  );
}
