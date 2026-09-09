import { getRunLog, type BotRun } from "../api/bots";
import { useApi } from "../api/client";
import { Card } from "./Card";
import { DASH, dateTime } from "../fmt";

/** The captured stdout of one run. A run that wrote no log answers 404, which
 * reads here as the plain sentence the API sends, not as a broken page. */
export function RunLog({
  slug,
  run,
  onClose,
}: {
  slug: string;
  run: BotRun;
  onClose: () => void;
}) {
  const log = useApi(() => getRunLog(slug, run.id), [slug, run.id]);

  return (
    <Card
      title={`Run #${run.id} · ${run.status}`}
      right={
        <button className="btn" onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="mb-2 flex flex-wrap gap-x-6 gap-y-1 text-muted">
        <span>
          Started <span className="text-ink">{dateTime(run.started)}</span>
        </span>
        <span>
          Finished <span className="text-ink">{dateTime(run.finished)}</span>
        </span>
        {run.error !== null && <span className="text-neg">{run.error}</span>}
      </div>
      {log.error !== null && <p className="text-neg">{log.error}</p>}
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-surface2 p-2 text-[12px]">
        {log.loading ? "Loading…" : (log.data ?? DASH)}
      </pre>
    </Card>
  );
}
