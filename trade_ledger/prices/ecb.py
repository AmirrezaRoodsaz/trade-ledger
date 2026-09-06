"""ECB daily reference exchange rates (`EXR` dataflow, SDMX CSV)."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

import httpx

_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.{ccy}.EUR.SP00.A"


def fetch_rates(
    ccy: str, start: date, end: date, client: httpx.Client | None = None
) -> list[tuple[date, Decimal]]:
    """Daily EUR reference rates for `ccy` between `start` and `end` (inclusive).

    ECB publishes units of `ccy` per 1 EUR (`OBS_VALUE`); we invert to
    `rate_to_eur` (EUR per 1 unit of `ccy`) to match `FxRate.rate_to_eur`.
    Returns `[(date, rate_to_eur), ...]`, oldest first.
    """
    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = client.get(
            _URL.format(ccy=ccy),
            params={
                "format": "csvdata",
                "startPeriod": start.isoformat(),
                "endPeriod": end.isoformat(),
            },
        )
    finally:
        if owns_client:
            client.close()
    response.raise_for_status()
    return _parse_csv(response.text)


def _parse_csv(text: str) -> list[tuple[date, Decimal]]:
    rates: list[tuple[date, Decimal]] = []
    for row in csv.DictReader(io.StringIO(text)):
        period, value = row.get("TIME_PERIOD"), row.get("OBS_VALUE")
        if not period or not value:
            continue
        rates.append((date.fromisoformat(period), Decimal(1) / Decimal(value)))
    rates.sort(key=lambda r: r[0])
    return rates
