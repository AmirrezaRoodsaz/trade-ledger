import type { Position } from "../api/bots";
import { DataTable } from "./DataTable";
import { num, signClass } from "../fmt";

/** What the bot last reported it holds. A position without a stop is the one
 * thing on this page that has to shout — K4 treats it as an integrity breach. */
export function PositionsTable({ positions }: { positions: Position[] }) {
  return (
    <DataTable
      rows={positions}
      rowKey={(position, index) => `${position.symbol}-${index}`}
      empty="No open positions reported."
      columns={[
        { key: "symbol", header: "Symbol", render: (p) => p.symbol },
        { key: "qty", header: "Qty", align: "right", render: (p) => num(p.qty, 6) },
        { key: "entry", header: "Avg entry", align: "right", render: (p) => num(p.avg_entry, 2) },
        {
          key: "stop",
          header: "Stop",
          render: (p) =>
            p.stop_present ? (
              <span>{num(p.stop_price, 2)}</span>
            ) : (
              <span className="font-medium text-neg">NO STOP</span>
            ),
        },
        {
          key: "unrealised",
          // The venue reports it in the market's quote currency; the bot
          // passes it through untouched, so it is not a EUR number.
          header: "Unrealised (quote)",
          align: "right",
          render: (p) => (
            <span className={signClass(p.unrealised_quote)}>{num(p.unrealised_quote, 2)}</span>
          ),
        },
      ]}
    />
  );
}
