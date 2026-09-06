/** `GET /api/portfolio/*`, `POST /api/prices/refresh` and the crypto lots of
 * `GET /api/tax/{year}/lots` that the holdings table joins in by symbol.
 */

import { get, post } from "./client";
import type { Holding, ModeFilter, TxType, ValueSeries } from "./types";

export interface PortfolioFilters {
  accountIds: number[];
  mode: ModeFilter;
}

export function portfolioQuery(
  filters: PortfolioFilters,
  extra: Record<string, string> = {},
): string {
  const params = new URLSearchParams();
  for (const id of filters.accountIds) params.append("account_id", String(id));
  params.set("mode", filters.mode);
  for (const [key, value] of Object.entries(extra)) params.set(key, value);
  return `?${params.toString()}`;
}

export interface Cashflow {
  date: string;
  type: TxType;
  amount_eur: string;
  account: string;
}

export interface Allocation {
  key: string;
  value_eur: string;
  weight: string;
}

export type AllocationBy = "asset_class" | "venue" | "instrument";

export interface Dividends {
  total: string;
  withholding: string;
  by_instrument: Record<string, string>;
  by_month: Record<string, string>;
  projected_next_12m: string;
}

export interface Fees {
  by_account: Record<string, string>;
  total: string;
}

export interface Returns {
  ttwror: string | null;
  xirr: string | null;
  start_value: string | null;
  end_value: string | null;
  net_flows: string;
}

export interface RefreshResult {
  prices_written: number;
  fx_written: number;
}

export interface TaxLot {
  wallet: string;
  instrument_id: number;
  symbol: string;
  acquired: string;
  quantity: string;
  cost_eur: string;
}

export interface TaxLots {
  lots: TaxLot[];
  disclaimer: string;
  form_status: string;
}

export const fetchHoldings = (filters: PortfolioFilters) =>
  get<Holding[]>(`/portfolio/holdings${portfolioQuery(filters)}`);

export const fetchCashflows = (filters: PortfolioFilters, from: string, to: string) =>
  get<Cashflow[]>(`/portfolio/cashflows${portfolioQuery(filters, { from, to })}`);

export const fetchValueSeries = (filters: PortfolioFilters, from: string, to: string) =>
  get<ValueSeries>(`/portfolio/value-series${portfolioQuery(filters, { from, to })}`);

export const fetchAllocation = (filters: PortfolioFilters, by: AllocationBy) =>
  get<Allocation[]>(`/portfolio/allocation${portfolioQuery(filters, { by })}`);

export const fetchDividends = (filters: PortfolioFilters, year: number) =>
  get<Dividends>(`/portfolio/dividends${portfolioQuery(filters, { year: String(year) })}`);

export const fetchFees = (filters: PortfolioFilters, year: number) =>
  get<Fees>(`/portfolio/fees${portfolioQuery(filters, { year: String(year) })}`);

export const fetchReturns = (filters: PortfolioFilters, from: string, to: string) =>
  get<Returns>(`/portfolio/returns${portfolioQuery(filters, { from, to })}`);

export const fetchTaxLots = (filters: PortfolioFilters, year: number) =>
  get<TaxLots>(`/tax/${year}/lots${portfolioQuery(filters)}`);

export const refreshPrices = (start: string, end: string) =>
  post<RefreshResult>("/prices/refresh", { start, end });

/** § 23 EStG: a crypto lot held longer than a year is tax free on sale. */
export const HOLDING_PERIOD_DAYS = 365;

/** Days left until the holding turns steuerfrei, by symbol — negative once it
 * already is. ponytail: one number per symbol, taken from the *oldest* open
 * lot, which is the one FIFO sells first and the one that frees up first.
 * Per-lot detail lives on the Steuer page.
 */
export function daysToTaxFree(lots: TaxLot[], today: Date = new Date()): Record<string, number> {
  const out: Record<string, number> = {};
  for (const lot of lots) {
    const held = Math.floor((today.getTime() - new Date(lot.acquired).getTime()) / 86_400_000);
    const left = HOLDING_PERIOD_DAYS - held;
    const current = out[lot.symbol];
    if (current === undefined || left < current) out[lot.symbol] = left;
  }
  return out;
}
