import type { Disposal } from "../api/tax";
import { date, eur, num } from "../fmt";
import { DataTable } from "./DataTable";
import { MoneyCell } from "./MoneyCell";

/** § 23 disposals: what was sold, how long it was held, what it made. */
export function DisposalsTable({ disposals }: { disposals: Disposal[] }) {
  return (
    <DataTable
      rows={disposals}
      rowKey={(row, index) => `${row.tx_id}-${row.instrument_id}-${index}`}
      empty="No disposals in this year."
      columns={[
        { key: "ts", header: "Verkauf", render: (row) => date(row.ts) },
        { key: "symbol", header: "Instrument", render: (row) => row.symbol || `#${row.instrument_id}` },
        { key: "wallet", header: "Wallet", render: (row) => row.wallet },
        { key: "acquired", header: "Anschaffung", render: (row) => date(row.acquired) },
        {
          key: "held",
          header: "Haltedauer",
          align: "right",
          render: (row) => `${num(row.holding_days, 0)} d`,
        },
        {
          key: "free",
          header: "> 1 Jahr",
          render: (row) =>
            row.over_one_year ? <span className="text-pos">steuerfrei</span> : "steuerpflichtig",
        },
        { key: "quantity", header: "Menge", align: "right", render: (row) => num(row.quantity, 8) },
        {
          key: "proceeds",
          header: "Veräußerungspreis",
          align: "right",
          render: (row) => eur(row.proceeds_eur),
        },
        {
          key: "cost",
          header: "Anschaffungskosten",
          align: "right",
          render: (row) => eur(row.cost_eur),
        },
        { key: "fee", header: "Gebühr", align: "right", render: (row) => eur(row.fee_eur) },
        {
          key: "gain",
          header: "Gewinn",
          align: "right",
          render: (row) => <MoneyCell value={row.gain_eur} sign />,
        },
      ]}
    />
  );
}
