"""The only module in this package that may write to an exchange.

Nothing under `trade_ledger/api/`, `trade_ledger/main.py` or anywhere else in
the app may import it — `tests/test_exchange_isolation.py` fails the build if
that ever changes. It is imported by the bot process alone, which reaches an
exchange only after the app has journalled the intent.

Writes it is allowed to make: `create_order`, `cancel_order`,
`cancel_all_orders`, `set_leverage`. Every one of them returns (and records in
`self.events`) an `order` event dict, so a run's exchange traffic can be
pushed to the app verbatim.

Swaps: `tdMode` rides on every order, and leverage is set once per symbol to
`min(leverage_cap, the market's own maximum)`.

Quantities and prices arrive as `Decimal` and are converted to `float` at the
ccxt call itself — that library speaks float — so nothing upstream of this
module ever has to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import ccxt

from ..prices.service import Candle


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class Exchange:
    def __init__(
        self,
        ccxt_id: str,
        key: str,
        secret: str,
        passphrase: str | None = None,
        *,
        demo: bool = False,
        market_type: str = "spot",
        isolated: bool = True,
        leverage_cap: Decimal = Decimal(1),
        exchange: Any = None,
    ) -> None:
        self.market_type = market_type
        self.isolated = isolated
        self.leverage_cap = Decimal(leverage_cap)
        self.events: list[dict] = []
        self._levered: set[str] = set()
        self._markets_loaded = False

        if exchange is not None:
            self._ex = exchange
        else:
            config: dict[str, Any] = {
                "apiKey": key,
                "secret": secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": market_type,
                    # ponytail: without this a spot market buy takes the
                    # amount in *quote* currency on several venues, which
                    # would silently size the order wrong by the price.
                    "createMarketBuyOrderRequiresPrice": False,
                },
            }
            if passphrase:
                config["password"] = passphrase
            self._ex = getattr(ccxt, ccxt_id)(config)

        if demo:
            self._ex.set_sandbox_mode(True)

    def close(self) -> None:
        close = getattr(self._ex, "close", None)
        if callable(close):
            close()

    # -- reads ---------------------------------------------------------------

    def balances(self) -> dict[str, Decimal]:
        totals = self._ex.fetch_balance().get("total") or {}
        return {ccy: Decimal(str(amount)) for ccy, amount in totals.items()}

    def positions(self) -> list[dict]:
        """Open derivative positions, normalised.

        `unrealised_quote` is in the market's **quote** currency (USDT on a
        USDT-margined swap), not EUR — the runner converts it before it goes
        anywhere near a EUR figure.

        Spot has no position concept — exposure is whatever `balances()`
        holds, and only the caller knows which of those balances its pairs
        refer to, so this returns `[]` there.
        """
        if self.market_type == "spot":
            return []
        out = []
        for raw in self._ex.fetch_positions() or []:
            contracts = raw.get("contracts")
            qty = _dec(contracts if contracts is not None else raw.get("amount"))
            if not qty:
                continue
            out.append(
                {
                    "symbol": raw["symbol"],
                    "side": raw.get("side"),
                    "qty": qty,
                    "avg_entry": _dec(raw.get("entryPrice")),
                    "unrealised_quote": _dec(raw.get("unrealizedPnl")),
                }
            )
        return out

    def open_orders(self) -> list[dict]:
        return list(self._ex.fetch_open_orders() or [])

    def candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        """The last `limit` **closed** bars. ccxt's last row is the bar still
        forming, so one extra is fetched and that row dropped — a strategy
        must never see a close that can still move.

        `Candle.date` carries a `datetime` here rather than a `date`: an
        intraday bar needs its time of day, and `datetime` is a `date`.
        """
        rows = self._ex.fetch_ohlcv(symbol, timeframe, limit=limit + 1) or []
        return [
            Candle(
                date=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
                open=_dec(row[1]),
                high=_dec(row[2]),
                low=_dec(row[3]),
                close=_dec(row[4]),
            )
            for row in rows[:-1][-limit:]
        ]

    def market_limits(self, symbol: str) -> dict:
        """`{"min_qty", "step", "tick"}`.

        `precision` is read as a step size, which is what crypto venues
        report (ccxt `TICK_SIZE` mode). A venue in `DECIMAL_PLACES` mode
        would report digits instead — convert here if one is ever added.
        """
        market = self._market(symbol)
        precision = market.get("precision") or {}
        amount = (market.get("limits") or {}).get("amount") or {}
        return {
            "min_qty": _dec(amount.get("min")),
            "step": _dec(precision.get("amount")),
            "tick": _dec(precision.get("price")),
        }

    def _market(self, symbol: str) -> dict:
        if not self._markets_loaded:
            self._ex.load_markets()
            self._markets_loaded = True
        return self._ex.market(symbol)

    # -- writes --------------------------------------------------------------

    @property
    def _margin_mode(self) -> str:
        return "isolated" if self.isolated else "cross"

    def _params(self) -> dict:
        """Per-order params. Spot has no margin mode."""
        return {} if self.market_type == "spot" else {"tdMode": self._margin_mode}

    def _ensure_leverage(self, symbol: str) -> str | None:
        """Once per symbol, and never above the preset's cap.

        Returns a warning string instead of raising when the venue refuses:
        leverage is already set from a previous run more often than not, and
        an exit must never be blocked by a configuration call. The order that
        follows still carries `tdMode`, and the cap it could not lower is on
        the event for the app to alert on.
        """
        if self.market_type == "spot" or symbol in self._levered:
            return None
        try:
            market_max = (
                (self._market(symbol).get("limits") or {}).get("leverage") or {}
            ).get("max")
            cap = self.leverage_cap
            if market_max is not None:
                cap = min(cap, Decimal(str(market_max)))
            # `mgnMode`, not `tdMode`: setLeverage and createOrder spell the
            # same setting differently on OKX.
            self._ex.set_leverage(max(1, int(cap)), symbol, params={"mgnMode": self._margin_mode})
        except ccxt.BaseError as exc:
            return f"leverage not set on {symbol}: {exc}"
        self._levered.add(symbol)
        return None

    def _event(self, action: str, symbol: str, order: dict | None, **extra: Any) -> dict:
        order = order or {}
        event = {
            "kind": "order",
            "action": action,
            "symbol": symbol,
            "order_id": order.get("id"),
            "client_id": order.get("clientOrderId"),
            "status": order.get("status"),
            "filled": str(order["filled"]) if order.get("filled") is not None else None,
            "average": str(order["average"]) if order.get("average") is not None else None,
            **extra,
        }
        self.events.append(event)
        return event

    def place_order(self, symbol: str, side: str, qty: Decimal, client_id: str) -> dict:
        """Market entry or exit. `client_id` is the journal's `external_ref`,
        so every order on the venue points back at a planned trade.
        """
        warning = self._ensure_leverage(symbol)
        params = {"clientOrderId": client_id, **self._params()}
        order = self._ex.create_order(symbol, "market", side, float(qty), None, params)
        # `client_id` from what was *sent*, not from the echo: a venue that
        # omits `clientOrderId` in its reply must not cost us the link back
        # to the trade.
        return self._event(
            "place_order", symbol, order, side=side, qty=str(qty),
            client_id=client_id, warning=warning,
        )

    def place_stop(
        self, symbol: str, side: str, qty: Decimal, stop_price: Decimal, client_id: str
    ) -> dict:
        """Resting stop. `reduceOnly` on derivatives so a stop can only ever
        shrink a position, never open the opposite one.

        The stop gets its own client order id, `<entry ref>sl` — two live
        orders may not share one id, and the shared prefix still ties the
        stop to its trade. `client_id` is trimmed to 29 characters first so
        the result fits the 32-character budget (OKX's `clOrdId` limit).
        """
        warning = self._ensure_leverage(symbol)
        stop_id = f"{client_id[:29]}sl"
        params = {
            "clientOrderId": stop_id,
            "stopLossPrice": float(stop_price),
            **self._params(),
        }
        if self.market_type != "spot":
            params["reduceOnly"] = True
        order = self._ex.create_order(symbol, "market", side, float(qty), None, params)
        return self._event(
            "place_stop",
            symbol,
            order,
            side=side,
            qty=str(qty),
            stop_price=str(stop_price),
            client_id=stop_id,
            warning=warning,
        )

    def cancel_all(self, symbol: str) -> None:
        """`ccxt` defines `cancel_all_orders` on every exchange class and
        raises `NotSupported` from most of them (OKX included), so the
        capability flag decides, never the attribute.
        """
        if (getattr(self._ex, "has", None) or {}).get("cancelAllOrders"):
            self._ex.cancel_all_orders(symbol)
        else:
            for order in self._ex.fetch_open_orders(symbol) or []:
                self._ex.cancel_order(order["id"], symbol)
        self._event("cancel_all", symbol, None, status="cancelled")

    def close_position(self, symbol: str) -> dict:
        """Flatten `symbol` at market. No-op event when there is nothing to
        close, so a caller can always report what happened.

        The spot branch sells the *total* base balance, which still counts
        coins locked in a resting order — call `cancel_all` first.
        """
        if self.market_type == "spot":
            base = symbol.partition("/")[0]
            qty, side = self.balances().get(base, Decimal(0)), "sell"
        else:
            position = next((p for p in self.positions() if p["symbol"] == symbol), None)
            if position is None:
                return self._event("close_position", symbol, None, status="noop")
            qty = abs(position["qty"])
            side = "sell" if position["side"] != "short" else "buy"

        if qty <= 0:
            return self._event("close_position", symbol, None, status="noop")

        params = {**self._params()}
        if self.market_type != "spot":
            params["reduceOnly"] = True
        order = self._ex.create_order(symbol, "market", side, float(qty), None, params)
        return self._event("close_position", symbol, order, side=side, qty=str(qty))
