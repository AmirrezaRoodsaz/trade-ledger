import type { Readiness } from "../api/bots";
import { num, toNumber } from "../fmt";

/** The four stages of `bots/readiness.py`, in the order they are worked. */
const LABELS: Record<string, string> = {
  backtest: "Backtest",
  drills: "Drills",
  incubation: "Incubation",
  live: "Real money",
};

/** 0..100, whatever the backend says — a bar can only draw what fits. */
export const clampPercent = (value: string | number | null): number => {
  const parsed = toNumber(value);
  return parsed === null ? 0 : Math.min(100, Math.max(0, parsed));
};

function Bar({ share, tone }: { share: number; tone: string }) {
  return (
    <div className="h-1.5 w-full rounded bg-surface2">
      <div className={`h-1.5 rounded ${tone}`} style={{ width: `${share.toFixed(1)}%` }} />
    </div>
  );
}

/** Readiness with its arithmetic on show: every stage's weight, how far it
 * got, and the factor that halves it while its gate fails.
 */
export function ReadinessBar({ readiness }: { readiness: Readiness }) {
  const percent = clampPercent(readiness.percent);

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="label">Readiness</span>
        <span className="text-[15px]">{num(readiness.percent, 1)} %</span>
      </div>
      <div
        className="mt-1"
        role="progressbar"
        aria-label="Readiness"
        aria-valuenow={Number(percent.toFixed(1))}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <Bar share={percent} tone="bg-accent" />
      </div>

      <ul className="mt-4 grid gap-3">
        {readiness.stages.map((stage) => {
          const weight = toNumber(stage.weight) ?? 0;
          const progress = toNumber(stage.progress) ?? 0;
          const factor = toNumber(stage.factor) ?? 1;
          const capped = factor < 1;
          return (
            <li key={stage.key}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span>
                  {LABELS[stage.key] ?? stage.key}{" "}
                  <span className="text-muted">· weight {num(weight, 0)} %</span>
                </span>
                <span className={capped ? "text-live" : "text-muted"}>
                  {num(weight * progress * factor, 1)} % of {num(weight, 0)} %
                </span>
              </div>
              <div className="mt-1">
                <Bar
                  share={progress * factor * 100}
                  tone={capped ? "bg-live" : progress >= 1 ? "bg-pos" : "bg-accent"}
                />
              </div>
              <p className="mt-1 text-[11px] text-muted">
                {stage.detail} · progress {num(progress, 2)} · factor {num(factor, 2)}
                {capped && <span className="text-live"> · gate failing caps this stage</span>}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
