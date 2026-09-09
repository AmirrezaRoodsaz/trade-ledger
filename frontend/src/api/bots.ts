/** Bots — `trade_ledger/api/bots.py`, `api/bot_controls.py`,
 * `api/bot_strategy.py`, `api/alerts.py`.
 *
 * Money and percentages arrive as strings; `drawdown_pct` and the readiness
 * map are already percentages (20 = 20 %), so they go through `fmt.num`, not
 * `fmt.pct`.
 */

import { get, post } from "./client";

export const BOT_STATUSES = ["disabled", "stale", "error", "paused", "running", "ok"] as const;
export type BotStatus = (typeof BOT_STATUSES)[number];

export const BOT_HOSTS = ["local", "remote"] as const;
export type BotHost = (typeof BOT_HOSTS)[number];

export const COMMAND_KINDS = [
  "pause",
  "resume",
  "flat",
  "run_now",
  "dry_run_on",
  "dry_run_off",
  "reload_config",
] as const;
export type CommandKind = (typeof COMMAND_KINDS)[number];

export const KILL_RULES = ["K1", "K2", "K3", "K4", "K5"] as const;
export type KillStatus = "ok" | "warning" | "triggered";

export interface KillRule {
  rule: string;
  status: KillStatus;
  value: string | null;
  threshold: string | null;
  action: string;
  detail: string;
}

/** `BotOut` — the registry row, never the token hash. */
export interface Bot {
  id: number;
  slug: string;
  name: string;
  account_id: number;
  strategy: string;
  preset_version_id: number | null;
  host: BotHost;
  schedule_every_s: number;
  schedule_at: string;
  grace_s: number;
  enabled: boolean;
  dry_run: boolean;
  paused_entries: boolean;
  stage_capital_eur: string | null;
  code_version: string | null;
  last_heartbeat: string | null;
  last_run_id: number | null;
  status: BotStatus;
  created: string;
}

/** `BotListItem` — `BotOut` plus what a fleet card shows without opening a bot. */
export interface BotListItem extends Bot {
  next_run: string | null;
  equity_eur: string | null;
  drawdown_pct: string | null;
  positions_count: number;
  positions_without_stop: number;
  kill_summary: Record<string, KillStatus>;
}

export interface BotIn {
  name: string;
  account_id: number;
  strategy: string;
  host: BotHost;
  schedule_every_s: number;
  schedule_at: string;
  grace_s: number;
  preset_version_id: number | null;
  stage_capital_eur: string | null;
  enabled: boolean;
  dry_run: boolean;
}

/** The token is returned twice in a bot's life: on create and on rotate. */
export interface BotCreated {
  bot: Bot;
  token: string;
}

export interface Position {
  symbol: string;
  qty: string;
  avg_entry: string | null;
  stop_present: boolean;
  stop_price: string | null;
  unrealised_eur: string | null;
}

export interface BotHealth {
  status: BotStatus;
  kill_rules: KillRule[];
  last_heartbeat: string | null;
  next_run: string;
  deadline: string;
  last_run: {
    id: number;
    started: string;
    finished: string | null;
    status: string;
    error: string | null;
  } | null;
  state: {
    ts: string;
    equity_eur: string | null;
    peak_equity_eur: string | null;
    drawdown_pct: string | null;
    reconciliation: string;
    reconciliation_detail: string | null;
    positions: Position[];
    positions_count: number;
    positions_without_stop: number;
    config_version: number | null;
  } | null;
  stage_capital_eur: string;
}

export interface BotCommandOut {
  id: number;
  bot_id: number;
  kind: CommandKind;
  reason: string | null;
  issued_ts: string;
  issued_by: string;
  acked_ts: string | null;
  result: string | null;
  result_detail: string | null;
}

export interface PresetAssigned {
  preset_version_id: number;
  event_id: number;
}

export interface Alert {
  id: number;
  bot_id: number | null;
  ts: string;
  severity: "info" | "warning" | "critical";
  kind: string;
  message: string;
  sent_telegram: boolean;
  acknowledged: boolean;
}

export const listBots = () => get<BotListItem[]>("/bots");
export const getBot = (slug: string) => get<Bot>(`/bots/${slug}`);
export const createBot = (payload: BotIn) => post<BotCreated>("/bots", payload);
export const rotateToken = (slug: string) => post<{ token: string }>(`/bots/${slug}/token`);
export const getEnvStatus = (slug: string) => get<Record<string, boolean>>(`/bots/${slug}/env-status`);
export const getHealth = (slug: string) => get<BotHealth>(`/bots/${slug}/health`);

/** `{slug: percent}` — one call for the whole fleet. */
export const fleetReadiness = () => get<Record<string, string>>("/bots/readiness/all");

/** `flat` needs `confirm === slug`, `resume` needs a reason — the API answers 422 without. */
export const issueCommand = (slug: string, kind: CommandKind, extra?: { reason?: string; confirm?: string }) =>
  post<BotCommandOut>(`/bots/${slug}/commands`, {
    kind,
    reason: extra?.reason ?? null,
    confirm: extra?.confirm ?? null,
  });

export const listAlerts = (params = "") => get<Alert[]>(`/alerts${params}`);

/** A live-mode bot's account makes `reason` mandatory — the API answers 422 without it. */
export const assignPreset = (slug: string, version_id: number, reason?: string) =>
  post<PresetAssigned>(`/bots/${slug}/preset`, { version_id, reason: reason || null });

// --- bot detail: runs, events, command queue --------------------------------
// Appended after the fleet's own exports: everything the detail page needs
// that the fleet page did not already declare. `ackAlert` sits with the other
// alert call. ponytail: the alert calls stay in this module — two functions
// do not earn a file.

import { ApiError } from "./client";

export interface BotRun {
  id: number;
  bot_id: number;
  started: string;
  finished: string | null;
  status: string;
  summary: Record<string, unknown>;
  error: string | null;
  has_log: boolean;
}

export const EVENT_KINDS = [
  "heartbeat",
  "info",
  "warning",
  "error",
  "kill_rule",
  "command",
  "config_applied",
  "reconcile",
  "order",
] as const;
export type EventKind = (typeof EVENT_KINDS)[number];

export interface BotEvent {
  id: number;
  bot_id: number;
  ts: string;
  kind: string;
  message: string;
  payload: Record<string, unknown>;
}

export const listRuns = (slug: string, limit = 50) =>
  get<BotRun[]>(`/bots/${slug}/runs?limit=${limit}`);
export const listEvents = (slug: string, kind?: string, limit = 200) =>
  get<BotEvent[]>(`/bots/${slug}/events?limit=${limit}${kind ? `&kind=${kind}` : ""}`);
export const listCommands = (slug: string) => get<BotCommandOut[]>(`/bots/${slug}/commands`);
export const ackAlert = (id: number) => post<Alert>(`/alerts/${id}/ack`);

/** The run log is `text/plain`, so it bypasses the JSON client. */
export async function getRunLog(slug: string, runId: number): Promise<string> {
  const response = await fetch(`/api/bots/${slug}/runs/${runId}/log`);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch {
      // Not a JSON error body: keep the status text.
    }
    throw new ApiError(response.status, detail);
  }
  return response.text();
}
