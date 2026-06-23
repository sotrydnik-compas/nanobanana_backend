from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, delete

from app.database.session import get_async_session
from app.core.security import hash_password
from app.models.user import User
from app.models.refresh_session import RefreshSession
from app.api.deps import require_admin

router = APIRouter(tags=["admin"])


def _now():
    return datetime.now(timezone.utc)


async def check_admin_access(current_user: User) -> None:
    """Проверка прав администратора"""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Требуются права администратора")


@router.post("/admin/users")
async def admin_create_user(
        email: str = Form(...),
        password: str = Form(...),
        role: str = Form("user"),
        is_active: bool = Form(True),
        email_verified: bool = Form(False),
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Создание пользователя администратором.
    """
    email_n = (email or "").strip().lower()
    if not email_n or "@" not in email_n:
        raise HTTPException(400, "Некорректный email")

    if not password:
        raise HTTPException(400, "Пароль не может быть пустым")

    if role not in ["user", "admin"]:
        raise HTTPException(400, "Роль должна быть 'user' или 'admin'")

    # Проверка уникальности email
    exists = (await session.execute(select(User.id).where(User.email == email_n))).first()
    if exists:
        raise HTTPException(409, "Адрес электронной почты уже зарегистрирован")

    # Создание пользователя
    u = User(
        email=email_n,
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
        email_verified=email_verified
    )
    session.add(u)
    await session.commit()
    await session.refresh(u)

    return {
        "id": u.id,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "email_verified": u.email_verified,
        "created_at": u.created_at,
        "updated_at": u.updated_at
    }


@router.patch("/admin/users/{user_id}")
async def admin_update_user(
        user_id: str,
        email: Optional[str] = Form(None),
        password: Optional[str] = Form(None),
        role: Optional[str] = Form(None),
        is_active: Optional[bool] = Form(None),
        email_verified: Optional[bool] = Form(None),
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Редактирование пользователя администратором.
    Все поля опциональны. Пароль может быть любым непустым.
    """
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "Пользователь не найден")

    if email is not None:
        email_n = (email or "").strip().lower()
        if not email_n or "@" not in email_n:
            raise HTTPException(400, "Некорректный email")

        # Проверка уникальности нового email
        if email_n != u.email:
            exists = (await session.execute(select(User.id).where(User.email == email_n))).first()
            if exists:
                raise HTTPException(409, "Адрес электронной почты уже используется")
            u.email = email_n

    if password is not None:
        if not password:
            raise HTTPException(400, "Пароль не может быть пустым")
        u.password_hash = hash_password(password)

    if role is not None:
        if role not in ["user", "admin"]:
            raise HTTPException(400, "Роль должна быть 'user' или 'admin'")
        u.role = role

    if is_active is not None:
        u.is_active = is_active

    if email_verified is not None:
        u.email_verified = email_verified

    await session.commit()
    await session.refresh(u)

    return {
        "id": u.id,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "email_verified": u.email_verified,
        "created_at": u.created_at,
        "updated_at": u.updated_at
    }


@router.get("/admin/users")
async def admin_list_users(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        search: Optional[str] = Query(None, description="Search by email"),
        role: Optional[str] = Query(None, description="Filter by role"),
        is_active: Optional[bool] = Query(None, description="Filter by active status"),
        email_verified: Optional[bool] = Query(None, description="Filter by email verification"),
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Получение списка всех пользователей с пагинацией и фильтрацией.
    """
    # Базовый запрос
    query = select(User)

    # Фильтры
    if search:
        query = query.where(User.email.ilike(f"%{search}%"))
    if role:
        query = query.where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    if email_verified is not None:
        query = query.where(User.email_verified == email_verified)

    # Пагинация
    query = query.order_by(User.created_at.desc()).offset(offset).limit(limit)

    # Получение результатов
    result = await session.execute(query)
    users = result.scalars().all()

    # Получение общего количества
    count_query = select(func.count()).select_from(User)
    if search:
        count_query = count_query.where(User.email.ilike(f"%{search}%"))
    if role:
        count_query = count_query.where(User.role == role)
    if is_active is not None:
        count_query = count_query.where(User.is_active == is_active)
    if email_verified is not None:
        count_query = count_query.where(User.email_verified == email_verified)

    total = await session.scalar(count_query)

    return {
        "items": [
            {
                "id": u.id,
                "email": u.email,
                "role": u.role,
                "is_active": u.is_active,
                "email_verified": u.email_verified,
                "created_at": u.created_at,
                "updated_at": u.updated_at
            }
            for u in users
        ],
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.get("/admin/users/{user_id}")
async def admin_get_user(
        user_id: str,
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Получение пользователя по ID.
    """
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "Пользователь не найден")

    # Получение количества активных сессий
    sessions_query = select(func.count()).where(
        RefreshSession.user_id == user_id,
        RefreshSession.revoked_at.is_(None)
    )
    active_sessions = await session.scalar(sessions_query)

    return {
        "id": u.id,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "email_verified": u.email_verified,
        "created_at": u.created_at,
        "updated_at": u.updated_at,
        "stats": {
            "active_sessions": active_sessions or 0
        }
    }


@router.post("/admin/users/{user_id}/logout-all")
async def admin_logout_user_all_sessions(
        user_id: str,
        session: AsyncSession = Depends(get_async_session),
        _: dict = Depends(require_admin),
):
    """
    Завершение всех сессий пользователя.
    """
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "Пользователь не найден")

    # Получаем все активные refresh сессии
    refresh_sessions = await session.execute(
        select(RefreshSession).where(
            RefreshSession.user_id == user_id,
            RefreshSession.revoked_at.is_(None)
        )
    )

    # Отзываем refresh токены
    for rs in refresh_sessions.scalars().all():
        rs.revoked_at = _now()

    await session.commit()

    return {"status": "ok", "message": f"All sessions for user {user_id} have been terminated"}


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(
        user_id: str,
        permanent: bool = Query(False, description="Permanently delete user (cannot be undone)"),
        session: AsyncSession = Depends(get_async_session),
        user: dict = Depends(require_admin),
):
    """
    Удаление или деактивация пользователя.
    По умолчанию просто деактивирует пользователя.
    С флагом permanent=true полностью удаляет из БД.
    """
    # Запрещаем админу удалять самого себя
    # if user_id == user.id:
    #     raise HTTPException(400, "Cannot delete your own account")

    # Поиск пользователя
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "Пользователь не найден")

    if permanent:
        # Полное удаление
        await session.execute(
            delete(RefreshSession).where(RefreshSession.user_id == user_id)
        )
        await session.delete(u)
        message = f"User {user_id} permanently deleted"
    else:
        # Просто деактивируем
        u.is_active = False
        await session.execute(
            update(RefreshSession)
            .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        message = f"User {user_id} deactivated"

    await session.commit()

    return {"status": "ok", "message": message}
