import {
  CandlestickSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { get, useApi } from "../api/client";
import type { ChartOut } from "../api/trades";
import { toNumber } from "../fmt";

const HEIGHT = 300;

/** lightweight-charts needs concrete colours, so the palette is read out of
 * the CSS variables the rest of the app themes itself with.
 */
function palette() {
  const style = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    text: read("--text", "#1a1a18"),
    muted: read("--muted", "#6f6f68"),
    border: read("--border", "#e2e1dc"),
    pos: read("--pos", "#0f7b4f"),
    neg: read("--neg", "#c0362c"),
    accent: read("--accent", "#2b5fd9"),
  };
}

const SHAPES: Record<string, SeriesMarker<Time>["shape"]> = {
  entry: "arrowUp",
  exit: "arrowDown",
};

/** Daily candles around one trade, with entry/exit markers and stop/target
 * price lines. `/trades/{id}/chart` answers 409 for a trade that never
 * opened — that reads as "nothing to draw", not as a failure.
 */
export function TradeChart({ tradeId }: { tradeId: number }) {
  const state = useApi(() => get<ChartOut>(`/trades/${tradeId}/chart`), [tradeId]);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = box.current;
    const data = state.data;
    if (element === null || data === null) return;

    const colors = palette();
    const chart = createChart(element, {
      autoSize: true,
      height: HEIGHT,
      layout: {
        background: { color: "transparent" },
        textColor: colors.muted,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: colors.border },
        horzLines: { color: colors.border },
      },
      rightPriceScale: { borderColor: colors.border },
      timeScale: { borderColor: colors.border },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: colors.pos,
      downColor: colors.neg,
      borderUpColor: colors.pos,
      borderDownColor: colors.neg,
      wickUpColor: colors.pos,
      wickDownColor: colors.neg,
    });

    const candles: CandlestickData<Time>[] = [];
    for (const candle of data.candles) {
      const open = toNumber(candle.open);
      const high = toNumber(candle.high);
      const low = toNumber(candle.low);
      const close = toNumber(candle.close);
      // A cached row may hold a close only; a candle without all four is
      // not drawable, so it is left out rather than faked from the close.
      if (open === null || high === null || low === null || close === null) continue;
      candles.push({ time: candle.time as Time, open, high, low, close });
    }
    series.setData(candles);

    const markers: SeriesMarker<Time>[] = [];
    for (const marker of data.markers) {
      const price = toNumber(marker.price);
      if (price === null) continue;
      if (marker.kind === "stop" || marker.kind === "target") {
        series.createPriceLine({
          price,
          color: marker.kind === "stop" ? colors.neg : colors.pos,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: marker.kind,
        });
        continue;
      }
      markers.push({
        time: marker.time as Time,
        position: marker.kind === "exit" ? "aboveBar" : "belowBar",
        shape: SHAPES[marker.kind] ?? "circle",
        color: marker.kind === "exit" ? colors.text : colors.accent,
        text: marker.kind,
      });
    }
    if (markers.length > 0) createSeriesMarkers(series, markers);

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [state.data]);

  if (state.error !== null) return <p className="text-muted">{state.error}</p>;
  if (state.data === null) return <p className="text-muted">{state.loading ? "Loading…" : null}</p>;
  if (state.data.candles.length === 0)
    return <p className="text-muted">No cached candles for these dates.</p>;

  return (
    <div>
      <div ref={box} style={{ height: HEIGHT }} />
      <p className="mt-1 text-[11px] text-muted">Resolution {state.data.resolution}</p>
    </div>
  );
}
