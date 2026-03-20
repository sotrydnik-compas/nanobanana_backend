from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import Plan
from app.models.system_plan_grant import SystemPlanGrant
from app.models.user_balance import UserBalance

SIGNUP_BONUS_GRANT_TYPE = "signup_bonus"


class SystemPlanNotConfigured(Exception):
    pass


class MultipleSystemPlansConfigured(Exception):
    pass


async def get_active_system_plan(session: AsyncSession) -> Plan:
    rows = (
        await session.execute(
            select(Plan).where(
                Plan.is_system.is_(True),
                Plan.is_active.is_(True),
            )
        )
    ).scalars().all()

    if not rows:
        raise SystemPlanNotConfigured("Active system plan is not configured")
    if len(rows) > 1:
        raise MultipleSystemPlansConfigured("Multiple active system plans are configured")

    return rows[0]


async def grant_signup_system_plan(session: AsyncSession, user_id: str) -> tuple[SystemPlanGrant, bool]:
    try:
        async with session.begin():
            existing = (
                await session.execute(
                    select(SystemPlanGrant).where(
                        SystemPlanGrant.user_id == user_id,
                        SystemPlanGrant.grant_type == SIGNUP_BONUS_GRANT_TYPE,
                    )
                )
            ).scalars().first()
            if existing:
                return existing, False

            plan = await get_active_system_plan(session)

            balance = (
                await session.execute(
                    select(UserBalance).where(UserBalance.user_id == user_id).with_for_update()
                )
            ).scalars().first()
            if not balance:
                balance = UserBalance(user_id=user_id, requests_left=0)
                session.add(balance)
                await session.flush()

            balance.requests_left += plan.requests_total

            grant = SystemPlanGrant(
                user_id=user_id,
                plan_id=plan.id,
                grant_type=SIGNUP_BONUS_GRANT_TYPE,
            )
            session.add(grant)
            await session.flush()
            return grant, True
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(
                select(SystemPlanGrant).where(
                    SystemPlanGrant.user_id == user_id,
                    SystemPlanGrant.grant_type == SIGNUP_BONUS_GRANT_TYPE,
                )
            )
        ).scalars().first()
        if existing:
            return existing, False
        raise
