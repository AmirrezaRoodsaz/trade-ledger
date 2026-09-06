"""Router auto-discovery.

Every module in this package that defines a module-level `router: APIRouter`
gets collected into `ROUTERS`. A later task adds a new `api/<name>.py` file
with its own `router` and never has to touch this list.

The `/api` prefix is applied once, here in `main.create_app()` — not on the
individual routers — so router modules just declare plain paths
(`/accounts`, not `/api/accounts`).
"""

from __future__ import annotations

import pkgutil
from importlib import import_module

from fastapi import APIRouter

ROUTERS: list[APIRouter] = []

for _module_info in pkgutil.iter_modules(__path__):
    _module = import_module(f"{__name__}.{_module_info.name}")
    _router = getattr(_module, "router", None)
    if isinstance(_router, APIRouter):
        ROUTERS.append(_router)
