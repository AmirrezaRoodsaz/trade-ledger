import type { Lot } from "../api/tax";
import { date, eur, num } from "../fmt";
import { DataTable } from "./DataTable";

const DAY_MS = 86_400_000;

/** The day a lot leaves the § 23 Spekulationsfrist: one year after acquisition. */
function taxFreeFrom(acquired: string): Date {
  const day = new Date(acquired);
  day.setFullYear(day.getFullYear() + 1);
  return day;
}

/** Open FIFO lots with the countdown to the twelve-month mark. */
export function LotsTable({ lots }: { lots: Lot[] }) {
  const now = Date.now();
  return (
    <DataTable
      rows={lots}
      rowKey={(row, index) => `${row.instrument_id}-${row.acquired}-${index}`}
      empty="No open lots."
      columns={[
        { key: "symbol", header: "Instrument", render: (row) => row.symbol || `#${row.instrument_id}` },
        { key: "wallet", header: "Wallet", render: (row) => row.wallet },
        { key: "acquired", header: "Anschaffung", render: (row) => date(row.acquired) },
        { key: "quantity", header: "Menge", align: "right", render: (row) => num(row.quantity, 8) },
        { key: "cost", header: "Kosten", align: "right", render: (row) => eur(row.cost_eur) },
        {
          key: "free_from",
          header: "steuerfrei ab",
          render: (row) =>
            row.regime === "p23" ? date(taxFreeFrom(row.acquired).toISOString()) : "—",
        },
        {
          key: "countdown",
          header: "Restfrist",
          align: "right",
          render: (row) => {
            // Only § 23 knows a holding period. Shares, funds and
            // Termingeschaefte stay taxable however long they are held.
            if (row.regime !== "p23") return "—";
            const days = Math.ceil((taxFreeFrom(row.acquired).getTime() - now) / DAY_MS);
            return days <= 0 ? (
              <span className="text-pos">steuerfrei</span>
            ) : (
              `${num(days, 0)} d`
            );
          },
        },
      ]}
    />
  );
}
