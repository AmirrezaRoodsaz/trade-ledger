import { useEffect, useState } from "react";
import { ApiError, del, get, post, postForm, put, soft, useAction, useApi } from "../api/client";
import {
  ACCOUNT_KINDS,
  IMPORT_FORMATS,
  MODES,
  VENUES,
  type Account,
  type AccountIn,
  type AccountKind,
  type EnvStatus,
  type ImportCommitResult,
  type ImportFormat,
  type ImportPreview,
  type Mode,
  type Playbook,
  type PlaybookVersion,
  type Setting,
  type SyncRun,
  type Tag,
  type Venue,
} from "../api/types";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/Layout";
import { ModeBadge } from "../components/ModeBadge";
import { MoneyCell } from "../components/MoneyCell";
import { DASH, dateTime, num, toNumber } from "../fmt";

const STAGE_CAPITAL_PAPER = "stage_capital_paper";
const STAGE_CAPITAL_LIVE = "stage_capital_live";
const NOTES_OUT_DIR = "notes_out_dir";

const EMPTY_ACCOUNT: AccountIn = {
  venue: "trading212",
  name: "",
  kind: "broker_invest",
  mode: "paper",
  base_ccy: "EUR",
  credential_env_prefix: null,
  tax_wallet: null,
};

function Err({ message }: { message: string | null }) {
  return message === null ? null : <p className="mt-2 text-neg">{message}</p>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

// --- accounts --------------------------------------------------------------

function AccountForm({
  initial,
  onSave,
  onCancel,
  busy,
}: {
  initial: AccountIn;
  onSave: (payload: AccountIn) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [form, setForm] = useState<AccountIn>(initial);
  useEffect(() => setForm(initial), [initial]);

  return (
    <form
      className="mb-4 grid grid-cols-2 gap-3 rounded border border-line bg-surface2 p-3 lg:grid-cols-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(form);
      }}
    >
      <Field label="Venue">
        <select
          className="field"
          value={form.venue}
          onChange={(event) => setForm({ ...form, venue: event.target.value as Venue })}
        >
          {VENUES.map((venue) => (
            <option key={venue} value={venue}>
              {venue}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Name">
        <input
          className="field"
          required
          value={form.name}
          onChange={(event) => setForm({ ...form, name: event.target.value })}
        />
      </Field>
      <Field label="Kind">
        <select
          className="field"
          value={form.kind}
          onChange={(event) => setForm({ ...form, kind: event.target.value as AccountKind })}
        >
          {ACCOUNT_KINDS.map((kind) => (
            <option key={kind} value={kind}>
              {kind}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Mode">
        <select
          className="field"
          value={form.mode}
          onChange={(event) => setForm({ ...form, mode: event.target.value as Mode })}
        >
          {MODES.map((mode) => (
            <option key={mode} value={mode}>
              {mode}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Credential env prefix">
        <input
          className="field"
          placeholder="T212_LIVE"
          value={form.credential_env_prefix ?? ""}
          onChange={(event) =>
            setForm({ ...form, credential_env_prefix: event.target.value || null })
          }
        />
      </Field>
      <Field label="Tax wallet">
        <input
          className="field"
          placeholder="defaults to the name"
          value={form.tax_wallet ?? ""}
          onChange={(event) => setForm({ ...form, tax_wallet: event.target.value || null })}
        />
      </Field>
      <div className="col-span-full flex gap-2">
        <button className="btn-accent" type="submit" disabled={busy}>
          Save
        </button>
        <button className="btn" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function AccountsSection({ accounts, reload }: { accounts: Account[]; reload: () => void }) {
  const [editing, setEditing] = useState<Account | "new" | null>(null);
  const [runs, setRuns] = useState<Record<number, SyncRun>>({});
  const [syncingId, setSyncingId] = useState<number | null>(null);
  const save = useAction();
  const sync = useAction();
  const remove = useAction();

  const initial: AccountIn =
    editing === null || editing === "new"
      ? EMPTY_ACCOUNT
      : {
          venue: editing.venue,
          name: editing.name,
          kind: editing.kind,
          mode: editing.mode,
          base_ccy: editing.base_ccy,
          credential_env_prefix: editing.credential_env_prefix,
          tax_wallet: editing.tax_wallet,
        };

  return (
    <Card
      title="Accounts"
      right={
        <button className="btn" onClick={() => setEditing("new")}>
          New account
        </button>
      }
    >
      {editing !== null && (
        <AccountForm
          initial={initial}
          busy={save.busy}
          onCancel={() => setEditing(null)}
          onSave={(payload) =>
            void save.run(async () => {
              if (editing === "new") await post<Account>("/accounts", payload);
              else await put<Account>(`/accounts/${editing.id}`, payload);
              setEditing(null);
              reload();
            })
          }
        />
      )}
      <Err message={save.error} />
      <Err message={sync.error} />
      <Err message={remove.error} />
      <DataTable
        rows={accounts}
        rowKey={(account) => account.id}
        empty="No accounts yet."
        columns={[
          { key: "name", header: "Name", render: (account) => account.name },
          { key: "venue", header: "Venue", render: (account) => account.venue },
          { key: "kind", header: "Kind", render: (account) => account.kind },
          { key: "mode", header: "Mode", render: (account) => <ModeBadge mode={account.mode} /> },
          {
            key: "prefix",
            header: "Env prefix",
            render: (account) => account.credential_env_prefix ?? DASH,
          },
          { key: "wallet", header: "Tax wallet", render: (account) => account.tax_wallet },
          {
            key: "sync",
            header: "Sync",
            align: "right",
            render: (account) => {
              const run = runs[account.id];
              return (
                <div className="flex items-center justify-end gap-2">
                  {run !== undefined && (
                    <span className={run.status === "error" ? "text-neg" : "text-muted"}>
                      {run.status === "error"
                        ? (run.error ?? "error")
                        : `+${num(run.added, 0)} / ${num(run.skipped, 0)} skipped · ${dateTime(
                            run.started,
                          )}`}
                    </span>
                  )}
                  <button
                    className="btn"
                    disabled={sync.busy && syncingId === account.id}
                    onClick={() => {
                      setSyncingId(account.id);
                      void sync.run(async () => {
                        const result = await post<SyncRun>(`/accounts/${account.id}/sync`);
                        setRuns((previous) => ({ ...previous, [account.id]: result }));
                      });
                    }}
                  >
                    {sync.busy && syncingId === account.id ? "Syncing…" : "Sync"}
                  </button>
                  <button className="btn" onClick={() => setEditing(account)}>
                    Edit
                  </button>
                  <button
                    className="btn"
                    disabled={remove.busy}
                    onClick={() => {
                      if (!window.confirm(`Delete account "${account.name}"?`)) return;
                      void remove.run(async () => {
                        await del(`/accounts/${account.id}`);
                        reload();
                      });
                    }}
                  >
                    Delete
                  </button>
                </div>
              );
            },
          },
        ]}
      />
    </Card>
  );
}

// --- environment -----------------------------------------------------------

function EnvSection() {
  const env = useApi(() => get<EnvStatus>("/settings/env-status"));
  const keys = Object.entries(env.data ?? {});

  return (
    <Card title="API keys (.env)">
      <Err message={env.error} />
      {keys.length === 0 ? (
        <EmptyState>{env.loading ? "Loading…" : "No keys declared in .env.example."}</EmptyState>
      ) : (
        <ul className="grid grid-cols-2 gap-x-6 gap-y-1 lg:grid-cols-3">
          {keys.map(([key, present]) => (
            <li key={key} className="flex items-center gap-2">
              <span
                className={`inline-block h-2 w-2 rounded-full ${present ? "bg-pos" : "bg-neg"}`}
                aria-label={present ? "set" : "missing"}
              />
              <span className="truncate">{key}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] text-muted">
        Presence only — values never leave the .env file.
      </p>
    </Card>
  );
}

// --- CSV import ------------------------------------------------------------

function ImportSection({ accounts }: { accounts: Account[] }) {
  const [accountId, setAccountId] = useState<string>("");
  const [format, setFormat] = useState<ImportFormat>("generic");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [committed, setCommitted] = useState<ImportCommitResult | null>(null);
  const action = useAction();

  const selected = accountId === "" ? (accounts[0]?.id ?? null) : Number(accountId);

  return (
    <Card title="CSV import">
      <div className="mb-4 grid grid-cols-1 gap-3 lg:grid-cols-4">
        <Field label="Account">
          <select
            className="field"
            value={selected ?? ""}
            onChange={(event) => setAccountId(event.target.value)}
          >
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Format">
          <select
            className="field"
            value={format}
            onChange={(event) => setFormat(event.target.value as ImportFormat)}
          >
            {IMPORT_FORMATS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="File">
          <input
            className="field"
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setPreview(null);
              setCommitted(null);
            }}
          />
        </Field>
        <div className="flex items-end gap-2">
          <button
            className="btn"
            disabled={action.busy || file === null || selected === null}
            onClick={() =>
              void action.run(async () => {
                if (file === null || selected === null) return;
                const form = new FormData();
                form.append("file", file);
                form.append("format", format);
                form.append("account_id", String(selected));
                setCommitted(null);
                setPreview(await postForm<ImportPreview>("/imports/preview", form));
              })
            }
          >
            Preview
          </button>
          <button
            className="btn-accent"
            disabled={action.busy || preview === null || preview.drafts.length === 0}
            onClick={() =>
              void action.run(async () => {
                if (preview === null || selected === null) return;
                setCommitted(
                  await post<ImportCommitResult>("/imports/commit", {
                    account_id: selected,
                    drafts: preview.drafts,
                  }),
                );
                setPreview(null);
              })
            }
          >
            Commit
          </button>
        </div>
      </div>

      <Err message={action.error} />

      {committed !== null && (
        <p className="text-muted">
          Imported: {num(committed.added, 0)} added, {num(committed.skipped, 0)} skipped.
        </p>
      )}

      {preview !== null && (
        <>
          <p className="mb-2 text-muted">
            {num(preview.drafts.length, 0)} rows · {num(preview.duplicates, 0)} duplicates ·{" "}
            {num(preview.errors.length, 0)} errors
          </p>
          {preview.errors.length > 0 && (
            <ul className="mb-3 text-neg">
              {preview.errors.map((rowError) => (
                <li key={`${rowError.row}-${rowError.reason}`}>
                  Row {rowError.row}: {rowError.reason}
                </li>
              ))}
            </ul>
          )}
          <DataTable
            rows={preview.drafts.slice(0, 50)}
            rowKey={(draft, index) => `${draft.ts}-${index}`}
            empty="No rows parsed."
            columns={[
              { key: "ts", header: "When", render: (draft) => dateTime(draft.ts) },
              { key: "type", header: "Type", render: (draft) => draft.type },
              {
                key: "symbol",
                header: "Instrument",
                render: (draft) => draft.instrument_symbol ?? DASH,
              },
              {
                key: "qty",
                header: "Quantity",
                align: "right",
                render: (draft) => num(draft.quantity, 8),
              },
              {
                key: "amount",
                header: "Amount",
                align: "right",
                render: (draft) => <MoneyCell value={draft.amount_eur} />,
              },
              {
                key: "fee",
                header: "Fee",
                align: "right",
                render: (draft) => <MoneyCell value={draft.fee_eur} />,
              },
            ]}
          />
          {preview.drafts.length > 50 && (
            <p className="mt-2 text-muted">
              Showing the first 50 of {num(preview.drafts.length, 0)} rows. Commit writes all of them.
            </p>
          )}
        </>
      )}
    </Card>
  );
}

// --- playbooks -------------------------------------------------------------

function PlaybooksSection() {
  const playbooks = useApi(() => get<Playbook[]>("/playbooks"));
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [newName, setNewName] = useState("");
  const [rules, setRules] = useState("");
  const [sourceLink, setSourceLink] = useState("");
  const action = useAction();
  const current = selectedId ?? playbooks.data?.[0]?.id ?? null;
  const versions = useApi(
    () =>
      current === null
        ? Promise.resolve<PlaybookVersion[]>([])
        : get<PlaybookVersion[]>(`/playbooks/${current}/versions`),
    [current],
  );

  return (
    <Card title="Playbooks">
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="New playbook">
          <input
            className="field"
            value={newName}
            placeholder="Turtle Donchian"
            onChange={(event) => setNewName(event.target.value)}
          />
        </Field>
        <button
          className="btn"
          disabled={action.busy || newName.trim() === ""}
          onClick={() =>
            void action.run(async () => {
              const created = await post<Playbook>("/playbooks", { name: newName.trim() });
              setNewName("");
              setSelectedId(created.id);
              playbooks.reload();
            })
          }
        >
          Create
        </button>
        <Field label="Playbook">
          <select
            className="field"
            value={current ?? ""}
            onChange={(event) => setSelectedId(Number(event.target.value))}
          >
            {(playbooks.data ?? []).map((playbook) => (
              <option key={playbook.id} value={playbook.id}>
                {playbook.name}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Err message={playbooks.error} />
      <Err message={action.error} />

      {current === null ? (
        <EmptyState>No playbooks yet.</EmptyState>
      ) : (
        <>
          <DataTable
            rows={versions.data ?? []}
            rowKey={(version) => version.id}
            empty="No versions yet — a rule change is always a new version."
            columns={[
              { key: "version", header: "Version", render: (version) => `v${version.version}` },
              { key: "created", header: "Created", render: (version) => dateTime(version.created) },
              {
                key: "rules",
                header: "Rules",
                render: (version) => (
                  <pre className="max-w-2xl whitespace-pre-wrap font-sans">{version.rules_md}</pre>
                ),
              },
              {
                key: "source",
                header: "Source",
                render: (version) =>
                  version.source_link === null ? (
                    DASH
                  ) : (
                    <a className="text-accent hover:underline" href={version.source_link}>
                      link
                    </a>
                  ),
              },
            ]}
          />
          <div className="mt-4 grid gap-3">
            <Field label="New version — rules (Markdown)">
              <textarea
                className="field h-32"
                value={rules}
                placeholder={"- Entry: …\n- Stop: …\n- Exit: …"}
                onChange={(event) => setRules(event.target.value)}
              />
            </Field>
            <Field label="Source link">
              <input
                className="field"
                value={sourceLink}
                onChange={(event) => setSourceLink(event.target.value)}
              />
            </Field>
            <div>
              <button
                className="btn-accent"
                disabled={action.busy || rules.trim() === ""}
                onClick={() =>
                  void action.run(async () => {
                    await post<PlaybookVersion>(`/playbooks/${current}/versions`, {
                      rules_md: rules,
                      source_link: sourceLink || null,
                    });
                    setRules("");
                    setSourceLink("");
                    versions.reload();
                  })
                }
              >
                Add version
              </button>
            </div>
          </div>
        </>
      )}
    </Card>
  );
}

// --- tags ------------------------------------------------------------------

const TAG_KINDS = ["setup", "mistake", "market", "emotion", "other"];

function TagsSection() {
  const tags = useApi(() => get<Tag[]>("/tags"));
  const [kind, setKind] = useState("setup");
  const [name, setName] = useState("");
  const action = useAction();

  return (
    <Card title="Tags">
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Kind">
          <select className="field" value={kind} onChange={(event) => setKind(event.target.value)}>
            {TAG_KINDS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Name">
          <input
            className="field"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <button
          className="btn"
          disabled={action.busy || name.trim() === ""}
          onClick={() =>
            void action.run(async () => {
              await post<Tag>("/tags", { kind, name: name.trim() });
              setName("");
              tags.reload();
            })
          }
        >
          Add tag
        </button>
      </div>
      <Err message={tags.error} />
      <Err message={action.error} />
      <DataTable
        rows={tags.data ?? []}
        rowKey={(tag) => tag.id}
        empty="No tags yet."
        columns={[
          { key: "kind", header: "Kind", render: (tag) => tag.kind },
          { key: "name", header: "Name", render: (tag) => tag.name },
          {
            key: "actions",
            header: "",
            align: "right",
            render: (tag) => (
              <button
                className="btn"
                disabled={action.busy}
                onClick={() =>
                  void action.run(async () => {
                    await del(`/tags/${tag.id}`);
                    tags.reload();
                  })
                }
              >
                Delete
              </button>
            ),
          },
        ]}
      />
    </Card>
  );
}

// --- stage capital and notes ----------------------------------------------

function AppSettingsSection() {
  const settings = useApi(() => get<Setting[]>("/settings"));
  const [values, setValues] = useState<Record<string, string>>({});
  const [exported, setExported] = useState<number | null>(null);
  const save = useAction();
  const exportNotes = useAction();

  useEffect(() => {
    if (settings.data === null) return;
    setValues(Object.fromEntries(settings.data.map((item) => [item.key, item.value])));
  }, [settings.data]);

  const field = (key: string) => values[key] ?? "";
  const setField = (key: string, value: string) =>
    setValues((previous) => ({ ...previous, [key]: value }));

  return (
    <Card title="Stage capital and notes">
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <Field label="Stage capital paper (EUR)">
          <input
            className="field"
            inputMode="decimal"
            value={field(STAGE_CAPITAL_PAPER)}
            onChange={(event) => setField(STAGE_CAPITAL_PAPER, event.target.value)}
          />
        </Field>
        <Field label="Stage capital live (EUR)">
          <input
            className="field"
            inputMode="decimal"
            value={field(STAGE_CAPITAL_LIVE)}
            onChange={(event) => setField(STAGE_CAPITAL_LIVE, event.target.value)}
          />
        </Field>
        <Field label="Notes export directory">
          <input
            className="field"
            placeholder="notes_out"
            value={field(NOTES_OUT_DIR)}
            onChange={(event) => setField(NOTES_OUT_DIR, event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          className="btn-accent"
          disabled={save.busy}
          onClick={() =>
            void save.run(async () => {
              await put<Setting[]>(
                "/settings",
                [STAGE_CAPITAL_PAPER, STAGE_CAPITAL_LIVE, NOTES_OUT_DIR]
                  // A blank input means "not set" — writing "" would store an
                  // empty stage capital that reads back as a real value.
                  .filter((key) => field(key).trim() !== "")
                  .map((key) => ({ key, value: field(key).trim() })),
              );
              settings.reload();
            })
          }
        >
          Save settings
        </button>
        <button
          className="btn"
          disabled={exportNotes.busy}
          onClick={() =>
            void exportNotes.run(async () => {
              const result = await post<{ files: number }>("/notes/export", {
                dir: field(NOTES_OUT_DIR) || null,
              });
              setExported(result.files);
            })
          }
        >
          {exportNotes.busy ? "Exporting…" : "Export notes"}
        </button>
        {exported !== null && (
          <span className="text-muted">{num(exported, 0)} notes written.</span>
        )}
      </div>

      <Err message={settings.error} />
      <Err message={save.error} />
      <Err message={exportNotes.error} />
    </Card>
  );
}

// --- bot readiness and backtest criteria -----------------------------------

/** Key, label and the backend's own default — shown as the placeholder, so a
 * blank field reads as "the default applies" rather than as zero. Kept in the
 * order of `bots/backtests.py` and `bots/readiness.py`.
 */
const BOT_CRITERIA: { key: string; label: string; placeholder: string }[] = [
  { key: "bt_min_trades", label: "Backtest: min trades", placeholder: "40" },
  { key: "bt_min_expectancy", label: "Backtest: min expectancy R", placeholder: "0" },
  { key: "bt_min_profit_factor", label: "Backtest: min profit factor", placeholder: "1.3" },
  { key: "bt_max_drawdown_pct", label: "Backtest: max drawdown %", placeholder: "30" },
];

const READINESS_WEIGHTS: { key: string; label: string; placeholder: string }[] = [
  { key: "readiness_w_backtest", label: "Weight backtest %", placeholder: "25" },
  { key: "readiness_w_drills", label: "Weight drills %", placeholder: "15" },
  { key: "readiness_w_incubation", label: "Weight incubation %", placeholder: "30" },
  { key: "readiness_w_live", label: "Weight real money %", placeholder: "30" },
  { key: "readiness_demo_trades", label: "Demo trades for full progress", placeholder: "30" },
  { key: "readiness_live_trades", label: "Live trades for full progress", placeholder: "50" },
];

const BENCHMARK_KEY = "bt_require_beat_benchmark";

function BotCriteriaSection() {
  const settings = useApi(() => get<Setting[]>("/settings"));
  const [values, setValues] = useState<Record<string, string>>({});
  const save = useAction();

  useEffect(() => {
    if (settings.data === null) return;
    setValues(Object.fromEntries(settings.data.map((item) => [item.key, item.value])));
  }, [settings.data]);

  const field = (key: string) => values[key] ?? "";
  const setField = (key: string, value: string) =>
    setValues((previous) => ({ ...previous, [key]: value }));

  const all = [...BOT_CRITERIA, ...READINESS_WEIGHTS];
  const weightSum = READINESS_WEIGHTS.slice(0, 4).reduce(
    (total, item) => total + (toNumber(field(item.key) || item.placeholder) ?? 0),
    0,
  );

  return (
    <Card title="Bot readiness & backtest criteria">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {all.map((item) => (
          <Field key={item.key} label={item.label}>
            <input
              className="field"
              inputMode="decimal"
              placeholder={item.placeholder}
              value={field(item.key)}
              onChange={(event) => setField(item.key, event.target.value)}
            />
          </Field>
        ))}
        <Field label="Backtest must beat the benchmark">
          <select
            className="field"
            value={field(BENCHMARK_KEY) || "true"}
            onChange={(event) => setField(BENCHMARK_KEY, event.target.value)}
          >
            <option value="true">yes</option>
            <option value="false">no</option>
          </select>
        </Field>
      </div>

      <p className="mt-3 text-[11px] text-muted">
        Blank means the default in the placeholder. The four weights add up to {num(weightSum, 0)} %
        {weightSum !== 100 && " — readiness can never reach 100 % unless they sum to that"}. Changing
        a criterion re-judges nothing: a stored backtest keeps the verdict it was given, re-upload it
        to judge it again.
      </p>

      <div className="mt-4">
        <button
          className="btn-accent"
          disabled={save.busy}
          onClick={() =>
            void save.run(async () => {
              await put<Setting[]>("/settings", [
                ...all
                  .filter((item) => field(item.key).trim() !== "")
                  .map((item) => ({ key: item.key, value: field(item.key).trim() })),
                { key: BENCHMARK_KEY, value: field(BENCHMARK_KEY) || "true" },
              ]);
              settings.reload();
            })
          }
        >
          Save criteria
        </button>
      </div>

      <Err message={settings.error} />
      <Err message={save.error} />
    </Card>
  );
}

// --- Telegram --------------------------------------------------------------

const TELEGRAM_KEYS = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"];

function TelegramSection() {
  // ponytail: env-status is 404-safe so this section still renders on a
  // backend that predates the two keys — they simply read "not configured".
  const env = useApi(() => soft(get<EnvStatus>("/settings/env-status")));
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const action = useAction();
  const status = env.data ?? {};

  return (
    <Card title="Telegram">
      <Err message={env.error} />
      <ul className="grid gap-1">
        {TELEGRAM_KEYS.map((key) => {
          const present = status[key] === true;
          return (
            <li key={key} className="flex items-center gap-2">
              <span
                className={`inline-block h-2 w-2 rounded-full ${present ? "bg-pos" : "bg-neg"}`}
                aria-label={present ? "set" : "missing"}
              />
              <span>{key}</span>
              {!present && <span className="text-muted">not configured</span>}
            </li>
          );
        })}
      </ul>

      <div className="mt-3 flex items-center gap-3">
        <button
          className="btn"
          disabled={action.busy}
          onClick={() =>
            void action.run(async () => {
              setResult(null);
              try {
                const sent = await post<{ ok: boolean; detail?: string }>(
                  "/settings/telegram/test",
                );
                setResult({
                  ok: sent.ok,
                  text: sent.detail ?? (sent.ok ? "Test message sent." : "Telegram refused it."),
                });
              } catch (caught) {
                if (caught instanceof ApiError && caught.status === 404) {
                  setResult({ ok: false, text: "Telegram not available yet." });
                  return;
                }
                throw caught;
              }
            })
          }
        >
          Send test message
        </button>
        {result !== null && (
          <span className={result.ok ? "text-pos" : "text-neg"}>{result.text}</span>
        )}
      </div>

      <Err message={action.error} />
      <p className="mt-3 text-[11px] text-muted">
        Outbound alerts and inbound commands both use these two keys; inbound only accepts messages
        from that chat id.
      </p>
    </Card>
  );
}

export function Settings() {
  const accounts = useApi(() => get<Account[]>("/accounts"));
  const rows = accounts.data ?? [];

  return (
    <>
      <PageHeader title="Settings" subtitle="Accounts, keys, imports, playbooks, tags." />
      <div className="grid gap-4">
        <Err message={accounts.error} />
        <AccountsSection accounts={rows} reload={accounts.reload} />
        <EnvSection />
        <ImportSection accounts={rows} />
        <PlaybooksSection />
        <TagsSection />
        <AppSettingsSection />
        <BotCriteriaSection />
        <TelegramSection />
      </div>
    </>
  );
}
