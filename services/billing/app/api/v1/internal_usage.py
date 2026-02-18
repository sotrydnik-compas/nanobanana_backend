import uuid
from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_internal_token
from app.database.session import get_async_session
from app.services.usage import reserve, cancel, confirm, fail, InsufficientFunds, ReservationConflict

router = APIRouter(tags=["internal"])


@router.post("/internal/usage/reserve")
async def reserve_usage(
    user_id: str = Form(...),
    request_id: str = Form(...),
    cost: int = Form(1),
    _: None = Depends(require_internal_token),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        rid = uuid.UUID(request_id)
    except Exception:
        raise HTTPException(400, "Invalid request_id")

    try:
        r = await reserve(session, user_id=user_id, request_id=rid, cost=cost)
        return {"status": "ok", "reservation_status": r.status}
    except InsufficientFunds:
        raise HTTPException(402, "Not enough requests")
    except ReservationConflict as e:
        raise HTTPException(409, str(e))


@router.post("/internal/usage/cancel")
async def cancel_usage(
    user_id: str = Form(...),
    request_id: str = Form(...),
    _: None = Depends(require_internal_token),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        rid = uuid.UUID(request_id)
    except Exception:
        raise HTTPException(400, "Invalid request_id")

    try:
        r = await cancel(session, user_id=user_id, request_id=rid)
        return {"status": "ok", "reservation_status": r.status}
    except ReservationConflict as e:
        raise HTTPException(409, str(e))


@router.post("/internal/usage/confirm")
async def confirm_usage(
    user_id: str = Form(...),
    request_id: str = Form(...),
    task_id: str = Form(...),
    _: None = Depends(require_internal_token),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        rid = uuid.UUID(request_id)
    except Exception:
        raise HTTPException(400, "Invalid request_id")

    try:
        r = await confirm(session, user_id=user_id, request_id=rid, task_id=task_id)
        return {"status": "ok", "reservation_status": r.status}
    except ReservationConflict as e:
        raise HTTPException(409, str(e))


@router.post("/internal/usage/fail")
async def fail_usage(
    user_id: str = Form(...),
    request_id: str = Form(...),
    task_id: str = Form(...),
    error: str | None = Form(None),
    _: None = Depends(require_internal_token),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        rid = uuid.UUID(request_id)
    except Exception:
        raise HTTPException(400, "Invalid request_id")

    try:
        r = await fail(session, user_id=user_id, request_id=rid, task_id=task_id, error=error)
        return {"status": "ok", "reservation_status": r.status}
    except ReservationConflict as e:
        raise HTTPException(409, str(e))
