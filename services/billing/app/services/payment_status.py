from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment
from app.models.plan import Plan
from app.models.user_balance import UserBalance


def map_to_internal_status(tochka_status: str | None) -> str:
    # храним только: pending/succeeded/failed/canceled
    if tochka_status == "APPROVED":
        return "succeeded"
    if tochka_status in ("EXPIRED", "REFUNDED", "ON-REFUND", "REFUNDED_PARTIALLY"):
        return "canceled"
    # CREATED / AUTHORIZED / WAIT_FULL_PAYMENT / None -> pending
    return "pending"


async def apply_payment_status(session: AsyncSession, payment: Payment, new_status: str) -> tuple[str, str]:
    """
    Обновляет payment.status. Если стал succeeded и раньше не был succeeded — начисляет баланс.
    Возвращает (old_status, new_status_after_apply).
    """
    old = payment.status

    # идемпотентность: succeeded -> succeeded не начисляет повторно
    if old == "succeeded" and new_status == "succeeded":
        return old, payment.status

    payment.status = new_status

    if new_status == "succeeded" and old != "succeeded":
        plan = (
            (await session.execute(select(Plan).where(Plan.id == payment.plan_id)))
            .scalars()
            .first()
        )
        if not plan:
            raise RuntimeError("Plan missing for payment")

        bal = (
            (await session.execute(
                select(UserBalance).where(UserBalance.user_id == payment.user_id).with_for_update()
            ))
            .scalars()
            .first()
        )
        if not bal:
            bal = UserBalance(user_id=payment.user_id, requests_left=0)
            session.add(bal)
            await session.flush()

        bal.requests_left += plan.requests_total

    return old, payment.status
