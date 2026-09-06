import { useState } from "react";
import { del, get, post, put, useAction, useApi } from "../api/client";
import {
  EMPTY_TX_FILTERS,
  TX_TYPES,
  dec,
  toLocalInput,
  txQuery,
  type TransactionIn,
  type TxFilters,
} from "../api/transactions";
import type { Instrument, Page, Transaction, TxType } from "../api/types";
import { DASH, dateTime, num, toNumber } from "../fmt";
import { DataTable } from "./DataTable";
import { MoneyCell } from "./MoneyCell";

const PAGE_SIZE = 50;

interface FormState {
  ts: string;
  type: TxType;
  instrument_id: string;
  quantity: string;
  price: string;
  amount_eur: string;
  fee_eur: string;
  withholding_tax_eur: string;
  note: string;
  external_id: string | null;
  link_id: string | null;
}

function emptyForm(): FormState {
  return {
    ts: toLocalInput(new Date().toISOString()),
    type: "buy",
    instrument_id: "",
    quantity: "0",
    price: "",
    amount_eur: "0",
    fee_eur: "",
    withholding_tax_eur: "",
    note: "",
    external_id: null,
    link_id: null,
  };
}

function toForm(tx: Transaction): FormState {
  return {
    ts: toLocalInput(tx.ts),
    type: tx.type,
    instrument_id: tx.instrument_id === null ? "" : String(tx.instrument_id),
    quantity: tx.quantity,
    price: tx.price ?? "",
    amount_eur: tx.amount_eur,
    fee_eur: tx.fee_eur,
    withholding_tax_eur: tx.withholding_tax_eur,
    note: tx.note ?? "",
    external_id: tx.external_id,
    link_id: tx.link_id,
  };
}

function toPayload(form: FormState, accountId: number, instruments: Instrument[]): TransactionIn {
  const instrumentId = form.instrument_id === "" ? null : Number(form.instrument_id);
  const instrument = instruments.find((one) => one.id === instrumentId);
  const price = dec(form.price);
  const fee = dec(form.fee_eur) || "0";
  return {
    account_id: accountId,
    ts: new Date(form.ts).toISOString(),
    type: form.type,
    instrument_id: instrumentId,
    quantity: dec(form.quantity) || "0",
    price: price === "" ? null : price,
    // ponytail: manual rows are booked in EUR; the FX columns stay empty
    // until a manual entry in a foreign currency actually shows up.
    price_ccy: price === "" ? null : (instrument?.quote_ccy ?? "EUR"),
    fee,
    fee_ccy: fee === "0" ? null : "EUR",
    fx_rate: null,
    fx_source: null,
    amount_eur: dec(form.amount_eur) || "0",
    fee_eur: fee,
    withholding_tax_eur: dec(form.withholding_tax_eur) || "0",
    external_id: form.external_id,
    link_id: form.link_id,
    note: form.note.trim() === "" ? null : form.note.trim(),
  };
}

