"""Pure computations: analytics, MAE/MFE, portfolio, returns. No DB access —
every function here takes plain data (ORM rows or duck-typed objects) and
returns dataclasses or dicts. The one exception is
`analytics.stage_capital`, which reads the stage gate's own setting row.
"""
