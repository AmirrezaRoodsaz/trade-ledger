import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  KILL_RULES,
  issueCommand,
  rotateToken,
  type BotListItem,
  type BotStatus,
  type CommandKind,
} from "../api/bots";
import { useAction } from "../api/client";
import type { Account } from "../api/types";
import { DASH, ago, date, dateTime, eur, num, toNumber } from "../fmt";
import { Card } from "./Card";
import { KillRulePanel, type KillRuleLike } from "./KillRulePanel";
import { ModeBadge } from "./ModeBadge";

const LIGHT: Record<BotStatus, string> = {
  ok: "bg-pos",
  running: "bg-accent animate-pulse",
  paused: "bg-live",
  error: "bg-neg",
  stale: "bg-neg opacity-40",
  disabled: "bg-muted",
};

/** The token is shown once, here and nowhere else. */
export function TokenBox({ slug, token }: { slug: string; token: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-3 rounded border border-live p-2">
      <p className="label text-live">Bot token</p>
      <div className="mt-1 flex gap-2">
        <input
          className="field font-mono"
          readOnly
          value={token}
          onFocus={(event) => event.target.select()}
        />
        <button
          type="button"
          className="btn shrink-0"
          onClick={() => {
            void navigator.clipboard
              ?.writeText(token)
              .then(() => setCopied(true))
              .catch(() => setCopied(false));
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <p className="mt-1 text-[11px] text-muted">
        shown once — put it in data/bots/{slug}/.env as BOT_TOKEN
      </p>
    </div>
  );
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="label">{label}</p>
      <p>{value}</p>
    </div>
  );
}

/** Readiness is already a percentage (62.5 = 62,5 %). */
function ReadinessBar({ percent }: { percent: string | null }) {
  const value = toNumber(percent);
  const share = value === null ? 0 : Math.min(100, Math.max(0, value));
  return (
    <div className="mt-3">
      <div className="flex justify-between">
        <span className="label">Readiness</span>
        <span>{value === null ? DASH : `${num(percent, 1)} %`}</span>
      </div>
      <div
        className="mt-1 h-1 w-full rounded bg-surface2"
        role="progressbar"
        aria-label="Readiness"
        aria-valuenow={Number(share.toFixed(1))}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="h-1 rounded bg-accent" style={{ width: `${share.toFixed(1)}%` }} />
      </div>
    </div>
  );
}

export function BotCard({
  bot,
  account,
  readiness,
  onChanged,
}: {
  bot: BotListItem;
  account: Account | undefined;
  readiness: string | null;
  onChanged: () => void;
}) {
  const [done, setDone] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const action = useAction();

  const rules: KillRuleLike[] = KILL_RULES.map((rule) => ({
    rule,
    status: bot.kill_summary[rule] ?? "ok",
  }));

  const command = (kind: CommandKind, label: string) => () =>
    void action.run(async () => {
      setDone(null);
      const issued = await issueCommand(bot.slug, kind);
      setDone(`${label} queued (command #${issued.id}) — the bot picks it up on its next poll.`);
      onChanged();
    });

  return (
    <Card
      title={
        <span className="flex items-center gap-2 text-[13px] normal-case tracking-normal text-ink">
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${LIGHT[bot.status]}`}
            title={bot.status}
            aria-label={`status ${bot.status}`}
          />
          <Link className="font-medium text-accent hover:underline" to={`/bots/${bot.slug}`}>
            {bot.name}
          </Link>
          <span className="text-muted">{bot.status}</span>
        </span>
      }
      right={
        <span className="flex items-center gap-2 text-muted">
          {bot.dry_run && <span className="rounded border border-line px-1 text-[10px]">dry run</span>}
          {bot.paused_entries && (
            <span className="rounded border border-live px-1 text-[10px] text-live">no entries</span>
          )}
          {account !== undefined && <ModeBadge mode={account.mode} />}
        </span>
      }
    >
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
        <Field label="Strategy" value={bot.strategy} />
        <Field label="Account" value={account?.name ?? DASH} />
        <Field label="Host" value={bot.host} />
        <Field label="Equity" value={eur(bot.equity_eur)} />
        <Field
          label="Drawdown"
          value={bot.drawdown_pct === null ? DASH : `${num(bot.drawdown_pct, 2)} %`}
        />
        <Field
          label="Positions"
          value={
            <>
              {num(bot.positions_count, 0)}
              {bot.positions_without_stop > 0 && (
                <span className="ml-1 text-neg" title="position without a stop">
                  ● {num(bot.positions_without_stop, 0)} without stop
                </span>
              )}
            </>
          }
        />
        <Field
          label="Last heartbeat"
          value={
            bot.last_heartbeat === null ? DASH : `${ago(bot.last_heartbeat)} · ${date(bot.last_heartbeat)}`
          }
        />
        <Field label="Next run" value={dateTime(bot.next_run)} />
        <Field label="Kill rules" value={<KillRulePanel rules={rules} />} />
      </div>

      <ReadinessBar percent={readiness} />

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className="btn" disabled={action.busy} onClick={command("pause", "Pause")}>
          Pause
        </button>
        <button
          type="button"
          className="btn"
          disabled={action.busy}
          onClick={command("run_now", "Run now")}
        >
          Run now
        </button>
        <button
          type="button"
          className="btn"
          disabled={action.busy}
          onClick={() => {
            if (!window.confirm(`Rotate the token of "${bot.name}"? The old one stops working at once.`))
              return;
            void action.run(async () => {
              setDone(null);
              setToken((await rotateToken(bot.slug)).token);
            });
          }}
        >
          Rotate token
        </button>
        <Link className="text-accent hover:underline" to={`/bots/${bot.slug}`}>
          Details
        </Link>
      </div>

      {action.error !== null && <p className="mt-2 text-neg">{action.error}</p>}
      {action.error === null && done !== null && <p className="mt-2 text-pos">{done}</p>}
      {token !== null && <TokenBox slug={bot.slug} token={token} />}
    </Card>
  );
}