function TransactionModal({
  form,
  setForm,
  instruments,
  busy,
  error,
  onSave,
  onClose,
}: {
  form: FormState;
  setForm: (next: FormState) => void;
  instruments: Instrument[];
  busy: boolean;
  error: string | null;
  onSave: () => void;
  onClose: () => void;
}) {
  // Every transaction type moves either an instrument or cash; a row that is
  // zero on both is an empty row, which the blank-defaults-to-"0" path would
  // otherwise let through.
  const blank =
    (toNumber(dec(form.quantity)) ?? 0) === 0 && (toNumber(dec(form.amount_eur)) ?? 0) === 0;
  return (
    <div className="fixed inset-0 z-10 flex items-start justify-center overflow-y-auto bg-black/40 p-8">
      <form
        className="w-full max-w-2xl rounded border border-line bg-surface p-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSave();
        }}
      >
        <h3 className="mb-3 text-[11px] font-medium uppercase tracking-wide text-muted">
          Transaction
        </h3>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
          <label className="block">
            <span className="label">When</span>
            <input
              className="field"
              type="datetime-local"
              required
              value={form.ts}
              onChange={(event) => setForm({ ...form, ts: event.target.value })}
            />
          </label>
          <label className="block">
            <span className="label">Type</span>
            <select
              className="field"
              value={form.type}
              onChange={(event) => setForm({ ...form, type: event.target.value as TxType })}
            >
              {TX_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="label">Instrument</span>
            <select
              className="field"
              value={form.instrument_id}
              onChange={(event) => setForm({ ...form, instrument_id: event.target.value })}
            >
              <option value="">— cash —</option>
              {instruments.map((instrument) => (
                <option key={instrument.id} value={instrument.id}>
                  {instrument.symbol} ({instrument.asset_class})
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="label">Quantity</span>
            <input
              className="field"
              inputMode="decimal"
              required
              value={form.quantity}
              onChange={(event) => setForm({ ...form, quantity: event.target.value })}
            />
          </label>
          <label className="block">
            <span className="label">Price</span>
            <input
              className="field"
              inputMode="decimal"
              value={form.price}
              onChange={(event) => setForm({ ...form, price: event.target.value })}
            />
          </label>
          <label className="block">
            <span className="label">Amount (EUR)</span>
            <input
              className="field"
              inputMode="decimal"
              required
              value={form.amount_eur}
              onChange={(event) => setForm({ ...form, amount_eur: event.target.value })}
            />
          </label>
          <label className="block">
            <span className="label">Fee (EUR)</span>
            <input
              className="field"
              inputMode="decimal"
              value={form.fee_eur}
              onChange={(event) => setForm({ ...form, fee_eur: event.target.value })}
            />
          </label>
          <label className="block">
            <span className="label">Withholding tax (EUR)</span>
            <input
              className="field"
              inputMode="decimal"
              value={form.withholding_tax_eur}
              onChange={(event) => setForm({ ...form, withholding_tax_eur: event.target.value })}
            />
          </label>
          <label className="block lg:col-span-3">
            <span className="label">Note</span>
            <input
              className="field"
              value={form.note}
              onChange={(event) => setForm({ ...form, note: event.target.value })}
            />
          </label>
        </div>
        {blank && <p className="mt-2 text-neg">A transaction needs a quantity or an amount.</p>}
        {error !== null && <p className="mt-2 text-neg">{error}</p>}
        <div className="mt-4 flex gap-2">
          <button className="btn-accent" type="submit" disabled={busy || blank}>
            Save
          </button>
          <button className="btn" type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}

/** The Account tab's Transactions view: filtered, paged, editable by hand,
 * exportable as the CSV of exactly what the filters select.
 */
export function TransactionsTable({
  accountId,
  instruments,
}: {
  accountId: number;
  instruments: Instrument[];
}) {
  const [filters, setFilters] = useState<TxFilters>(EMPTY_TX_FILTERS);
  const [page, setPage] = useState(1);
  const [form, setForm] = useState<FormState | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const save = useAction();
  const remove = useAction();

  const query = txQuery(accountId, filters);
  const rows = useApi(
    () => get<Page<Transaction>>(`/transactions?${query}&page=${page}&page_size=${PAGE_SIZE}`),
    [query, page],
  );
  const total = rows.data?.total ?? 0;
  const items = rows.data?.items ?? [];
  const symbols = new Map(instruments.map((one) => [one.id, one.symbol]));

  const setFilter = (patch: Partial<TxFilters>) => {
    setFilters({ ...filters, ...patch });
    setPage(1);
  };

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="label">Type</span>
          <select
            className="field"
            value={filters.type}
            onChange={(event) => setFilter({ type: event.target.value })}
          >
            <option value="">all</option>
            {TX_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">Instrument</span>
          <select
            className="field"
            value={filters.instrumentId}
            onChange={(event) => setFilter({ instrumentId: event.target.value })}
          >
            <option value="">all</option>
            {instruments.map((instrument) => (
              <option key={instrument.id} value={instrument.id}>
                {instrument.symbol}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">From</span>
          <input
            className="field"
            type="date"
            value={filters.from}
            onChange={(event) => setFilter({ from: event.target.value })}
          />
        </label>
        <label className="block">
          <span className="label">To</span>
          <input
            className="field"
            type="date"
            value={filters.to}
            onChange={(event) => setFilter({ to: event.target.value })}
          />
        </label>
        <button
          className="btn"
          onClick={() => {
            setEditingId(null);
            setForm(emptyForm());
          }}
        >
          Add transaction
        </button>
        <a className="btn" href={`/api/transactions/export.csv?${query}`}>
          Export CSV
        </a>
      </div>

      {rows.error !== null && <p className="mb-2 text-neg">{rows.error}</p>}
      {remove.error !== null && <p className="mb-2 text-neg">{remove.error}</p>}

      <DataTable
        rows={items}
        rowKey={(tx) => tx.id}
        empty={rows.loading ? "Loading…" : "No transactions match these filters."}
        columns={[
          { key: "ts", header: "When", render: (tx) => dateTime(tx.ts) },
          { key: "type", header: "Type", render: (tx) => tx.type },
          {
            key: "instrument",
            header: "Instrument",
            render: (tx) =>
              tx.instrument_id === null ? DASH : (symbols.get(tx.instrument_id) ?? DASH),
          },
          {
            key: "quantity",
            header: "Quantity",
            align: "right",
            render: (tx) => num(tx.quantity, 8),
          },
          { key: "price", header: "Price", align: "right", render: (tx) => num(tx.price, 4) },
          {
            key: "amount",
            header: "Amount",
            align: "right",
            render: (tx) => <MoneyCell value={tx.amount_eur} />,
          },
          {
            key: "fee",
            header: "Fee",
            align: "right",
            render: (tx) => <MoneyCell value={tx.fee_eur} />,
          },
          { key: "source", header: "Source", render: (tx) => tx.source },
          {
            key: "actions",
            header: "",
            align: "right",
            render: (tx) => (
              <div className="flex justify-end gap-2">
                <button
                  className="btn"
                  onClick={() => {
                    setEditingId(tx.id);
                    setForm(toForm(tx));
                  }}
                >
                  Edit
                </button>
                <button
                  className="btn"
                  disabled={remove.busy}
                  onClick={() => {
                    if (!window.confirm(`Delete transaction #${tx.id}?`)) return;
                    void remove.run(async () => {
                      await del(`/transactions/${tx.id}`);
                      rows.reload();
                    });
                  }}
                >
                  Delete
                </button>
              </div>
            ),
          },
        ]}
      />

      <div className="mt-3 flex items-center gap-3 text-muted">
        <button className="btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>
          Previous
        </button>
        <button
          className="btn"
          disabled={page * PAGE_SIZE >= total}
          onClick={() => setPage(page + 1)}
        >
          Next
        </button>
        <span>
          {total === 0
            ? "0 transactions"
            : `${num((page - 1) * PAGE_SIZE + 1, 0)}–${num(
                Math.min(page * PAGE_SIZE, total),
                0,
              )} of ${num(total, 0)}`}
        </span>
      </div>

      {form !== null && (
        <TransactionModal
          form={form}
          setForm={setForm}
          instruments={instruments}
          busy={save.busy}
          error={save.error}
          onClose={() => setForm(null)}
          onSave={() =>
            void save.run(async () => {
              const payload = toPayload(form, accountId, instruments);
              if (editingId === null) await post<Transaction>("/transactions", payload);
              else await put<Transaction>(`/transactions/${editingId}`, payload);
              setForm(null);
              rows.reload();
            })
          }
        />
      )}
    </>
  );
}
