import { useState } from "react";
import { soft, useAction, useApi } from "../api/client";
import { generateWeekly, listReports, reportUrl, type ReportRef } from "../api/reports";
import type { ModeFilter } from "../api/types";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/Layout";
import { dateTime, isoDate, num } from "../fmt";

const MODES: ModeFilter[] = ["live", "paper", "demo", "all"];
const UNAVAILABLE = "Report service not available yet.";

/** Sizes are file sizes, so kB/MB, not the money formatter. */
function fileSize(bytes: number): string {
  if (bytes < 1024) return `${num(bytes, 0)} B`;
  if (bytes < 1024 * 1024) return `${num(bytes / 1024, 1)} kB`;
  return `${num(bytes / 1024 / 1024, 1)} MB`;
}

export function Reports() {
  const [weekEnd, setWeekEnd] = useState(isoDate(new Date()));
  const [mode, setMode] = useState<ModeFilter>("live");
  const [created, setCreated] = useState<ReportRef | null>(null);
  // The router is a separate task: a 404 means "not built yet", not an error.
  const [missing, setMissing] = useState(false);

  const list = useApi(() => soft(listReports()), []);
  const action = useAction();
  const listMissing = !list.loading && list.error === null && list.data === null;

  return (
    <>
      <PageHeader title="Reports" subtitle="Weekly review PDF" />

      <Card title="Generate">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void action.run(async () => {
              const report = await soft(generateWeekly(weekEnd, mode));
              setMissing(report === null);
              setCreated(report);
              if (report !== null) list.reload();
            });
          }}
        >
          <label className="block">
            <span className="label">Week end</span>
            <input
              className="field"
              type="date"
              value={weekEnd}
              onChange={(event) => setWeekEnd(event.target.value)}
              required
            />
          </label>
          <label className="block">
            <span className="label">Mode</span>
            <select
              className="field"
              value={mode}
              onChange={(event) => setMode(event.target.value as ModeFilter)}
            >
              {MODES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <button className="btn-accent" type="submit" disabled={action.busy}>
            {action.busy ? "Generating…" : "Generate"}
          </button>
        </form>

        {action.error !== null && <p className="mt-2 text-neg">{action.error}</p>}
        {missing && <p className="mt-2 text-muted">{UNAVAILABLE}</p>}
        {created !== null && (
          <p className="mt-2">
            <a
              className="text-accent hover:underline"
              href={created.url || reportUrl(created.name)}
              download={created.name}
            >
              {created.name}
            </a>{" "}
            <span className="text-muted">{created.path}</span>
          </p>
        )}
      </Card>

      <Card title="Existing reports" className="mt-4">
        {list.error !== null && <p className="text-neg">{list.error}</p>}
        {list.loading && <p className="text-muted">Loading…</p>}
        {listMissing && <EmptyState>{UNAVAILABLE}</EmptyState>}
        {list.data !== null && (
          <DataTable
            rows={list.data}
            rowKey={(row) => row.name}
            empty="No reports generated yet."
            columns={[
              {
                key: "name",
                header: "File",
                render: (row) => (
                  <a
                    className="text-accent hover:underline"
                    href={reportUrl(row.name)}
                    download={row.name}
                  >
                    {row.name}
                  </a>
                ),
              },
              { key: "created", header: "Created", render: (row) => dateTime(row.created) },
              {
                key: "size",
                header: "Size",
                align: "right",
                render: (row) => fileSize(row.size),
              },
            ]}
          />
        )}
      </Card>
    </>
  );
}
