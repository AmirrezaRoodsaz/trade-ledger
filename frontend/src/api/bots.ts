/** Bot registry — `trade_ledger/api/bots.py` and `api/bot_controls.py`.
 * Only what the presets page needs: the list, and pointing a bot at a version.
 */

import { get, post } from "./client";

export interface Bot {
  id: number;
  slug: string;
  name: string;
  account_id: number;
  strategy: string;
  preset_version_id: number | null;
  status: string;
}

export interface PresetAssigned {
  preset_version_id: number;
  event_id: number;
}

export const listBots = () => get<Bot[]>("/bots");

/** A live-mode bot's account makes `reason` mandatory — the API answers 422 without it. */
export const assignPreset = (slug: string, version_id: number, reason?: string) =>
  post<PresetAssigned>(`/bots/${slug}/preset`, { version_id, reason: reason || null });
