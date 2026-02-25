from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy import select, update, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.database.session import get_async_session
from app.models.payment import Payment
from app.models.user_balance import UserBalance


router = APIRouter(tags=["admin-payments"])


@router.get("/admin/payments")
async def get_payments(
        page: int = Query(1, ge=1, description="Номер страницы"),
        page_size: int = Query(10, ge=1, le=100, description="Количество элементов на странице"),
        session: AsyncSession = Depends(get_async_session),
        # _: dict = Depends(require_admin),
):
    # Вычисляем offset
    offset = (page - 1) * page_size

    # Получаем общее количество записей
    total_count_query = select(func.count()).select_from(Payment)
    total_count_result = await session.execute(total_count_query)
    total_count = total_count_result.scalar()

    query = select(Payment).order_by(Payment.created_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)
    payments = result.scalars().all()

    return {
        "items": payments,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": (total_count + page_size - 1) // page_size
    }


@router.get("/admin/payments/{payment_id}")
async def get_payment(
        payment_id: str,
        session: AsyncSession = Depends(get_async_session),
        # _: dict = Depends(require_admin),
):
    payment = await session.get(Payment, payment_id)

    if payment is None:
        raise HTTPException(status_code=404, detail='Платеж не найден')

    return payment


@router.get("/admin/balances")
async def get_users_balances(
        page: int = Query(1, ge=1, description="Номер страницы"),
        page_size: int = Query(10, ge=1, le=100, description="Количество элементов на странице"),
        session: AsyncSession = Depends(get_async_session),
        # _: dict = Depends(require_admin),
):
    # Вычисляем offset
    offset = (page - 1) * page_size

    # Получаем общее количество записей
    total_count_query = select(func.count()).select_from(UserBalance)
    total_count_result = await session.execute(total_count_query)
    total_count = total_count_result.scalar()

    query = select(UserBalance).order_by(UserBalance.updated_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)
    balances = result.scalars().all()

    return {
        "items": balances,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": (total_count + page_size - 1) // page_size
    }


@router.get("/admin/balances/{user_id}")
async def get_user_balance(
        user_id: str,
        session: AsyncSession = Depends(get_async_session),
        # _: dict = Depends(require_admin),
):
    balance = await session.get(UserBalance, user_id)

    if balance is None:
        raise HTTPException(status_code=404, detail='Баланс пользователя не найден')

    return balance


@router.post("/admin/add-balance")
async def add_user_balance(
        user_id: str = Form(...),
        requests_amount: int = Form(...),
        session: AsyncSession = Depends(get_async_session),
        # _: dict = Depends(require_admin),
):
    if requests_amount < 0:
        raise HTTPException(400, "requests_amount must be >= 0")

    balance = await session.get(UserBalance, user_id)
    if balance:
        raise HTTPException(status_code=409, detail='Баланс для этого пользователя уже существует')

    ub = UserBalance(user_id=user_id, requests_left=requests_amount)
    session.add(ub)
    await session.commit()
    await session.refresh(ub)

    return {"user_balance": {
        "user_id": ub.user_id,
        "requests_left": ub.requests_left,
        "created_at": ub.created_at,
        "updated_at": ub.updated_at,
    }}


@router.patch("/admin/upd-balance")
async def upd_user_balance(
        user_id: str = Form(...),
        requests_amount: int = Form(...),
        session: AsyncSession = Depends(get_async_session),
        # _: dict = Depends(require_admin),
):
    if requests_amount < 0:
        raise HTTPException(400, "requests_amount must be >= 0")

    ub = (await session.execute(select(UserBalance).where(UserBalance.user_id == user_id))).scalars().first()

    if not ub:
        raise HTTPException(404, "Balance not found")

    await session.execute(
        update(UserBalance).where(UserBalance.user_id == user_id).values(requests_left=requests_amount)
    )

    await session.commit()

    return {"status": "ok"}
