import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import {
  EVENT_KINDS,
  ackAlert,
  assignPreset,
  getBot,
  getEnvStatus,
  getHealth,
  listAlerts,
  listCommands,
  listEvents,
  listRuns,
  type Bot as BotRow,
  type BotHealth,
  type BotRun,
} from "../api/bots";
import { useAction, useApi } from "../api/client";
import { listPresets, listVersions, type Preset, type PresetVersion } from "../api/presets";
import { Card } from "../components/Card";
import { CommandBar } from "../components/CommandBar";
import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { KillRuleStatus, KillRuleTable } from "../components/KillRuleTable";
import { PageHeader } from "../components/Layout";
import { PositionsTable } from "../components/PositionsTable";
import { RunLog } from "../components/RunLog";
import { StrategyTab } from "../components/StrategyTab";
import { DASH, dateTime, eur, num } from "../fmt";

const TABS = ["Overview", "Strategy", "Runs", "Timeline", "Config", "Controls", "Alerts"] as const;
type Tab = (typeof TABS)[number];

const POLL_MS = 30_000;

/** Status colours: green is only "ok", red only the two states that need a
 * human, amber everything in between. */
const STATUS_TONE: Record<string, string> = {
  ok: "text-pos",
  running: "text-accent",
  paused: "text-live",
  stale: "text-live",
  error: "text-neg",
  disabled: "text-muted",
};

const SEVERITY_TONE: Record<string, string> = {
  info: "text-muted",
  warning: "text-live",
  critical: "text-neg",
};

/** `drawdown_pct` and the preset percentages arrive already in percent. */
const percent = (value: string | null) => (value === null ? DASH : `${num(value, 2)} %`);

/** Re-read while the tab is open; the interval dies with the component. */
function usePoll(reload: () => void) {
  useEffect(() => {
    const timer = setInterval(reload, POLL_MS);
    return () => clearInterval(timer);
  }, [reload]);
}

function Err({ message }: { message: string | null }) {
  return message === null ? null : <p className="mt-2 text-neg">{message}</p>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <span className="label">{label}</span>
      <div>{children}</div>
    </div>
  );
}

function Flag({ label, on, tone }: { label: string; on: boolean; tone?: string }) {
  return (
    <span className={`rounded border px-1 text-[11px] ${on ? (tone ?? "border-accent text-accent") : "border-line text-muted"}`}>
      {label} {on ? "on" : "off"}
    </span>
  );
}

// --- Overview ---------------------------------------------------------------

