from __future__ import annotations

from datetime import date
from decimal import Decimal

from trade_ledger.engine.mae_mfe import compute_excursions
from trade_ledger.enums import Direction
from trade_ledger.models import Price, Trade
from trade_ledger.prices.service import Candle


def _candle(day: int, low: str, high: str) -> Candle:
    return Candle(
        date=date(2026, 1, day),
        open=Decimal(low),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(high),
    )


def _trade(direction: str, entry="100", qty="2") -> Trade:
    return Trade(direction=direction, avg_entry=Decimal(entry), quantity=Decimal(qty))


# --- compute_excursions (pure) ----------------------------------------------


def test_long_mae_mfe_from_daily_lows_and_highs():
    trade = _trade(Direction.LONG)
    candles = [_candle(1, "95", "105"), _candle(2, "90", "115"), _candle(3, "100", "130")]

    result = compute_excursions(trade, candles)

    assert result == (Decimal(-20), Decimal(60))


def test_short_mirrors_long_a_rally_hurts_a_drop_helps():
    trade = _trade(Direction.SHORT)
    # Mirrored range: worst case is the high (110), best case is the low (70).
    candles = [_candle(1, "95", "105"), _candle(2, "70", "110"), _candle(3, "80", "100")]

    result = compute_excursions(trade, candles)

    assert result == (Decimal(-20), Decimal(60))


def test_no_candles_returns_none_without_raising():
    trade = _trade(Direction.LONG)

    assert compute_excursions(trade, []) is None


def test_candles_with_no_low_or_high_return_none():
    trade = _trade(Direction.LONG)
    blank = Candle(date=date(2026, 1, 1), open=None, high=None, low=None, close=None)

    assert compute_excursions(trade, [blank]) is None


# --- API: POST /trades/{id}/excursions --------------------------------------


def _open_and_close(
    client,
    account,
    instrument,
    opened="2026-01-02T10:00:00+00:00",
    closed="2026-01-04T10:00:00+00:00",
):
    trade = client.post(
        "/api/trades",
        json={
            "account_id": account.id,
            "instrument_id": instrument.id,
            "direction": "long",
            "planned_entry": "100",
            "planned_stop": "90",
            "planned_target": "140",
            "risk_eur": "20",
            "note_pre": "breakout",
        },
    ).json()
    client.post(
        f"/api/trades/{trade['id']}/open",
        json={"manual": {"ts": opened, "quantity": "2", "price": "100", "fee_eur": "0"}},
    )
    closed = client.post(
        f"/api/trades/{trade['id']}/close",
        json={"manual": {"ts": closed, "quantity": "2", "price": "130", "fee_eur": "0"}},
    ).json()
    return closed


def _seed_prices(session, instrument, rows):
    for day, low, high in rows:
        session.add(
            Price(
                instrument_id=instrument.id,
                date=date(2026, 1, day),
                open=Decimal(low),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal(high),
                ccy="EUR",
                source="manual",
            )
        )
    session.commit()


def test_post_excursions_computes_and_stores(client, session, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    trade = _open_and_close(client, account, instrument)
    _seed_prices(session, instrument, [(2, "95", "105"), (3, "90", "130"), (4, "100", "110")])

    resp = client.post(f"/api/trades/{trade['id']}/excursions")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mae_eur"] == "-20"
    assert body["mfe_eur"] == "60"
    assert body["mae_r"] == "-1"
    assert body["mfe_r"] == "3"
    assert body["resolution"] == "1d"
    assert client.get(f"/api/trades/{trade['id']}").json()["mae_eur"] == "-20"


def test_post_excursions_without_cached_candles_is_404(client, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    trade = _open_and_close(client, account, instrument)

    resp = client.post(f"/api/trades/{trade['id']}/excursions")

    assert resp.status_code == 404


def test_post_excursions_on_a_planned_trade_is_409(client, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    trade = client.post(
        "/api/trades",
        json={
            "account_id": account.id,
            "instrument_id": instrument.id,
            "direction": "long",
            "planned_entry": "100",
            "planned_stop": "90",
            "risk_eur": "20",
            "note_pre": "not opened yet",
        },
    ).json()

    resp = client.post(f"/api/trades/{trade['id']}/excursions")

    assert resp.status_code == 409


# --- API: POST /trades/excursions/recompute ---------------------------------


def test_recompute_updates_closed_trades_with_candles_and_skips_the_rest(
    client, session, account_factory, instrument_factory
):
    account = account_factory()
    priced, unpriced = instrument_factory(), instrument_factory()
    with_prices = _open_and_close(client, account, priced)
    without_prices = _open_and_close(client, account, unpriced)
    _seed_prices(session, priced, [(2, "95", "105"), (3, "90", "130"), (4, "100", "110")])

    resp = client.post("/api/trades/excursions/recompute")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 1, "skipped": 1}
    assert client.get(f"/api/trades/{with_prices['id']}").json()["mae_eur"] == "-20"
    assert client.get(f"/api/trades/{without_prices['id']}").json()["mae_eur"] is None


# --- API: GET /trades/{id}/chart --------------------------------------------


def test_chart_returns_candles_and_all_four_markers(
    client, session, account_factory, instrument_factory
):
    account, instrument = account_factory(), instrument_factory()
    trade = _open_and_close(client, account, instrument)
    _seed_prices(session, instrument, [(2, "95", "105"), (3, "90", "130"), (4, "100", "110")])

    resp = client.get(f"/api/trades/{trade['id']}/chart", params={"padding_days": 0})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolution"] == "1d"
    assert [c["time"] for c in body["candles"]] == ["2026-01-02", "2026-01-03", "2026-01-04"]
    markers = {m["kind"]: (m["time"], m["price"]) for m in body["markers"]}
    assert markers == {
        "entry": ("2026-01-02", "100"),
        "exit": ("2026-01-04", "130"),
        "stop": ("2026-01-02", "90"),
        "target": ("2026-01-02", "140"),
    }


def test_chart_omits_markers_whose_value_is_none(
    client, session, account_factory, instrument_factory
):
    account, instrument = account_factory(), instrument_factory()
    trade = client.post(
        "/api/trades",
        json={
            "account_id": account.id,
            "instrument_id": instrument.id,
            "direction": "long",
            "planned_entry": "100",
            "planned_stop": "90",
            "risk_eur": "20",
            "note_pre": "no target set",
        },
    ).json()
    client.post(
        f"/api/trades/{trade['id']}/open",
        json={
            "manual": {
                "ts": "2026-01-02T10:00:00+00:00",
                "quantity": "2",
                "price": "100",
                "fee_eur": "0",
            }
        },
    )

    resp = client.get(f"/api/trades/{trade['id']}/chart", params={"padding_days": 0})

    kinds = {m["kind"] for m in resp.json()["markers"]}
    assert kinds == {"entry", "stop"}


def test_chart_on_unopened_trade_is_409(client, account_factory, instrument_factory):
    account, instrument = account_factory(), instrument_factory()
    trade = client.post(
        "/api/trades",
        json={
            "account_id": account.id,
            "instrument_id": instrument.id,
            "direction": "long",
            "planned_entry": "100",
            "planned_stop": "90",
            "risk_eur": "20",
            "note_pre": "still planned",
        },
    ).json()

    resp = client.get(f"/api/trades/{trade['id']}/chart")

    assert resp.status_code == 409
