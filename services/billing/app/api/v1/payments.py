import uuid
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.logger import logger
from app.database.session import get_async_session
from app.integrations.tochka_client import TochkaClient, TochkaError
from app.models.plan import Plan
from app.models.payment import Payment
from app.services.payment_status import map_to_internal_status, apply_payment_status

router = APIRouter(tags=["payments"])


def _to_amount_str(amount_minor: int) -> str:
    q = Decimal(amount_minor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(q)


def _client() -> TochkaClient:
    return TochkaClient(
        base_url=settings.TOCHKA_BASE_URL,
        api_prefix=settings.TOCHKA_API_PREFIX,
        bearer_token=settings.TOCHKA_BEARER_TOKEN,
    )


@router.post("/payments")
async def create_payment(
    plan_id: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
    user=Depends(get_current_user),
):
    if not settings.PAYMENT_ENABLED:
        logger.warning("PAYMENT_ENABLED=False, пропускаем create_payment_operation")
        raise HTTPException(400, "Payments are disabled")

    user_id = user["user_id"]
    user_email = user['email']

    try:
        pid = uuid.UUID(plan_id)
    except Exception:
        raise HTTPException(400, "Invalid plan_id")

    plan = (
        (await session.execute(select(Plan).where(Plan.id == pid, Plan.is_active.is_(True))))
        .scalars()
        .first()
    )
    if not plan:
        raise HTTPException(404, "Plan not found")
    if not plan.is_purchasable:
        raise HTTPException(400, "Plan is not available for purchase")

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

    amount_str = _to_amount_str(plan.price_minor)

    purpose = f"NanoBanana: {plan.title}".strip()
    if len(purpose) > 140:
        purpose = purpose[:140]
    name = f"Тариф: {plan.title}".strip()

    # paymentLinkId <= 45 — UUID платежа
    payment_link_id = str(p.id)
    if len(payment_link_id) > 45:
        payment_link_id = payment_link_id[:45]

    client_data = {
        "email": user_email,
    }
    items_data = [{
        "name": name,
        "amount": amount_str,
        "quantity": "1",
    },]

    data = {
        "customerCode": settings.TOCHKA_CUSTOMER_CODE,
        "amount": amount_str,
        "purpose": purpose,
        "redirectUrl": settings.TOCHKA_REDIRECT_URL,
        "failRedirectUrl": settings.TOCHKA_FAIL_REDIRECT_URL,
        # "merchantId": settings.TOCHKA_MERCHANT_ID,
        "paymentMode": settings.TOCHKA_PAYMENT_MODES,
        "paymentLinkId": payment_link_id,
        "Client": client_data,
        "Items": items_data,
        "ttl": 15,
    }

    try:
        resp = await _client().create_payment_operation(data)
        d = resp.get("Data") or {}
        operation_id = d.get("operationId")
        payment_url = d.get("paymentLink")
    except TochkaError as e:
        logger.error(f"[tochka] create_payment error: {e}")
        p.status = "failed"
        await session.commit()
        raise HTTPException(502, "Payment provider error")
    except Exception as e:
        logger.exception(f"[tochka] unexpected error: {e}")
        p.status = "failed"
        await session.commit()
        raise HTTPException(502, "Payment provider error")

    if not operation_id or not payment_url:
        logger.error(f"[tochka] bad response: {resp}")
        p.status = "failed"
        await session.commit()
        raise HTTPException(502, "Payment provider error")

    p.provider_payment_id = operation_id
    # пока просто pending (платёж не завершён)
    p.status = "pending"
    await session.commit()

    return {
        "payment_id": str(p.id),
        "status": p.status,
        "provider": p.provider,
        "provider_payment_id": p.provider_payment_id,
        "payment_url": payment_url,
    }


@router.get("/payments/{payment_id}/provider")
async def get_payment_provider_info(
    payment_id: str,
    session: AsyncSession = Depends(get_async_session),
    user=Depends(get_current_user),
):
    user_id = user["user_id"]

    try:
        puid = uuid.UUID(payment_id)
    except Exception:
        raise HTTPException(400, "Invalid payment_id")

    # прочитали payment
    p = (
        (await session.execute(select(Payment).where(Payment.id == puid)))
        .scalars()
        .first()
    )
    if not p:
        raise HTTPException(404, "Payment not found")
    if p.user_id != user_id:
        raise HTTPException(403, "Forbidden")
    if not p.provider_payment_id:
        raise HTTPException(409, "Provider payment not created yet")

    operation_id = p.provider_payment_id

    # закрываем autobegin-транзакцию, чтобы не падать на begin()
    await session.rollback()

    # GET в Точку (вне транзакции БД)
    try:
        resp = await _client().get_payment_operation(operation_id)
    except TochkaError as e:
        logger.error(f"[tochka] get_payment error: {e}")
        raise HTTPException(502, "Payment provider error")

    ops = (((resp.get("Data") or {}).get("Operation")) or [])
    if not ops:
        raise HTTPException(502, "Payment provider error")

    provider_status = (ops[0] or {}).get("status")
    new_status = map_to_internal_status(provider_status)

    # атомарно применяем статус + начисление (идемпотентно)
    p_locked = (
        (await session.execute(
            select(Payment).where(Payment.id == puid).with_for_update()
        ))
        .scalars()
        .first()
    )
    if not p_locked:
        raise HTTPException(404, "Payment not found")

    old_status, applied = await apply_payment_status(session, p_locked, new_status)
    await session.commit()

    return {
        "payment_id": str(puid),
        "provider_payment_id": operation_id,
        "provider_status": provider_status,
        "status_before": old_status,
        "status_after": applied,
    }
