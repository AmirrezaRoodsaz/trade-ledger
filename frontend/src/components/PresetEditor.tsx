import { useEffect, useState } from "react";
import {
  STRATEGY_DEFAULTS,
  TIMEFRAMES,
  type PresetVersionIn,
  type Strategy,
} from "../api/presets";

function defaultParams(strategy: string): string {
  const preset = STRATEGY_DEFAULTS[strategy as Strategy];
  return JSON.stringify(preset ?? {}, null, 2);
}

/** "BTC/EUR, ETH/EUR " -> ["BTC/EUR", "ETH/EUR"]; a stray comma adds no empty pair. */
function splitPairs(text: string): string[] {
  return text
    .split(",")
    .map((pair) => pair.trim())
    .filter((pair) => pair !== "");
}

/** Blank stays null so the backend keeps its own default. */
function orNull(value: string): string | null {
  return value.trim() === "" ? null : value.trim();
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

/** The "new version" form: JSON params for the strategy plus the risk envelope.
 * Params are validated on every keystroke — a bad JSON blob never reaches the API.
 */
export function PresetEditor({
  strategy,
  busy,
  onSubmit,
}: {
  strategy: string;
  busy: boolean;
  onSubmit: (payload: PresetVersionIn) => void;
}) {
  const [params, setParams] = useState(() => defaultParams(strategy));
  const [timeframe, setTimeframe] = useState<string>("4h");
  const [pairs, setPairs] = useState("");
  const [riskPct, setRiskPct] = useState("");
  const [maxPositionPct, setMaxPositionPct] = useState("");
  const [leverageCap, setLeverageCap] = useState("1");
  const [note, setNote] = useState("");

  // A different preset means different defaults — refill rather than carry them over.
  useEffect(() => setParams(defaultParams(strategy)), [strategy]);

  let parsed: Record<string, unknown> | null = null;
  let jsonError: string | null = null;
  try {
    const value: unknown = JSON.parse(params);
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      jsonError = "Params must be a JSON object.";
    } else {
      parsed = value as Record<string, unknown>;
    }
  } catch (caught) {
    jsonError = caught instanceof Error ? caught.message : String(caught);
  }

  return (
    <form
      className="mt-4 grid gap-3 rounded border border-line bg-surface2 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (parsed === null) return;
        onSubmit({
          params: parsed,
          timeframe,
          pairs: splitPairs(pairs),
          risk_pct: orNull(riskPct),
          max_position_pct: orNull(maxPositionPct),
          leverage_cap: orNull(leverageCap) ?? "1",
          note: orNull(note),
        });
      }}
    >
      <Field label={`Params (JSON) — ${strategy}`}>
        <textarea
          className="field h-40 font-mono"
          spellCheck={false}
          value={params}
          onChange={(event) => setParams(event.target.value)}
        />
      </Field>
      {jsonError !== null && <p className="text-neg">Invalid JSON: {jsonError}</p>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Field label="Timeframe">
          <select
            className="field"
            value={timeframe}
            onChange={(event) => setTimeframe(event.target.value)}
          >
            {TIMEFRAMES.map((one) => (
              <option key={one} value={one}>
                {one}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Risk %">
          <input
            className="field"
            type="number"
            step="0.1"
            min="0"
            placeholder="1"
            value={riskPct}
            onChange={(event) => setRiskPct(event.target.value)}
          />
        </Field>
        <Field label="Max position %">
          <input
            className="field"
            type="number"
            step="1"
            min="0"
            placeholder="25"
            value={maxPositionPct}
            onChange={(event) => setMaxPositionPct(event.target.value)}
          />
        </Field>
        <Field label="Leverage cap">
          <input
            className="field"
            type="number"
            step="0.5"
            min="1"
            value={leverageCap}
            onChange={(event) => setLeverageCap(event.target.value)}
          />
        </Field>
      </div>

      <Field label="Pairs (comma separated)">
        <input
          className="field"
          placeholder="BTC/EUR, ETH/EUR"
          value={pairs}
          onChange={(event) => setPairs(event.target.value)}
        />
      </Field>
      <Field label="Note">
        <input
          className="field"
          placeholder="why this version exists"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </Field>

      <div>
        <button className="btn-accent" disabled={busy || parsed === null}>
          Add version
        </button>
      </div>
    </form>
  );
}
