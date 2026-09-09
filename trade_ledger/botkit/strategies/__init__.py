"""Strategy registry: name -> `signals(candles, params, open_positions)`.

`Signal` lives in `donchian` and is re-exported here — the shared import
path is `trade_ledger.botkit.strategies`, so a second strategy imports it
from the package, not from its neighbour.
"""

from __future__ import annotations

from . import donchian
from .donchian import Signal

STRATEGIES = {"donchian": donchian.signals}
DEFAULT_PARAMS = {"donchian": donchian.default_params()}

__all__ = ["DEFAULT_PARAMS", "STRATEGIES", "Signal", "donchian"]
