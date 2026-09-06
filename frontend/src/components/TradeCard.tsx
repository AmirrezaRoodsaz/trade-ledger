import { useState } from "react";
import { del, get, post, postForm, useAction, useApi } from "../api/client";
import { dec, toLocalInput } from "../api/transactions";
import { MISTAKES, type FillsIn, type Mistake, type ReviewIn } from "../api/trades";
import type { Trade, TradeStatus, Transaction } from "../api/types";
import { DASH, dateTime, num, r, signClass } from "../fmt";
import { MoneyCell } from "./MoneyCell";

type Panel = "fills" | "review" | "screenshots" | null;

const STATUS_TONE: Record<TradeStatus, string> = {
  planned: "text-muted",
  open: "text-accent",
  closed: "text-ink",
  cancelled: "text-muted line-through",
};

function Value({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="label">{label}</p>
      <p>{children}</p>
    </div>
  );
}

/** Link existing buy/sell rows as fills, or type one in by hand. The same
 * panel opens and closes a trade — the endpoint is the only difference.
 */
function FillPanel({ trade, onDone }: { trade: Trade; onDone: () => void }) {
  const suggested = useApi(
    () => get<Transaction[]>(`/trades/${trade.id}/suggest-fills`),
    [trade.id],
  );
  const [picked, setPicked] = useState<number[]>([]);
  const [ts, setTs] = useState(toLocalInput(new Date().toISOString()));
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [fee, setFee] = useState("");
  const action = useAction();

  const manualReady = quantity.trim() !== "" && price.trim() !== "";
  const endpoint = trade.status === "planned" ? "open" : "close";

  return (
    <div className="mt-3 rounded border border-line bg-surface2 p-3">
      <p className="label mb-2">Suggested fills</p>
      {suggested.error !== null && <p className="text-neg">{suggested.error}</p>}
      {(suggested.data ?? []).length === 0 ? (
        <p className="text-muted">
          {suggested.loading ? "Loading…" : "No unlinked buy/sell rows near this trade."}
        </p>
      ) : (
        <ul className="grid gap-1">
          {(suggested.data ?? []).map((fill) => (
            <li key={fill.id}>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={picked.includes(fill.id)}
                  onChange={(event) =>
                    setPicked(
                      event.target.checked
                        ? [...picked, fill.id]
                        : picked.filter((one) => one !== fill.id),
                    )
                  }
                />
                <span>
                  {dateTime(fill.ts)} · {fill.type} · {num(fill.quantity, 8)} @ {num(fill.price, 4)}{" "}
                  · <MoneyCell value={fill.amount_eur} />
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}

      <p className="label mb-1 mt-3">…or a manual fill</p>
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <input
          className="field"
          type="datetime-local"
          value={ts}
          onChange={(event) => setTs(event.target.value)}
        />
        <input
          className="field"
          inputMode="decimal"
          placeholder="quantity"
          value={quantity}
          onChange={(event) => setQuantity(event.target.value)}
        />
        <input
          className="field"
          inputMode="decimal"
          placeholder="price"
          value={price}
          onChange={(event) => setPrice(event.target.value)}
        />
        <input
          className="field"
          inputMode="decimal"
          placeholder="fee (EUR)"
          value={fee}
          onChange={(event) => setFee(event.target.value)}
        />
      </div>

      {action.error !== null && <p className="mt-2 text-neg">{action.error}</p>}
      <button
        className="btn-accent mt-3"
        disabled={action.busy || (picked.length === 0 && !manualReady)}
        onClick={() =>
          void action.run(async () => {
            const payload: FillsIn = {
              fill_ids: picked,
              manual: manualReady
                ? {
                    ts: new Date(ts).toISOString(),
                    quantity: dec(quantity),
                    price: dec(price),
                    fee_eur: dec(fee) || "0",
                  }
                : null,
            };
            await post<Trade>(`/trades/${trade.id}/${endpoint}`, payload);
            onDone();
          })
        }
      >
        {endpoint === "open" ? "Open trade" : "Close trade"}
      </button>
    </div>
  );
}

function ReviewPanel({ trade, onDone }: { trade: Trade; onDone: () => void }) {
  const [adherence, setAdherence] = useState(
    trade.adherence === null ? "" : String(trade.adherence),
  );
  const [mistake, setMistake] = useState(trade.mistake ?? "");
  const [emotion, setEmotion] = useState(trade.emotion_post ?? "");
  const [lesson, setLesson] = useState(trade.note_post ?? "");
  const action = useAction();

  return (
    <div className="mt-3 grid gap-2 rounded border border-line bg-surface2 p-3">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <label className="block">
          <span className="label">Followed the plan</span>
          <select
            className="field"
            value={adherence}
            onChange={(event) => setAdherence(event.target.value)}
          >
            <option value="">— not graded —</option>
            <option value="true">yes</option>
            <option value="false">no</option>
          </select>
        </label>
        <label className="block">
          <span className="label">Mistake</span>
          <select
            className="field"
            value={mistake}
            onChange={(event) => setMistake(event.target.value)}
          >
            <option value="">— none —</option>
            {MISTAKES.map((one) => (
              <option key={one} value={one}>
                {one}
              </option>
            ))}
          </select>
        </label>
        <label className="block lg:col-span-2">
          <span className="label">Emotion after</span>
          <input
            className="field"
            value={emotion}
            onChange={(event) => setEmotion(event.target.value)}
          />
        </label>
      </div>
      <label className="block">
        <span className="label">Lesson</span>
        <textarea
          className="field h-20"
          value={lesson}
          onChange={(event) => setLesson(event.target.value)}
        />
      </label>
      {action.error !== null && <p className="text-neg">{action.error}</p>}
      <div>
        <button
          className="btn-accent"
          disabled={action.busy}
          onClick={() =>
            void action.run(async () => {
              const payload: ReviewIn = {
                adherence: adherence === "" ? null : adherence === "true",
                mistake: mistake === "" ? null : (mistake as Mistake),
                emotion_post: emotion.trim() === "" ? null : emotion.trim(),
                note_post: lesson.trim() === "" ? null : lesson.trim(),
              };
              await post<Trade>(`/trades/${trade.id}/review`, payload);
              onDone();
            })
          }
        >
          Save review
        </button>
      </div>
    </div>
  );
}

function ScreenshotPanel({ trade, onDone }: { trade: Trade; onDone: () => void }) {
  const [files, setFiles] = useState<FileList | null>(null);
  const action = useAction();

  return (
    <div className="mt-3 rounded border border-line bg-surface2 p-3">
      {trade.screenshots.length === 0 ? (
        <p className="text-muted">No screenshots yet.</p>
      ) : (
        <ul className="mb-2">
          {trade.screenshots.map((path) => (
            <li key={path} className="text-muted">
              {path}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="field w-auto"
          type="file"
          multiple
          accept=".png,.jpg,.jpeg,.webp"
          onChange={(event) => setFiles(event.target.files)}
        />
        <button
          className="btn"
          disabled={action.busy || files === null || files.length === 0}
          onClick={() =>
            void action.run(async () => {
              if (files === null) return;
              const form = new FormData();
              for (const file of Array.from(files)) form.append("files", file);
              await postForm<Trade>(`/trades/${trade.id}/screenshots`, form);
              setFiles(null);
              onDone();
            })
          }
        >
          Upload
        </button>
      </div>
      {action.error !== null && <p className="mt-2 text-neg">{action.error}</p>}
    </div>
  );
}

/** One trade in a journal list: the plan, what came of it, and every action
 * its current status allows.
 */
export function TradeCard({
  trade,
  symbol,
  accountName,
  onChanged,
  onSelect,
  selected = false,
}: {
  trade: Trade;
  symbol: string;
  accountName: string;
  onChanged: () => void;
  onSelect?: (trade: Trade) => void;
  selected?: boolean;
}) {
  const [panel, setPanel] = useState<Panel>(null);
  const action = useAction();
  const toggle = (next: Panel) => setPanel(panel === next ? null : next);
  const done = () => {
    setPanel(null);
    onChanged();
  };

  return (
    <article
      className={`rounded border p-3 ${selected ? "border-accent" : "border-line"} bg-surface`}
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <span className="font-medium">{symbol}</span>
          <span className="text-muted">{trade.direction}</span>
          <span className={STATUS_TONE[trade.status]}>{trade.status}</span>
          {accountName !== "" && <span className="text-muted">· {accountName}</span>}
          {trade.external_ref !== null && (
            <span className="text-muted">· {trade.external_ref}</span>
          )}
        </div>
        <span className={signClass(trade.r_multiple)}>{r(trade.r_multiple)}</span>
      </header>

      <div className="mt-2 grid grid-cols-3 gap-3 lg:grid-cols-6">
        <Value label="Entry">{num(trade.planned_entry, 4)}</Value>
        <Value label="Stop">{num(trade.planned_stop, 4)}</Value>
        <Value label="Target">{num(trade.planned_target, 4)}</Value>
        <Value label="Risk">
          <MoneyCell value={trade.risk_eur} />
        </Value>
        <Value label="Planned qty">{num(trade.planned_qty, 8)}</Value>
        <Value label="Result">
          <MoneyCell value={trade.result_eur} sign />
        </Value>
      </div>

      {trade.status !== "planned" && (
        <div className="mt-2 grid grid-cols-3 gap-3 lg:grid-cols-6">
          <Value label="Avg entry">{num(trade.avg_entry, 4)}</Value>
          <Value label="Avg exit">{num(trade.avg_exit, 4)}</Value>
          <Value label="Quantity">{num(trade.quantity, 8)}</Value>
          <Value label="Fees">
            <MoneyCell value={trade.fees_eur} />
          </Value>
          <Value label="Opened">{dateTime(trade.opened_ts)}</Value>
          <Value label="Closed">{dateTime(trade.closed_ts)}</Value>
        </div>
      )}

      {trade.status === "closed" && (
        <div className="mt-2 grid grid-cols-3 gap-3 lg:grid-cols-6">
          <Value label="Adherence">
            {trade.adherence === null ? DASH : trade.adherence ? "yes" : "no"}
          </Value>
          <Value label="Mistake">{trade.mistake ?? DASH}</Value>
          <Value label="Emotion">{trade.emotion_post ?? DASH}</Value>
        </div>
      )}

      {trade.note_pre !== null && (
        <details className="mt-2">
          <summary className="cursor-pointer text-muted">Plan notes</summary>
          <pre className="whitespace-pre-wrap font-sans">{trade.note_pre}</pre>
        </details>
      )}
      {trade.note_post !== null && (
        <details className="mt-1">
          <summary className="cursor-pointer text-muted">Review notes</summary>
          <pre className="whitespace-pre-wrap font-sans">{trade.note_post}</pre>
        </details>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {trade.status === "planned" && (
          <>
            <button className="btn" onClick={() => toggle("fills")}>
              Open…
            </button>
            <button
              className="btn"
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  await post<Trade>(`/trades/${trade.id}/cancel`);
                  onChanged();
                })
              }
            >
              Cancel
            </button>
          </>
        )}
        {trade.status === "open" && (
          <button className="btn" onClick={() => toggle("fills")}>
            Close…
          </button>
        )}
        {trade.status === "closed" && (
          <button className="btn" onClick={() => toggle("review")}>
            Review…
          </button>
        )}
        {(trade.status === "planned" || trade.status === "cancelled") && (
          <button
            className="btn"
            disabled={action.busy}
            onClick={() => {
              if (!window.confirm(`Delete trade #${trade.id}?`)) return;
              void action.run(async () => {
                await del(`/trades/${trade.id}`);
                onChanged();
              });
            }}
          >
            Delete
          </button>
        )}
        <button className="btn" onClick={() => toggle("screenshots")}>
          Screenshots ({trade.screenshots.length})
        </button>
        {onSelect !== undefined && (
          <button className="btn" onClick={() => onSelect(trade)}>
            Details
          </button>
        )}
      </div>

      {action.error !== null && <p className="mt-2 text-neg">{action.error}</p>}
      {panel === "fills" && <FillPanel trade={trade} onDone={done} />}
      {panel === "review" && <ReviewPanel trade={trade} onDone={done} />}
      {panel === "screenshots" && <ScreenshotPanel trade={trade} onDone={onChanged} />}
    </article>
  );
}
