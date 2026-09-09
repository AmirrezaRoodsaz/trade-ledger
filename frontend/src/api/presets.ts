/** Presets and their versions — `trade_ledger/api/presets.py`.
 * A version is immutable: a parameter change is a new version, never an edit.
 */

import { get, post } from "./client";

export const STRATEGIES = ["donchian"] as const;
export type Strategy = (typeof STRATEGIES)[number];

export const TIMEFRAMES = ["1h", "4h", "1d"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

/** Pre-filled params for a new version, per strategy. */
export const STRATEGY_DEFAULTS: Record<Strategy, Record<string, unknown>> = {
  donchian: { entry: 55, exit: 20, atr_len: 20, atr_mult: 2 },
};

export interface Preset {
  id: number;
  name: string;
  strategy: string;
  description: string | null;
}

export interface PresetIn {
  name: string;
  strategy: string;
  description: string | null;
}

/** Money and percentages arrive as strings; `risk_pct` is a percent (3 = 3 %). */
export interface PresetVersion {
  id: number;
  preset_id: number;
  version: number;
  params: Record<string, unknown>;
  timeframe: string;
  pairs: string[];
  risk_pct: string | null;
  max_position_pct: string | null;
  leverage_cap: string;
  note: string | null;
  created: string;
}

export interface PresetVersionIn {
  params: Record<string, unknown>;
  timeframe: string;
  pairs: string[];
  risk_pct: string | null;
  max_position_pct: string | null;
  leverage_cap: string;
  note: string | null;
}

export const listPresets = () => get<Preset[]>("/presets");
export const createPreset = (payload: PresetIn) => post<Preset>("/presets", payload);
export const listVersions = (presetId: number) =>
  get<PresetVersion[]>(`/presets/${presetId}/versions`);
export const createVersion = (presetId: number, payload: PresetVersionIn) =>
  post<PresetVersion>(`/presets/${presetId}/versions`, payload);
