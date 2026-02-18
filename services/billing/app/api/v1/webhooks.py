from jose import jwt
from jose.exceptions import JWTError

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import logger
from app.database.session import get_async_session
from app.models.payment import Payment
from app.services.payment_status import map_to_internal_status, apply_payment_status

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/{provider}")
async def provider_webhook(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    if provider != settings.PAYMENT_PROVIDER:
        raise HTTPException(404, "Unknown provider")

    # Точка присылает JWT строкой в body
    raw = (await request.body()).decode("utf-8", errors="ignore").strip()
    if not raw:
        raise HTTPException(400, "Empty body")

    # verify RS256
    try:
        payload = jwt.decode(
            raw,
            settings.TOCHKA_WEBHOOK_PUBLIC_KEY,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except JWTError as e:
        logger.error(f"[tochka-webhook] invalid signature: {e}")
        raise HTTPException(401, "Invalid webhook signature")

    # интересует только acquiringInternetPayment
    if payload.get("webhookType") != "acquiringInternetPayment":
        return {"status": "ignored"}

    operation_id = payload.get("operationId")
    tochka_status = payload.get("status")  # APPROVED / AUTHORIZED
    if not operation_id or not tochka_status:
        raise HTTPException(400, "Invalid payload")

    new_status = map_to_internal_status(tochka_status)

    async with session.begin():
        # ищем платеж по provider_payment_id
        p = (
            (await session.execute(
                select(Payment)
                .where(
                    Payment.provider == settings.PAYMENT_PROVIDER,
                    Payment.provider_payment_id == operation_id,
                )
                .with_for_update()
            ))
            .scalars()
            .first()
        )
        if not p:
            # вернуть 200, чтобы Точка не ретраяла
            logger.warning(f"[tochka-webhook] payment not found operationId={operation_id}")
            return {"status": "ok"}

        await apply_payment_status(session, p, new_status)

    return {"status": "ok"}
