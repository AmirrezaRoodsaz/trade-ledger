"""Which German tax regime a transaction falls under.

Legal background (checked 2026-09-06): BMF letter of 6 March 2025 on the income
tax treatment of Kryptowerte. Crypto held in a spot account or private wallet is
a private asset, so a disposal is a privates Veraeusserungsgeschaeft under
Section 23 EStG; staking rewards and airdrops received for a service are
sonstige Leistungen under Section 22 Nr. 3 EStG; crypto derivatives, perps and
CFDs are Termingeschaefte under Section 20 Abs. 2 EStG.
VERIFY: the Rn. numbers usually quoted for these points come from secondary
sources, not from a reading of the letter itself — confirm before citing them
anywhere user-facing.
"""

from __future__ import annotations

from ..enums import AccountKind, AssetClass, TaxRegime, TxType

_BY_ASSET_CLASS = {
    AssetClass.STOCK: TaxRegime.P20_AKTIEN,
    AssetClass.ETF: TaxRegime.P20_INV,
    AssetClass.FX: TaxRegime.P23,
    AssetClass.PERP: TaxRegime.P20_TERMIN,
    AssetClass.CFD: TaxRegime.P20_TERMIN,
}

# Account kinds in which crypto is a derivative rather than a spot holding.
_DERIVATIVE_KINDS = (AccountKind.CRYPTO_DERIVATIVES, AccountKind.BROKER_CFD)
_SPOT_KINDS = (AccountKind.CRYPTO_SPOT, AccountKind.WALLET)


def regime_for(tx, instrument, account) -> TaxRegime:
    """Tax regime for `tx`. `instrument` may be None (cash-only rows)."""
    override = getattr(instrument, "tax_regime", None)
    if override:
        return TaxRegime(override)

    if tx.type in (TxType.DIVIDEND, TxType.INTEREST):
        return TaxRegime.P20_SONSTIGE
    if tx.type == TxType.AIRDROP:
        # An airdrop with no market value on receipt is not a taxable Leistung.
        return TaxRegime.NONE if tx.amount_eur == 0 else TaxRegime.P22
    if tx.type == TxType.STAKING_REWARD:
        return TaxRegime.P22

    asset_class = getattr(instrument, "asset_class", None)
    kind = getattr(account, "kind", None)
    if asset_class == AssetClass.CRYPTO:
        if kind in _DERIVATIVE_KINDS:
            return TaxRegime.P20_TERMIN
        if kind in _SPOT_KINDS:
            return TaxRegime.P23
        return TaxRegime.NONE
    return _BY_ASSET_CLASS.get(asset_class, TaxRegime.NONE)
