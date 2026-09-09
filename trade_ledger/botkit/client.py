"""The bot's only way to reach the app: push protocol plus journal calls.

Retries are **method-dependent**, because a retry is only safe when the call
is:

* `GET` and `PATCH` (idempotent) retry a connection failure or a 5xx three
  times, 1/2/4 s apart;
* `POST` is sent exactly once. A 502 from a proxy does not say whether the
  app committed the write, so a blind retry is how you get two planned
  trades — or two orders — for one signal.

Either way an unknown outcome raises `AppUnreachable`, the safety interlock
the runner is built on: no app, no journal entry, and therefore no order. The
runner reconciles against the journal and the exchange; it never retries.

A 4xx raises straight away — a rejected payload or a bad token does not get
better by asking again.
"""

from __future__ import annotations

import re
import socket
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

_BACKOFF_S = (1, 2, 4)  # the three retries after the first try
_RETRYABLE_METHODS = frozenset({"GET", "PATCH"})

# Patched in tests. A module attribute rather than a constructor argument so
# the signature stays the one the plan and the runner agreed on.
_sleep = time.sleep


class AppUnreachable(Exception):
    """The app did not answer within the retry budget."""


def _jsonable(value: Any) -> Any:
    """`Decimal` -> string (the app's `Money` fields read strings), aware
    datetime -> ISO 8601, containers recursively. Nothing else is touched.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def _quote_ccy(symbol: str) -> str:
    """`"BTC/USDT:USDT"` -> `"USDT"`, `"BTC/EUR"` -> `"EUR"`.

    A symbol with no quote leg is a caller bug, not a EUR instrument —
    guessing here would book a USDT position against a EUR cost base.
    """
    _, slash, rest = symbol.partition("/")
    quote = rest.partition(":")[0]
    if not slash or not quote:
        raise ValueError(f"symbol without a quote currency: {symbol!r}")
    return quote


class BotClient:
    """One bot's session against one app.

    `slug` is part of the constructor because every push endpoint lives under
    `/api/bots/{slug}/…`; the token alone identifies the bot to the app but
    not the URL to the client.
    """

    def __init__(
        self, base_url: str, token: str, slug: str, client: httpx.Client | None = None
    ) -> None:
        self.slug = slug
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client if client is not None else httpx.Client(timeout=30)
        self._config: dict | None = None
        self._instruments: dict[tuple[str, str], int] = {}

    # -- transport -----------------------------------------------------------

    def _request(
        self, method: str, path: str, body: Any = None, params: dict | None = None
    ) -> Any:
        url = f"{self._base}{path}"
        attempts = 1 + len(_BACKOFF_S) if method in _RETRYABLE_METHODS else 1
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method,
                    url,
                    headers=self._headers,
                    json=None if body is None else _jsonable(body),
                    params=params,
                )
            except httpx.HTTPError as exc:
                last = exc
            else:
                if response.status_code < 500:
                    response.raise_for_status()
                    return response.json() if response.content else None
                last = httpx.HTTPStatusError(
                    f"{response.status_code} from {url}",
                    request=response.request,
                    response=response,
                )
            if attempt + 1 < attempts:
                _sleep(_BACKOFF_S[attempt])
        raise AppUnreachable(f"{method} {path} failed after {attempts} attempt(s): {last}")

    def _bot(self, method: str, tail: str, body: Any = None) -> Any:
        return self._request(method, f"/api/bots/{self.slug}/{tail}", body)

    # -- push protocol -------------------------------------------------------

    def heartbeat(self, next_run: datetime | None, code_version: str) -> None:
        self._bot(
            "POST",
            "heartbeat",
            {
                "ts": datetime.now(UTC),
                "host": socket.gethostname(),
                "code_version": code_version,
                "next_run": next_run,
            },
        )

    def start_run(self) -> int:
        return self._bot("POST", "runs", {"started": datetime.now(UTC)})["id"]

    def finish_run(
        self,
        run_id: int,
        status: str,
        summary: dict,
        error: str | None = None,
        log: str | None = None,
    ) -> None:
        self._bot(
            "PATCH",
            f"runs/{run_id}",
            {"status": status, "summary": summary, "error": error, "log": log},
        )

    def push_state(self, **fields: Any) -> None:
        self._bot("POST", "state", fields)

    def push_events(self, events: list[dict]) -> None:
        if events:
            self._bot("POST", "events", events)

    def get_config(self) -> dict:
        self._config = self._bot("GET", "config")
        return self._config

    def ack_command(self, command_id: int, result: str, detail: str = "") -> None:
        self._bot("POST", f"commands/{command_id}/ack", {"result": result, "detail": detail})

    # -- journal -------------------------------------------------------------

    @property
    def account_id(self) -> int:
        """The bot's account, from `GET config`. Fetched once per process."""
        config = self._config if self._config is not None else self.get_config()
        return config["bot"]["account_id"]

    def _client_ref(self) -> str:
        # Doubles as the exchange `clientOrderId`, so: letters and digits
        # only, <= 32 characters (OKX's limit, the tightest of the venues).
        return f"{re.sub(r'[^A-Za-z0-9]', '', self.slug)[:12]}{uuid4().hex[:12]}"

    def _instrument_id(self, symbol: str, asset_class: str) -> int:
        key = (symbol, asset_class)
        if key not in self._instruments:
            rows = self._request("GET", "/api/instruments", params={"symbol": symbol})
            match = next(
                (r for r in rows if r["symbol"] == symbol and r["asset_class"] == asset_class),
                None,
            )
            if match is None:
                match = self._request(
                    "POST",
                    "/api/instruments",
                    {
                        "symbol": symbol,
                        "asset_class": asset_class,
                        "quote_ccy": _quote_ccy(symbol),
                        "price_source": "manual",
                    },
                )
            self._instruments[key] = match["id"]
        return self._instruments[key]

    def plan_trade(
        self,
        *,
        instrument_symbol: str,
        asset_class: str,
        direction: str,
        entry: Decimal,
        stop: Decimal,
        risk_eur: Decimal,
        qty: Decimal,
        reason: str,
    ) -> dict:
        """Journal the intent *before* the order exists. The returned trade's
        `external_ref` is the client order id the order must carry, so a fill
        can always be traced back to the plan that authorised it.

        Sent once, never retried (see the module docstring): a duplicate
        planned trade would authorise a second order for one signal.
        # ponytail: no pre-flight "does this ref already exist" GET, because
        # `/api/trades` has no `external_ref` filter to ask with. Add one
        # here if the app ever grows that query param.
        """
        return self._request(
            "POST",
            "/api/trades",
            {
                "account_id": self.account_id,
                "instrument_id": self._instrument_id(instrument_symbol, asset_class),
                "direction": direction,
                "planned_entry": entry,
                "planned_stop": stop,
                "risk_eur": risk_eur,
                "planned_qty": qty,
                "note_pre": reason,
                "external_ref": self._client_ref(),
            },
        )

    def _fill(self, trade_id: int, what: str, ts, quantity, price, fee_eur) -> dict:
        return self._request(
            "POST",
            f"/api/trades/{trade_id}/{what}",
            {"manual": {"ts": ts, "quantity": quantity, "price": price, "fee_eur": fee_eur}},
        )

    def open_trade(self, trade_id: int, *, ts, quantity, price, fee_eur) -> dict:
        return self._fill(trade_id, "open", ts, quantity, price, fee_eur)

    def close_trade(self, trade_id: int, *, ts, quantity, price, fee_eur) -> dict:
        return self._fill(trade_id, "close", ts, quantity, price, fee_eur)

    def open_trades(self) -> list[dict]:
        """Open trades on the bot's account — the journal side of
        reconciliation. `mode=all` because the account decides the mode; the
        default `paper` would hide a demo or live bot's own trades.
        """
        page = self._request(
            "GET",
            "/api/trades",
            params={
                "account_id": self.account_id,
                "mode": "all",
                "status": "open",
                "page_size": 500,
            },
        )
        return page["items"]
