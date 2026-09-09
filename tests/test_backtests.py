from __future__ import annotations

import json
from decimal import Decimal

from trade_ledger.bots.backtests import criteria, evaluate
from trade_ledger.models import Setting


def _result(**over) -> dict:
    """The brief's passing run: 45 trades, PF 1,8, DD 21 %, CAGR ÷ DD 1,4
    against a benchmark's 1,1.
    """
    result = {
        "trades": 45,
        "expectancy_r": Decimal("0.18"),
        "profit_factor": Decimal("1.8"),
        "win_rate": Decimal("0.42"),
        "max_drawdown_pct": Decimal(21),
        "cagr_pct": Decimal("29.4"),
        "benchmark_cagr_pct": Decimal(22),
        "benchmark_max_drawdown_pct": Decimal(20),
    }
    result.update(over)
    return result


def _payload(**over) -> dict:
    body = {
        "strategy": "donchian",
        "label": "donchian 4h 2020-2026",
        "period_start": "2020-01-01",
        "period_end": "2026-01-01",
        "data_source": "okx ohlcv",
        "timeframe": "4h",
        "pairs": ["BTC/EUR"],
        "costs_note": "0,10 % per side",
        "trades": 45,
        "expectancy_r": "0.18",
        "profit_factor": "1.8",
        "win_rate": "0.42",
        "max_drawdown_pct": "21",
        "cagr_pct": "29.4",
        "benchmark_cagr_pct": "22",
        "benchmark_max_drawdown_pct": "20",
        "notes": "trend following, no leverage",
    }
    body.update(over)
    return body


# --- evaluate ---------------------------------------------------------------


def test_defaults_pass_the_worked_example(session):
    passed, reasons = evaluate(_result(), criteria(session))
    assert (passed, reasons) == (True, [])


def test_35_trades_fail_on_min_trades(session):
    passed, reasons = evaluate(_result(trades=35), criteria(session))
    assert passed is False
    assert [r.split(":")[0] for r in reasons] == ["min_trades"]
    assert "35 < 40" in reasons[0]


def test_profit_factor_1_2_fails(session):
    passed, reasons = evaluate(_result(profit_factor=Decimal("1.2")), criteria(session))
    assert passed is False
    assert [r.split(":")[0] for r in reasons] == ["min_profit_factor"]


def test_flat_expectancy_and_deep_drawdown_both_reported(session):
    passed, reasons = evaluate(
        _result(expectancy_r=Decimal(0), max_drawdown_pct=Decimal(34)), criteria(session)
    )
    assert passed is False
    # a 34 % drawdown also drags the return/drawdown ratio under the benchmark's
    assert [r.split(":")[0] for r in reasons] == [
        "min_expectancy",
        "max_drawdown_pct",
        "beat_benchmark",
    ]


def test_benchmark_ratio_must_be_beaten(session):
    passed, reasons = evaluate(_result(cagr_pct=Decimal(21)), criteria(session))
    assert passed is False
    assert reasons[0].startswith("beat_benchmark")


def test_benchmark_check_is_skipped_without_benchmark_fields(session):
    passed, _ = evaluate(
        _result(cagr_pct=None, benchmark_cagr_pct=None, benchmark_max_drawdown_pct=None),
        criteria(session),
    )
    assert passed is True


def test_criteria_come_from_settings(session):
    session.add(Setting(key="bt_min_trades", value="60"))
    session.add(Setting(key="bt_require_beat_benchmark", value="false"))
    session.commit()

    values = criteria(session)
    assert values["min_trades"] == 60
    assert values["require_beat_benchmark"] is False
    assert evaluate(_result(cagr_pct=Decimal(1)), values)[1] == ["min_trades: 45 < 60"]


# --- api --------------------------------------------------------------------


def test_post_stores_the_verdict_and_the_lists(client):
    body = client.post("/api/backtests", json=_payload()).json()

    assert body["passed"] is True
    assert body["fail_reasons"] == []
    assert body["pairs"] == ["BTC/EUR"]
    assert body["bot_id"] is None
    assert body["expectancy_r"] == "0.18"


def test_post_records_why_a_run_failed(client):
    body = client.post("/api/backtests", json=_payload(trades=35)).json()

    assert body["passed"] is False
    assert body["fail_reasons"] == ["min_trades: 35 < 40"]


def test_upload_accepts_a_json_file(client):
    payload = _payload(label="uploaded", equity_r=[["2020-01-01", "0"], ["2020-02-01", "1.5"]])
    resp = client.post(
        "/api/backtests/upload",
        files={"file": ("backtest.json", json.dumps(payload), "application/json")},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["label"] == "uploaded"
    assert body["passed"] is True
    assert body["equity_r"] == [["2020-01-01", "0"], ["2020-02-01", "1.5"]]


def test_upload_rejects_a_file_that_is_not_json(client):
    files = {"file": ("x.json", b"not json", "text/plain")}
    resp = client.post("/api/backtests/upload", files=files)
    assert resp.status_code == 422


def test_bot_push_stamps_the_bot_id(client, bot_factory):
    bot, token = bot_factory()

    resp = client.post(
        "/api/backtests", json=_payload(), headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["bot_id"] == bot.id


def test_a_stale_bot_token_is_rejected(client):
    resp = client.post("/api/backtests", json=_payload(), headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_list_filters_by_strategy_and_bot(client, bot_factory):
    bot, token = bot_factory()
    client.post("/api/backtests", json=_payload(strategy="breakout"))
    client.post("/api/backtests", json=_payload(), headers={"Authorization": f"Bearer {token}"})

    assert [r["strategy"] for r in client.get("/api/backtests").json()] == ["donchian", "breakout"]
    assert len(client.get("/api/backtests?strategy=breakout").json()) == 1
    by_bot = client.get(f"/api/backtests?bot={bot.slug}").json()
    assert [r["bot_id"] for r in by_bot] == [bot.id]


def test_delete_removes_the_result(client):
    created = client.post("/api/backtests", json=_payload()).json()

    assert client.delete(f"/api/backtests/{created['id']}").status_code == 204
    assert client.get("/api/backtests").json() == []
    assert client.delete(f"/api/backtests/{created['id']}").status_code == 404
