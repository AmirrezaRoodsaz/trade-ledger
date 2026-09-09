"""String enums shared across the app. Values are the wire/DB representation."""

from enum import StrEnum


class Venue(StrEnum):
    TRADING212 = "trading212"
    OKX = "okx"
    KRAKEN = "kraken"
    BINANCE = "binance"
    IBKR = "ibkr"
    WALLET = "wallet"
    OTHER = "other"


class AccountKind(StrEnum):
    BROKER_INVEST = "broker_invest"
    BROKER_CFD = "broker_cfd"
    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_DERIVATIVES = "crypto_derivatives"
    WALLET = "wallet"


class Mode(StrEnum):
    LIVE = "live"
    PAPER = "paper"
    DEMO = "demo"


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    STOCK = "stock"
    ETF = "etf"
    FX = "fx"
    CFD = "cfd"
    PERP = "perp"


class FundType(StrEnum):
    AKTIEN = "aktien"
    MISCH = "misch"
    IMMO = "immo"
    IMMO_AUSLAND = "immo_ausland"
    SONSTIGE = "sonstige"


class TxType(StrEnum):
    BUY = "buy"
    SELL = "sell"
    DEPOSIT = "deposit"  # fiat cash only
    WITHDRAWAL = "withdrawal"  # fiat cash only
    TRANSFER_IN = "transfer_in"  # instrument moves (crypto deposits/withdrawals, wallet moves)
    TRANSFER_OUT = "transfer_out"
    DIVIDEND = "dividend"
    INTEREST = "interest"
    FEE = "fee"
    STAKING_REWARD = "staking_reward"
    AIRDROP = "airdrop"
    SPLIT = "split"
    VORABPAUSCHALE = "vorabpauschale"
    ADJUSTMENT = "adjustment"


class TxSource(StrEnum):
    API = "api"
    CSV = "csv"
    MANUAL = "manual"


class TradeStatus(StrEnum):
    PLANNED = "planned"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"


class Mistake(StrEnum):
    MOVED_STOP = "moved_stop"
    NO_STOP = "no_stop"
    OVERSIZED = "oversized"
    REVENGE = "revenge"
    FOMO = "fomo"
    EARLY_EXIT = "early_exit"
    LATE_ENTRY = "late_entry"
    OFF_PLAN = "off_plan"


class TaxRegime(StrEnum):
    P23 = "p23"  # Section 23 EStG private Veraeusserungsgeschaeft (crypto spot, FX cash)
    P20_AKTIEN = "p20_aktien"  # Section 20 shares
    P20_INV = "p20_inv"  # Section 20 via Anlage KAP-INV (funds/ETFs)
    P20_SONSTIGE = "p20_sonstige"  # dividends, interest
    P20_TERMIN = "p20_termin"  # Termingeschaefte: CFD, perps, futures
    P22 = "p22"  # Section 22 Nr. 3 sonstige Leistungen (staking, lending, airdrop with service)
    NONE = "none"


class BotHost(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"


class BotStatus(StrEnum):
    """Listed in priority order: the first one that applies wins."""

    DISABLED = "disabled"
    STALE = "stale"
    ERROR = "error"
    PAUSED = "paused"
    RUNNING = "running"
    OK = "ok"


class RunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    DRY_RUN = "dry_run"
    SKIPPED = "skipped"


class EventKind(StrEnum):
    HEARTBEAT = "heartbeat"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    KILL_RULE = "kill_rule"
    COMMAND = "command"
    CONFIG_APPLIED = "config_applied"
    RECONCILE = "reconcile"
    ORDER = "order"


class CommandKind(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    FLAT = "flat"
    RUN_NOW = "run_now"
    DRY_RUN_ON = "dry_run_on"
    DRY_RUN_OFF = "dry_run_off"
    RELOAD_CONFIG = "reload_config"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
