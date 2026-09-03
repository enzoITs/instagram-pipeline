"""Endpoints for listing connected accounts and their headline numbers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account
from app.schemas import AccountOut, AccountSummary, MediaTypeBreakdown, TimeseriesPoint
from app.services import analytics

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def get_account(account_id: int, db: Session = Depends(get_db)) -> Account:
    """Dependency resolving `account_id` or returning a 404."""
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    return account


@router.get("", response_model=list[AccountOut], summary="List connected accounts")
def list_accounts(
    db: Session = Depends(get_db),
    include_inactive: bool = Query(default=True),
) -> list[Account]:
    stmt = select(Account).order_by(Account.created_at.desc())
    if not include_inactive:
        stmt = stmt.where(Account.is_active.is_(True))
    return list(db.scalars(stmt).all())


@router.get("/{account_id}", response_model=AccountOut, summary="Get one account")
def read_account(account: Account = Depends(get_account)) -> Account:
    return account


@router.get(
    "/{account_id}/summary",
    response_model=AccountSummary,
    summary="Headline totals across the newest snapshot of every post",
)
def read_summary(
    account: Account = Depends(get_account), db: Session = Depends(get_db)
) -> AccountSummary:
    data = analytics.account_summary(db, account)
    data.pop("_counted_media", None)
    return AccountSummary(account=AccountOut.model_validate(account), **data)


@router.get(
    "/{account_id}/timeseries",
    response_model=list[TimeseriesPoint],
    summary="Engagement totals per collection run",
)
def read_timeseries(
    account: Account = Depends(get_account),
    db: Session = Depends(get_db),
    days: int = Query(default=90, ge=1, le=730),
) -> list[TimeseriesPoint]:
    return [
        TimeseriesPoint(**point)
        for point in analytics.engagement_timeseries(db, account, days=days)
    ]


@router.get(
    "/{account_id}/breakdown",
    response_model=list[MediaTypeBreakdown],
    summary="Average engagement per media product type",
)
def read_breakdown(
    account: Account = Depends(get_account), db: Session = Depends(get_db)
) -> list[MediaTypeBreakdown]:
    return [
        MediaTypeBreakdown(**row) for row in analytics.media_type_breakdown(db, account)
    ]
