"""Endpoints for triggering collection manually and inspecting run history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, CollectionRun
from app.routers.accounts import get_account
from app.schemas import CollectionResultOut, CollectionRunOut, TokenRefreshOut
from app.services.collector import collect_account, collect_all_accounts
from app.services.token_service import refresh_account_token

router = APIRouter(prefix="/api", tags=["jobs"])


@router.post(
    "/accounts/{account_id}/collect",
    response_model=CollectionResultOut,
    summary="Collect metrics for one account right now",
)
def collect_one(
    account: Account = Depends(get_account),
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None, ge=1, le=200),
) -> CollectionResultOut:
    if not account.is_active or not account.access_token_encrypted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This account is disconnected. Reconnect it before collecting.",
        )
    result = collect_account(db, account, trigger="manual", limit=limit)
    return CollectionResultOut(
        run_id=result.run_id,
        account_id=result.account_id,
        status=result.status.value,
        media_seen=result.media_seen,
        media_created=result.media_created,
        snapshots_created=result.snapshots_created,
        api_calls=result.api_calls,
        error=result.error,
    )


@router.post(
    "/collect",
    response_model=list[CollectionResultOut],
    summary="Collect metrics for every active account",
)
def collect_everything(db: Session = Depends(get_db)) -> list[CollectionResultOut]:
    results = collect_all_accounts(db, trigger="manual")
    return [
        CollectionResultOut(
            run_id=r.run_id,
            account_id=r.account_id,
            status=r.status.value,
            media_seen=r.media_seen,
            media_created=r.media_created,
            snapshots_created=r.snapshots_created,
            api_calls=r.api_calls,
            error=r.error,
        )
        for r in results
    ]


@router.post(
    "/accounts/{account_id}/refresh-token",
    response_model=TokenRefreshOut,
    summary="Extend this account's long-lived token for another 60 days",
)
def refresh_token(
    account: Account = Depends(get_account),
    db: Session = Depends(get_db),
    force: bool = Query(default=True),
) -> TokenRefreshOut:
    result = refresh_account_token(db, account, force=force)
    return TokenRefreshOut(
        account_id=result.account_id,
        username=result.username,
        refreshed=result.refreshed,
        expires_at=result.expires_at,
        error=result.error,
    )


@router.get(
    "/runs",
    response_model=list[CollectionRunOut],
    summary="Recent collection runs, newest first",
)
def list_runs(
    db: Session = Depends(get_db),
    account_id: int | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
) -> list[CollectionRunOut]:
    stmt = select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(limit)
    if account_id is not None:
        stmt = stmt.where(CollectionRun.account_id == account_id)
    runs = db.scalars(stmt).all()
    return [
        CollectionRunOut(
            id=run.id,
            account_id=run.account_id,
            started_at=run.started_at,
            finished_at=run.finished_at,
            status=run.status.value,
            trigger=run.trigger,
            media_seen=run.media_seen,
            media_created=run.media_created,
            snapshots_created=run.snapshots_created,
            api_calls=run.api_calls,
            error=run.error,
        )
        for run in runs
    ]
