import { useState } from "react";
import { get, post, useAction, useApi } from "../api/client";
import { dec } from "../api/transactions";
import { composeNotePre, plannedQty, type TradeIn } from "../api/trades";
import {
  ASSET_CLASSES,
  type Account,
  type AssetClass,
  type Direction,
  type Instrument,
  type Playbook,
  type PlaybookVersion,
  type Trade,
} from "../api/types";
import { ModeBadge } from "./ModeBadge";

const DIRECTIONS: Direction[] = ["long", "short"];

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

/** Plan a trade before the order exists: rules first, size derived from the
 * risk you are willing to lose, and the three questions answered in writing.
 */
export function TradeForm({
  accounts,
  instruments,
  onCreated,
  onInstrumentCreated,
}: {
  accounts: Account[];
  instruments: Instrument[];
  onCreated: () => void;
  onInstrumentCreated: () => void;
}) {
  const [accountId, setAccountId] = useState("");
  const [instrumentId, setInstrumentId] = useState("");
  const [search, setSearch] = useState("");
  const [newSymbol, setNewSymbol] = useState("");
  const [newAssetClass, setNewAssetClass] = useState<AssetClass>("crypto");
  const [direction, setDirection] = useState<Direction>("long");
  const [playbookId, setPlaybookId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [entry, setEntry] = useState("");
  const [stop, setStop] = useState("");
  const [target, setTarget] = useState("");
  const [risk, setRisk] = useState("");
  const [qtyOverride, setQtyOverride] = useState<string | null>(null);
  const [emotionPre, setEmotionPre] = useState("");
  const [why, setWhy] = useState("");
  const [whereWrong, setWhereWrong] = useState("");
  const [whatWouldStop, setWhatWouldStop] = useState("");
  const create = useAction();
  const addInstrument = useAction();

  const playbooks = useApi(() => get<Playbook[]>("/playbooks"));
  const versions = useApi(
    () =>
      playbookId === ""
        ? Promise.resolve<PlaybookVersion[]>([])
        : get<PlaybookVersion[]>(`/playbooks/${playbookId}/versions`),
    [playbookId],
  );

  const term = search.trim().toLowerCase();
  const matches = instruments.filter(
    (one) =>
      term === "" ||
      one.symbol.toLowerCase().includes(term) ||
      (one.name ?? "").toLowerCase().includes(term),
  );

  const derivedQty = plannedQty(dec(entry), dec(stop), dec(risk));
  const qty = qtyOverride ?? derivedQty;
  const sameLevel = dec(entry) !== "" && dec(entry) === dec(stop);
  const ready =
    accountId !== "" &&
    instrumentId !== "" &&
    entry.trim() !== "" &&
    stop.trim() !== "" &&
    risk.trim() !== "" &&
    !sameLevel;

  const reset = () => {
    setInstrumentId("");
    setSearch("");
    setEntry("");
    setStop("");
    setTarget("");
    setRisk("");
    setQtyOverride(null);
    setEmotionPre("");
    setWhy("");
    setWhereWrong("");
    setWhatWouldStop("");
  };

  return (
    <form
      className="grid gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready) return;
        void create.run(async () => {
          const payload: TradeIn = {
            account_id: Number(accountId),
            instrument_id: Number(instrumentId),
            direction,
            playbook_version_id: versionId === "" ? null : Number(versionId),
            planned_entry: dec(entry),
            planned_stop: dec(stop),
            planned_target: target.trim() === "" ? null : dec(target),
            risk_eur: dec(risk),
            planned_qty: qty === "" ? null : dec(qty),
            emotion_pre: emotionPre.trim() === "" ? null : emotionPre.trim(),
            note_pre: composeNotePre(why, whereWrong, whatWouldStop),
            tags: [],
            external_ref: null,
          };
          await post<Trade>("/trades", payload);
          reset();
          onCreated();
        });
      }}
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Field label="Account">
          <select
            className="field"
            value={accountId}
            onChange={(event) => setAccountId(event.target.value)}
          >
            <option value="">— pick one —</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} ({account.mode})
              </option>
            ))}
          </select>
        </Field>
        <Field label="Direction">
          <select
            className="field"
            value={direction}
            onChange={(event) => setDirection(event.target.value as Direction)}
          >
            {DIRECTIONS.map((one) => (
              <option key={one} value={one}>
                {one}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Playbook">
          <select
            className="field"
            value={playbookId}
            onChange={(event) => {
              setPlaybookId(event.target.value);
              setVersionId("");
            }}
          >
            <option value="">— none —</option>
            {(playbooks.data ?? []).map((playbook) => (
              <option key={playbook.id} value={playbook.id}>
                {playbook.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Playbook version">
          <select
            className="field"
            value={versionId}
            disabled={playbookId === ""}
            onChange={(event) => setVersionId(event.target.value)}
          >
            <option value="">— none —</option>
            {(versions.data ?? []).map((version) => (
              <option key={version.id} value={version.id}>
                v{version.version}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Field label="Find instrument">
          <input
            className="field"
            placeholder="BTC, AAPL…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </Field>
        <Field label="Instrument">
          <select
            className="field"
            value={instrumentId}
            onChange={(event) => setInstrumentId(event.target.value)}
          >
            <option value="">— pick one —</option>
            {matches.map((instrument) => (
              <option key={instrument.id} value={instrument.id}>
                {instrument.symbol} ({instrument.asset_class})
              </option>
            ))}
          </select>
        </Field>
        <Field label="…or new symbol">
          <input
            className="field"
            placeholder="BTC/EUR"
            value={newSymbol}
            onChange={(event) => setNewSymbol(event.target.value)}
          />
        </Field>
        <div className="flex items-end gap-2">
          <Field label="Asset class">
            <select
              className="field"
              value={newAssetClass}
              onChange={(event) => setNewAssetClass(event.target.value as AssetClass)}
            >
              {ASSET_CLASSES.map((assetClass) => (
                <option key={assetClass} value={assetClass}>
                  {assetClass}
                </option>
              ))}
            </select>
          </Field>
          <button
            className="btn"
            type="button"
            disabled={addInstrument.busy || newSymbol.trim() === ""}
            onClick={() =>
              void addInstrument.run(async () => {
                const created = await post<Instrument>("/instruments", {
                  symbol: newSymbol.trim(),
                  asset_class: newAssetClass,
                });
                setNewSymbol("");
                setInstrumentId(String(created.id));
                setSearch(created.symbol);
                onInstrumentCreated();
              })
            }
          >
            Create
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Field label="Entry">
          <input
            className="field"
            inputMode="decimal"
            value={entry}
            onChange={(event) => setEntry(event.target.value)}
          />
        </Field>
        <Field label="Stop">
          <input
            className="field"
            inputMode="decimal"
            value={stop}
            onChange={(event) => setStop(event.target.value)}
          />
        </Field>
        <Field label="Target">
          <input
            className="field"
            inputMode="decimal"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          />
        </Field>
        <Field label="Risk (EUR)">
          <input
            className="field"
            inputMode="decimal"
            value={risk}
            onChange={(event) => setRisk(event.target.value)}
          />
        </Field>
        <Field label="Planned quantity">
          <input
            className="field"
            inputMode="decimal"
            value={qty}
            placeholder="risk / |entry − stop|"
            onChange={(event) => setQtyOverride(event.target.value)}
          />
        </Field>
      </div>
      {qtyOverride !== null && (
        <p className="text-muted">
          Overridden — the rule says {derivedQty === "" ? "nothing yet" : derivedQty}.{" "}
          <button className="underline" type="button" onClick={() => setQtyOverride(null)}>
            use it
          </button>
        </p>
      )}
      {sameLevel && <p className="text-neg">Stop must differ from entry.</p>}

      <div className="grid gap-3 lg:grid-cols-3">
        <Field label="Why this trade">
          <textarea
            className="field h-20"
            value={why}
            onChange={(event) => setWhy(event.target.value)}
          />
        </Field>
        <Field label="Where I am wrong">
          <textarea
            className="field h-20"
            value={whereWrong}
            onChange={(event) => setWhereWrong(event.target.value)}
          />
        </Field>
        <Field label="What would stop me taking it">
          <textarea
            className="field h-20"
            value={whatWouldStop}
            onChange={(event) => setWhatWouldStop(event.target.value)}
          />
        </Field>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Emotion before">
          <input
            className="field"
            value={emotionPre}
            onChange={(event) => setEmotionPre(event.target.value)}
          />
        </Field>
        <button className="btn-accent" type="submit" disabled={!ready || create.busy}>
          Plan trade
        </button>
        {accountId !== "" && (
          <ModeBadge
            mode={
              accounts.find((account) => account.id === Number(accountId))?.mode ?? "paper"
            }
          />
        )}
      </div>

      {playbooks.error !== null && <p className="text-neg">{playbooks.error}</p>}
      {versions.error !== null && <p className="text-neg">{versions.error}</p>}
      {addInstrument.error !== null && <p className="text-neg">{addInstrument.error}</p>}
      {create.error !== null && <p className="text-neg">{create.error}</p>}
    </form>
  );
}
