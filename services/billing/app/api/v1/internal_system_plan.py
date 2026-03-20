from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_internal_token
from app.database.session import get_async_session
from app.services.system_plan import (
    MultipleSystemPlansConfigured,
    SystemPlanNotConfigured,
    grant_signup_system_plan,
)

router = APIRouter(tags=["internal-system-plan"])


@router.post("/internal/system-plan/grant-signup")
async def grant_signup_system_plan_endpoint(
    user_id: str = Form(...),
    _: None = Depends(require_internal_token),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        grant, created = await grant_signup_system_plan(session, user_id=user_id)
    except SystemPlanNotConfigured as e:
        raise HTTPException(503, str(e))
    except MultipleSystemPlansConfigured as e:
        raise HTTPException(503, str(e))

    return {
        "status": "ok",
        "granted": created,
        "grant_id": str(grant.id),
        "plan_id": str(grant.plan_id),
        "grant_type": grant.grant_type,
    }
