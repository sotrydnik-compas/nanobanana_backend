import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_async_session
from app.models.payment import Payment
from app.models.plan import Plan
from app.models.user_balance import UserBalance

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/{provider}")
async def provider_webhook(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    payload = await request.json()

    # Минимальный контракт для теста:
    # { "payment_id": "<uuid>", "status": "succeeded|failed|canceled", "provider_payment_id": "..."? }
    pid = payload.get("payment_id")
    status = payload.get("status")

    if not pid or status not in ("succeeded", "failed", "canceled"):
        raise HTTPException(400, "Invalid payload")

    try:
        payment_uuid = uuid.UUID(pid)
    except Exception:
        raise HTTPException(400, "Invalid payment_id")

    async with session.begin():
        p = (await session.execute(select(Payment).where(Payment.id == payment_uuid))).scalars().first()
        if not p:
            raise HTTPException(404, "Payment not found")

        # идемпотентность: если уже succeeded — повторный succeeded не начисляет второй раз
        if p.status == "succeeded" and status == "succeeded":
            return {"status": "ok"}

        p.provider_payment_id = payload.get("provider_payment_id") or p.provider_payment_id
        p.status = status

        if status == "succeeded":
            plan = (await session.execute(select(Plan).where(Plan.id == p.plan_id))).scalars().first()
            if not plan:
                raise HTTPException(500, "Plan missing")

            bal = (
                await session.execute(
                    select(UserBalance).where(UserBalance.user_id == p.user_id).with_for_update()
                )
            ).scalars().first()
            if not bal:
                bal = UserBalance(user_id=p.user_id, requests_left=0)
                session.add(bal)
                await session.flush()

            bal.requests_left += plan.requests_total

    return {"status": "ok"}
