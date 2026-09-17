"""Endpoints for triggering collection manually and inspecting run history."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, CollectionRun, RunStatus
from app.routers.accounts import get_account
from app.schemas import CollectionResultOut, CollectionRunOut, TokenRefreshOut
from app.security import require_session
from app.services.collector import collect_account, collect_all_accounts
from app.services.jobs_runtime import (
    QueueUnavailableError,
    dispatch_account_collection,
    dispatch_all_accounts_collection,
    has_running_collection,
)
from app.services.token_service import refresh_account_token

router = APIRouter(prefix="/api", tags=["jobs"], dependencies=[Depends(require_session)])

_ALREADY_RUNNING = (
    "A collection for this account is already running. Wait for it to finish "
    "before starting another."
)
_QUEUE_DOWN = "Processing queue unavailable. Try again in a moment."


@router.post(
    "/accounts/{account_id}/collect",
    response_model=CollectionResultOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Collect metrics for one account",
)
def collect_one(
    background_tasks: BackgroundTasks,
    response: Response,
    account: Account = Depends(get_account),
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None, ge=1, le=200),
    wait: bool = Query(
        default=False,
        description=(
            "Block until the collection finishes instead of returning a run id "
            "to poll. Only sensible for small accounts: a full run can take "
            "minutes and will outlive most proxy timeouts."
        ),
    ),
) -> CollectionResultOut:
    if not account.is_active or not account.access_token_encrypted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This account is disconnected. Reconnect it before collecting.",
        )
    if has_running_collection(db, account.id):
        raise HTTPException(status.HTTP_409_CONFLICT, _ALREADY_RUNNING)

    if wait:
        response.status_code = status.HTTP_200_OK
        result = collect_account(db, account, trigger="manual", limit=limit)
        return _result_out(result)

    # Claim the run row synchronously so the caller gets an id it can poll. The
    # partial unique index on (account_id) WHERE status='RUNNING' is what makes
    # two simultaneous requests resolve into one collection instead of two.
    run = CollectionRun(account_id=account.id, trigger="manual", status=RunStatus.RUNNING)
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, _ALREADY_RUNNING) from exc
    db.refresh(run)

    try:
        dispatch_account_collection(account.id, run.id, limit, background_tasks)
    except QueueUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _QUEUE_DOWN) from exc

    return CollectionResultOut(
        run_id=run.id,
        account_id=account.id,
        status=run.status.value,
        media_seen=0,
        media_created=0,
        snapshots_created=0,
        api_calls=0,
    )


@router.post(
    "/collect",
    response_model=list[CollectionResultOut],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Collect metrics for every active account",
)
def collect_everything(
    background_tasks: BackgroundTasks,
    response: Response,
    db: Session = Depends(get_db),
    wait: bool = Query(default=False, description="Block until every account is done."),
) -> list[CollectionResultOut]:
    if wait:
        response.status_code = status.HTTP_200_OK
        return [_result_out(result) for result in collect_all_accounts(db, trigger="manual")]

    try:
        dispatch_all_accounts_collection("manual", background_tasks)
    except QueueUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _QUEUE_DOWN) from exc
    return []


@router.post(
    "/accounts/{account_id}/refresh-token",
    response_model=TokenRefreshOut,
    summary="Extend this account's long-lived token for another 60 days",
)
def refresh_token(
    account: Account = Depends(get_account),
    db: Session = Depends(get_db),
    force: bool = Query(
        default=False,
        description=(
            "Refresh even when the token is not near expiry. Meta rejects a "
            "token younger than 24 hours, so forcing right after connecting "
            "will fail."
        ),
    ),
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
    return [_run_out(run) for run in runs]


@router.get(
    "/runs/{run_id}",
    response_model=CollectionRunOut,
    summary="One collection run — poll this to follow a collection in progress",
)
def read_run(run_id: int, db: Session = Depends(get_db)) -> CollectionRunOut:
    run = db.get(CollectionRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection run not found.")
    return _run_out(run)


def _run_out(run: CollectionRun) -> CollectionRunOut:
    return CollectionRunOut(
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


def _result_out(result) -> CollectionResultOut:  # noqa: ANN001 - collector.CollectionResult
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
