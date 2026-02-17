import uuid

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.database.session import get_async_session
from app.models.plan import Plan

router = APIRouter(tags=["admin-plans"])


def _to_dict(p: Plan) -> dict:
    return {
        "id": str(p.id),
        "title": p.title,
        "price_minor": p.price_minor,
        "currency": p.currency,
        "requests_total": p.requests_total,
        "is_active": p.is_active,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


@router.get("/admin/plans")
async def admin_list_plans(
    session: AsyncSession = Depends(get_async_session),
    _: dict = Depends(require_admin),
):
    q = select(Plan).order_by(desc(Plan.created_at))
    rows = (await session.execute(q)).scalars().all()
    return {"plans": [_to_dict(p) for p in rows]}


@router.post("/admin/plans")
async def admin_create_plan(
    title: str = Form(...),
    price_minor: int = Form(...),
    requests_total: int = Form(...),
    currency: str = Form("RUB"),
    is_active: bool = Form(True),
    session: AsyncSession = Depends(get_async_session),
    _: dict = Depends(require_admin),
):
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    if len(title) > 64:
        raise HTTPException(400, "title too long")
    if price_minor < 0:
        raise HTTPException(400, "price_minor must be >= 0")
    if requests_total <= 0:
        raise HTTPException(400, "requests_total must be > 0")

    p = Plan(
        title=title,
        price_minor=price_minor,
        currency=(currency or "RUB").strip().upper(),
        requests_total=requests_total,
        is_active=bool(is_active),
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return {"plan": _to_dict(p)}


@router.post("/admin/plans/{plan_id}")
async def admin_update_plan(
    plan_id: str,
    title: str | None = Form(None),
    price_minor: int | None = Form(None),
    requests_total: int | None = Form(None),
    currency: str | None = Form(None),
    is_active: bool | None = Form(None),
    session: AsyncSession = Depends(get_async_session),
    _: dict = Depends(require_admin),
):
    try:
        pid = uuid.UUID(plan_id)
    except Exception:
        raise HTTPException(400, "Invalid plan_id")

    p = (await session.execute(select(Plan).where(Plan.id == pid))).scalars().first()
    if not p:
        raise HTTPException(404, "Plan not found")

    values: dict = {}

    if title is not None:
        t = (title or "").strip()
        if not t:
            raise HTTPException(400, "title must not be empty")
        if len(t) > 64:
            raise HTTPException(400, "title too long")
        values["title"] = t

    if price_minor is not None:
        if price_minor < 0:
            raise HTTPException(400, "price_minor must be >= 0")
        values["price_minor"] = int(price_minor)

    if requests_total is not None:
        if requests_total <= 0:
            raise HTTPException(400, "requests_total must be > 0")
        values["requests_total"] = int(requests_total)

    if currency is not None:
        values["currency"] = (currency or "RUB").strip().upper()

    if is_active is not None:
        values["is_active"] = bool(is_active)

    if not values:
        return {"plan": _to_dict(p)}

    await session.execute(update(Plan).where(Plan.id == pid).values(**values))
    await session.commit()

    p = (await session.execute(select(Plan).where(Plan.id == pid))).scalars().first()
    return {"plan": _to_dict(p)}


@router.post("/admin/plans/{plan_id}/activate")
async def admin_activate_plan(
    plan_id: str,
    session: AsyncSession = Depends(get_async_session),
    _: dict = Depends(require_admin),
):
    try:
        pid = uuid.UUID(plan_id)
    except Exception:
        raise HTTPException(400, "Invalid plan_id")

    p = (await session.execute(select(Plan).where(Plan.id == pid))).scalars().first()
    if not p:
        raise HTTPException(404, "Plan not found")

    if p.is_active:
        return {"status": "ok"}

    await session.execute(update(Plan).where(Plan.id == pid).values(is_active=True))
    await session.commit()
    return {"status": "ok"}


@router.post("/admin/plans/{plan_id}/deactivate")
async def admin_deactivate_plan(
    plan_id: str,
    session: AsyncSession = Depends(get_async_session),
    _: dict = Depends(require_admin),
):
    try:
        pid = uuid.UUID(plan_id)
    except Exception:
        raise HTTPException(400, "Invalid plan_id")

    p = (await session.execute(select(Plan).where(Plan.id == pid))).scalars().first()
    if not p:
        raise HTTPException(404, "Plan not found")

    if not p.is_active:
        return {"status": "ok"}

    await session.execute(update(Plan).where(Plan.id == pid).values(is_active=False))
    await session.commit()
    return {"status": "ok"}
