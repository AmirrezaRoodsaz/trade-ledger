import { useEffect, useState } from "react";
import { setDrill, type Drill } from "../api/bots";
import { useAction } from "../api/client";
import { DASH, dateTime } from "../fmt";

/** The five drills of the spec, in the order they are meant to be done. */
const LABELS: Record<string, { title: string; hint: string }> = {
  capability_test: {
    title: "Capability test on the account",
    hint: "The account can place, amend and cancel what the strategy needs.",
  },
  dry_run: { title: "Dry run", hint: "A full run with dry_run on, ending in a planned trade." },
  self_check: {
    title: "Self-check against the backtest",
    hint: "The bot's signals on known bars match the backtest's.",
  },
  recovery_drill: {
    title: "Recovery drill",
    hint: "Kill the process mid-run: it comes back flat and reconciled.",
  },
  scheduling: {
    title: "Scheduling with heartbeat",
    hint: "It runs on its own schedule and the heartbeat arrives in time.",
  },
};

/** Checkbox and note per drill, saved the moment either changes. The note is
 * written on blur — one PUT per edit, not per keystroke.
 */
export function DrillChecklist({
  slug,
  drills,
  onChanged,
}: {
  slug: string;
  drills: Drill[];
  onChanged: () => void;
}) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  const action = useAction();

  useEffect(() => {
    setNotes(Object.fromEntries(drills.map((drill) => [drill.key, drill.note ?? ""])));
  }, [drills]);

  const save = (drill: Drill, done: boolean, note: string) =>
    void action.run(async () => {
      await setDrill(slug, drill.key, done, note.trim() === "" ? null : note.trim());
      onChanged();
    });

  return (
    <>
      <ul className="grid gap-3">
        {drills.map((drill) => {
          const label = LABELS[drill.key];
          const note = notes[drill.key] ?? "";
          return (
            <li key={drill.key} className="border-b border-line pb-3 last:border-0 last:pb-0">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={drill.done}
                  disabled={action.busy}
                  onChange={(event) => save(drill, event.target.checked, note)}
                />
                <span>
                  <span className={drill.done ? "text-pos" : ""}>
                    {label?.title ?? drill.key}
                  </span>
                  <span className="block text-[11px] text-muted">
                    {label?.hint ?? ""} · done {drill.done ? dateTime(drill.done_ts) : DASH}
                  </span>
                </span>
              </label>
              <input
                className="field mt-2"
                placeholder="note — what was tested, what came out"
                value={note}
                disabled={action.busy}
                onChange={(event) =>
                  setNotes((previous) => ({ ...previous, [drill.key]: event.target.value }))
                }
                onBlur={() => {
                  if (note.trim() !== (drill.note ?? "").trim()) save(drill, drill.done, note);
                }}
              />
            </li>
          );
        })}
      </ul>
      {action.error !== null && <p className="mt-2 text-neg">{action.error}</p>}
    </>
  );
}
