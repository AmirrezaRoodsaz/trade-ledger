/** Journal-side contracts: the plan/fill/review payloads, the chart and
 * calendar responses, daily notes, and the two pure helpers the plan form
 * needs. Read rows reuse `Trade` from `types.ts`.
 */

// Extension spelled out so `npm run check` can import this file under Node.
import { toNumber } from "../fmt.ts";
import type { Direction } from "./types";

export const MISTAKES = [
  "moved_stop",
  "no_stop",
  "oversized",
  "revenge",
  "fomo",
  "early_exit",
  "late_entry",
  "off_plan",
] as const;
export type Mistake = (typeof MISTAKES)[number];

/** `POST`/`PUT /api/trades` */
export interface TradeIn {
  account_id: number;
  instrument_id: number;
  direction: Direction;
  playbook_version_id: number | null;
  planned_entry: string | null;
  planned_stop: string | null;
  planned_target: string | null;
  risk_eur: string | null;
  planned_qty: string | null;
  emotion_pre: string | null;
  note_pre: string | null;
  tags: string[];
  external_ref: string | null;
}

export interface ManualFillIn {
  ts: string;
  quantity: string;
  price: string;
  fee_eur: string;
}

/** `POST /api/trades/{id}/open` and `/close` — linked fills, a manual one, or both. */
export interface FillsIn {
  fill_ids: number[];
  manual: ManualFillIn | null;
}

/** `POST /api/trades/{id}/review` */
export interface ReviewIn {
  adherence: boolean | null;
  mistake: Mistake | null;
  emotion_post: string | null;
  note_post: string | null;
}

/** `GET /api/trades/calendar?year&month&mode` — keyed by ISO close date. */
export interface CalendarDay {
  count: number;
  result_eur: string;
  r: string;
}

export type Calendar = Record<string, CalendarDay>;

/** `GET /api/trades/{id}/chart` — `time` is `YYYY-MM-DD`. */
export interface ChartCandle {
  time: string;
  open: string | null;
  high: string | null;
  low: string | null;
  close: string | null;
}

export interface ChartMarker {
  time: string;
  kind: string;
  price: string;
}

export interface ChartOut {
  candles: ChartCandle[];
  markers: ChartMarker[];
  resolution: string;
}

/** `POST /api/trades/{id}/excursions` */
export interface Excursions {
  mae_eur: string;
  mfe_eur: string;
  mae_r: string | null;
  mfe_r: string | null;
  resolution: string;
}

/** `GET`/`PUT /api/daily-notes/{date}` */
export interface DailyNoteIn {
  text: string;
  mood: string | null;
  hours_spent: string | null;
}

export interface DailyNote extends DailyNoteIn {
  id: number;
  date: string;
}

/** Position size that puts exactly `risk` euro between entry and stop.
 * Empty string when the inputs cannot answer it — the field then stays blank
 * rather than showing a made-up number.
 */
export function plannedQty(entry: string, stop: string, risk: string): string {
  const entryValue = toNumber(entry);
  const stopValue = toNumber(stop);
  const riskValue = toNumber(risk);
  if (entryValue === null || stopValue === null || riskValue === null) return "";
  const perUnit = Math.abs(entryValue - stopValue);
  if (perUnit === 0) return "";
  // ponytail: 8 decimals is the ledger's smallest unit; trailing zeroes off.
  return (riskValue / perUnit).toFixed(8).replace(/\.?0+$/, "");
}

/** The three pre-trade questions become one Markdown `note_pre`. Empty
 * answers leave no empty heading behind.
 */
export function composeNotePre(
  why: string,
  whereWrong: string,
  whatWouldStop: string,
): string | null {
  const sections: [string, string][] = [
    ["Why", why],
    ["Where I am wrong", whereWrong],
    ["What would stop me", whatWouldStop],
  ];
  const text = sections
    .filter(([, body]) => body.trim() !== "")
    .map(([heading, body]) => `## ${heading}\n\n${body.trim()}`)
    .join("\n\n");
  return text === "" ? null : text;
}
