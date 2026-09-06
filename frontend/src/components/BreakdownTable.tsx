import { useState } from "react";
import type { Breakdown } from "../api/analytics";
import type { Stats } from "../api/types";
import { DataTable } from "./DataTable";
import type { Column } from "./DataTable";
import { eur, num, pct, r, signClass, toNumber } from "../fmt";

interface Row {
  key: string;
  stats: Stats;
}

type SortKey = "key" | "count" | "win_rate" | "expectancy_r" | "total_r" | "total_eur" | "profit_factor" | "max_dd_r";

const NUMERIC: Record<Exclude<SortKey, "key">, (stats: Stats) => number | null> = {
  count: (s) => s.count,
  win_rate: (s) => toNumber(s.win_rate),
  expectancy_r: (s) => toNumber(s.expectancy_r),
  total_r: (s) => toNumber(s.total_r),
  total_eur: (s) => toNumber(s.total_eur),
  profit_factor: (s) => toNumber(s.profit_factor),
  max_dd_r: (s) => toNumber(s.max_dd_r),
};

/** Stats grouped by playbook, instrument, weekday, … — one row per group,
 * sortable by clicking a column header.
 */
export function BreakdownTable({ breakdown }: { breakdown: Breakdown }) {
  const [sort, setSort] = useState<SortKey>("total_r");
  const [desc, setDesc] = useState(true);

  const rows: Row[] = Object.entries(breakdown).map(([key, stats]) => ({ key, stats }));
  rows.sort((a, b) => {
    if (sort === "key") return a.key.localeCompare(b.key, "de");
    const left = NUMERIC[sort](a.stats);
    const right = NUMERIC[sort](b.stats);
    // Groups the backend could not compute sort to the bottom either way.
    if (left === null) return right === null ? 0 : 1;
    if (right === null) return -1;
    return left - right;
  });
  if (desc) rows.reverse();

  const header = (key: SortKey, label: string) => (
    <button
      type="button"
      className="uppercase tracking-wide hover:text-ink"
      onClick={() => {
        if (key === sort) setDesc(!desc);
        else {
          setSort(key);
          setDesc(key !== "key");
        }
      }}
    >
      {label}
      {key === sort ? (desc ? " ↓" : " ↑") : ""}
    </button>
  );

  const columns: Column<Row>[] = [
    { key: "key", header: header("key", "Group"), render: (row) => row.key },
    {
      key: "count",
      header: header("count", "Trades"),
      align: "right",
      render: (row) => num(row.stats.count, 0),
    },
    {
      key: "win_rate",
      header: header("win_rate", "Win rate"),
      align: "right",
      render: (row) => pct(row.stats.win_rate),
    },
    {
      key: "expectancy_r",
      header: header("expectancy_r", "Expectancy"),
      align: "right",
      render: (row) => (
        <span className={signClass(row.stats.expectancy_r)}>{r(row.stats.expectancy_r)}</span>
      ),
    },
    {
      key: "total_r",
      header: header("total_r", "Total R"),
      align: "right",
      render: (row) => <span className={signClass(row.stats.total_r)}>{r(row.stats.total_r)}</span>,
    },
    {
      key: "total_eur",
      header: header("total_eur", "Total P&L"),
      align: "right",
      render: (row) => (
        <span className={signClass(row.stats.total_eur)}>{eur(row.stats.total_eur)}</span>
      ),
    },
    {
      key: "profit_factor",
      header: header("profit_factor", "Profit factor"),
      align: "right",
      render: (row) => num(row.stats.profit_factor),
    },
    {
      key: "max_dd_r",
      header: header("max_dd_r", "Max DD"),
      align: "right",
      render: (row) => r(row.stats.max_dd_r),
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(row) => row.key}
      empty="No closed trades in this selection."
    />
  );
}
