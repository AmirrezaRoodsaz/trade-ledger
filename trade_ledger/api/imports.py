"""CSV import endpoints: preview a file against `IMPORTERS` without writing
anything, then commit the (possibly user-edited) drafts.

`preview` takes the raw file body plus `format`/`account_id` as query
params rather than a true `multipart/form-data` upload — the brief's
literal transport needs `python-multipart`, which isn't a project
dependency and "Do NOT add dependencies" is a hard constraint here. This is
the simplest reading that keeps the same three inputs (file bytes, format,
account) without a new dependency; flagged in the task report.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..enums import AssetClass, TxSource, TxType
from ..importers import IMPORTERS, RowError
from ..ledger import TxDraft, _hash_external_id, upsert_transactions
from ..models import Account, SyncRun, Transaction
from ._common import get_account_or_404
from .schemas import BaseModel, Money

router = APIRouter()


class TxDraftOut(BaseModel):
    """Mirrors `ledger.TxDraft` with `Money` fields so amounts serialise as
    strings, matching every other response in this API.
    """

    ts: datetime
    type: TxType
    quantity: Money
    price: Money | None = None
    price_ccy: str | None = None
    fee: Money = Decimal(0)
    fee_ccy: str | None = None
    fx_rate: Money | None = None
    fx_source: str | None = None
    amount_eur: Money
    fee_eur: Money
    withholding_tax_eur: Money
    external_id: str | None = None
    link_id: str | None = None
    source: TxSource
    raw_json: str | None = None
    note: str | None = None
    instrument_symbol: str | None = None
    asset_class: AssetClass | None = None
    isin: str | None = None


class ImportPreviewOut(BaseModel):
    drafts: list[TxDraftOut]
    errors: list[RowError]
    duplicates: int


class ImportCommitIn(BaseModel):
    account_id: int
    drafts: list[TxDraft]


class ImportCommitOut(BaseModel):
    added: int
    skipped: int


def _count_duplicates(session: Session, account: Account, drafts: list[TxDraft]) -> int:
    """Same dedup rule as `upsert_transactions` (DB match or repeat within
    the batch) but read-only, for the preview response's `duplicates` count.
    """
    seen: set[str] = set()
    duplicates = 0
    for draft in drafts:
        external_id = draft.external_id or _hash_external_id(draft)
        if external_id in seen:
            duplicates += 1
            continue
        exists = session.execute(
            select(Transaction.id).where(
                Transaction.account_id == account.id, Transaction.external_id == external_id
            )
        ).first()
        if exists is not None:
            duplicates += 1
        else:
            seen.add(external_id)
    return duplicates


@router.post("/imports/preview", response_model=ImportPreviewOut)
async def preview_import(
    format: str,
    account_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    account = get_account_or_404(session, account_id)
    importer = IMPORTERS.get(format)
    if importer is None:
        raise HTTPException(status_code=422, detail=f"unknown import format: {format!r}")

    data = await request.body()
    result = importer(data)
    duplicates = _count_duplicates(session, account, result.drafts)
    return ImportPreviewOut(
        drafts=[TxDraftOut(**draft.model_dump()) for draft in result.drafts],
        errors=result.errors,
        duplicates=duplicates,
    )


@router.post("/imports/commit", response_model=ImportCommitOut)
def commit_import(payload: ImportCommitIn, session: Session = Depends(get_session)):
    account = get_account_or_404(session, payload.account_id)
    added, skipped = upsert_transactions(session, account, payload.drafts)
    session.add(
        SyncRun(
            account_id=account.id,
            status="ok",
            added=added,
            skipped=skipped,
            finished=datetime.now(UTC),
        )
    )
    session.commit()
    return ImportCommitOut(added=added, skipped=skipped)
