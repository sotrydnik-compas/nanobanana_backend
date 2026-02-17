from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_async_session
from app.models.plan import Plan

router = APIRouter(tags=["plans"])


@router.get("/plans")
async def list_plans(session: AsyncSession = Depends(get_async_session)):
    q = select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.created_at.desc())
    rows = (await session.execute(q)).scalars().all()
    return {
        "plans": [
            {
                "id": str(p.id),
                "title": p.title,
                "price_minor": p.price_minor,
                "currency": p.currency,
                "requests_total": p.requests_total,
            }
            for p in rows
        ]
    }
