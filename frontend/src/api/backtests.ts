/** Backtest results — `trade_ledger/api/backtests.py`.
 *
 * The app never runs a backtest: a result is uploaded as a JSON file and the
 * API stores the verdict with it. `max_drawdown_pct`, `cagr_pct` and the two
 * benchmark fields are already percentages (30 = 30 %), so they go through
 * `fmt.num`; `win_rate` is a ratio like the journal's, so it goes through
 * `fmt.pct`.
 */

import { del, get, postForm } from "./client";

export interface Backtest {
  id: number;
  strategy: string;
  preset_version_id: number | null;
  bot_id: number | null;
  label: string;
  period_start: string;
  period_end: string;
  data_source: string;
  timeframe: string;
  pairs: string[];
  costs_note: string;
  trades: number;
  expectancy_r: string;
  profit_factor: string | null;
  win_rate: string;
  max_drawdown_pct: string;
  cagr_pct: string | null;
  benchmark_cagr_pct: string | null;
  benchmark_max_drawdown_pct: string | null;
  /** `[[date, cum_r], …]` exactly as uploaded — the numbers may arrive as numbers. */
  equity_r: (string | number)[][] | null;
  notes: string;
  passed: boolean;
  /** Empty when it passed; one entry per failed criterion otherwise. */
  fail_reasons: string[];
  created: string;
}

export function listBacktests(filter: { strategy?: string; bot?: string } = {}) {
  const params = new URLSearchParams();
  if (filter.strategy) params.set("strategy", filter.strategy);
  if (filter.bot) params.set("bot", filter.bot);
  const query = params.toString();
  return get<Backtest[]>(`/backtests${query ? `?${query}` : ""}`);
}

/** The JSON file carries every field, `strategy` included — the upload
 * endpoint takes nothing but the file. */
export function uploadBacktest(file: File) {
  const form = new FormData();
  form.append("file", file);
  return postForm<Backtest>("/backtests/upload", form);
}

export const deleteBacktest = (id: number) => del(`/backtests/${id}`);
