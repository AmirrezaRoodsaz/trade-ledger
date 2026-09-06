/** Hand-written mirrors of the backend Pydantic models.
 * Money and other Decimals arrive as strings ("1234.56"), dates as ISO.
 */

export const VENUES = [
  "trading212",
  "okx",
  "kraken",
  "binance",
  "ibkr",
  "wallet",
  "other",
] as const;
export type Venue = (typeof VENUES)[number];

export const ACCOUNT_KINDS = [
  "broker_invest",
  "broker_cfd",
  "crypto_spot",
  "crypto_derivatives",
  "wallet",
] as const;
export type AccountKind = (typeof ACCOUNT_KINDS)[number];

export const MODES = ["live", "paper", "demo"] as const;
export type Mode = (typeof MODES)[number];
export type ModeFilter = Mode | "all";

export const ASSET_CLASSES = ["crypto", "stock", "etf", "fx", "cfd", "perp"] as const;
export type AssetClass = (typeof ASSET_CLASSES)[number];

export type FundType = "aktien" | "misch" | "immo" | "immo_ausland" | "sonstige";

export type TaxRegime =
  | "p23"
  | "p20_aktien"
  | "p20_inv"
  | "p20_sonstige"
  | "p20_termin"
  | "p22"
  | "none";

export type TxType =
  | "buy"
  | "sell"
  | "deposit"
  | "withdrawal"
  | "transfer_in"
  | "transfer_out"
  | "dividend"
  | "interest"
  | "fee"
  | "staking_reward"
  | "airdrop"
  | "split"
  | "vorabpauschale"
  | "adjustment";

export type TxSource = "api" | "csv" | "manual";
export type TradeStatus = "planned" | "open" | "closed" | "cancelled";
export type Direction = "long" | "short";

export const IMPORT_FORMATS = ["trading212", "okx", "kraken", "binance", "generic"] as const;
export type ImportFormat = (typeof IMPORT_FORMATS)[number];

export interface Page<T> {
  items: T[];
  total: number;
}

export interface Account {
  id: number;
  venue: Venue;
  name: string;
  kind: AccountKind;
  mode: Mode;
  base_ccy: string;
  credential_env_prefix: string | null;
  tax_wallet: string;
  active: boolean;
  created: string;
}

export interface AccountIn {
  venue: Venue;
  name: string;
  kind: AccountKind;
  mode: Mode;
  base_ccy: string;
  credential_env_prefix: string | null;
  tax_wallet: string | null;
}

export interface Instrument {
  id: number;
  symbol: string;
  asset_class: AssetClass;
  isin: string | null;
  quote_ccy: string;
  name: string | null;
  price_source: string | null;
  price_symbol: string | null;
  fund_type: FundType | null;
  tax_regime: TaxRegime | null;
}

export interface Transaction {
  id: number;
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
  source: TxSource;
  raw_json: string | null;
  note: string | null;
}

export interface Trade {
  id: number;
  account_id: number;
  instrument_id: number;
  direction: Direction;
  status: TradeStatus;
  playbook_version_id: number | null;
  planned_entry: string | null;
  planned_stop: string | null;
  planned_target: string | null;
  risk_eur: string | null;
  planned_qty: string | null;
  opened_ts: string | null;
  closed_ts: string | null;
  avg_entry: string | null;
  avg_exit: string | null;
  quantity: string | null;
  fees_eur: string | null;
  result_eur: string | null;
  mae_eur: string | null;
  mfe_eur: string | null;
  r_multiple: string | null;
  adherence: boolean | null;
  mistake: string | null;
  emotion_pre: string | null;
  emotion_post: string | null;
  note_pre: string | null;
  note_post: string | null;
  screenshots: string[];
  tags: string[];
  external_ref: string | null;
}

export interface Playbook {
  id: number;
  name: string;
  created: string;
}

export interface PlaybookVersion {
  id: number;
  playbook_id: number;
  version: number;
  rules_md: string;
  source_link: string | null;
  created: string;
}

export interface Tag {
  id: number;
  kind: string;
  name: string;
}

export interface SyncRun {
  id: number;
  account_id: number;
  started: string;
  finished: string | null;
  status: string;
  added: number;
  skipped: number;
  error: string | null;
}

export interface Setting {
  key: string;
  value: string;
}

/** `GET /api/settings/env-status` — presence only, never values. */
export type EnvStatus = Record<string, boolean>;

export interface TxDraft {
  ts: string;
  type: TxType;
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
  source: TxSource;
  raw_json: string | null;
  note: string | null;
  instrument_symbol: string | null;
  asset_class: AssetClass | null;
  isin: string | null;
}

export interface RowError {
  row: number;
  reason: string;
}

export interface ImportPreview {
  drafts: TxDraft[];
  errors: RowError[];
  duplicates: number;
}

export interface ImportCommitResult {
  added: number;
  skipped: number;
}

export interface InstrumentRef {
  id: number;
  symbol: string;
  asset_class: string;
  name: string | null;
}

export interface Holding {
  instrument: InstrumentRef;
  quantity: string;
  avg_cost_eur: string;
  cost_eur: string;
  price_eur: string | null;
  value_eur: string | null;
  unrealised_eur: string | null;
  weight: string;
}

export interface ValuePoint {
  date: string;
  value_eur: string;
}

export interface ValueSeries {
  points: ValuePoint[];
  missing_dates: string[];
}

/** `GET /api/analytics/stats?mode=` — Decimals as strings, null when undefined. */
export interface Stats {
  count: number;
  wins: number;
  losses: number;
  flat: number;
  win_rate: string | null;
  expectancy_r: string | null;
  total_r: string | null;
  total_eur: string | null;
  avg_win_r: string | null;
  avg_loss_r: string | null;
  payoff: string | null;
  profit_factor: string | null;
  sqn: string | null;
  std_r: string | null;
  max_dd_r: string | null;
  max_dd_eur: string | null;
  recovery_factor: string | null;
  max_win_streak: number;
  max_loss_streak: number;
  avg_hold_hours_win: string | null;
  avg_hold_hours_loss: string | null;
  adherence: string | null;
  cost_of_mistakes_r: string | null;
  cost_of_mistakes_eur: string | null;
  mistakes: Record<string, number>;
  avg_mae_r: string | null;
  avg_mfe_r: string | null;
  capture: string | null;
}

/** `GET /api/tax/{year}/summary?mode=` */
export interface YearSummary {
  p23: { net: string; freigrenze: string };
  p20: {
    sparerpauschbetrag: string;
    dividends: string;
    interest: string;
  };
  p22: { income: string; freigrenze: string };
  disclaimer: string;
  form_status: string;
}
