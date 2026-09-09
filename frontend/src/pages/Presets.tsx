import { useState } from "react";
import { assignPreset, listBots, type Bot } from "../api/bots";
import { get, useAction, useApi } from "../api/client";
import {
  STRATEGIES,
  createPreset,
  createVersion,
  listPresets,
  listVersions,
  type Preset,
  type PresetVersion,
} from "../api/presets";
import type { Account } from "../api/types";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/Layout";
import { PresetEditor } from "../components/PresetEditor";
import { DASH, dateTime, num } from "../fmt";

interface PresetRow {
  preset: Preset;
  versions: PresetVersion[];
}

function Err({ message }: { message: string | null }) {
  return message === null ? null : <p className="mt-2 text-neg">{message}</p>;
}

function Ok({ message }: { message: string | null }) {
  return message === null ? null : <p className="mt-2 text-pos">{message}</p>;
}

function percent(value: string | null): string {
  return value === null ? DASH : `${num(value, 2)} %`;
}

// --- new preset ------------------------------------------------------------

function NewPresetForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [strategy, setStrategy] = useState<string>(STRATEGIES[0]);
  const [description, setDescription] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const action = useAction();

  return (
    <Card title="New preset">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void action.run(async () => {
            const created = await createPreset({
              name: name.trim(),
              strategy,
              description: description.trim() || null,
            });
            setName("");
            setDescription("");
            setDone(`Preset "${created.name}" created.`);
            onCreated();
          });
        }}
      >
        <label className="block">
          <span className="label">Name</span>
          <input
            className="field"
            required
            placeholder="Donchian 55/20 crypto"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
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
        <label className="block grow">
          <span className="label">Description</span>
          <input
            className="field"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <button className="btn-accent" disabled={action.busy || name.trim() === ""}>
          Create
        </button>
      </form>
      <Err message={action.error} />
      <Ok message={action.error === null ? done : null} />
    </Card>
  );
}

// --- assign ----------------------------------------------------------------

function AssignCell({
  version,
  bots,
  liveAccounts,
  onAssigned,
}: {
  version: PresetVersion;
  bots: Bot[];
  liveAccounts: Set<number>;
  onAssigned: () => void;
}) {
  const [slug, setSlug] = useState("");
  const [reason, setReason] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const action = useAction();

  const chosen = bots.find((bot) => bot.slug === slug);
  const needsReason = chosen !== undefined && liveAccounts.has(chosen.account_id);

  if (bots.length === 0) return <span className="text-muted">{DASH}</span>;

  return (
    <div className="flex flex-col gap-1">
      <select
        className="field w-44"
        aria-label={`Assign v${version.version} to a bot`}
        value={slug}
        onChange={(event) => {
          setSlug(event.target.value);
          setDone(null);
        }}
      >
        <option value="">assign to bot…</option>
        {bots.map((bot) => (
          <option key={bot.slug} value={bot.slug}>
            {bot.name}
            {liveAccounts.has(bot.account_id) ? " (live)" : ""}
          </option>
        ))}
      </select>
      {needsReason && (
        <input
          className="field w-44"
          aria-label="Reason for assigning to a live bot"
          placeholder="reason (live bot)"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      )}
      {slug !== "" && (
        <button
          className="btn w-44"
          disabled={action.busy || (needsReason && reason.trim() === "")}
          onClick={() =>
            void action.run(async () => {
              await assignPreset(slug, version.id, reason.trim() || undefined);
              setDone(`v${version.version} assigned to ${chosen?.name ?? slug}.`);
              setReason("");
              onAssigned();
            })
          }
        >
          Assign
        </button>
      )}
      <Err message={action.error} />
      <Ok message={action.error === null ? done : null} />
    </div>
  );
}

// --- one preset ------------------------------------------------------------

function PresetCard({
  row,
  bots,
  liveAccounts,
  reload,
}: {
  row: PresetRow;
  bots: Bot[];
  liveAccounts: Set<number>;
  reload: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const action = useAction();
  const latest = row.versions[row.versions.length - 1];

  return (
    <Card
      title={row.preset.name}
      right={
        <button className="btn" onClick={() => setOpen((was) => !was)}>
          {open ? "Hide versions" : `Versions (${row.versions.length})`}
        </button>
      }
    >
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-muted">
        <span>
          Strategy <span className="text-ink">{row.preset.strategy}</span>
        </span>
        <span>
          Latest{" "}
          <span className="text-ink">
            {latest === undefined ? DASH : `v${latest.version} · ${latest.timeframe}`}
          </span>
        </span>
        {row.preset.description !== null && <span>{row.preset.description}</span>}
      </div>

      {open && (
        <div className="mt-3">
          <DataTable
            rows={row.versions}
            rowKey={(version) => version.id}
            empty="No versions yet — add the first one below."
            columns={[
              { key: "version", header: "Version", render: (v) => `v${v.version}` },
              { key: "timeframe", header: "Timeframe", render: (v) => v.timeframe },
              {
                key: "pairs",
                header: "Pairs",
                render: (v) => (v.pairs.length === 0 ? DASH : v.pairs.join(", ")),
              },
              { key: "risk", header: "Risk", align: "right", render: (v) => percent(v.risk_pct) },
              {
                key: "max_position",
                header: "Max position",
                align: "right",
                render: (v) => percent(v.max_position_pct),
              },
              {
                key: "leverage",
                header: "Leverage cap",
                align: "right",
                render: (v) => `${num(v.leverage_cap, 2)}×`,
              },
              { key: "note", header: "Note", render: (v) => v.note ?? DASH },
              { key: "created", header: "Created", render: (v) => dateTime(v.created) },
              {
                key: "assign",
                header: "Assign",
                render: (v) => (
                  <AssignCell
                    version={v}
                    bots={bots}
                    liveAccounts={liveAccounts}
                    onAssigned={reload}
                  />
                ),
              },
            ]}
          />

          <PresetEditor
            strategy={row.preset.strategy}
            busy={action.busy}
            onSubmit={(payload) =>
              void action.run(async () => {
                const created = await createVersion(row.preset.id, payload);
                setDone(`Version v${created.version} added.`);
                reload();
              })
            }
          />
          <Err message={action.error} />
          <Ok message={action.error === null ? done : null} />
        </div>
      )}
    </Card>
  );
}

// --- page ------------------------------------------------------------------

export function Presets() {
  const rows = useApi<PresetRow[]>(async () => {
    const presets = await listPresets();
    const versions = await Promise.all(presets.map((preset) => listVersions(preset.id)));
    return presets.map((preset, index) => ({ preset, versions: versions[index] ?? [] }));
  });
  const bots = useApi(listBots);
  const accounts = useApi(() => get<Account[]>("/accounts"));

  const liveAccounts = new Set(
    (accounts.data ?? []).filter((one) => one.mode === "live").map((one) => one.id),
  );

  return (
    <>
      <PageHeader
        title="Presets"
        subtitle="Named, versioned parameter sets. A version is never edited — a change is a new version."
      />
      <div className="grid gap-4">
        <Err message={rows.error} />
        <Err message={bots.error} />
        <Err message={accounts.error} />
        <NewPresetForm onCreated={rows.reload} />
        {(rows.data ?? []).length === 0 ? (
          <EmptyState>{rows.loading ? "Loading…" : "No presets yet."}</EmptyState>
        ) : (
          (rows.data ?? []).map((row) => (
            <PresetCard
              key={row.preset.id}
              row={row}
              bots={bots.data ?? []}
              liveAccounts={liveAccounts}
              reload={rows.reload}
            />
          ))
        )}
      </div>
    </>
  );
}
