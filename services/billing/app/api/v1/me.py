from fastapi import APIRouter, Depends
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database.session import get_async_session
from app.models.user_balance import UserBalance
from app.models.payment import Payment

router = APIRouter(tags=["me"])


@router.get("/me/balance")
async def my_balance(
    session: AsyncSession = Depends(get_async_session),
    user=Depends(get_current_user),
):
    user_id = user["user_id"]
    bal = (await session.execute(select(UserBalance).where(UserBalance.user_id == user_id))).scalars().first()
    return {"user_id": user_id, "requests_left": bal.requests_left if bal else 0}


@router.get("/me/payments")
async def my_payments(
    session: AsyncSession = Depends(get_async_session),
    user=Depends(get_current_user),
    limit: int = 50,
    offset: int = 0,
):
    user_id = user["user_id"]
    q = (
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(desc(Payment.created_at))
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(q)).scalars().all()
    return {
        "payments": [
            {
                "id": str(p.id),
                "plan_id": str(p.plan_id),
                "status": p.status,
                "provider": p.provider,
                "provider_payment_id": p.provider_payment_id,
                "amount_minor": p.amount_minor,
                "currency": p.currency,
                "created_at": p.created_at,
            }
            for p in rows
        ]
    }
