/** Transaction-side contracts for the Account tab: the write payload, the
 * account summary, and the one query builder the table and the CSV link share.
 * Read rows reuse `Transaction` from `types.ts`.
 */

import type { TxType } from "./types";

export const TX_TYPES: readonly TxType[] = [
  "buy",
  "sell",
  "deposit",
  "withdrawal",
  "transfer_in",
  "transfer_out",
  "dividend",
  "interest",
  "fee",
  "staking_reward",
  "airdrop",
  "split",
  "vorabpauschale",
  "adjustment",
] as const;

/** `GET /api/accounts/{id}/summary` */
export interface AccountPosition {
  instrument: string;
  quantity: string;
}

export interface AccountSummary {
  cash_eur: string;
  positions: AccountPosition[];
  tx_count: number;
  last_sync: string | null;
}

/** `POST`/`PUT /api/transactions` — every money field is a decimal string. */
export interface TransactionIn {
  account_id: number;
  ts: string;
  type: TxType;
  instrument_id: number | null;
  quantity: string;
  price: string | null;
  price_ccy: string | null;
  fee: string;
  fee_ccy: string | null;
  fx_rate: string | null;
  fx_source: string | null;
  amount_eur: string;
  fee_eur: string;
  withholding_tax_eur: string;
  external_id: string | null;
  link_id: string | null;
  note: string | null;
}

/** German keyboards type "12,50"; every API decimal is a "12.50" string.
 * Shared by the transaction form and the plan-a-trade form.
 */
export function dec(value: string): string {
  return value.trim().replace(",", ".");
}

/** An ISO instant as `<input type="datetime-local">` wants it, in local time.
 * `new Date(value).toISOString()` is the way back.
 */
export function toLocalInput(iso: string): string {
  const parsed = new Date(iso);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}T${pad(
    parsed.getHours(),
  )}:${pad(parsed.getMinutes())}`;
}

export interface TxFilters {
  type: string;
  instrumentId: string;
  from: string;
  to: string;
}

export const EMPTY_TX_FILTERS: TxFilters = { type: "", instrumentId: "", from: "", to: "" };

/** One place builds the filter query — the paged list and the CSV export link
 * must never disagree about what is on screen.
 */
export function txQuery(accountId: number, filters: TxFilters): string {
  const params = new URLSearchParams({ account_id: String(accountId) });
  if (filters.type !== "") params.set("type", filters.type);
  if (filters.instrumentId !== "") params.set("instrument_id", filters.instrumentId);
  if (filters.from !== "") params.set("date_from", filters.from);
  // A bare "2026-09-30" is midnight UTC, which drops that day's own rows —
  // the end of the selected day is what the filter means.
  if (filters.to !== "") params.set("date_to", `${filters.to}T23:59:59Z`);
  return params.toString();
}
