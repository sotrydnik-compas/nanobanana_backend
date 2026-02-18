import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_balance import UserBalance
from app.models.usage_reservation import UsageReservation


def _now():
    return datetime.now(timezone.utc)


class InsufficientFunds(Exception):
    pass


class ReservationConflict(Exception):
    pass


async def reserve(session: AsyncSession, user_id: str, request_id: uuid.UUID, cost: int) -> UsageReservation:
    async with session.begin():
        existing = (
            await session.execute(select(UsageReservation).where(UsageReservation.request_id == request_id))
        ).scalars().first()

        if existing:
            if existing.user_id != user_id:
                raise ReservationConflict("request_id belongs to another user")
            if existing.status == "canceled":
                raise ReservationConflict("reservation already canceled")
            return existing  # reserved/confirmed -> OK (идемпотентно)

        bal = (
            await session.execute(
                select(UserBalance).where(UserBalance.user_id == user_id).with_for_update()
            )
        ).scalars().first()

        if not bal:
            bal = UserBalance(user_id=user_id, requests_left=0)
            session.add(bal)
            await session.flush()
            # lock уже не нужен — строка новая внутри транзакции

        if bal.requests_left < cost:
            raise InsufficientFunds()

        bal.requests_left -= cost

        r = UsageReservation(
            user_id=user_id,
            request_id=request_id,
            cost=cost,
            status="reserved",
        )
        session.add(r)
        await session.flush()
        return r


async def cancel(session: AsyncSession, user_id: str, request_id: uuid.UUID) -> UsageReservation:
    async with session.begin():
        r = (
            await session.execute(select(UsageReservation).where(UsageReservation.request_id == request_id))
        ).scalars().first()

        if not r:
            raise ReservationConflict("reservation not found")

        if r.user_id != user_id:
            raise ReservationConflict("reservation belongs to another user")

        if r.status == "canceled":
            return r

        if r.status == "confirmed":
            raise ReservationConflict("cannot cancel confirmed reservation")

        bal = (
            await session.execute(
                select(UserBalance).where(UserBalance.user_id == user_id).with_for_update()
            )
        ).scalars().first()

        if not bal:
            bal = UserBalance(user_id=user_id, requests_left=0)
            session.add(bal)
            await session.flush()

        bal.requests_left += r.cost
        r.status = "canceled"
        r.canceled_at = _now()
        await session.flush()
        return r


async def confirm(session: AsyncSession, user_id: str, request_id: uuid.UUID, task_id: str) -> UsageReservation:
    async with session.begin():
        r = (
            await session.execute(select(UsageReservation).where(UsageReservation.request_id == request_id))
        ).scalars().first()

        if not r:
            raise ReservationConflict("reservation not found")

        if r.user_id != user_id:
            raise ReservationConflict("reservation belongs to another user")

        if r.status == "canceled":
            raise ReservationConflict("cannot confirm canceled reservation")

        if r.status == "confirmed":
            # идемпотентно: если task_id уже задан, не спорим
            if r.task_id and r.task_id != task_id:
                raise ReservationConflict("reservation already confirmed with another task_id")
            return r

        r.status = "confirmed"
        r.task_id = task_id
        r.confirmed_at = _now()
        await session.flush()
        return r


async def fail(
    session: AsyncSession,
    user_id: str,
    request_id: uuid.UUID,
    task_id: str,
    error: str | None = None,  # пока не сохраняем, просто принимаем
) -> UsageReservation:
    """
    fail = "генерация не удалась -> вернуть запрос пользователю"
    Правила:
      - если reservation reserved -> делаем cancel (возврат cost)
      - если confirmed -> refund (возврат cost) и status=refunded
      - если canceled/refunded -> идемпотентно return
    """
    async with session.begin():
        r = (
            await session.execute(select(UsageReservation).where(UsageReservation.request_id == request_id))
        ).scalars().first()

        if not r:
            raise ReservationConflict("reservation not found")

        if r.user_id != user_id:
            raise ReservationConflict("reservation belongs to another user")

        if r.status in ("canceled", "refunded"):
            return r

        # если confirmed — проверяем, что task_id не конфликтует
        if r.status == "confirmed":
            if r.task_id and r.task_id != task_id:
                raise ReservationConflict("reservation confirmed with another task_id")
            if not r.task_id:
                r.task_id = task_id

            bal = (
                await session.execute(
                    select(UserBalance).where(UserBalance.user_id == user_id).with_for_update()
                )
            ).scalars().first()

            if not bal:
                bal = UserBalance(user_id=user_id, requests_left=0)
                session.add(bal)
                await session.flush()

            bal.requests_left += r.cost
            r.status = "refunded"
            r.refunded_at = _now()
            await session.flush()
            return r

        # иначе status == reserved -> по смыслу это cancel
        bal = (
            await session.execute(
                select(UserBalance).where(UserBalance.user_id == user_id).with_for_update()
            )
        ).scalars().first()

        if not bal:
            bal = UserBalance(user_id=user_id, requests_left=0)
            session.add(bal)
            await session.flush()

        bal.requests_left += r.cost
        r.status = "canceled"
        r.canceled_at = _now()
        await session.flush()
        return r
