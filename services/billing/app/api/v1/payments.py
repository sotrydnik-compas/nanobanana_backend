import uuid
from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database.session import get_async_session
from app.models.plan import Plan
from app.models.payment import Payment
from app.core.config import settings

router = APIRouter(tags=["payments"])


@router.post("/payments")
async def create_payment(
    plan_id: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
    user=Depends(get_current_user),
):
    user_id = user["user_id"]

    try:
        pid = uuid.UUID(plan_id)
    except Exception:
        raise HTTPException(400, "Invalid plan_id")

    plan = (await session.execute(select(Plan).where(Plan.id == pid, Plan.is_active.is_(True)))).scalars().first()
    if not plan:
        raise HTTPException(404, "Plan not found")

    p = Payment(
        user_id=user_id,
        plan_id=plan.id,
        provider=settings.PAYMENT_PROVIDER,
        provider_payment_id=None,
        status="pending",
        amount_minor=plan.price_minor,
        currency=plan.currency,
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)

    # Пока заглушка: провайдера подключим позже
    return {
        "payment_id": str(p.id),
        "status": p.status,
        "provider": p.provider,
        "provider_payment_id": p.provider_payment_id,
        "payment_url": None,
    }
