"""Feed of insights and the main-screen widget summary."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import Conn, SettingsDep, User, company_access
from app.api.v1.auth import ERR_401, ERR_403
from app.api.v1.schemas import CompanySummary, FeedOut, FeedSummaryOut, InsightCard
from app.core.errors import ProblemError
from app.domain.contracts.insight import Role
from app.services import feed as svc
from app.services import interactions, users

router = APIRouter(tags=["feed"])


@router.get(
    "/feed",
    response_model=FeedOut,
    responses={**ERR_401, **ERR_403, 422: {"description": "Неверный курсор или параметры"}},
    summary="Персональная лента инсайтов компании (по релевантности, курсорная пагинация)",
)
async def get_feed(
    user: User,
    conn: Conn,
    settings: SettingsDep,
    company_id: Annotated[str, Query()],
    role: Annotated[Role | None, Query(description="По умолчанию — роль пользователя в компании")] = None,
    filter: Annotated[Literal["all", "urgent", "risks", "opportunities", "saved", "hidden"], Query()] = "all",
    period: Annotated[Literal["day", "week", "month", "all"], Query()] = "all",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> FeedOut:
    access = await company_access(conn, user, company_id, settings)
    effective_role = role.value if role else access.role
    try:
        items, next_cursor = await svc.feed(
            conn, user.id, company_id, effective_role, filter, period, limit, cursor
        )
    except (ValueError, KeyError) as exc:
        raise ProblemError(422, "validation", "Invalid cursor") from exc
    if cursor is None:  # one view per opened feed, not per page
        await interactions.log_action(
            conn,
            user.id,
            company_id,
            None,
            "feed_view",
            {"filter": filter, "period": period, "items": len(items)},
        )
    return FeedOut(
        company_id=company_id,
        role=Role(effective_role),
        items=[InsightCard(**i) for i in items],
        next_cursor=next_cursor,
    )


@router.get(
    "/feed/summary",
    response_model=FeedSummaryOut,
    responses=ERR_401,
    summary="Виджет на главном экране: непрочитанные, срочные и главный инсайт по каждой компании",
)
async def feed_summary(user: User, conn: Conn, settings: SettingsDep) -> FeedSummaryOut:
    companies = await users.user_companies(conn, user.id, settings.eligible_client_types)
    out = []
    for c in companies:
        s = await svc.company_summary(conn, user.id, c["company_id"], c["role"])
        out.append(
            CompanySummary(
                company_id=c["company_id"],
                title=c["title"],
                unread=s["unread"],
                urgent=s["urgent"],
                top=InsightCard(**s["top"]) if s["top"] else None,
            )
        )
    if out:  # the widget on the main page was shown (CJM 01)
        await interactions.log_action(
            conn, user.id, out[0].company_id, None, "summary_view", {"unread": sum(c.unread for c in out)}
        )
    return FeedSummaryOut(unread=sum(c.unread for c in out), urgent=sum(c.urgent for c in out), companies=out)
