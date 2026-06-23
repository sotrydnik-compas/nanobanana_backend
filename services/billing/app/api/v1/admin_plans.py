import uuid

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import select, update, desc
from sqlalchemy.exc import IntegrityError
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
        "is_system": p.is_system,
        "is_purchasable": p.is_purchasable,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


async def _ensure_single_system_plan(
    session: AsyncSession,
    *,
    requested_is_system: bool,
    current_plan_id: uuid.UUID | None = None,
) -> None:
    if not requested_is_system:
        return

    query = select(Plan.id).where(Plan.is_system.is_(True))
    if current_plan_id is not None:
        query = query.where(Plan.id != current_plan_id)

    exists = (await session.execute(query)).first()
    if exists:
        raise HTTPException(409, "Разрешен только один системный тариф")


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
    is_system: bool = Form(False),
    session: AsyncSession = Depends(get_async_session),
    _: dict = Depends(require_admin),
):
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "Поле title обязательно")
    if len(title) > 64:
        raise HTTPException(400, "Поле title слишком длинное")
    if price_minor < 0:
        raise HTTPException(400, "Поле price_minor должно быть >= 0")
    if requests_total <= 0:
        raise HTTPException(400, "Поле requests_total должно быть > 0")

    await _ensure_single_system_plan(session, requested_is_system=bool(is_system))

    p = Plan(
        title=title,
        price_minor=price_minor,
        currency=(currency or "RUB").strip().upper(),
        requests_total=requests_total,
        is_active=bool(is_active),
        is_system=bool(is_system),
        is_purchasable=not bool(is_system),
    )
    session.add(p)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if bool(is_system):
            raise HTTPException(409, "Разрешен только один системный тариф")
        raise HTTPException(409, "Не удалось создать тариф из-за конфликта данных")
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
    is_system: bool | None = Form(None),
    session: AsyncSession = Depends(get_async_session),
    _: dict = Depends(require_admin),
):
    try:
        pid = uuid.UUID(plan_id)
    except Exception:
        raise HTTPException(400, "Некорректный plan_id")

    p = (await session.execute(select(Plan).where(Plan.id == pid))).scalars().first()
    if not p:
        raise HTTPException(404, "Тариф не найден")

    values: dict = {}

    if title is not None:
        t = (title or "").strip()
        if not t:
            raise HTTPException(400, "Поле title не должно быть пустым")
        if len(t) > 64:
            raise HTTPException(400, "Поле title слишком длинное")
        values["title"] = t

    if price_minor is not None:
        if price_minor < 0:
            raise HTTPException(400, "Поле price_minor должно быть >= 0")
        values["price_minor"] = int(price_minor)

    if requests_total is not None:
        if requests_total <= 0:
            raise HTTPException(400, "Поле requests_total должно быть > 0")
        values["requests_total"] = int(requests_total)

    if currency is not None:
        values["currency"] = (currency or "RUB").strip().upper()

    if is_active is not None:
        if p.is_system and not bool(is_active):
            raise HTTPException(400, "Системный тариф нельзя деактивировать")
        values["is_active"] = bool(is_active)

    if is_system is not None:
        raise HTTPException(400, "Поле is_system нельзя изменять у существующего тарифа")

    if p.is_system:
        values["is_purchasable"] = False

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
        raise HTTPException(400, "Некорректный plan_id")

    p = (await session.execute(select(Plan).where(Plan.id == pid))).scalars().first()
    if not p:
        raise HTTPException(404, "Тариф не найден")

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
        raise HTTPException(400, "Некорректный plan_id")

    p = (await session.execute(select(Plan).where(Plan.id == pid))).scalars().first()
    if not p:
        raise HTTPException(404, "Тариф не найден")

    if not p.is_active:
        return {"status": "ok"}
    if p.is_system:
        raise HTTPException(400, "Системный тариф нельзя деактивировать")

    await session.execute(update(Plan).where(Plan.id == pid).values(is_active=False))
    await session.commit()
    return {"status": "ok"}
