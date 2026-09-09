"""The write boundary.

Two halves: the app must never pull `botkit.exchange` into its process, and
the wrapper itself must send the identifying and risk-limiting parameters
(`clientOrderId`, `tdMode`, `reduceOnly`, a capped leverage) on every write.
Every test here talks to a fake ccxt object — the network is never touched.
"""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from trade_ledger.botkit.exchange import Exchange

REPO = Path(__file__).resolve().parent.parent

# Run in a clean interpreter: this test process has already imported the
# module (three lines up), so `sys.modules` here proves nothing.
ISOLATION_PROBE = """
import sys

import trade_ledger.api  # imports every router module
import trade_ledger.main

leaked = sorted(m for m in sys.modules if m.startswith("trade_ledger.botkit"))
assert not leaked, f"the app process imported the botkit: {leaked}"
"""


def test_the_app_never_imports_the_write_capable_module():
    result = subprocess.run(
        [sys.executable, "-c", ISOLATION_PROBE],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# -- fake exchange ------------------------------------------------------------


class FakeCcxt:
    """Only the ccxt surface `Exchange` is allowed to touch."""

    def __init__(self, positions=None, balance=None, ohlcv=None, leverage_max=10):
        self.orders: list[dict] = []
        self.cancelled: list[str] = []
        self.leverage_calls: list[tuple] = []
        self.sandbox_calls: list[bool] = []
        self.markets_loaded = 0
        self._positions = positions or []
        self._balance = balance or {"total": {}}
        self._ohlcv = ohlcv or []
        self._leverage_max = leverage_max

    # reads
    def fetch_balance(self):
        return self._balance

    def fetch_positions(self):
        return self._positions

    def fetch_open_orders(self, symbol=None):
        return [{"id": "o1"}, {"id": "o2"}]

    def fetch_ohlcv(self, symbol, timeframe, limit=None):
        return self._ohlcv[:limit] if limit else self._ohlcv

    def load_markets(self):
        self.markets_loaded += 1
        return {}

    def market(self, symbol):
        return {
            "precision": {"amount": 0.001, "price": 0.1},
            "limits": {"amount": {"min": 0.01}, "leverage": {"max": self._leverage_max}},
        }

    # writes
    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        order = {
            "symbol": symbol,
            "type": type_,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params or {},
            "id": f"x{len(self.orders) + 1}",
            "clientOrderId": (params or {}).get("clientOrderId"),
            "status": "closed",
            "filled": amount,
            "average": 60010.0,
        }
        self.orders.append(order)
        return order

    def cancel_all_orders(self, symbol):
        self.cancelled.append(symbol)

    def set_leverage(self, leverage, symbol, params=None):
        self.leverage_calls.append((leverage, symbol, params))

    def set_sandbox_mode(self, enabled):
        self.sandbox_calls.append(enabled)


def _swap(fake, **kw) -> Exchange:
    return Exchange(
        "okx",
        "k",
        "s",
        "p",
        market_type="swap",
        leverage_cap=Decimal(3),
        exchange=fake,
        **kw,
    )


def test_demo_turns_on_sandbox_mode():
    fake = FakeCcxt()
    _swap(fake, demo=True)
    assert fake.sandbox_calls == [True]


def test_place_order_sends_the_client_id_and_isolated_margin():
    fake = FakeCcxt()

    event = _swap(fake).place_order("BTC/USDT:USDT", "buy", Decimal("0.025"), "ref123")

    order = fake.orders[0]
    assert order["params"]["clientOrderId"] == "ref123"
    assert order["params"]["tdMode"] == "isolated"
    assert (order["type"], order["side"], order["amount"]) == ("market", "buy", 0.025)
    assert event["kind"] == "order"
    assert event["order_id"] == "x1"
    assert event["filled"] == "0.025"


def test_cross_margin_when_isolated_is_off():
    fake = FakeCcxt()
    _swap(fake, isolated=False).place_order("BTC/USDT:USDT", "buy", Decimal(1), "ref")
    assert fake.orders[0]["params"]["tdMode"] == "cross"


def test_leverage_is_capped_by_the_preset_and_set_once_per_symbol():
    fake = FakeCcxt(leverage_max=10)  # venue allows 10, the preset caps at 3
    exchange = _swap(fake)

    exchange.place_order("BTC/USDT:USDT", "buy", Decimal(1), "a")
    exchange.place_order("BTC/USDT:USDT", "buy", Decimal(1), "b")

    assert fake.leverage_calls == [(3, "BTC/USDT:USDT", {"mgnMode": "isolated"})]


def test_leverage_never_exceeds_the_venue_maximum():
    fake = FakeCcxt(leverage_max=2)
    _swap(fake).place_order("BTC/USDT:USDT", "buy", Decimal(1), "a")
    assert fake.leverage_calls[0][0] == 2


def test_place_stop_is_reduce_only_with_a_stop_price():
    fake = FakeCcxt()

    _swap(fake).place_stop("BTC/USDT:USDT", "sell", Decimal("0.025"), Decimal(57000), "ref123")

    params = fake.orders[0]["params"]
    assert params["stopLossPrice"] == 57000.0
    assert params["reduceOnly"] is True
    assert params["clientOrderId"] == "ref123"


def test_spot_orders_carry_no_margin_parameters_and_set_no_leverage():
    fake = FakeCcxt()
    spot = Exchange("okx", "k", "s", market_type="spot", exchange=fake)

    spot.place_order("BTC/EUR", "buy", Decimal("0.01"), "ref")

    assert fake.orders[0]["params"] == {"clientOrderId": "ref"}
    assert fake.leverage_calls == []


def test_close_position_flattens_the_open_side_reduce_only():
    fake = FakeCcxt(
        positions=[
            {
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "contracts": 0.03,
                "entryPrice": 60000,
                "unrealizedPnl": 12.5,
            }
        ]
    )

    event = _swap(fake).close_position("BTC/USDT:USDT")

    assert (fake.orders[0]["side"], fake.orders[0]["amount"]) == ("sell", 0.03)
    assert fake.orders[0]["params"]["reduceOnly"] is True
    assert event["action"] == "close_position"


def test_close_position_is_a_no_op_event_when_nothing_is_open():
    fake = FakeCcxt(positions=[])

    event = _swap(fake).close_position("BTC/USDT:USDT")

    assert fake.orders == []
    assert event["status"] == "noop"


def test_spot_close_position_sells_the_base_balance():
    fake = FakeCcxt(balance={"total": {"BTC": 0.4, "EUR": 100}})
    spot = Exchange("okx", "k", "s", market_type="spot", exchange=fake)

    spot.close_position("BTC/EUR")

    assert (fake.orders[0]["side"], fake.orders[0]["amount"]) == ("sell", 0.4)
    assert "reduceOnly" not in fake.orders[0]["params"]


def test_cancel_all_prefers_the_bulk_call_and_records_an_event():
    fake = FakeCcxt()
    exchange = _swap(fake)

    exchange.cancel_all("BTC/USDT:USDT")

    assert fake.cancelled == ["BTC/USDT:USDT"]
    assert exchange.events[-1]["action"] == "cancel_all"


def test_cancel_all_falls_back_to_cancelling_each_open_order():
    class NoBulk(FakeCcxt):
        cancel_all_orders = None

        def cancel_order(self, order_id, symbol=None):
            self.cancelled.append(order_id)

    fake = NoBulk()
    _swap(fake).cancel_all("BTC/USDT:USDT")

    assert fake.cancelled == ["o1", "o2"]


def test_every_write_is_recorded_as_an_order_event():
    fake = FakeCcxt()
    exchange = _swap(fake)

    exchange.place_order("BTC/USDT:USDT", "buy", Decimal(1), "a")
    exchange.place_stop("BTC/USDT:USDT", "sell", Decimal(1), Decimal(57000), "a")
    exchange.cancel_all("BTC/USDT:USDT")

    assert [e["action"] for e in exchange.events] == ["place_order", "place_stop", "cancel_all"]
    assert all(e["kind"] == "order" for e in exchange.events)


# -- reads --------------------------------------------------------------------


def test_candles_drop_the_bar_that_is_still_forming():
    # ccxt's last row is the current, incomplete bar.
    rows = [[1_700_000_000_000 + i * 14_400_000, 150, 155, 145, 150, 9] for i in range(4)]
    fake = FakeCcxt(ohlcv=rows)

    candles = _swap(fake).candles("BTC/USDT:USDT", "4h", 3)

    assert len(candles) == 3  # four rows in, the newest one dropped
    assert candles[0].date.isoformat() == "2023-11-14T22:13:20+00:00"
    assert candles[-1].date.isoformat() == "2023-11-15T06:13:20+00:00"  # two 4h bars later
    assert candles[0].close == Decimal(150)
    assert isinstance(candles[0].high, Decimal)


def test_market_limits_maps_min_step_and_tick():
    assert _swap(FakeCcxt()).market_limits("BTC/USDT:USDT") == {
        "min_qty": Decimal("0.01"),
        "step": Decimal("0.001"),
        "tick": Decimal("0.1"),
    }


def test_positions_normalise_to_decimal_and_skip_flat_rows():
    fake = FakeCcxt(
        positions=[
            {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.03, "entryPrice": 60000},
            {"symbol": "ETH/USDT:USDT", "side": "long", "contracts": 0},
        ]
    )

    assert _swap(fake).positions() == [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "qty": Decimal("0.03"),
            "avg_entry": Decimal(60000),
            "unrealised": None,
        }
    ]


def test_spot_has_no_positions_only_balances():
    fake = FakeCcxt(balance={"total": {"BTC": 0.4}})
    spot = Exchange("okx", "k", "s", market_type="spot", exchange=fake)

    assert spot.positions() == []
    assert spot.balances() == {"BTC": Decimal("0.4")}


@pytest.mark.parametrize("method", ["create_order", "cancel_all_orders", "set_leverage"])
def test_the_wrapper_is_the_only_caller_of_the_write_methods(method):
    """A grep-style guard: no other module in the package names these."""
    hits = subprocess.run(
        ["grep", "-rl", f"{method}(", "trade_ledger"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert hits == ["trade_ledger/botkit/exchange.py"]
