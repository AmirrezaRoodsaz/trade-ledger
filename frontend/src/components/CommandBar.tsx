import { useState } from "react";
import { issueCommand, type CommandKind } from "../api/bots";
import { ApiError, errorMessage } from "../api/client";

/** The five operator actions, each with the confirmation the API insists on:
 * resume needs a typed reason, emergency flat needs the slug typed out. A
 * second command of the same kind comes back as 409 — shown as "already
 * pending" rather than as an error the operator is meant to retry. */
export function CommandBar({
  slug,
  dryRun,
  onIssued,
}: {
  slug: string;
  dryRun: boolean;
  onIssued: () => void;
}) {
  const [busy, setBusy] = useState<CommandKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");

  async function send(kind: CommandKind, body: { reason?: string; confirm?: string } = {}) {
    setBusy(kind);
    setError(null);
    setDone(null);
    try {
      await issueCommand(slug, kind, body);
      setDone(`${kind} queued — the bot picks it up at its next config pull.`);
      setReason("");
      setConfirm("");
      onIssued();
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError && caught.status === 409
          ? `A ${kind} command is already pending.`
          : errorMessage(caught),
      );
    } finally {
      setBusy(null);
    }
  }

  const disabled = busy !== null;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <button className="btn" disabled={disabled} onClick={() => void send("pause")}>
          Pause entries
        </button>
        <button className="btn" disabled={disabled} onClick={() => void send("run_now")}>
          Run now
        </button>
        <button
          className="btn"
          disabled={disabled}
          onClick={() => void send(dryRun ? "dry_run_off" : "dry_run_on")}
        >
          {dryRun ? "Dry-run off" : "Dry-run on"}
        </button>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="label">Resume reason</span>
          <input
            className="field w-72"
            placeholder="why entries may start again"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <button
          className="btn"
          disabled={disabled || reason.trim() === ""}
          onClick={() => void send("resume", { reason: reason.trim() })}
        >
          Resume
        </button>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="label">Emergency flat — type {slug}</span>
          <input
            className="field w-72"
            placeholder={slug}
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </label>
        <button
          className="btn border-neg text-neg"
          disabled={disabled || confirm !== slug}
          onClick={() => void send("flat", { confirm })}
        >
          Close everything
        </button>
      </div>

      {error !== null && <p className="text-neg">{error}</p>}
      {error === null && done !== null && <p className="text-pos">{done}</p>}
    </div>
  );
}
