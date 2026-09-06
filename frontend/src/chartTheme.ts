/** Recharts writes colours into SVG presentation attributes, which do not
 * resolve `var(--x)`. So the palette tokens are read from the document once
 * and re-read when the OS theme flips.
 */

import { useEffect, useState } from "react";

const TOKENS = ["--accent", "--pos", "--neg", "--muted", "--border", "--surface", "--text"] as const;

export type ChartColors = Record<(typeof TOKENS)[number], string>;

function readTokens(): ChartColors {
  const style = getComputedStyle(document.documentElement);
  const entries = TOKENS.map((token) => [token, style.getPropertyValue(token).trim()]);
  return Object.fromEntries(entries) as ChartColors;
}

export function useChartColors(): ChartColors {
  const [colors, setColors] = useState(readTokens);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setColors(readTokens());
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return colors;
}

/** Categorical slices (allocation pies). Deliberately no green and no red —
 * those two are reserved for the sign of a P&L figure. Readable on both the
 * light and the dark surface.
 */
export const CATEGORICAL: readonly [string, ...string[]] = [
  "#2b5fd9",
  "#8a5cd1",
  "#0e8f9e",
  "#c07a1f",
  "#5f7189",
  "#a8578f",
  "#3f7fb5",
  "#8b7a3f",
];

export const sliceColor = (index: number): string =>
  CATEGORICAL[index % CATEGORICAL.length] ?? CATEGORICAL[0];

/** Shared Recharts tooltip chrome — the app's surface, not Recharts' default. */
export function tooltipStyle(colors: ChartColors) {
  return {
    contentStyle: {
      background: colors["--surface"],
      border: `1px solid ${colors["--border"]}`,
      borderRadius: 4,
      color: colors["--text"],
      fontSize: 12,
    },
    labelStyle: { color: colors["--muted"] },
    itemStyle: { color: colors["--text"] },
  };
}
