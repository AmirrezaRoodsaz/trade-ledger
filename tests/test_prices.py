from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from trade_ledger.enums import TxSource, TxType
from trade_ledger.models import FxRate, Price, Transaction
from trade_ledger.prices import bitstamp, ecb, service, stooq


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- ecb -----------------------------------------------------------------


def test_ecb_fetch_rates_parses_csv_and_inverts_obs_value():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["startPeriod"] == "2026-01-01"
        assert request.url.params["endPeriod"] == "2026-01-02"
        csv_text = "TIME_PERIOD,OBS_VALUE\n2026-01-02,1.1000\n2026-01-01,1.0800\n"
        return httpx.Response(200, text=csv_text)

    rates = ecb.fetch_rates(
        "USD", date(2026, 1, 1), date(2026, 1, 2), client=_mock_client(handler)
    )

    assert rates == [
        (date(2026, 1, 1), Decimal(1) / Decimal("1.0800")),
        (date(2026, 1, 2), Decimal(1) / Decimal("1.1000")),
    ]


# --- bitstamp --------------------------------------------------------------


def test_bitstamp_fetch_ohlc_paginates_until_end_reached():
    start = date(2026, 1, 1)
    end = date(2026, 1, 3)
    ts0 = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp())
    ts1 = ts0 + 86400
    ts2 = ts0 + 2 * 86400
    calls: list[int] = []

    def row(ts: int, o: str, h: str, low: str, c: str) -> dict:
        return {"timestamp": str(ts), "open": o, "high": h, "low": low, "close": c, "volume": "1"}

    def handler(request: httpx.Request) -> httpx.Response:
        start_param = int(request.url.params["start"])
        calls.append(start_param)
        if start_param == ts0:
            rows = [row(ts0, "100", "110", "90", "105"), row(ts1, "105", "115", "95", "110")]
        elif start_param == ts2:
            rows = [row(ts2, "110", "120", "100", "115")]
        else:
            rows = []
        return httpx.Response(200, json={"data": {"pair": "btceur", "ohlc": rows}})

    candles = bitstamp.fetch_ohlc("btc", start, end, client=_mock_client(handler))

    assert calls == [ts0, ts2]  # two pages
    assert [c[0] for c in candles] == [start, date(2026, 1, 2), end]
    assert candles[0] == (start, Decimal(100), Decimal(110), Decimal(90), Decimal(105))


# --- stooq -------------------------------------------------------------


