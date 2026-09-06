"""Bitstamp daily OHLC candles, always quoted against EUR."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

_URL = "https://www.bitstamp.net/api/v2/ohlc/{pair}/"
_STEP = 86400  # one day, in seconds
_LIMIT = 1000  # Bitstamp's max rows per request


def fetch_ohlc(
    symbol: str, start: date, end: date, client: httpx.Client | None = None
) -> list[tuple[date, Decimal, Decimal, Decimal, Decimal]]:
    """Daily `(date, open, high, low, close)` for `symbol` vs EUR, `start`..`end`
    inclusive. Paginates: each request's last candle seeds the next request's
    `start`, until a request's last candle reaches `end` or returns nothing new.
    """
    pair = f"{symbol.lower()}eur"
    owns_client = client is None
    client = client or httpx.Client()
    start_ts = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp())
    end_ts = int(datetime.combine(end, datetime.min.time(), tzinfo=UTC).timestamp())
    candles: dict[date, tuple[date, Decimal, Decimal, Decimal, Decimal]] = {}
    cursor = start_ts
    try:
        while cursor <= end_ts:
            response = client.get(
                _URL.format(pair=pair), params={"step": _STEP, "limit": _LIMIT, "start": cursor}
            )
            response.raise_for_status()
            rows = response.json()["data"]["ohlc"]
            if not rows:
                break
            for row in rows:
                d = datetime.fromtimestamp(int(row["timestamp"]), tz=UTC).date()
                candles[d] = (
                    d,
                    Decimal(row["open"]),
                    Decimal(row["high"]),
                    Decimal(row["low"]),
                    Decimal(row["close"]),
                )
            last_ts = int(rows[-1]["timestamp"])
            if last_ts <= cursor:
                break  # no progress — avoid an infinite loop on a flat response
            cursor = last_ts + _STEP
    finally:
        if owns_client:
            client.close()
    return sorted((c for c in candles.values() if start <= c[0] <= end), key=lambda c: c[0])
