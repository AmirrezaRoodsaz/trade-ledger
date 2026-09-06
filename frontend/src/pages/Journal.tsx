import { useEffect, useState } from "react";
import { get, post, put, soft, useAction, useApi } from "../api/client";
import { dec } from "../api/transactions";
import type { Calendar, DailyNote, Excursions } from "../api/trades";
import {
  MODES,
  type Account,
  type Instrument,
  type Mode,
  type Page,
  type Trade,
  type TradeStatus,
} from "../api/types";
import { CalendarGrid } from "../components/CalendarGrid";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/Layout";
import { MoneyCell } from "../components/MoneyCell";
import { TradeCard } from "../components/TradeCard";
import { TradeChart } from "../components/TradeChart";
import { TradeForm } from "../components/TradeForm";
import { DASH, dateTime, num, r } from "../fmt";

const LISTS: { status: TradeStatus; title: string }[] = [
  { status: "planned", title: "Planned" },
  { status: "open", title: "Open" },
  { status: "closed", title: "Closed" },
];

const todayIso = () => new Date().toISOString().slice(0, 10);

/** One note per day: what the market did, how it felt, how long it took. */
function DailyNoteEditor({ day, onDay }: { day: string; onDay: (value: string) => void }) {
  const note = useApi(() => soft(get<DailyNote>(`/daily-notes/${day}`)), [day]);
  const [text, setText] = useState("");
  const [mood, setMood] = useState("");
  const [hours, setHours] = useState("");
  const save = useAction();
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setText(note.data?.text ?? "");
    setMood(note.data?.mood ?? "");
    setHours(note.data?.hours_spent ?? "");
    setSaved(false);
  }, [note.data, day]);

  return (
    <Card
      title="Daily note"
      right={
        <input
          className="field w-auto"
          type="date"
          value={day}
          onChange={(event) => onDay(event.target.value || todayIso())}
        />
      }
    >
      {note.error !== null && <p className="mb-2 text-neg">{note.error}</p>}
      <textarea
        className="field h-28"
        placeholder="What happened, what I did, what I would do differently."
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="label">Mood</span>
          <input
            className="field"
            value={mood}
            onChange={(event) => setMood(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Hours spent</span>
          <input
            className="field"
            inputMode="decimal"
            value={hours}
            onChange={(event) => setHours(event.target.value)}
          />
        </label>
        <button
          className="btn-accent"
          disabled={save.busy || note.loading}
          onClick={() =>
            void save.run(async () => {
              await put<DailyNote>(`/daily-notes/${day}`, {
                text,
                mood: mood.trim() === "" ? null : mood.trim(),
                hours_spent: hours.trim() === "" ? null : dec(hours),
              });
              setSaved(true);
              note.reload();
            })
          }
        >
          Save note
        </button>
        {saved && <span className="text-muted">Saved.</span>}
      </div>
      {save.error !== null && <p className="mt-2 text-neg">{save.error}</p>}
    </Card>
  );
}

/** Chart, plan-versus-outcome and the excursions for the selected trade. */
function TradeDetail({
  trade,
  symbol,
  onClose,
  onChanged,
}: {
  trade: Trade;
  symbol: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [excursions, setExcursions] = useState<Excursions | null>(null);
  const action = useAction();

  useEffect(() => setExcursions(null), [trade.id]);

  return (
    <Card
      title={`${symbol} · trade #${trade.id}`}
      right={
        <button className="btn" onClick={onClose}>
          Close
        </button>
      }
    >
      {trade.status === "planned" ? (
        <p className="text-muted">The chart appears once the trade is open.</p>
      ) : (
        <TradeChart tradeId={trade.id} />
      )}

      <div className="mt-4 grid grid-cols-2 gap-3">
        <div>
          <p className="label">MAE</p>
          <p>
            <MoneyCell value={trade.mae_eur} /> {excursions !== null && `· ${r(excursions.mae_r)}`}
          </p>
        </div>
        <div>
          <p className="label">MFE</p>
          <p>
            <MoneyCell value={trade.mfe_eur} /> {excursions !== null && `· ${r(excursions.mfe_r)}`}
          </p>
        </div>
        <div>
          <p className="label">Opened</p>
          <p>{dateTime(trade.opened_ts)}</p>
        </div>
        <div>
          <p className="label">Closed</p>
          <p>{dateTime(trade.closed_ts)}</p>
        </div>
      </div>

      <button
        className="btn mt-3"
        disabled={action.busy || trade.opened_ts === null}
        onClick={() =>
          void action.run(async () => {
            setExcursions(await post<Excursions>(`/trades/${trade.id}/excursions`));
            onChanged();
          })
        }
      >
        {action.busy ? "Computing…" : "Compute MAE/MFE"}
      </button>
      {action.error !== null && <p className="mt-2 text-neg">{action.error}</p>}
    </Card>
  );
}

export function Journal() {
  const [mode, setMode] = useState<Mode>("paper");
  const [planOpen, setPlanOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [day, setDay] = useState(todayIso());
  const now = new Date();
  const [month, setMonth] = useState({ year: now.getFullYear(), month: now.getMonth() + 1 });

  const accounts = useApi(() => get<Account[]>("/accounts"));
  const instruments = useApi(() => get<Instrument[]>("/instruments"));
  const trades = useApi(() => get<Page<Trade>>(`/trades?mode=${mode}&page_size=200`), [mode]);
  const calendar = useApi(
    () => get<Calendar>(`/trades/calendar?year=${month.year}&month=${month.month}&mode=${mode}`),
    [month.year, month.month, mode],
  );

  const items = trades.data?.items ?? [];
  const selected = items.find((trade) => trade.id === selectedId) ?? null;
  const symbols = new Map((instruments.data ?? []).map((one) => [one.id, one.symbol]));
  const accountNames = new Map((accounts.data ?? []).map((one) => [one.id, one.name]));
  const symbolOf = (trade: Trade) => symbols.get(trade.instrument_id) ?? `#${trade.instrument_id}`;

  const shiftMonth = (delta: number) => {
    const shifted = new Date(Date.UTC(month.year, month.month - 1 + delta, 1));
    setMonth({ year: shifted.getUTCFullYear(), month: shifted.getUTCMonth() + 1 });
  };

  const reload = () => {
    trades.reload();
    calendar.reload();
  };

  return (
    <>
      <PageHeader title="Journal" subtitle="Plan it, take it, grade it." />

      <div className="mb-4 flex gap-2">
        {MODES.map((one) => (
          <button
            key={one}
            className={`btn ${mode === one ? "border-accent text-accent" : ""}`}
            onClick={() => setMode(one)}
          >
            {one}
          </button>
        ))}
      </div>

      <Card
        title="Plan a trade"
        className="mb-4"
        right={
          <button className="btn" onClick={() => setPlanOpen(!planOpen)}>
            {planOpen ? "Hide" : "Show"}
          </button>
        }
      >
        {planOpen ? (
          <TradeForm
            accounts={accounts.data ?? []}
            instruments={instruments.data ?? []}
            onCreated={reload}
            onInstrumentCreated={instruments.reload}
          />
        ) : (
          <p className="text-muted">
            Entry, stop, target and the risk you accept — before the order exists.
          </p>
        )}
      </Card>

      {trades.error !== null && <p className="mb-2 text-neg">{trades.error}</p>}
      {accounts.error !== null && <p className="mb-2 text-neg">{accounts.error}</p>}

      <div className={selected === null ? "" : "grid gap-4 xl:grid-cols-[1fr_28rem]"}>
        <div className="grid gap-4">
          {LISTS.map((list) => {
            const rows = items.filter((trade) => trade.status === list.status);
            return (
              <Card key={list.status} title={`${list.title} (${num(rows.length, 0)})`}>
                {rows.length === 0 ? (
                  <EmptyState>
                    {trades.loading ? "Loading…" : `No ${list.status} trades in ${mode}.`}
                  </EmptyState>
                ) : (
                  <div className="grid gap-2">
                    {rows.map((trade) => (
                      <TradeCard
                        key={trade.id}
                        trade={trade}
                        symbol={symbolOf(trade)}
                        accountName={accountNames.get(trade.account_id) ?? DASH}
                        onChanged={reload}
                        onSelect={() => setSelectedId(trade.id)}
                        selected={selectedId === trade.id}
                      />
                    ))}
                  </div>
                )}
              </Card>
            );
          })}
        </div>

        {selected !== null && (
          <TradeDetail
            trade={selected}
            symbol={symbolOf(selected)}
            onClose={() => setSelectedId(null)}
            onChanged={reload}
          />
        )}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Month">
          {calendar.error !== null && <p className="mb-2 text-neg">{calendar.error}</p>}
          <CalendarGrid
            year={month.year}
            month={month.month}
            days={calendar.data ?? {}}
            onShift={shiftMonth}
            onSelectDay={setDay}
            selectedDay={day}
          />
        </Card>
        <DailyNoteEditor day={day} onDay={setDay} />
      </div>
    </>
  );
}