def test_stooq_fetch_ohlc_parses_csv_and_skips_bad_rows():
    csv_text = (
        "Date,Open,High,Low,Close,Volume\n"
        "2026-01-01,100,105,95,102,1000\n"
        "2026-01-02,102,108,100,106,1200\n"
        "N/D,-,-,-,-,-\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["s"] == "aapl.us"
        return httpx.Response(200, text=csv_text)

    candles = stooq.fetch_ohlc("aapl.us", client=_mock_client(handler))

    assert len(candles) == 2
    assert candles[0] == (
        date(2026, 1, 1),
        Decimal(100),
        Decimal(105),
        Decimal(95),
        Decimal(102),
    )


# --- service.get_fx --------------------------------------------------------


def test_get_fx_eur_is_always_one(session):
    assert service.get_fx(session, "EUR", date(2026, 1, 1)) == Decimal(1)


def test_get_fx_maps_usdt_and_usdc_to_usd(session):
    session.add(FxRate(ccy="USD", date=date(2026, 1, 5), rate_to_eur=Decimal("0.9200")))
    session.commit()

    assert service.get_fx(session, "USDT", date(2026, 1, 5)) == Decimal("0.9200")
    assert service.get_fx(session, "USDC", date(2026, 1, 5)) == Decimal("0.9200")


def test_get_fx_weekend_falls_back_to_last_available_rate(session):
    friday = date(2026, 1, 2)
    saturday = date(2026, 1, 3)
    session.add(FxRate(ccy="USD", date=friday, rate_to_eur=Decimal("0.9100")))
    session.commit()

    assert service.get_fx(session, "USD", saturday) == Decimal("0.9100")


def test_get_fx_raises_price_missing_beyond_7_day_gap(session):
    with pytest.raises(service.PriceMissing):
        service.get_fx(session, "USD", date(2026, 1, 10))

    # 9 days back is outside the 7-day lookback window
    session.add(FxRate(ccy="USD", date=date(2026, 1, 1), rate_to_eur=Decimal("0.9")))
    session.commit()
    with pytest.raises(service.PriceMissing):
        service.get_fx(session, "USD", date(2026, 1, 10))


# --- service.fill_pending_eur -----------------------------------------------


def test_fill_pending_eur_converts_usdt_trade_using_usd_rate(session, account_factory, instrument_factory):
    account = account_factory()
    instrument = instrument_factory(symbol="ETH")
    ts = datetime(2026, 1, 5, tzinfo=UTC)
    session.add(FxRate(ccy="USD", date=ts.date(), rate_to_eur=Decimal("0.9200")))
    tx = Transaction(
        account_id=account.id,
        ts=ts,
        type=TxType.BUY,
        instrument_id=instrument.id,
        quantity=Decimal(2),
        price=Decimal(100),
        price_ccy="USDT",
        fee=Decimal(0),
        fee_ccy=None,
        amount_eur=Decimal(0),
        fee_eur=Decimal(0),
        fx_source="pending",
        source=TxSource.API,
    )
    session.add(tx)
    session.commit()

    updated = service.fill_pending_eur(session)

    assert updated == 1
    session.refresh(tx)
    assert tx.fx_source == "ecb"
    assert tx.fx_rate == Decimal("0.9200")
    assert tx.amount_eur == Decimal(2) * Decimal(100) * Decimal("0.9200")
    assert tx.fee_eur == Decimal(0)


def test_fill_pending_eur_leaves_row_pending_without_exception_when_rate_missing(
    session, account_factory, instrument_factory
):
    account = account_factory()
    instrument = instrument_factory(symbol="ETH")
    tx = Transaction(
        account_id=account.id,
        ts=datetime(2026, 1, 5, tzinfo=UTC),
        type=TxType.BUY,
        instrument_id=instrument.id,
        quantity=Decimal(2),
        price=Decimal(100),
        price_ccy="USDT",
        fee=Decimal(0),
        amount_eur=Decimal(0),
        fee_eur=Decimal(0),
        fx_source="pending",
        source=TxSource.API,
    )
    session.add(tx)
    session.commit()

    updated = service.fill_pending_eur(session)

    assert updated == 0
    session.refresh(tx)
    assert tx.fx_source == "pending"
    assert tx.amount_eur == Decimal(0)


# --- service.get_close / get_close_eur / candles ----------------------------


def test_get_close_eur_converts_native_close(session, instrument_factory):
    instrument = instrument_factory(symbol="AAPL", asset_class="stock", quote_ccy="USD")
    session.add(
        Price(
            instrument_id=instrument.id,
            date=date(2026, 1, 5),
            close=Decimal(150),
            ccy="USD",
            source="stooq",
        )
    )
    session.add(FxRate(ccy="USD", date=date(2026, 1, 5), rate_to_eur=Decimal("0.9")))
    session.commit()

    assert service.get_close(session, instrument, date(2026, 1, 5)) == Decimal(150)
    assert service.get_close_eur(session, instrument, date(2026, 1, 5)) == Decimal(135)
    assert service.get_close_eur(session, instrument, date(2026, 1, 6)) is None


def test_candles_returns_rows_in_range_ordered_by_date(session, instrument_factory):
    instrument = instrument_factory()
    for day, close in [(1, "10"), (2, "11"), (3, "12")]:
        session.add(
            Price(
                instrument_id=instrument.id,
                date=date(2026, 1, day),
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                ccy="EUR",
                source="bitstamp",
            )
        )
    session.commit()

    result = service.candles(session, instrument, date(2026, 1, 2), date(2026, 1, 3))

    assert [c.date for c in result] == [date(2026, 1, 2), date(2026, 1, 3)]
    assert result[0].close == Decimal(11)


# --- service.ensure_prices / ensure_fx --------------------------------------


def test_ensure_prices_bitstamp_caches_rows(session, instrument_factory):
    instrument = instrument_factory(
        symbol="BTC", price_source="bitstamp", price_symbol="btc", quote_ccy="EUR"
    )
    ts0 = int(
        datetime.combine(date(2026, 1, 1), datetime.min.time(), tzinfo=UTC).timestamp()
    )

    def handler(request: httpx.Request) -> httpx.Response:
        rows = [
            {
                "timestamp": str(ts0),
                "open": "100",
                "high": "110",
                "low": "90",
                "close": "105",
                "volume": "1",
            }
        ]
        return httpx.Response(200, json={"data": {"pair": "btceur", "ohlc": rows}})

    written = service.ensure_prices(
        session, instrument, date(2026, 1, 1), date(2026, 1, 1), client=_mock_client(handler)
    )

    assert written == 1
    row = session.execute(
        select(Price).where(Price.instrument_id == instrument.id)
    ).scalar_one()
    assert row.close == Decimal(105)
    assert row.ccy == "EUR"
    assert row.source == "bitstamp"


def test_ensure_fx_eur_is_a_noop(session):
    assert service.ensure_fx(session, "EUR", date(2026, 1, 1), date(2026, 1, 2)) == 0


def test_ensure_fx_stores_rates(session):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="TIME_PERIOD,OBS_VALUE\n2026-01-01,1.0800\n")

    written = service.ensure_fx(
        session, "USD", date(2026, 1, 1), date(2026, 1, 1), client=_mock_client(handler)
    )

    assert written == 1
    assert service.get_fx(session, "USD", date(2026, 1, 1)) == Decimal(1) / Decimal("1.0800")


# --- API routes --------------------------------------------------------


def test_put_and_get_price_manual(client, instrument_factory):
    instrument = instrument_factory(symbol="AAPL", asset_class="stock", quote_ccy="USD")

    resp = client.put(
        f"/api/prices/{instrument.id}",
        json={"date": "2026-01-02", "close": "150.25", "ccy": "USD"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["close"] == "150.25"

    resp = client.get(
        f"/api/prices/{instrument.id}", params={"from": "2026-01-01", "to": "2026-01-05"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["close"] == "150.25"
    assert body[0]["date"] == "2026-01-02"


def test_get_prices_missing_instrument_404(client):
    resp = client.get("/api/prices/999", params={"from": "2026-01-01", "to": "2026-01-05"})
    assert resp.status_code == 404


def test_refresh_prices_missing_instrument_404(client):
    resp = client.post(
        "/api/prices/refresh",
        json={"instrument_id": 999, "start": "2026-01-01", "end": "2026-01-02"},
    )
    assert resp.status_code == 404
