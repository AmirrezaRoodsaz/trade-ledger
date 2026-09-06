import type { AnlageLine } from "../api/tax";
import { eur, toNumber } from "../fmt";
import { DataTable } from "./DataTable";
import { EmptyState } from "./EmptyState";

/** Group the lines by form, first-seen order (Anlage SO, KAP, KAP-INV). */
function byForm(lines: AnlageLine[]): [string, AnlageLine[]][] {
  const groups = new Map<string, AnlageLine[]>();
  for (const line of lines) {
    const bucket = groups.get(line.form);
    if (bucket) bucket.push(line);
    else groups.set(line.form, [line]);
  }
  return [...groups];
}

/** A money line renders as money; a checkbox, a period or a caption as it came. */
function lineValue(value: string) {
  const parsed = toNumber(value);
  return parsed === null ? value : eur(value);
}

export function AnlagePanel({ lines, note }: { lines: AnlageLine[]; note: string | null }) {
  return (
    <div>
      <p className="mb-3 rounded border border-line bg-surface2 px-3 py-2">
        <span className="font-medium">Vorschlag, nicht amtlich</span> — line numbers and values are a
        suggestion for filling the forms by hand.
        {note !== null && <span className="text-muted"> {note}</span>}
      </p>
      {lines.length === 0 ? (
        <EmptyState>No form mapping for this year.</EmptyState>
      ) : (
        byForm(lines).map(([form, formLines]) => (
          <div key={form} className="mb-5 last:mb-0">
            <h3 className="label mb-1">{form}</h3>
            <DataTable
              rows={formLines}
              rowKey={(line) => `${line.form}-${line.zeile}`}
              columns={[
                { key: "zeile", header: "Zeile", align: "right", render: (line) => line.zeile },
                { key: "label", header: "Bezeichnung", render: (line) => line.label },
                {
                  key: "value",
                  header: "Vorschlag",
                  align: "right",
                  render: (line) => lineValue(line.value),
                },
                {
                  key: "note",
                  header: "Hinweis",
                  render: (line) => <span className="text-muted">{line.note}</span>,
                },
              ]}
            />
          </div>
        ))
      )}
    </div>
  );
}
