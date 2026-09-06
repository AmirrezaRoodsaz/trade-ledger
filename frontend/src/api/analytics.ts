/** `GET /api/analytics/*` — the trade-performance endpoints and the filter
 * set they all share. Money and other Decimals arrive as strings, null when
 * undefined.
 */

import { get } from "./client";
import type { ModeFilter, Stats } from "./types";

export interface Filters {
  accountIds: number[];
  mode: ModeFilter;
  playbookId: number | null;
  instrumentId: number | null;
  tag: string | null;
  dateFrom: string | null;
  dateTo: string | null;
}

export const EMPTY_FILTERS: Filters = {
  accountIds: [],
  mode: "paper",
  playbookId: null,
  instrumentId: null,
  tag: null,
  dateFrom: null,
  dateTo: null,
};

/** The common query params of every stats endpoint. `account_id` repeats. */
export function filterQuery(filters: Filters, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams();
  for (const id of filters.accountIds) params.append("account_id", String(id));
  params.set("mode", filters.mode);
  if (filters.playbookId !== null) params.set("playbook_id", String(filters.playbookId));
  if (filters.instrumentId !== null) params.set("instrument_id", String(filters.instrumentId));
  if (filters.tag) params.set("tag", filters.tag);
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  for (const [key, value] of Object.entries(extra)) params.set(key, value);
  return `?${params.toString()}`;
}

/** `useApi` re-runs on identity change, so filters go in as one stable string. */
export const filterKey = (filters: Filters): string => filterQuery(filters);

export interface EquityPoint {
  date: string;
  cum_r: string;
  cum_eur: string;
  /** Distance below the running peak, as a positive magnitude. */
  dd_r: string;
  dd_eur: string;
}

export interface HistogramBin {
  lo: string;
  hi: string;
  count: number;
}

export type Breakdown = Record<string, Stats>;

export const BREAKDOWN_KEYS = [
  "playbook",
  "instrument",
  "weekday",
  "month",
  "hold_bucket",
  "tag",
  "emotion",
  "account",
] as const;
export type BreakdownKey = (typeof BREAKDOWN_KEYS)[number];

export interface StageCheck {
  name: string;
  passed: boolean;
  actual: string;
  required: string;
}

export interface StageGate {
  mode: string;
  capital: string;
  checks: StageCheck[];
  passed: boolean;
  /** What to do next — the panel's reason text. */
  next: string;
}

export const fetchStats = (filters: Filters) =>
  get<Stats>(`/analytics/stats${filterQuery(filters)}`);

export const fetchEquity = (filters: Filters) =>
  get<EquityPoint[]>(`/analytics/equity${filterQuery(filters)}`);

export const fetchHistogram = (filters: Filters, binSize: string) =>
  get<HistogramBin[]>(`/analytics/histogram${filterQuery(filters, { bin_size: binSize })}`);

export const fetchBreakdown = (filters: Filters, by: BreakdownKey) =>
  get<Breakdown>(`/analytics/breakdown${filterQuery(filters, { by })}`);

/** Only `paper` and `live` — the gate is meaningless across mixed modes. */
export const fetchStageGate = (filters: Filters) =>
  get<StageGate>(`/analytics/stage-gate${filterQuery(filters)}`);
