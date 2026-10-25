"""Reference data: bank products and news items (first sources)."""

import json
import uuid

from fastapi import APIRouter

from app.api.deps import Conn, User
from app.api.v1.auth import ERR_401
from app.api.v1.schemas import BankProductOut, NewsOut
from app.core.errors import ProblemError
from app.services.tools import bank_products

router = APIRouter(tags=["catalog"])


@router.get(
    "/bank-products", response_model=list[BankProductOut], responses=ERR_401, summary="Каталог продуктов"
)
async def list_products(user: User, conn: Conn) -> list[BankProductOut]:
    return [BankProductOut(**p) for p in await bank_products(conn, [])]


@router.get(
    "/news/{news_id}",
    response_model=NewsOut,
    responses={**ERR_401, 404: {"description": "Новость не найдена"}},
    summary="Новость (первоисточник события)",
)
async def get_news(news_id: uuid.UUID, user: User, conn: Conn) -> NewsOut:
    row = await conn.fetchrow(
        """SELECT n.*, en.event_id, s.is_fixture FROM core.news_items n
           JOIN core.sources s ON s.id = n.source_id
           LEFT JOIN core.event_news en ON en.news_id = n.id WHERE n.id = $1""",
        news_id,
    )
    if row is None:
        raise ProblemError(404, "not-found", "News not found")
    return NewsOut(
        news_id=row["id"],
        event_id=row["event_id"],
        title=row["title"],
        summary=row["summary"],
        body=row["body"],
        url=row["url"],
        publisher=row["publisher"],
        published_at=row["published_at"],
        event_type=row["event_type"],
        tags=json.loads(row["tags"]),
        flags=list(row["flags"]),
        is_fixture=row["is_fixture"],
    )
