import type { ReactNode } from "react";
import { get, useApi } from "../api/client";
import type { Filters } from "../api/analytics";
import { EMPTY_FILTERS } from "../api/analytics";
import type { Account, Instrument, ModeFilter, Playbook, Tag } from "../api/types";
import { MODES } from "../api/types";

const MODE_OPTIONS: ModeFilter[] = [...MODES, "all"];

interface Lists {
  accounts: Account[];
  playbooks: Playbook[];
  instruments: Instrument[];
  tags: Tag[];
}

const loadLists = async (): Promise<Lists> => {
  const [accounts, playbooks, instruments, tags] = await Promise.all([
    get<Account[]>("/accounts"),
    get<Playbook[]>("/playbooks"),
    get<Instrument[]>("/instruments"),
    get<Tag[]>("/tags"),
  ]);
  return { accounts, playbooks, instruments, tags };
};

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

/** The one control surface for every Analytics panel. Accounts are a native
 * multi-select: no account picked means "every account in this mode".
 */
export function FilterBar({
  value,
  onChange,
}: {
  value: Filters;
  onChange: (next: Filters) => void;
}) {
  const lists = useApi(loadLists, []);
  const set = <K extends keyof Filters>(key: K, next: Filters[K]) =>
    onChange({ ...value, [key]: next });

  const accounts = lists.data?.accounts ?? [];
  const playbooks = lists.data?.playbooks ?? [];
  const instruments = lists.data?.instruments ?? [];
  const tags = lists.data?.tags ?? [];

  return (
    <div className="mb-4 rounded border border-line bg-surface p-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4 xl:grid-cols-7">
        <Field label="Accounts">
          <select
            multiple
            size={4}
            className="field h-[76px]"
            value={value.accountIds.map(String)}
            onChange={(event) =>
              set(
                "accountIds",
                Array.from(event.target.selectedOptions, (option) => Number(option.value)),
              )
            }
          >
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} ({account.mode})
              </option>
            ))}
          </select>
        </Field>

        <Field label="Mode">
          <select
            className="field"
            value={value.mode}
            onChange={(event) => set("mode", event.target.value as ModeFilter)}
          >
            {MODE_OPTIONS.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Playbook">
          <select
            className="field"
            value={value.playbookId ?? ""}
            onChange={(event) =>
              set("playbookId", event.target.value === "" ? null : Number(event.target.value))
            }
          >
            <option value="">all</option>
            {playbooks.map((playbook) => (
              <option key={playbook.id} value={playbook.id}>
                {playbook.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Instrument">
          <select
            className="field"
            value={value.instrumentId ?? ""}
            onChange={(event) =>
              set("instrumentId", event.target.value === "" ? null : Number(event.target.value))
            }
          >
            <option value="">all</option>
            {instruments.map((instrument) => (
              <option key={instrument.id} value={instrument.id}>
                {instrument.symbol}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Tag">
          <select
            className="field"
            value={value.tag ?? ""}
            onChange={(event) => set("tag", event.target.value === "" ? null : event.target.value)}
          >
            <option value="">all</option>
            {tags.map((tag) => (
              <option key={tag.id} value={tag.name}>
                {tag.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="From">
          <input
            type="date"
            className="field"
            value={value.dateFrom ?? ""}
            onChange={(event) => set("dateFrom", event.target.value || null)}
          />
        </Field>

        <Field label="To">
          <input
            type="date"
            className="field"
            value={value.dateTo ?? ""}
            onChange={(event) => set("dateTo", event.target.value || null)}
          />
        </Field>
      </div>

      <div className="mt-3 flex items-center gap-4">
        <button type="button" className="btn" onClick={() => onChange(EMPTY_FILTERS)}>
          Reset
        </button>
        {lists.error !== null && <span className="text-neg">{lists.error}</span>}
        <span className="text-muted">
          {value.accountIds.length === 0
            ? `all ${value.mode === "all" ? "" : value.mode} accounts`
            : `${value.accountIds.length} account(s)`}
        </span>
      </div>
    </div>
  );
}
