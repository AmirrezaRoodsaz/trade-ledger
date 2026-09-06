/** `GET /api/tax/...` — the Steuer screens.
 *
 * `types.ts` carries the trimmed `YearSummary` the Dashboard card needs; the
 * full shapes of `trade_ledger/api/tax.py` live here. Money is a string, dates
 * are ISO, and every response repeats `disclaimer` and `form_status`.
 */

import { get } from "./client";
import type { FundType, ModeFilter, TaxRegime } from "./types";

export interface TaxEnvelope {
  disclaimer: string;
  form_status: string;
}

export interface P23 {
  taxable_gains: string;
  taxable_losses: string;
  taxfree_gains: string;
  werbungskosten: string;
  proceeds: string;
  cost: string;
  net: string;
  freigrenze: string;
  exceeded: boolean;
}

export interface P22 {
  income: string;
  werbungskosten: string;
  net: string;
  freigrenze: string;
  exceeded: boolean;
}

export interface P20 {
  aktien_gains: string;
  aktien_losses: string;
  termin_gains: string;
  termin_losses: string;
  dividends: string;
  interest: string;
  withholding_tax: string;
  sonstige_net: string;
  other_losses: string;
  total_foreign: string;
  sparerpauschbetrag: string;
}

export interface InvRow {
  symbol: string;
  fund_type: FundType | string;
  teilfreistellung_pct: number;
  distributions: string;
  vorabpauschale: string;
  sale_gain: string;
  sale_loss: string;
}

export interface EoyHolding {
  wallet: string;
  symbol: string;
  quantity: string;
  value_eur: string | null;
}

/** DAC8 reconciliation: what one venue would report for the year. */
export interface VenueRow {
  account: string;
  gross_proceeds: string;
  gross_acquisitions: string;
  disposals_count: number;
  deposits: string;
  withdrawals: string;
}

export interface Disposal {
  wallet: string;
  instrument_id: number;
  symbol: string;
  tx_id: number;
  ts: string;
  acquired: string;
  holding_days: number;
  over_one_year: boolean;
  regime: TaxRegime;
  quantity: string;
  proceeds_eur: string;
  cost_eur: string;
  fee_eur: string;
  gain_eur: string;
}

export interface Lot {
  wallet: string;
  instrument_id: number;
  symbol: string;
  acquired: string;
  quantity: string;
  cost_eur: string;
}

export interface AnlageLine {
  form: string;
  zeile: number;
  label: string;
  value: string;
  note: string;
}

export interface TaxSummary extends TaxEnvelope {
  year: number;
  p23: P23;
  p22: P22;
  p20: P20;
  inv: InvRow[];
  eoy_holdings: EoyHolding[];
  venues: VenueRow[];
  warnings: string[];
}

export interface YearsResponse extends TaxEnvelope {
  years: number[];
}

export interface DisposalsResponse extends TaxEnvelope {
  disposals: Disposal[];
}

export interface LotsResponse extends TaxEnvelope {
  lots: Lot[];
}

export interface AnlageResponse extends TaxEnvelope {
  vz: number;
  lines: AnlageLine[];
  note: string | null;
}

export const EXPORT_FORMATS = ["disposals", "lots", "blockpit", "cointracking"] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

export const taxYears = (mode: ModeFilter) => get<YearsResponse>(`/tax/years?mode=${mode}`);

export const taxSummary = (year: number, mode: ModeFilter) =>
  get<TaxSummary>(`/tax/${year}/summary?mode=${mode}`);

export const taxDisposals = (year: number, mode: ModeFilter) =>
  get<DisposalsResponse>(`/tax/${year}/disposals?mode=${mode}`);

export const taxLots = (year: number, mode: ModeFilter) =>
  get<LotsResponse>(`/tax/${year}/lots?mode=${mode}`);

export const taxAnlage = (year: number, mode: ModeFilter) =>
  get<AnlageResponse>(`/tax/${year}/anlage?mode=${mode}`);

/** The CSV downloads are plain links, not fetches — the browser saves them. */
export const taxExportUrl = (year: number, mode: ModeFilter, format: ExportFormat) =>
  `/api/tax/${year}/export?format=${format}&mode=${mode}`;
