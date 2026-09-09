import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  BOT_HOSTS,
  createBot,
  fleetReadiness,
  listBots,
  type BotHost,
  type BotListItem,
} from "../api/bots";
import { get, useAction, useApi } from "../api/client";
import { STRATEGIES, listPresets, listVersions } from "../api/presets";
import type { Account } from "../api/types";
import { BotCard, TokenBox } from "../components/BotCard";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/Layout";
import { DASH, toNumber } from "../fmt";

const REFRESH_MS = 30_000;

const EVERY = [
  { label: "1 h", value: 3600 },
  { label: "4 h", value: 14400 },
  { label: "24 h", value: 86400 },
];

interface Fleet {
  bots: BotListItem[];
  readiness: Record<string, string>;
}

async function loadFleet(): Promise<Fleet> {
  const [bots, readiness] = await Promise.all([listBots(), fleetReadiness()]);
  return { bots, readiness };
}

/** `{version_id: label}` for the preset select — presets and their versions in one go. */
async function loadVersionOptions(): Promise<{ id: number; label: string }[]> {
  const presets = await listPresets();
  const perPreset = await Promise.all(
    presets.map((preset) =>
      listVersions(preset.id).then((versions) =>
        versions.map((version) => ({
          id: version.id,
          label: `${preset.name} · v${version.version} · ${version.timeframe}`,
        })),
      ),
    ),
  );
  return perPreset.flat();
}

function NewBotForm({
  accounts,
  versions,
  onCreated,
}: {
  accounts: Account[];
  versions: { id: number; label: string }[];
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [accountId, setAccountId] = useState("");
  const [strategy, setStrategy] = useState<string>(STRATEGIES[0]);
  const [host, setHost] = useState<BotHost>("local");
  const [every, setEvery] = useState(14400);
  const [at, setAt] = useState("00:05");
  const [graceMin, setGraceMin] = useState("55");
  const [versionId, setVersionId] = useState("");
  const [capital, setCapital] = useState("");
  const [created, setCreated] = useState<{ slug: string; token: string } | null>(null);
  const action = useAction();

  return (
    <Card title="New bot">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void action.run(async () => {
            const result = await createBot({
              name: name.trim(),
              account_id: Number(accountId),
              strategy,
              host,
              schedule_every_s: every,
              schedule_at: at,
              grace_s: Math.round(Number(graceMin) * 60),
              preset_version_id: versionId === "" ? null : Number(versionId),
              stage_capital_eur: capital.trim() === "" ? null : capital.trim(),
              enabled: true,
              dry_run: false,
            });
            setName("");
            setCapital("");
            setCreated({ slug: result.bot.slug, token: result.token });
            onCreated();
          });
        }}
      >
        <label className="block">
          <span className="label">Name</span>
          <input
            className="field"
            required
            placeholder="OKX Donchian 4h"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Account</span>
          <select
            className="field"
            required
            value={accountId}
            onChange={(event) => setAccountId(event.target.value)}
          >
            <option value="">choose…</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} ({account.mode})
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">Strategy</span>
          <select
            className="field"
            value={strategy}
            onChange={(event) => setStrategy(event.target.value)}
          >
            {STRATEGIES.map((one) => (
              <option key={one} value={one}>
                {one}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">Host</span>
          <select
            className="field"
            value={host}
            onChange={(event) => setHost(event.target.value as BotHost)}
          >
            {BOT_HOSTS.map((one) => (
              <option key={one} value={one}>
                {one}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">Every</span>
          <select
            className="field"
            value={every}
            onChange={(event) => setEvery(Number(event.target.value))}
          >
            {EVERY.map((one) => (
              <option key={one.value} value={one.value}>
                {one.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">At (UTC)</span>
          <input
            className="field"
            type="time"
            required
            value={at}
            onChange={(event) => setAt(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Grace (min)</span>
          <input
            className="field w-24"
            type="number"
            min={1}
            required
            value={graceMin}
            onChange={(event) => setGraceMin(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Preset version</span>
          <select
            className="field"
            value={versionId}
            onChange={(event) => setVersionId(event.target.value)}
          >
            <option value="">none yet</option>
            {versions.map((version) => (
              <option key={version.id} value={version.id}>
                {version.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">Stage capital €</span>
          <input
            className="field w-32"
            type="number"
            step="0.01"
            min={0}
            placeholder="account default"
            value={capital}
            onChange={(event) => setCapital(event.target.value)}
          />
        </label>
        <button className="btn-accent" disabled={action.busy || name.trim() === "" || accountId === ""}>
          Create
        </button>
      </form>
      {action.error !== null && <p className="mt-2 text-neg">{action.error}</p>}
      {action.error === null && created !== null && (
        <TokenBox slug={created.slug} token={created.token} />
      )}
    </Card>
  );
}

export function Bots() {
  const fleet = useApi(loadFleet, []);
  // Accounts and preset versions change on their own pages, not by the minute:
  // fetched once, not on every 30-second refresh.
  const accounts = useApi(() => get<Account[]>("/accounts"), []);
  const versions = useApi(loadVersionOptions, []);
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    const timer = setInterval(fleet.reload, REFRESH_MS);
    return () => clearInterval(timer);
  }, [fleet.reload]);

  const readiness = fleet.data?.readiness ?? {};
  const bots = [...(fleet.data?.bots ?? [])].sort(
    (a, b) => (toNumber(readiness[b.slug]) ?? 0) - (toNumber(readiness[a.slug]) ?? 0),
  );
  const byId = new Map((accounts.data ?? []).map((account) => [account.id, account]));

  return (
    <>
      <PageHeader
        title="Bots"
        subtitle={
          <>
            The fleet, refreshed every 30 s ·{" "}
            <Link className="text-accent hover:underline" to="/bots/presets">
              Presets
            </Link>
          </>
        }
      />

      <div className="mb-4 flex items-center gap-3">
        <button type="button" className="btn" onClick={() => setShowForm((was) => !was)}>
          {showForm ? "Hide form" : "New bot"}
        </button>
        {fleet.error !== null && <span className="text-neg">{fleet.error}</span>}
      </div>

      {showForm && (
        <div className="mb-4">
          <NewBotForm
            accounts={accounts.data ?? []}
            versions={versions.data ?? []}
            onCreated={fleet.reload}
          />
        </div>
      )}

      {fleet.data === null ? (
        <p className="text-muted">{fleet.loading ? "Loading…" : DASH}</p>
      ) : bots.length === 0 ? (
        <EmptyState>No bots yet — create one above.</EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {bots.map((bot) => (
            <BotCard
              key={bot.slug}
              bot={bot}
              account={byId.get(bot.account_id)}
              readiness={readiness[bot.slug] ?? null}
              onChanged={fleet.reload}
            />
          ))}
        </div>
      )}
    </>
  );
}
