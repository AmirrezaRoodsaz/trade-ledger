"""`BotClient` against `httpx.MockTransport` — no app, no network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from trade_ledger.botkit import client as client_module
from trade_ledger.botkit.client import AppUnreachable, BotClient

BASE = "http://app.test"
SLUG = "okx-donchian-4h"
CONFIG = {
    "bot": {
        "slug": SLUG,
        "account_id": 4,
        "dry_run": False,
        "paused_entries": False,
        "enabled": True,
        "mode": "demo",
        "venue": "okx",
        "stage_capital_eur": "2500",
    },
    "preset": None,
    "commands": [],
}


class Recorder:
    """Answers every request from a `{(method, path): payload}` table and
    keeps what was sent."""

    def __init__(self, routes: dict, status: int = 200):
        self.routes = routes
        self.status = status
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        body = self.routes.get((request.method, request.url.path), {})
        return httpx.Response(self.status, json=body)

    def body(self, method: str, path: str):
        request = next(r for r in self.calls if r.method == method and r.url.path == path)
        return json.loads(request.content)

    @property
    def seen(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.calls]


def _client(handler) -> BotClient:
    return BotClient(BASE, "tok", SLUG, client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(client_module, "_sleep", slept.append)
    return slept


# -- retries ------------------------------------------------------------------


def test_connection_failure_retries_three_times_then_raises(no_sleep):
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AppUnreachable, match="after 4 attempts"):
        _client(handler).heartbeat(None, "abc123")

    assert len(attempts) == 4  # the first try plus three retries
    assert no_sleep == [1, 2, 4]


def test_server_error_retries_and_then_succeeds(no_sleep):
    replies = [500, 502, 200]

    def handler(request):
        return httpx.Response(replies.pop(0), json={"id": 9})

    assert _client(handler).start_run() == 9
    assert no_sleep == [1, 2]


def test_client_error_is_not_retried(no_sleep):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(422, json={"detail": "bad payload"})

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).push_state(equity_eur=Decimal(1000))

    assert len(calls) == 1
    assert no_sleep == []


# -- push protocol ------------------------------------------------------------


def test_push_endpoints_carry_the_bearer_token_and_the_slug_path():
    recorder = Recorder({("POST", f"/api/bots/{SLUG}/runs"): {"id": 12}})
    client = _client(recorder)

    client.heartbeat(datetime(2026, 9, 9, 12, 5, tzinfo=UTC), "v1")
    run_id = client.start_run()
    client.finish_run(run_id, "ok", {"orders": 1}, error=None, log="ran")
    client.push_state(equity_eur=Decimal("2480.50"), reconciliation="ok")
    client.push_events([{"ts": datetime(2026, 9, 9, tzinfo=UTC), "kind": "info", "message": "hi"}])
    client.ack_command(7, "ok", "paused")

    assert recorder.seen == [
        ("POST", f"/api/bots/{SLUG}/heartbeat"),
        ("POST", f"/api/bots/{SLUG}/runs"),
        ("PATCH", f"/api/bots/{SLUG}/runs/12"),
        ("POST", f"/api/bots/{SLUG}/state"),
        ("POST", f"/api/bots/{SLUG}/events"),
        ("POST", f"/api/bots/{SLUG}/commands/7/ack"),
    ]
    assert all(r.headers["authorization"] == "Bearer tok" for r in recorder.calls)

    heartbeat = recorder.body("POST", f"/api/bots/{SLUG}/heartbeat")
    assert heartbeat["next_run"] == "2026-09-09T12:05:00+00:00"
    assert heartbeat["code_version"] == "v1"
    assert heartbeat["host"]

    # Decimals go over the wire as strings, the way every Money field reads them.
    assert recorder.body("POST", f"/api/bots/{SLUG}/state")["equity_eur"] == "2480.50"
    assert recorder.body("POST", f"/api/bots/{SLUG}/events") == [
        {"ts": "2026-09-09T00:00:00+00:00", "kind": "info", "message": "hi"}
    ]
    assert recorder.body("POST", f"/api/bots/{SLUG}/commands/7/ack") == {
        "result": "ok",
        "detail": "paused",
    }


def test_push_events_skips_the_call_when_there_is_nothing_to_push():
    recorder = Recorder({})
    _client(recorder).push_events([])
    assert recorder.seen == []


def test_get_config_is_returned_verbatim():
    recorder = Recorder({("GET", f"/api/bots/{SLUG}/config"): CONFIG})
    assert _client(recorder).get_config() == CONFIG


# -- journal ------------------------------------------------------------------


def _journal_recorder(instruments: list[dict]) -> Recorder:
    return Recorder(
        {
            ("GET", f"/api/bots/{SLUG}/config"): CONFIG,
            ("GET", "/api/instruments"): instruments,
            ("POST", "/api/instruments"): {"id": 7, "symbol": "BTC/USDT:USDT"},
            ("POST", "/api/trades"): {"id": 33, "external_ref": "planned"},
        }
    )


def test_plan_trade_posts_the_journal_payload_with_an_external_ref():
    recorder = _journal_recorder(instruments=[])
    client = _client(recorder)

    trade = client.plan_trade(
        instrument_symbol="BTC/USDT:USDT",
        asset_class="perp",
        direction="long",
        entry=Decimal(60000),
        stop=Decimal(57000),
        risk_eur=Decimal(75),
        qty=Decimal("0.025"),
        reason="55-bar breakout",
    )

    assert trade["id"] == 33
    payload = recorder.body("POST", "/api/trades")
    ref = payload.pop("external_ref")
    assert payload == {
        "account_id": 4,  # from GET config -> bot.account_id
        "instrument_id": 7,
        "direction": "long",
        "planned_entry": "60000",
        "planned_stop": "57000",
        "risk_eur": "75",
        "planned_qty": "0.025",
        "note_pre": "55-bar breakout",
    }
    # Doubles as the exchange client order id: alphanumeric, <= 32 characters.
    assert ref.isalnum() and 0 < len(ref) <= 32
    assert ref.startswith("okxdonchian")


def test_plan_trade_creates_the_instrument_only_when_it_is_missing():
    known = [{"id": 5, "symbol": "BTC/USDT:USDT", "asset_class": "perp"}]
    recorder = _journal_recorder(instruments=known)
    client = _client(recorder)

    for _ in range(2):
        client.plan_trade(
            instrument_symbol="BTC/USDT:USDT",
            asset_class="perp",
            direction="long",
            entry=Decimal(60000),
            stop=Decimal(57000),
            risk_eur=Decimal(75),
            qty=Decimal("0.025"),
            reason="breakout",
        )

    assert ("POST", "/api/instruments") not in recorder.seen
    assert recorder.body("POST", "/api/trades")["instrument_id"] == 5
    # config and the instrument list are each fetched once per process
    assert recorder.seen.count(("GET", "/api/instruments")) == 1
    assert recorder.seen.count(("GET", f"/api/bots/{SLUG}/config")) == 1


def test_new_instrument_takes_its_quote_currency_from_the_symbol():
    recorder = _journal_recorder(instruments=[])
    client = _client(recorder)

    client.plan_trade(
        instrument_symbol="BTC/USDT:USDT",
        asset_class="perp",
        direction="long",
        entry=Decimal(60000),
        stop=Decimal(57000),
        risk_eur=Decimal(75),
        qty=Decimal("0.025"),
        reason="breakout",
    )

    assert recorder.body("POST", "/api/instruments") == {
        "symbol": "BTC/USDT:USDT",
        "asset_class": "perp",
        "quote_ccy": "USDT",
        "price_source": "manual",
    }


def test_open_and_close_post_manual_fills():
    recorder = Recorder(
        {
            ("POST", "/api/trades/33/open"): {"id": 33, "status": "open"},
            ("POST", "/api/trades/33/close"): {"id": 33, "status": "closed"},
        }
    )
    client = _client(recorder)
    ts = datetime(2026, 9, 9, 12, 6, tzinfo=UTC)

    client.open_trade(
        33, ts=ts, quantity=Decimal("0.025"), price=Decimal(60010), fee_eur=Decimal("1.2")
    )
    closed = client.close_trade(
        33, ts=ts, quantity=Decimal("0.025"), price=Decimal(62000), fee_eur=Decimal("1.3")
    )

    assert closed["status"] == "closed"
    assert recorder.body("POST", "/api/trades/33/open") == {
        "manual": {
            "ts": "2026-09-09T12:06:00+00:00",
            "quantity": "0.025",
            "price": "60010",
            "fee_eur": "1.2",
        }
    }


def test_open_trades_asks_for_the_bots_own_account_across_modes():
    recorder = Recorder(
        {
            ("GET", f"/api/bots/{SLUG}/config"): CONFIG,
            ("GET", "/api/trades"): {"items": [{"id": 33}], "total": 1},
        }
    )

    assert _client(recorder).open_trades() == [{"id": 33}]

    query = dict(next(r for r in recorder.calls if r.url.path == "/api/trades").url.params)
    assert query == {"account_id": "4", "mode": "all", "status": "open", "page_size": "500"}
