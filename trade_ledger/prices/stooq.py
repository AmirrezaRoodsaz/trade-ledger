"""Stooq daily OHLC CSV (native quote currency — no FX applied here)."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

import httpx

_URL = "https://stooq.com/q/d/l/"


def fetch_ohlc(
    price_symbol: str, client: httpx.Client | None = None
) -> list[tuple[date, Decimal, Decimal, Decimal, Decimal]]:
    """Full daily `(date, open, high, low, close)` history for `price_symbol`.

    Stooq's CSV endpoint takes no date range — it always returns full
    history; callers filter to their window themselves.
    """
    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = client.get(_URL, params={"s": price_symbol, "i": "d"})
    finally:
        if owns_client:
            client.close()
    response.raise_for_status()
    return _parse_csv(response.text)


def _parse_csv(text: str) -> list[tuple[date, Decimal, Decimal, Decimal, Decimal]]:
    candles: list[tuple[date, Decimal, Decimal, Decimal, Decimal]] = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            candles.append(
                (
                    date.fromisoformat(row["Date"]),
                    Decimal(row["Open"]),
                    Decimal(row["High"]),
                    Decimal(row["Low"]),
                    Decimal(row["Close"]),
                )
            )
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue  # header repeats / "N/D" no-data rows on some symbols
    return candles
