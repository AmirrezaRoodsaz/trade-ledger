import type { Mode } from "../api/types";

const TONE: Record<Mode, string> = {
  live: "border-live text-live",
  paper: "border-paper text-paper",
  demo: "border-demo text-demo",
};

export function ModeBadge({ mode }: { mode: Mode }) {
  return (
    <span
      className={`rounded border px-1 text-[10px] uppercase leading-4 tracking-wide ${TONE[mode]}`}
    >
      {mode}
    </span>
  );
}