function Overview({ slug, health }: { slug: string; health: BotHealth }) {
  const env = useApi(() => getEnvStatus(slug), [slug]);
  const state = health.state;

  return (
    <div className="grid gap-4">
      {state?.reconciliation === "mismatch" && (
        <div className="rounded border border-neg px-4 py-2 text-neg">
          Reconciliation mismatch — the bot's view and the exchange disagree.{" "}
          {state.reconciliation_detail ?? "No detail reported."}
        </div>
      )}

      <Card title="Health">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="Status">
            <span className={STATUS_TONE[health.status] ?? "text-ink"}>{health.status}</span>
          </Field>
          <Field label="Last heartbeat">{dateTime(health.last_heartbeat)}</Field>
          <Field label="Next run">{dateTime(health.next_run)}</Field>
          <Field label="Heartbeat deadline">{dateTime(health.deadline)}</Field>
          <Field label="Last run">
            {health.last_run === null
              ? DASH
              : `#${health.last_run.id} · ${health.last_run.status} · ${dateTime(
                  health.last_run.started,
                )}`}
          </Field>
          <Field label="Stage capital">{eur(health.stage_capital_eur)}</Field>
          <Field label="Reconciliation">{state?.reconciliation ?? DASH}</Field>
          <Field label="Config version reported">{state?.config_version ?? DASH}</Field>
        </div>
      </Card>

      <Card title="State" right={<span className="text-muted">as of {dateTime(state?.ts)}</span>}>
        {state === null ? (
          <EmptyState>The bot has not pushed any state yet.</EmptyState>
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label="Equity">{eur(state.equity_eur)}</Field>
            <Field label="Peak equity">{eur(state.peak_equity_eur)}</Field>
            <Field label="Drawdown">{percent(state.drawdown_pct)}</Field>
            <Field label="Positions">
              {state.positions_count}
              {state.positions_without_stop > 0 && (
                <span className="text-neg"> · {state.positions_without_stop} without stop</span>
              )}
            </Field>
          </div>
        )}
      </Card>

      <Card title="Kill rules">
        <KillRuleTable rules={health.kill_rules} />
      </Card>

      <Card title="Positions">
        <PositionsTable positions={state?.positions ?? []} />
      </Card>

      <Card title="Bot env file">
        <Err message={env.error} />
        <ul className="flex flex-wrap gap-x-6 gap-y-1">
          {Object.entries(env.data ?? {}).map(([key, present]) => (
            <li key={key} className="flex items-center gap-2">
              <span
                className={`inline-block h-2 w-2 rounded-full ${present ? "bg-pos" : "bg-neg"}`}
                aria-label={present ? "set" : "missing"}
              />
              <span className={present ? "text-ink" : "text-muted"}>{key}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

// --- Runs -------------------------------------------------------------------

/** `{"trades": 2, "signals": 1}` → `trades 2 · signals 1`. */
function summaryText(summary: Record<string, unknown>): string {
  const parts = Object.entries(summary).map(([key, value]) => `${key} ${String(value)}`);
  return parts.length === 0 ? DASH : parts.join(" · ");
}

function Runs({ slug }: { slug: string }) {
  const runs = useApi(() => listRuns(slug, 50), [slug]);
  const [open, setOpen] = useState<BotRun | null>(null);
  usePoll(runs.reload);

  return (
    <div className="grid gap-4">
      <Card title="Runs">
        <Err message={runs.error} />
        <DataTable
          rows={runs.data ?? []}
          rowKey={(run) => run.id}
          empty={runs.loading ? "Loading…" : "No runs recorded yet."}
          columns={[
            { key: "id", header: "Run", render: (run) => `#${run.id}` },
            { key: "started", header: "Started", render: (run) => dateTime(run.started) },
            { key: "finished", header: "Finished", render: (run) => dateTime(run.finished) },
            {
              key: "status",
              header: "Status",
              render: (run) => (
                <span className={run.status === "error" ? "text-neg" : "text-ink"}>
                  {run.status}
                </span>
              ),
            },
            {
              key: "summary",
              header: "Summary",
              render: (run) => <span className="text-muted">{summaryText(run.summary)}</span>,
            },
            {
              key: "error",
              header: "Error",
              render: (run) =>
                run.error === null ? (
                  <span className="text-muted">{DASH}</span>
                ) : (
                  <span className="text-neg">{run.error}</span>
                ),
            },
            {
              key: "log",
              header: "Log",
              render: (run) =>
                run.has_log ? (
                  <button className="btn" onClick={() => setOpen(run)}>
                    View
                  </button>
                ) : (
                  <span className="text-muted">{DASH}</span>
                ),
            },
          ]}
        />
      </Card>
      {open !== null && <RunLog slug={slug} run={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

// --- Timeline ---------------------------------------------------------------

function Timeline({ slug }: { slug: string }) {
  const [kind, setKind] = useState<string>("");
  const events = useApi(() => listEvents(slug, kind || undefined, 200), [slug, kind]);
  usePoll(events.reload);

  return (
    <Card title="Timeline">
      <div className="mb-3 flex flex-wrap gap-2">
        {["", ...EVENT_KINDS].map((one) => (
          <button
            key={one || "all"}
            className={`btn ${kind === one ? "border-accent text-accent" : ""}`}
            onClick={() => setKind(one)}
          >
            {one === "" ? "all" : one}
          </button>
        ))}
      </div>
      <Err message={events.error} />
      <DataTable
        rows={events.data ?? []}
        rowKey={(event) => event.id}
        empty={events.loading ? "Loading…" : "No events for this filter."}
        columns={[
          { key: "ts", header: "When", render: (event) => dateTime(event.ts) },
          { key: "kind", header: "Kind", render: (event) => event.kind },
          { key: "message", header: "Message", render: (event) => event.message || DASH },
          {
            key: "payload",
            header: "Payload",
            render: (event) =>
              Object.keys(event.payload).length === 0 ? (
                <span className="text-muted">{DASH}</span>
              ) : (
                <details>
                  <summary className="cursor-pointer text-muted">
                    {Object.keys(event.payload).length} keys
                  </summary>
                  <pre className="mt-1 max-w-lg overflow-x-auto rounded border border-line bg-surface2 p-2 text-[12px]">
                    {JSON.stringify(event.payload, null, 2)}
                  </pre>
                </details>
              ),
          },
        ]}
      />
    </Card>
  );
}

// --- Config -----------------------------------------------------------------

interface PresetRow {
  preset: Preset;
  versions: PresetVersion[];
}

function Config({
  bot,
  health,
  onAssigned,
}: {
  bot: BotRow;
  health: BotHealth | null;
  onAssigned: () => void;
}) {
  const rows = useApi<PresetRow[]>(async () => {
    const presets = await listPresets();
    const versions = await Promise.all(presets.map((preset) => listVersions(preset.id)));
    return presets.map((preset, index) => ({ preset, versions: versions[index] ?? [] }));
  });
  const [reason, setReason] = useState("");
  const [applying, setApplying] = useState<PresetVersion | null>(null);
  const action = useAction();

  const owner = (rows.data ?? []).find((row) =>
    row.versions.some((version) => version.id === bot.preset_version_id),
  );
  const assigned = owner?.versions.find((version) => version.id === bot.preset_version_id) ?? null;
  const latest = owner?.versions.reduce(
    (best, version) => (best === null || version.version > best.version ? version : best),
    null as PresetVersion | null,
  );
  const reported = health?.state?.config_version ?? null;
  // The botkit reports the preset *version id*, not the version number.
  const applied = assigned !== null && reported !== null && reported === assigned.id;

  return (
    <div className="grid gap-4">
      <Card title="Effective flags">
        <div className="flex flex-wrap gap-2">
          <Flag label="enabled" on={bot.enabled} tone="border-pos text-pos" />
          <Flag label="dry-run" on={bot.dry_run} />
          <Flag label="entries paused" on={bot.paused_entries} tone="border-live text-live" />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="Host">{bot.host}</Field>
          <Field label="Schedule">
            every {num(bot.schedule_every_s / 3600, 2)} h at {bot.schedule_at} UTC
          </Field>
          <Field label="Grace">{num(bot.grace_s / 60, 0)} min</Field>
          <Field label="Code version">{bot.code_version ?? DASH}</Field>
        </div>
      </Card>

      <Card
        title="Assigned preset"
        right={
          assigned === null ? undefined : (
            <span className={applied ? "text-pos" : "text-live"}>
              {applied ? "applied" : `pending (bot reports ${reported ?? DASH})`}
            </span>
          )
        }
      >
        <Err message={rows.error} />
        {assigned === null || owner === undefined ? (
          <EmptyState>
            {rows.loading ? "Loading…" : "No preset version assigned to this bot."}
          </EmptyState>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Field label="Preset">{owner.preset.name}</Field>
              <Field label="Version">v{assigned.version}</Field>
              <Field label="Strategy">{owner.preset.strategy}</Field>
              <Field label="Timeframe">{assigned.timeframe}</Field>
              <Field label="Pairs">
                {assigned.pairs.length === 0 ? DASH : assigned.pairs.join(", ")}
              </Field>
              <Field label="Risk">{percent(assigned.risk_pct)}</Field>
              <Field label="Max position">{percent(assigned.max_position_pct)}</Field>
              <Field label="Leverage cap">{num(assigned.leverage_cap, 2)}×</Field>
            </div>
            <pre className="mt-3 overflow-x-auto rounded border border-line bg-surface2 p-2 text-[12px]">
              {JSON.stringify(assigned.params, null, 2)}
            </pre>
            {latest !== undefined && latest !== null && latest.id !== assigned.id && (
              <div className="mt-3 flex items-center gap-3">
                <span className="text-live">
                  v{latest.version} available (created {dateTime(latest.created)})
                </span>
                <button className="btn" onClick={() => setApplying(latest)}>
                  Apply v{latest.version}
                </button>
              </div>
            )}
          </>
        )}
      </Card>

      {applying !== null && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md">
            <Card title={`Apply v${applying.version} to ${bot.name}`}>
              <p className="mb-3 text-muted">
                The bot picks the new parameters up at its next config pull. A live account
                refuses the change without a reason.
              </p>
              <label className="block">
                <span className="label">Reason</span>
                <input
                  className="field"
                  autoFocus
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <Err message={action.error} />
              <div className="mt-3 flex gap-2">
                <button
                  className="btn-accent"
                  disabled={action.busy}
                  onClick={() =>
                    void action.run(async () => {
                      await assignPreset(bot.slug, applying.id, reason.trim() || undefined);
                      setApplying(null);
                      setReason("");
                      onAssigned();
                    })
                  }
                >
                  Apply
                </button>
                <button className="btn" onClick={() => setApplying(null)}>
                  Cancel
                </button>
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

// --- Controls ---------------------------------------------------------------

function Controls({ bot, onIssued }: { bot: BotRow; onIssued: () => void }) {
  const commands = useApi(() => listCommands(bot.slug), [bot.slug]);
  usePoll(commands.reload);

  return (
    <div className="grid gap-4">
      <Card title="Controls">
        <CommandBar
          slug={bot.slug}
          dryRun={bot.dry_run}
          onIssued={() => {
            commands.reload();
            onIssued();
          }}
        />
      </Card>
      <Card title="Commands">
        <Err message={commands.error} />
        <DataTable
          rows={commands.data ?? []}
          rowKey={(command) => command.id}
          empty={commands.loading ? "Loading…" : "No commands issued yet."}
          columns={[
            { key: "kind", header: "Command", render: (command) => command.kind },
            { key: "issued", header: "Issued", render: (command) => dateTime(command.issued_ts) },
            { key: "by", header: "By", render: (command) => command.issued_by },
            { key: "reason", header: "Reason", render: (command) => command.reason ?? DASH },
            {
              key: "acked",
              header: "Acked",
              render: (command) =>
                command.acked_ts === null ? (
                  <span className="text-live">pending</span>
                ) : (
                  dateTime(command.acked_ts)
                ),
            },
            {
              key: "result",
              header: "Result",
              render: (command) => (
                <span className={command.result === "error" ? "text-neg" : "text-ink"}>
                  {command.result ?? DASH}
                  {command.result_detail !== null && (
                    <span className="text-muted"> · {command.result_detail}</span>
                  )}
                </span>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}

// --- Alerts -----------------------------------------------------------------

function Alerts({ slug }: { slug: string }) {
  const alerts = useApi(() => listAlerts(`?bot=${slug}`), [slug]);
  const action = useAction();
  usePoll(alerts.reload);

  return (
    <Card title="Alerts">
      <Err message={alerts.error} />
      <Err message={action.error} />
      <DataTable
        rows={alerts.data ?? []}
        rowKey={(alert) => alert.id}
        empty={alerts.loading ? "Loading…" : "No alerts for this bot."}
        columns={[
          { key: "ts", header: "When", render: (alert) => dateTime(alert.ts) },
          {
            key: "severity",
            header: "Severity",
            render: (alert) => (
              <span className={`uppercase ${SEVERITY_TONE[alert.severity] ?? "text-ink"}`}>
                {alert.severity}
              </span>
            ),
          },
          { key: "kind", header: "Kind", render: (alert) => alert.kind },
          { key: "message", header: "Message", render: (alert) => alert.message },
          {
            key: "telegram",
            header: "Telegram",
            render: (alert) => (alert.sent_telegram ? "sent" : DASH),
          },
          {
            key: "ack",
            header: "",
            render: (alert) =>
              alert.acknowledged ? (
                <span className="text-muted">acknowledged</span>
              ) : (
                <button
                  className="btn"
                  disabled={action.busy}
                  onClick={() =>
                    void action.run(async () => {
                      await ackAlert(alert.id);
                      alerts.reload();
                    })
                  }
                >
                  Ack
                </button>
              ),
          },
        ]}
      />
    </Card>
  );
}

// --- page -------------------------------------------------------------------

export function BotDetail() {
  const { slug = "" } = useParams();
  const [tab, setTab] = useState<Tab>("Overview");
  const bot = useApi(() => getBot(slug), [slug]);
  const health = useApi(() => getHealth(slug), [slug]);
  // The bot row holds dry_run, paused_entries and enabled — an acked command
  // flips those, so it is re-read on the same beat as the health report.
  const reloadAll = useCallback(() => {
    bot.reload();
    health.reload();
  }, [bot.reload, health.reload]);
  usePoll(reloadAll);

  return (
    <>
      <PageHeader
        title={bot.data?.name ?? slug}
        subtitle={
          bot.data === null ? (
            bot.error
          ) : (
            <span className="inline-flex flex-wrap items-center gap-2">
              {bot.data.slug} · {bot.data.strategy} · {bot.data.host}
              {health.data !== null && (
                <span className={STATUS_TONE[health.data.status] ?? "text-ink"}>
                  {health.data.status}
                </span>
              )}
              {health.data?.kill_rules.map((rule) => (
                <span key={rule.rule} className="rounded border border-line px-1 text-[11px]">
                  {rule.rule} <KillRuleStatus status={rule.status} />
                </span>
              ))}
            </span>
          )
        }
      />

      <div role="tablist" aria-label="Bot views" className="mb-4 flex flex-wrap gap-2">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            className={`btn ${tab === name ? "border-accent text-accent" : ""}`}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </div>

      <Err message={bot.error} />
      <Err message={health.error} />

      {bot.data === null ? (
        <EmptyState>{bot.loading ? "Loading…" : "Unknown bot."}</EmptyState>
      ) : (
        <>
          {tab === "Overview" &&
            (health.data === null ? (
              <EmptyState>{health.loading ? "Loading…" : "No health report."}</EmptyState>
            ) : (
              <Overview slug={slug} health={health.data} />
            ))}
          {tab === "Strategy" && <StrategyTab bot={bot.data} />}
          {tab === "Runs" && <Runs slug={slug} />}
          {tab === "Timeline" && <Timeline slug={slug} />}
          {tab === "Config" && (
            <Config bot={bot.data} health={health.data} onAssigned={reloadAll} />
          )}
          {tab === "Controls" && <Controls bot={bot.data} onIssued={reloadAll} />}
          {tab === "Alerts" && <Alerts slug={slug} />}
        </>
      )}
    </>
  );
}

export default BotDetail;
