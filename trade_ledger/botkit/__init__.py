"""Bot-side toolkit. Runs in the *bot* process, never in the app process.

`exchange` is the only module in the whole package that may place, cancel or
size an order, and nothing under `trade_ledger/api/` or `trade_ledger/main.py`
may import it (`tests/test_exchange_isolation.py` keeps that true). Nothing is
re-exported here on purpose — importing `trade_ledger.botkit` must not drag
`ccxt` (or write capability) into whatever imported it.
"""
